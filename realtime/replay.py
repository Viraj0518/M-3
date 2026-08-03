"""[5] THE REPLAY PATH — the money beat (GOAL victory condition 1, NEVER cut).

A fresh reader at offset 0 re-derives the ENTIRE graph from the durable log into
an empty graph key in seconds. "Watch me rewind and re-derive the memory" is the
literal embodiment of memory-meets-motion, and no other sponsor in the stack can
do it.

We prove it is real, not a coincidence, by asserting a NODE/EDGE DIGEST MATCH
between two INDEPENDENT read mechanisms over the same log:

    warm  <- consumer group  (Topic.consumer_group, polling='first')   -> build
    cold  <- replay-from-0    (Topic.replay)                            -> build

Both feed ``pipeline.build_canonical`` (log-coordinate order), so the sensing
gate makes identical decisions and the two graphs hash identically. If the
digests match, the log alone re-created the memory — that is the falsifiable
claim behind the ablation.

Log primitive ONLY: replay-from-offset is a Log operation; nothing here touches
Views/Changes/State/Graph/Memory.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.bridge import graphstore

from . import consumer as consumer_mod
from . import laser_io
from . import pipeline


async def drain_via_group(
    log: laser_io.LaserLog, topic: str, group: str, *, idle_timeout: float = 3.0
) -> List[Dict[str, Any]]:
    """Read the whole backlog of ``topic`` from offset 0 through a consumer
    group, committing after each surfaced record. Returns normalized records."""
    consumer = await consumer_mod.open_consumer(log, topic, group, polling="first", idle_timeout=idle_timeout)
    out: List[Dict[str, Any]] = []
    try:
        async for evt in consumer.events(stop_on_idle=True):
            out.append(evt)
            await consumer.commit(evt)
    finally:
        await consumer.stop()
    return out


async def drain_via_replay(log: laser_io.LaserLog, topic: str, *, max_records: int = 1_000_000) -> List[Dict[str, Any]]:
    """Read the whole backlog of ``topic`` from offset 0 through a REPLAY cursor.
    A completely different read mechanism from the consumer group."""
    return await log.drain_from_zero(topic, max_records=max_records)


async def replay_into_cold(
    log: laser_io.LaserLog,
    *,
    topic: str,
    cold_graph: str,
    embedder_kind: str = "test",
    use_gate: bool = True,
    reset: bool = True,
) -> Dict[str, Any]:
    """The demo action: replay ``topic`` from 0 into ``cold_graph``. Drops the
    cold key first (``reset``) so the A/B starts from genuinely empty."""
    cold = graphstore.check_graph_key(cold_graph)
    if reset:
        graphstore.drop_graph(cold)
        graphstore.forget_ensured(cold)
    records = await drain_via_replay(log, topic)
    built = await pipeline.build_canonical(
        records, graph_key=cold, embedder_kind=embedder_kind, use_gate=use_gate
    )
    built["source"] = "replay-from-0"
    built["topic"] = topic
    return built


async def verify_replay_parity(
    log: laser_io.LaserLog,
    *,
    topic: str,
    warm_graph: str,
    cold_graph: str,
    group: str = "graph-writers-parity",
    embedder_kind: str = "test",
    use_gate: bool = True,
) -> Dict[str, Any]:
    """Build ``warm_graph`` from a consumer group and ``cold_graph`` from a
    replay cursor, both from offset 0, and assert their digests match.

    Returns ``{match, records_match, warm, cold}``. Both graph keys are reset
    first so the comparison is clean.
    """
    warm = graphstore.check_graph_key(warm_graph)
    cold = graphstore.check_graph_key(cold_graph)
    for gk in (warm, cold):
        graphstore.drop_graph(gk)
        graphstore.forget_ensured(gk)

    group_records = await drain_via_group(log, topic, group)
    replay_records = await drain_via_replay(log, topic)

    warm_built = await pipeline.build_canonical(
        group_records, graph_key=warm, embedder_kind=embedder_kind, use_gate=use_gate
    )
    cold_built = await pipeline.build_canonical(
        replay_records, graph_key=cold, embedder_kind=embedder_kind, use_gate=use_gate
    )

    # Compare on EVENT CONTENT (the recentchange meta.id), not on raw seq
    # coordinates: the consumer and the replay cursor legitimately expose the
    # log coordinate differently, but the durable RECORD SET they read from
    # offset 0 must be identical. This is the honest "same events" check.
    def _event_ids(records: List[Dict[str, Any]]) -> List[str]:
        out = []
        for r in records:
            v = r.get("value") or {}
            meta = v.get("meta") or {} if isinstance(v, dict) else {}
            out.append(str(meta.get("id") or (v.get("id") if isinstance(v, dict) else None)))
        return sorted(out)

    group_seqs = _event_ids(group_records)
    replay_seqs = _event_ids(replay_records)

    warm_digest = warm_built["digest"]["digest"]
    cold_digest = cold_built["digest"]["digest"]
    return {
        "match": warm_digest == cold_digest,
        "records_match": group_seqs == replay_seqs,
        "group_record_count": len(group_records),
        "replay_record_count": len(replay_records),
        "warm": warm_built,
        "cold": cold_built,
        "warm_digest": warm_digest,
        "cold_digest": cold_digest,
    }


__all__ = [
    "drain_via_group",
    "drain_via_replay",
    "replay_into_cold",
    "verify_replay_parity",
]
