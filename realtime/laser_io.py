"""The LaserData seam — Log primitive ONLY, gated behind capabilities().

WHY THIS MODULE EXISTS
======================
Every process that touches the log spine (producer, consumer, replay, the
bridge's three stream verbs) speaks to LaserData THROUGH HERE, so the transport
choice, the connection string handling and the SCOPE DISCIPLINE live in one
place. That is the same move ``memory/graphstore.py`` makes for FalkorDB.

TRANSPORT DECISION (documented, per the build brief)
====================================================
Verified live in-session against the running laser-stack iggy (127.0.0.1:8090,
stream ``live``): the ``laser-sdk`` package drives Iggy's Log primitive cleanly
via a thin async API, so we use it directly — NO raw-TCP or bare-iggy fallback
was needed.

  * connect            ``await Laser.connect(conn, stream=...)``
  * ensure a topic     ``await topic.ensure(partitions)``
  * publish            ``prod = await topic.producer(...); await prod.init();
                         await prod.send(payload_bytes, key=...)``
                       (Producer.send takes str/bytes, NOT a dict — we JSON-
                        encode. Verified: a dict raises InvalidError.)
  * consume (group)    ``cg = await topic.consumer_group(group, polling=...,
                         auto_commit='disabled'); await cg.init();
                         msg = await cg.next(); await cg.commit(msg)``
  * replay from 0      ``cur = await topic.replay(); batch = await cur.poll()``

MESSAGE CONTRACT (verified by round-trip)
=========================================
A ``ConsumerMessage`` / replay record exposes, as ATTRIBUTES (not methods) —
this bit us in-session, they look callable but are ints::

    msg.offset          # per-partition log position (0-based) == the SEQUENCE
    msg.current_offset  # the consumer group's high-water
    msg.partition_id    # which partition
    msg.message_id      # 128-bit iggy id — PRESENT here but the unblock monitor
                        # docstring warns common frames lack it, so we DO NOT
                        # dedup on it (see consumer.py). ``.json()`` IS a method.

THE STREAM SEQUENCE, for the dedup invariant, is therefore ``(partition_id,
offset)`` — a durable, always-present log coordinate, never ``message_id``.

CAPABILITIES GATE
=================
``capabilities()`` is called once on connect. Against this substrate the premium
flags (graph/query/managed) happen to read TRUE, but PALIMPSEST refuses to use
anything beyond Log — ``require_log_only()`` records the snapshot and we simply
never call the premium methods. That is what keeps local-stack -> Cloud -> bare
Iggy a one-env-var swap.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Dict, List, Optional

from memory import config

# ─── the SDK, GUARDED (mirrors graphstore's falkordb guard) ─────────────────
# server.py / the bridge must import cleanly on py3.9 with no venv hot, and any
# module reachable from a stream verb must not hard-require laser_sdk at import.
try:  # pragma: no cover - import-environment dependent
    import laser_sdk as _laser  # type: ignore[import-not-found]

    LASER_AVAILABLE = True
except Exception:  # noqa: BLE001 - any import failure is the same outcome
    _laser = None  # type: ignore[assignment]
    LASER_AVAILABLE = False


class LaserUnavailable(RuntimeError):
    """The log spine cannot be reached / the SDK is not installed. Raised, never
    swallowed — a silent empty tail is indistinguishable from 'nothing is
    happening', and 'the datastream is constant' is the entire thesis."""


async def _aw(value: Any) -> Any:
    """Await ``value`` if it is awaitable, else return it. The SDK returns
    coroutines from most calls; a couple of accessors are plain values."""
    if inspect.isawaitable(value):
        return await value
    return value


def _require_sdk() -> Any:
    if not LASER_AVAILABLE:
        raise LaserUnavailable(
            "the `laser-sdk` package is not installed — the log spine cannot be "
            "opened. Install it in the realtime venv: "
            "pip install -r realtime/requirements.txt (needs Python >= 3.12)."
        )
    return _laser


