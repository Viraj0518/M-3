"""The FOUR streaming invariants, proved with an in-process fake source (no
broker) so they run anywhere. Each maps to the docstring in
vendor/unblock-reuse/.../comms/monitor.py:28-42.
"""

from __future__ import annotations

import asyncio

from realtime.consumer import StreamConsumer


class FakeMsg:
    """The subset of a laser ConsumerMessage the consumer touches."""

    def __init__(self, partition: int, offset: int, value: dict, current: int = 0) -> None:
        self.partition_id = partition
        self.offset = offset
        self.current_offset = current
        self.message_id = None  # common frames lack it — dedup must NOT use it
        self._value = value

    def json(self) -> dict:
        return self._value


class FakeSource:
    """Yields a fixed list of messages, then blocks (so stop_on_idle can fire).
    Records every commit so ordering can be asserted."""

    def __init__(self, msgs) -> None:
        self._msgs = list(msgs)
        self._i = 0
        self.commits = []
        self.shutdown_calls = 0

    async def next(self):
        if self._i < len(self._msgs):
            m = self._msgs[self._i]
            self._i += 1
            return m
        await asyncio.sleep(3600)  # idle -> StreamConsumer.wait_for times out

    def commit(self, msg):
        self.commits.append((msg.partition_id, msg.offset))

    def shutdown(self):
        self.shutdown_calls += 1


def _drain(consumer, *, handle=None):
    async def go():
        seen = []
        async for evt in consumer.events(stop_on_idle=True):
            seen.append(evt)
            if handle is not None:
                await handle(evt)
            await consumer.commit(evt)
        return seen
    return asyncio.run(go())


# ── INVARIANT 2: dedup on STREAM SEQUENCE, not message_id ───────────────────
def test_dedup_on_sequence_not_message_id():
    # two frames with the SAME (partition, offset) seq but different content;
    # message_id is None on both, so a message_id-based dedup would crash/pass
    # the duplicate through. Sequence dedup catches it.
    msgs = [
        FakeMsg(0, 0, {"n": 1}),
        FakeMsg(0, 1, {"n": 2}),
        FakeMsg(0, 0, {"n": 1, "redelivered": True}),  # duplicate SEQ (0,0)
        FakeMsg(1, 0, {"n": 3}),                        # different partition, same offset 0 -> NOT a dup
    ]
    src = FakeSource(msgs)
    c = StreamConsumer(src, idle_timeout=0.2)
    seen = _drain(c)

    surfaced_seqs = [tuple(e["seq"]) for e in seen]
    assert surfaced_seqs == [(0, 0), (0, 1), (1, 0)], "the (0,0) redelivery must not re-surface; (1,0) is distinct"
    assert c.duplicates == 1
    assert c.fetched == 4 and c.surfaced == 3
    # the duplicate was still ACKED so the broker stops redelivering it
    assert (0, 0) in src.commits
    assert src.commits.count((0, 0)) == 2  # surfaced-then-committed once + dup acked once


# ── INVARIANT 1: enqueue(surface)-before-ack; commit is caller-driven ───────
def test_surface_before_ack_and_commit_is_explicit():
    msgs = [FakeMsg(0, i, {"n": i}) for i in range(5)]
    src = FakeSource(msgs)
    c = StreamConsumer(src, idle_timeout=0.2)

    async def go():
        surfaced_before_any_commit = []
        async for evt in c.events(stop_on_idle=True):
            # at the moment of surfacing, THIS event has not been committed yet
            assert (evt["partition"], evt["offset"]) not in src.commits
            surfaced_before_any_commit.append(evt)
            await c.commit(evt)
        return surfaced_before_any_commit

    seen = asyncio.run(go())
    assert len(seen) == 5
    assert c.committed == 5
    # every surfaced record was committed, in order
    assert src.commits == [(0, i) for i in range(5)]


# ── INVARIANT 3: backpressure, not drop — a slow handler drops nothing ──────
def test_backpressure_slow_handler_drops_nothing():
    msgs = [FakeMsg(0, i, {"n": i}) for i in range(50)]
    src = FakeSource(msgs)
    c = StreamConsumer(src, idle_timeout=0.2)

    async def slow(evt):
        await asyncio.sleep(0.001)  # a slow side effect

    seen = _drain(c, handle=slow)
    assert len(seen) == 50
    assert c.fetched == 50 and c.surfaced == 50 and c.committed == 50
    # pull-based: never read ahead of the writer, so nothing is buffered-then-dropped
    assert [e["offset"] for e in seen] == list(range(50))


# ── INVARIANT 4: cancellation-safe, idempotent teardown ─────────────────────
def test_teardown_is_idempotent():
    src = FakeSource([FakeMsg(0, 0, {"n": 0})])
    c = StreamConsumer(src, idle_timeout=0.2)
    _drain(c)

    async def stop_twice():
        await c.stop()
        await c.stop()  # idempotent — must not raise

    asyncio.run(stop_twice())
    assert src.shutdown_calls == 2  # each stop() forwards a best-effort shutdown
