"""The bridge's LaserData adapter — the live backing for the three stream verbs.

``server.py`` imports this LAZILY (inside the handler bodies) so a bare
``import app.bridge.server`` on the system Python 3.9 never pulls in
``laser-sdk`` — exactly the clean-import guarantee the MCP/FalkorDB guards give.
Everything reachable from here (``realtime.laser_io``) guards its own SDK import
too, so this module also imports cleanly without the SDK; only a live call
raises, and even then the verbs degrade to an HONEST envelope rather than a
crash (the projector needs a 200 with a declared-empty tail, never a throw — see
``rest.py``).

SCOPE: publish / bounded-tail / replay-from-offset. Log primitive ONLY.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from memory import config

from realtime import laser_io, pipeline, replay

# One cached connection for the bridge process (single event loop). Never cached
# on failure, so a transient outage self-heals on the next call.
_LOG: Optional[laser_io.LaserLog] = None
_LOCK = asyncio.Lock()

#: Bounded-scan cap for the tail read (see `tail`). The money beat is replay,
#: not an unbounded live tail; this keeps a /stream_tail poll cheap.
TAIL_MAX_SCAN = 10_000


async def get_log() -> laser_io.LaserLog:
    """The cached LaserLog, connecting on first use. Raises LaserUnavailable
    (honest) when the SDK is missing or iggy is unreachable."""
    global _LOG
    if _LOG is not None:
        return _LOG
    async with _LOCK:
        if _LOG is None:
            _LOG = await laser_io.connect()
    return _LOG


def reset_log() -> None:
    """Drop the cached connection (tests / a reconnect hook)."""
    global _LOG
    _LOG = None


def _laser_down(exc: Exception) -> Dict[str, Any]:
    return {
        "reachable": False,
        "reason": "{0}: {1}".format(type(exc).__name__, exc),
        "endpoint": "127.0.0.1:8090",
    }


# ═══════════════════════════════════════════════════════════════════════════
# stream_publish
# ═══════════════════════════════════════════════════════════════════════════

async def publish(topic: str, payload: Any, *, key: Optional[str] = None) -> Dict[str, Any]:
    """Append one durable record to ``topic`` keyed by ``key``. Returns an
    honest envelope: ``ok:false`` + ``code`` only on a genuine failure."""
    try:
        log = await get_log()
    except laser_io.LaserUnavailable as exc:
        return {"ok": False, "code": "LASER_UNAVAILABLE", "error": str(exc),
                "topic": topic, "laser": _laser_down(exc)}
    try:
        await log.publish(topic, payload, key=key)
    except Exception as exc:  # noqa: BLE001
        reset_log()
        return {"ok": False, "code": "LASER_PUBLISH_FAILED",
                "error": "{0}: {1}".format(type(exc).__name__, exc), "topic": topic}
    return {"ok": True, "topic": topic, "key": key, "published": 1,
            "capabilities": log.require_log_only()}


# ═══════════════════════════════════════════════════════════════════════════
# stream_tail  (real records + offset — no longer a stub)
# ═══════════════════════════════════════════════════════════════════════════

async def tail(topic: str, *, limit: int = 25, since_offset: Optional[int] = None) -> Dict[str, Any]:
    """The newest ``limit`` records off ``topic``, with their log offsets.

    Bounded-scan implementation: replays up to ``TAIL_MAX_SCAN`` records from
    offset 0 and returns the last ``limit`` of the scanned window. Real records,
    real offsets, HONESTLY labelled (`scanned`, `capped`). On a laser outage it
    returns a declared-empty tail with ``ok:true`` so the projector strip stays
    live rather than throwing to the mock graph.
    """
    try:
        log = await get_log()
    except laser_io.LaserUnavailable as exc:
        return {
            "ok": True, "stub": False, "topic": topic, "records": [], "events": [],
            "offset": None, "limit": limit, "laser": _laser_down(exc),
            "note": "log spine unreachable — declared-empty tail (projector stays live)",
        }
    try:
        records = await log.drain_from_zero(topic, max_records=TAIL_MAX_SCAN)
    except Exception as exc:  # noqa: BLE001
        reset_log()
        return {
            "ok": True, "stub": False, "topic": topic, "records": [], "events": [],
            "offset": None, "limit": limit, "laser": _laser_down(exc),
            "note": "tail read failed — declared-empty tail",
        }

    if since_offset is not None:
        records = [r for r in records if (r.get("offset") or 0) >= since_offset]

    scanned = len(records)
    window = records[-limit:] if limit and limit > 0 else records
    out: List[Dict[str, Any]] = [
        {
            "partition": r.get("partition"),
            "offset": r.get("offset"),
            "seq": list(r.get("seq")) if r.get("seq") else None,
            "value": r.get("value"),
        }
        for r in window
    ]
    max_offset = max((r.get("offset") or 0 for r in records), default=None)
    return {
        "ok": True,
        "stub": False,
        "topic": topic,
        "records": out,
        "events": out,          # both keys: rest.py's flattenRecords reads either
        "count": len(out),
        "offset": max_offset,
        "scanned": scanned,
        "capped": scanned >= TAIL_MAX_SCAN,
        "limit": limit,
    }


# ═══════════════════════════════════════════════════════════════════════════
# stream_replay  (the rewind)
# ═══════════════════════════════════════════════════════════════════════════

async def stream_replay(
    topic: str,
    *,
    from_offset: int = 0,
    target_graph: Optional[str] = None,
    embedder_kind: str = "test",
    use_gate: bool = True,
    reset: bool = True,
) -> Dict[str, Any]:
    """Replay ``topic`` from ``from_offset`` (0 = the beginning). When
    ``target_graph`` is given, re-derives the graph into it (the A/B); otherwise
    returns the replayed records + offsets so a caller can drive its own build.

    Only offset 0 (full rewind) is supported for the graph re-derive today; a
    non-zero ``from_offset`` returns records but does not partial-rebuild, and
    says so on the envelope (honest, not silent).
    """
    try:
        log = await get_log()
    except laser_io.LaserUnavailable as exc:
        return {"ok": False, "code": "LASER_UNAVAILABLE", "error": str(exc),
                "topic": topic, "laser": _laser_down(exc)}

    if target_graph:
        if from_offset and from_offset != 0:
            return {
                "ok": False, "code": "PARTIAL_REPLAY_UNSUPPORTED",
                "error": "graph re-derive supports from_offset=0 (full rewind) only; "
                         "got {0}. Omit target_graph to fetch records from an offset.".format(from_offset),
                "topic": topic,
            }
        try:
            built = await replay.replay_into_cold(
                log, topic=topic, cold_graph=target_graph,
                embedder_kind=embedder_kind, use_gate=use_gate, reset=reset,
            )
        except Exception as exc:  # noqa: BLE001
            reset_log()
            return {"ok": False, "code": "REPLAY_FAILED",
                    "error": "{0}: {1}".format(type(exc).__name__, exc), "topic": topic}
        digest = built["digest"]
        return {
            "ok": True,
            "topic": topic,
            "from_offset": 0,
            "target_graph": built["graph_key"],
            "records": built["records"],
            "node_count": digest["node_count"],
            "edge_count": digest["edge_count"],
            "digest": digest["digest"],
            "stats": built["stats"],
            "source": "replay-from-0",
        }

    # No target graph: just fetch records from the offset.
    try:
        records = await replay.drain_via_replay(log, topic)
    except Exception as exc:  # noqa: BLE001
        reset_log()
        return {"ok": False, "code": "REPLAY_FAILED",
                "error": "{0}: {1}".format(type(exc).__name__, exc), "topic": topic}
    if from_offset:
        records = [r for r in records if (r.get("offset") or 0) >= from_offset]
    out = [{"partition": r.get("partition"), "offset": r.get("offset"), "value": r.get("value")}
           for r in records]
    return {"ok": True, "topic": topic, "from_offset": from_offset,
            "records": out, "count": len(out), "source": "replay-cursor"}


async def verify_parity(
    *,
    topic: str = config.TOPIC_SIGNAL_RAW,
    warm_graph: str = config.GRAPH_WARM,
    cold_graph: str = config.GRAPH_COLD,
    embedder_kind: str = "test",
    use_gate: bool = True,
) -> Dict[str, Any]:
    """Convenience wrapper for the gate: two-mechanism replay parity."""
    log = await get_log()
    return await replay.verify_replay_parity(
        log, topic=topic, warm_graph=warm_graph, cold_graph=cold_graph,
        embedder_kind=embedder_kind, use_gate=use_gate,
    )


__all__ = ["get_log", "reset_log", "publish", "tail", "stream_replay", "verify_parity", "TAIL_MAX_SCAN"]
