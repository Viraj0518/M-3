"""[2] The durable consumer — the FOUR unblock streaming invariants, ported.

Source of the invariants (stated verbatim in the docstring at
``vendor/unblock-reuse/unblock_mcp/src/unblock_mcp/comms/monitor.py:28-42``):

  1. ENQUEUE-BEFORE-ACK — append to the buffer BEFORE committing the offset, so
     a crash in between is a harmless at-least-once redelivery, never silent
     loss. Here it is even stronger: we commit only AFTER the caller's side
     effect lands (``commit(evt)``), i.e. commit-after-side-effects.
  2. DEDUP ON STREAM SEQUENCE, NOT message_id — keyed on ``(partition, offset)``
     (``laser_io.normalize_record``'s ``seq``). The unblock docstring warns the
     common frames lack ``message_id``; the log coordinate is always present.
  3. BACKPRESSURE, NOT DROP — a bounded buffer; when it fills the fetch loop
     STOPS pulling. With ``auto_commit='disabled'`` an un-enqueued frame stays
     uncommitted and durable on the broker. We never ack-then-drop.
  4. CANCELLATION-SAFE TEARDOWN — ``stop()`` cancels and awaits the fetch task
     cleanly and is idempotent.

Delivery is AT-LEAST-ONCE by design, so every downstream write MUST be
idempotent — the graph writer MERGEs on domain ids and the action layer fences
on an idempotency key.

TESTABILITY: the consumer drives any object exposing ``async next()`` and
``async commit(msg)`` (plus the record attributes ``partition_id`` / ``offset``
/ ``json()``). ``realtime/tests`` injects a fake to prove the invariants without
a live broker; the pipeline injects a real ``laser-sdk`` consumer group.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Optional, Tuple

from . import laser_io


class StreamConsumer:
    """PULL-BASED consumer with the four invariants:

        consumer = StreamConsumer(src)
        async for evt in consumer.events(stop_on_idle=True):
            handle(evt["value"])           # your side effect
            await consumer.commit(evt)      # commit AFTER it lands
        await consumer.stop()

    WHY PULL-BASED, NOT A BACKGROUND-FILLED BOUNDED QUEUE
    ----------------------------------------------------
    unblock's monitor is a background subscriber, so it uses a bounded queue with
    a producer task. A laser-sdk consumer is NOT safe for a concurrent
    ``next()`` and ``commit()`` on the SAME partition — a background fetch loop
    that reads ahead while the caller commits deadlocks (verified in-session:
    it wedged after 8 of 400 records). Pulling one record at a time, only when
    the caller asks, means ``next()`` and ``commit()`` never overlap on a
    partition — and it is the STRONGEST form of "backpressure, not drop": the
    reader can never run ahead of the writer, so nothing is ever buffered-then-
    dropped. All four invariants below are preserved.
    """

    def __init__(
        self,
        source: Any,
        *,
        dedup_cap: int = 500_000,
        idle_timeout: float = 2.0,
    ) -> None:
        self._src = source
        self._seen: set = set()
        self._dedup_cap = dedup_cap
        self._idle_timeout = idle_timeout
        self._stopped = False
        # observability counters (asserted by the invariant tests)
        self.fetched = 0
        self.duplicates = 0
        self.surfaced = 0
        self.committed = 0

    def _mark_seen(self, seq: Tuple) -> None:
        if len(self._seen) >= self._dedup_cap:
            # bounded memory: the window is far larger than any redelivery gap.
            self._seen.clear()
        self._seen.add(seq)

    async def _next(self, stop_on_idle: bool) -> Any:
        if stop_on_idle:
            return await asyncio.wait_for(self._src.next(), timeout=self._idle_timeout)
        return await self._src.next()

    async def _commit_msg(self, msg: Any) -> None:
        res = self._src.commit(msg)
        if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
            await res

    # ── the public pull API ──────────────────────────────────────────────────
    async def events(self, *, stop_on_idle: bool = False) -> AsyncIterator[Dict[str, Any]]:
        """Yield normalized records, one at a time, ON DEMAND. With
        ``stop_on_idle`` the iterator ends once the durable backlog is drained
        (a bounded run); else it runs until :meth:`stop`. Commit each event with
        :meth:`commit` after your side effect lands."""
        while not self._stopped:
            try:
                msg = await self._next(stop_on_idle)
            except asyncio.TimeoutError:
                break  # backlog drained (bounded run)
            except StopAsyncIteration:
                break
            except asyncio.CancelledError:
                raise

            self.fetched += 1
            rec = laser_io.normalize_record(msg)
            seq: Tuple = rec["seq"]

            # ── INVARIANT 2: dedup on stream SEQUENCE (never message_id) ──────
            if seq in self._seen:
                self.duplicates += 1
                # a redelivered frame is ACKED (so it stops redelivering) but
                # NOT re-surfaced.
                await self._commit_msg(msg)
                continue

            self._mark_seen(seq)
            rec["_msg"] = msg
            self.surfaced += 1
            # ── INVARIANT 1: the record is SURFACED (handed to the caller)
            # before any ack — the caller commits AFTER its side effect. A crash
            # between surface and commit is a harmless at-least-once redelivery.
            yield rec

    async def commit(self, evt: Dict[str, Any]) -> None:
        """Commit the offset of a handled event — INVARIANT 1, after side effects.
        Safe to call once per surfaced event; ``next()`` is never in flight on
        this partition at commit time (pull-based), so it cannot deadlock."""
        msg = evt.get("_msg")
        if msg is None:
            return
        await self._commit_msg(msg)
        self.committed += 1

    # ── INVARIANT 4: cancellation-safe, idempotent teardown ──────────────────
    async def stop(self) -> None:
        self._stopped = True
        shutdown = getattr(self._src, "shutdown", None)
        if callable(shutdown):
            with contextlib.suppress(Exception):
                res = shutdown()
                if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                    await res

    def stats(self) -> Dict[str, int]:
        return {
            "fetched": self.fetched,
            "surfaced": self.surfaced,
            "duplicates": self.duplicates,
            "committed": self.committed,
        }


class PartitionFanIn:
    """Composes N single-partition consumers into ONE reader exposing the
    ``async next()`` / ``commit(msg)`` / ``shutdown()`` shape ``StreamConsumer``
    drives. This is what makes a single process read a MULTI-partition topic
    completely — a consumer group with one member only ever gets partition 0
    (verified in-session), which silently dropped 34% of a 4-partition topic.

    ``next()`` races a pending ``next()`` per partition and returns the first to
    arrive, so a drained partition never starves a partition that still has
    data. ``commit`` routes back to the owning partition consumer by
    ``partition_id``. The dedup key ``(partition, offset)`` stays globally unique
    across the fan-in, so ``StreamConsumer``'s invariants are unchanged.
    """

    def __init__(self, consumers: list) -> None:
        self._cs = list(consumers)
        self._pending: Dict[int, asyncio.Task] = {}
        self._owner: Dict[Any, Any] = {}

    async def next(self) -> Any:
        # ensure one in-flight fetch per partition consumer
        for i, c in enumerate(self._cs):
            task = self._pending.get(i)
            if task is None or task.done() and task.cancelled():
                self._pending[i] = asyncio.ensure_future(self._pull(c))
        if not self._pending:
            raise StopAsyncIteration
        done, _pending = await asyncio.wait(
            list(self._pending.values()), return_when=asyncio.FIRST_COMPLETED
        )
        # return the first completed; leave the rest in flight for next time
        for i, task in list(self._pending.items()):
            if task in done:
                del self._pending[i]
                msg = task.result()
                self._owner[getattr(msg, "partition_id", None)] = self._cs[i]
                return msg
        # should not happen; recurse to await again
        return await self.next()

    @staticmethod
    async def _pull(consumer: Any) -> Any:
        res = consumer.next()
        return await res if asyncio.iscoroutine(res) or hasattr(res, "__await__") else res

    def commit(self, msg: Any) -> Any:
        owner = self._owner.get(getattr(msg, "partition_id", None))
        if owner is None:
            return None
        return owner.commit(msg)

    async def shutdown(self) -> None:
        for task in self._pending.values():
            task.cancel()
        for task in list(self._pending.values()):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._pending.clear()
        for c in self._cs:
            sd = getattr(c, "shutdown", None)
            if callable(sd):
                with contextlib.suppress(Exception):
                    res = sd()
                    if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                        await res


async def open_consumer(
    log: laser_io.LaserLog,
    topic: str,
    group: str,
    *,
    polling: str = "first",
    partitions: int = None,
    idle_timeout: float = 2.0,
) -> StreamConsumer:
    """Bind a COMPLETE reader over ``topic`` (all partitions) and wrap it in the
    invariant-enforcing StreamConsumer. ``polling='first'`` reads the whole
    history from offset 0 (the warm-build read)."""
    from memory import config as _config

    parts = partitions if partitions is not None else _config.TOPIC_PARTITIONS
    consumers = await log.partition_consumers(topic, group, partitions=parts, polling=polling)
    fan_in = PartitionFanIn(consumers)
    return StreamConsumer(fan_in, idle_timeout=idle_timeout)


__all__ = ["StreamConsumer", "PartitionFanIn", "open_consumer"]
