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

#: Idle bound (seconds) for the live tail's per-partition ``next()``: once a
#: partition's ``polling='last'`` backfill is exhausted the next read would block
#: waiting for a NEW append, so we stop there. Full partitions never wait.
_TAIL_IDLE_S = 1.0

#: Bounded SEEK window for the replay/scrub: records per partition FROM the seek
#: point. The money beat is a rewind to an INSTANT, not a full re-fetch, so a
#: wall-clock/offset replay returns a window, not the whole tail.
_SEEK_LIMIT_DEFAULT = 500
_SEEK_IDLE_S = 1.5


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

async def publish(
    topic: str,
    payload: Any,
    *,
    key: Optional[str] = None,
    headers: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Append one durable record to ``topic`` keyed by ``key``. Returns an
    honest envelope: ``ok:false`` + ``code`` only on a genuine failure.

    ``headers`` carry out-of-band attribution (``author_agent`` / ``case_id``)
    onto the record so a consumer reads provenance from ``rec['headers']`` rather
    than re-parsing the body. It is forwarded only when present, so the bridge's
    existing key-only publish path is byte-for-byte unchanged."""
    try:
        log = await get_log()
    except laser_io.LaserUnavailable as exc:
        return {"ok": False, "code": "LASER_UNAVAILABLE", "error": str(exc),
                "topic": topic, "laser": _laser_down(exc)}
    try:
        await log.publish(topic, payload, key=key, headers=headers)
    except Exception as exc:  # noqa: BLE001
        reset_log()
        return {"ok": False, "code": "LASER_PUBLISH_FAILED",
                "error": "{0}: {1}".format(type(exc).__name__, exc), "topic": topic}
    out = {"ok": True, "topic": topic, "key": key, "published": 1,
           "capabilities": log.require_log_only()}
    if headers:
        out["attributed"] = True
    return out


# ═══════════════════════════════════════════════════════════════════════════
# stream_tail  (real records + offset — no longer a stub)
# ═══════════════════════════════════════════════════════════════════════════

async def tail(topic: str, *, limit: int = 25, since_offset: Optional[int] = None) -> Dict[str, Any]:
    """The newest ``limit`` records off ``topic`` — the LIVE EDGE, with offsets.

    LIVE-CONSUMER implementation. A ``polling='last'`` consumer on EACH of the
    ``config.TOPIC_PARTITIONS`` partitions backfills that partition's newest
    ``limit`` records, ending at its ``current_offset`` (the live edge — verified
    in-session), and we return the newest ``limit`` of the union ordered by the
    log's own append clock. ``auto_commit`` is disabled, so repeated polls are
    idempotent and never advance a cursor.

    This REPLACES the old bounded-scan tail, which returned the last ``limit`` of
    the FIRST 10k records from offset 0: on a 50-100 ev/s firehose that froze at
    ~offset 9,975 within 2-3 minutes and never showed the present, contradicting
    the tool doc's promise of "what is happening RIGHT NOW".

    On a laser outage it returns a declared-empty tail with ``ok:true`` so the
    projector strip stays live rather than throwing to the mock graph.
    """
    try:
        log = await get_log()
    except laser_io.LaserUnavailable as exc:
        return {
            "ok": True, "stub": False, "topic": topic, "records": [], "events": [],
            "offset": None, "limit": limit, "laser": _laser_down(exc),
            "note": "log spine unreachable — declared-empty tail (projector stays live)",
        }

    n = limit if (limit and limit > 0) else 25
    parts = config.TOPIC_PARTITIONS

    async def _tail_partition(p: int) -> List[Dict[str, Any]]:
        c = await log.consumer(
            topic, "ui-tail-p{0}".format(p), partition=p,
            polling="last", batch_length=n, auto_commit="disabled",
        )
        try:
            return await log.drain_consumer(c, max_records=n, idle_timeout=_TAIL_IDLE_S)
        finally:
            await log.shutdown_consumer(c)

    try:
        per_partition = await asyncio.gather(*[_tail_partition(p) for p in range(parts)])
    except Exception as exc:  # noqa: BLE001
        reset_log()
        return {
            "ok": True, "stub": False, "topic": topic, "records": [], "events": [],
            "offset": None, "limit": limit, "laser": _laser_down(exc),
            "note": "tail read failed — declared-empty tail",
        }

    records = [r for sub in per_partition for r in sub]
    if since_offset is not None:
        records = [r for r in records if (r.get("offset") or 0) >= since_offset]

    # Newest `limit` of the union, ordered by the log's APPEND clock then the
    # (partition, offset) coordinate — a stable cross-partition "now".
    records.sort(key=lambda r: (r.get("timestamp_micros") or 0, r.get("partition") or 0, r.get("offset") or 0))
    window = records[-n:] if n else records

    out: List[Dict[str, Any]] = [
        {
            "partition": r.get("partition"),
            "offset": r.get("offset"),
            "seq": list(r.get("seq")) if r.get("seq") else None,
            "timestamp_micros": r.get("timestamp_micros"),
            "origin_timestamp_micros": r.get("origin_timestamp_micros"),
            "checksum": r.get("checksum"),
            "value": r.get("value"),
        }
        for r in window
    ]
    # Per-partition live edge (high-water) — the honest "how far the log has got".
    live_edge: Dict[str, int] = {}
    for r in records:
        p = r.get("partition")
        hw = r.get("current_offset")
        if p is not None and hw is not None:
            live_edge[str(p)] = max(live_edge.get(str(p), hw), hw)
    max_offset = max((r.get("offset") for r in window if r.get("offset") is not None), default=None)
    return {
        "ok": True,
        "stub": False,
        "topic": topic,
        "records": out,
        "events": out,          # both keys: rest.py's flattenRecords reads either
        "count": len(out),
        "offset": max_offset,
        "partitions": parts,
        "live_edge": live_edge,
        "limit": limit,
        "source": "consumer-last",
    }


# ═══════════════════════════════════════════════════════════════════════════
# seek helpers (shared by the offset replay and the wall-clock scrub)
# ═══════════════════════════════════════════════════════════════════════════

async def _seek_across_partitions(
    log: laser_io.LaserLog,
    topic: str,
    *,
    polling: str,
    limit: int,
    offset: Optional[int] = None,
    timestamp_micros: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Fan a SEEK consumer across every partition and return the union of up to
    ``limit`` records per partition FROM the seek point. ``polling='offset'`` +
    ``offset`` is a log-position seek; ``polling='timestamp'`` + ``timestamp_micros``
    is the WALL-CLOCK seek (the only way to seek by time — ``Topic.replay`` has no
    timestamp cursor). ``allow_replay=True`` so the seek reaches history behind any
    committed cursor. Server-seek: never fetch-everything-then-filter-in-Python."""
    parts = config.TOPIC_PARTITIONS

    async def _one(p: int) -> List[Dict[str, Any]]:
        c = await log.consumer(
            topic, "ui-seek-p{0}".format(p), partition=p, polling=polling,
            batch_length=limit, offset=offset, timestamp_micros=timestamp_micros,
            allow_replay=True, auto_commit="disabled",
        )
        try:
            return await log.drain_consumer(c, max_records=limit, idle_timeout=_SEEK_IDLE_S)
        finally:
            await log.shutdown_consumer(c)

    per = await asyncio.gather(*[_one(p) for p in range(parts)])
    return [r for sub in per for r in sub]


def _seek_out(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Seek records -> the wire shape, ordered chronologically from the seek
    point (so a scrub reads T, T+, T++ as you step forward)."""
    records.sort(key=lambda r: (r.get("timestamp_micros") or 0, r.get("partition") or 0, r.get("offset") or 0))
    return [
        {
            "partition": r.get("partition"),
            "offset": r.get("offset"),
            "timestamp_micros": r.get("timestamp_micros"),
            "origin_timestamp_micros": r.get("origin_timestamp_micros"),
            "checksum": r.get("checksum"),
            "value": r.get("value"),
        }
        for r in records
    ]


# ═══════════════════════════════════════════════════════════════════════════
# stream_replay  (the rewind)
# ═══════════════════════════════════════════════════════════════════════════

async def stream_replay(
    topic: str,
    *,
    from_offset: int = 0,
    from_timestamp_micros: Optional[int] = None,
    target_graph: Optional[str] = None,
    embedder_kind: str = "test",
    use_gate: bool = True,
    reset: bool = True,
    limit: int = _SEEK_LIMIT_DEFAULT,
) -> Dict[str, Any]:
    """Replay ``topic`` from a log position or a WALL-CLOCK instant.

    Three read modes when ``target_graph`` is omitted (returns records + offsets):

      * ``from_timestamp_micros=T`` — the HEADLINE time-scrub: a
        ``polling='timestamp'`` consumer SERVER-SEEKS every partition to the
        wall-clock instant ``T`` and returns the window from there. This is the
        ONLY wall-clock seek available — ``Topic.replay`` takes a per-partition
        offset dict and has no timestamp cursor.
      * ``from_offset=N`` (N>0) — a ``polling='offset'`` SERVER-SEEK to log
        position ``N`` (replaces the old fetch-everything-then-filter-in-Python).
      * neither — the full rewind from offset 0 via the replay cursor (unchanged).

    When ``target_graph`` is given it re-derives the whole graph into that key
    (the A/B) — that path is the FULL rewind only (from_offset=0, no timestamp);
    a partial seek there returns an honest ``PARTIAL_REPLAY_UNSUPPORTED``.
    """
    try:
        log = await get_log()
    except laser_io.LaserUnavailable as exc:
        return {"ok": False, "code": "LASER_UNAVAILABLE", "error": str(exc),
                "topic": topic, "laser": _laser_down(exc)}

    if target_graph:
        if (from_offset and from_offset != 0) or from_timestamp_micros is not None:
            return {
                "ok": False, "code": "PARTIAL_REPLAY_UNSUPPORTED",
                "error": "graph re-derive supports the FULL rewind (from_offset=0, no "
                         "timestamp) only; got from_offset={0}, from_timestamp_micros={1}. "
                         "Omit target_graph to fetch records from an offset or a "
                         "wall-clock instant.".format(from_offset, from_timestamp_micros),
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

    # No target graph: SERVER-SEEK the records off the durable log.
    #   • from_timestamp_micros -> WALL-CLOCK time-scrub (polling='timestamp')
    #   • from_offset > 0        -> log-position seek     (polling='offset')
    #   • else (0, no timestamp) -> the full rewind via the replay cursor
    try:
        if from_timestamp_micros is not None:
            records = await _seek_across_partitions(
                log, topic, polling="timestamp", limit=limit,
                timestamp_micros=from_timestamp_micros,
            )
            out = _seek_out(records)
            return {"ok": True, "topic": topic,
                    "from_timestamp_micros": from_timestamp_micros,
                    "records": out, "count": len(out),
                    "partitions": config.TOPIC_PARTITIONS, "limit": limit,
                    "source": "consumer-timestamp"}
        if from_offset and from_offset > 0:
            records = await _seek_across_partitions(
                log, topic, polling="offset", limit=limit, offset=from_offset,
            )
            out = _seek_out(records)
            return {"ok": True, "topic": topic, "from_offset": from_offset,
                    "records": out, "count": len(out),
                    "partitions": config.TOPIC_PARTITIONS, "limit": limit,
                    "source": "consumer-offset"}
        records = await replay.drain_via_replay(log, topic)
    except Exception as exc:  # noqa: BLE001
        reset_log()
        return {"ok": False, "code": "REPLAY_FAILED",
                "error": "{0}: {1}".format(type(exc).__name__, exc), "topic": topic}
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


__all__ = ["get_log", "reset_log", "publish", "tail", "stream_replay", "verify_parity"]