def normalize_record(rec: Any) -> Dict[str, Any]:
    """One ConsumerMessage / replay record -> a plain dict with its LOG
    COORDINATE. ``seq`` is ``(partition, offset)`` — the dedup key.

    ``value`` is the decoded JSON body when it parses, else the raw text under
    ``raw``. Never raises: a poison record still yields a coordinate so the
    consumer can ack-and-skip rather than wedge the whole partition.
    """
    partition = getattr(rec, "partition_id", None)
    offset = getattr(rec, "offset", None)
    current = getattr(rec, "current_offset", None)
    message_id = getattr(rec, "message_id", None)
    out: Dict[str, Any] = {
        "partition": partition,
        "offset": offset,
        "seq": (partition, offset),
        "current_offset": current,
        "message_id": message_id,
    }
    try:
        out["value"] = rec.json()
    except Exception:  # noqa: BLE001 - non-JSON payload; keep the coordinate
        try:
            payload = getattr(rec, "payload", None)
            out["value"] = None
            out["raw"] = payload.decode("utf-8", "replace") if isinstance(payload, (bytes, bytearray)) else str(payload)
        except Exception:  # noqa: BLE001
            out["value"] = None
    return out


class LaserLog:
    """A connected LaserData handle, scoped to ONE stream. Cheap to hold; the
    SDK shares the connection and producer cache internally."""

    def __init__(self, laser: Any, stream: str, capabilities: Optional[dict]) -> None:
        self._laser = laser
        self._stream = stream
        self.capabilities = capabilities or {}
        self._producers: Dict[str, Any] = {}

    # ── connection ──────────────────────────────────────────────────────────
    @classmethod
    async def connect(cls, *, stream: Optional[str] = None) -> "LaserLog":
        """Open the log spine using ``memory/config.laser_connection()`` — the
        ONE place the connection string is read (``iggy:laser@...``, never the
        SDK examples' ``iggy:iggy@...``). A stream MUST be pinned or the SDK
        raises ConfigError('no default stream set') — verified in-session."""
        sdk = _require_sdk()
        conn = config.laser_connection()  # raises with an honest message if unset
        st = stream or config.LASER_STREAM
        try:
            laser = await _aw(sdk.Laser.connect(conn, stream=st))
        except Exception as exc:  # noqa: BLE001
            raise LaserUnavailable(
                "could not connect to LaserData on stream {0!r}: {1}: {2}. "
                "Is the laser-stack iggy up on 127.0.0.1:8090? Is the connection "
                "string the exact `iggy:laser@...` ./scripts/up printed?".format(
                    st, type(exc).__name__, exc
                )
            ) from exc
        caps = await cls._read_capabilities(laser)
        return cls(laser, st, caps)

    @staticmethod
    async def _read_capabilities(laser: Any) -> dict:
        """Snapshot the advertised premium capabilities. We RECORD them and use
        NONE of them — Log primitive only."""
        try:
            caps = await _aw(laser.capabilities())
        except Exception:  # noqa: BLE001
            return {}
        flags = (
            "graph", "query", "managed", "kv", "forks", "watch", "authz",
            "durable_dedup", "sessions", "agent_workflow", "a2a_gateway",
        )
        snap: Dict[str, Any] = {}
        for f in flags:
            try:
                snap[f] = bool(getattr(caps, f))
            except Exception:  # noqa: BLE001
                pass
        return snap

    def require_log_only(self) -> dict:
        """Assert we are operating within the Log primitive and return the
        recorded capability snapshot for logging. This never *enables* a premium
        path — it exists so the discipline is a call site, not just a comment."""
        return dict(self.capabilities)

    # ── topics ──────────────────────────────────────────────────────────────
    async def topic(self, name: str) -> Any:
        return await _aw(self._laser.topic(name))

    async def ensure_topic(self, name: str, partitions: int = config.TOPIC_PARTITIONS) -> Any:
        t = await self.topic(name)
        try:
            await _aw(t.ensure(partitions))
        except Exception:  # noqa: BLE001 - already exists / benign
            pass
        return t

    # ── publish ─────────────────────────────────────────────────────────────
    async def producer(self, topic_name: str) -> Any:
        """A cached, INITIALISED producer for ``topic_name``. ``producer.init()``
        is called for you BEFORE the first send (SCHEMA.md trap 9 / PIPE_NOTES
        §6: required, not optional)."""
        prod = self._producers.get(topic_name)
        if prod is not None:
            return prod
        t = await self.ensure_topic(topic_name)
        prod = await _aw(
            t.producer(
                batch_length=config.PRODUCER_BATCH_LENGTH,
                linger_ms=config.PRODUCER_LINGER_MS,
                partitions=config.TOPIC_PARTITIONS,
            )
        )
        await _aw(prod.init())
        self._producers[topic_name] = prod
        return prod

    async def publish(self, topic_name: str, value: Any, *, key: Optional[str] = None) -> None:
        """Append ONE durable record. ``value`` is JSON-encoded to bytes because
        Producer.send rejects a dict (InvalidError, verified). ``key`` selects
        the partition, so same-key records keep their relative order across
        parallel consumers (key=wiki in the edge tap)."""
        prod = await self.producer(topic_name)
        payload = value if isinstance(value, (bytes, bytearray)) else json.dumps(value, default=str).encode("utf-8")
        await _aw(prod.send(payload, key=key))

    # ── consume (group) ─────────────────────────────────────────────────────
    async def consumer_group(
        self,
        topic_name: str,
        group: str,
        *,
        polling: str = "first",
        batch_length: int = 200,
    ) -> Any:
        """A consumer-group reader with ``auto_commit='disabled'`` — commit
        AFTER side effects land (crash-safety beat). ``polling='first'`` starts a
        fresh group at offset 0 (the whole-history read warm build needs)."""
        t = await self.topic(topic_name)
        cg = await _aw(
            t.consumer_group(
                group,
                polling=polling,
                auto_commit="disabled",
                batch_length=batch_length,
            )
        )
        await _aw(cg.init())
        return cg

    async def partition_consumer(
        self,
        topic_name: str,
        name: str,
        partition: int,
        *,
        polling: str = "first",
        batch_length: int = 200,
    ) -> Any:
        """A single-PARTITION consumer with ``auto_commit='disabled'``. Verified
        in-session: a consumer-GROUP with one member is assigned only partition
        0, so reading a 4-partition topic completely requires one consumer per
        partition (the design's 'one partition per member'). ``consumer.py``'s
        fan-in composes these into one complete reader."""
        t = await self.topic(topic_name)
        c = await _aw(
            t.consumer(
                name,
                partition=partition,
                polling=polling,
                auto_commit="disabled",
                batch_length=batch_length,
            )
        )
        await _aw(c.init())
        return c

    async def partition_consumers(
        self,
        topic_name: str,
        name: str,
        *,
        partitions: int = config.TOPIC_PARTITIONS,
        polling: str = "first",
    ) -> List[Any]:
        """One consumer per partition, for a complete read of the whole topic."""
        return [
            await self.partition_consumer(topic_name, "{0}-p{1}".format(name, p), p, polling=polling)
            for p in range(partitions)
        ]

    # ── replay (from an offset) ─────────────────────────────────────────────
    async def replay_all(self, topic_name: str) -> Any:
        """A replay cursor from the VERY BEGINNING of every partition (offset 0)
        — the rewind. ``await cursor.poll()`` returns a batch (empty when
        drained)."""
        t = await self.topic(topic_name)
        return await _aw(t.replay())

    async def drain_from_zero(self, topic_name: str, *, max_records: int = 1_000_000) -> List[Dict[str, Any]]:
        """Replay a whole topic from offset 0 into a list of normalized records,
        in log order. Bounded by ``max_records`` so a runaway topic cannot hang.

        This is the primitive under BOTH the replay path (money beat) and the
        bounded stream_tail read."""
        cur = await self.replay_all(topic_name)
        out: List[Dict[str, Any]] = []
        empties = 0
        while len(out) < max_records:
            batch = await _aw(cur.poll())
            if not batch:
                empties += 1
                # poll() returns empty at the live edge; one empty poll is the
                # end of the durable backlog for a replay cursor.
                break
            for rec in batch:
                out.append(normalize_record(rec))
                if len(out) >= max_records:
                    break
        return out


async def connect(*, stream: Optional[str] = None) -> LaserLog:
    """Module-level convenience: ``log = await laser_io.connect()``."""
    return await LaserLog.connect(stream=stream)


__all__ = [
    "LASER_AVAILABLE",
    "LaserUnavailable",
    "LaserLog",
    "connect",
    "normalize_record",
]
