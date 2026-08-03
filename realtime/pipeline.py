"""The pipeline that ties consumer -> gate -> graph writer, plus the graph
DIGEST that makes the replay A/B a falsifiable MATCH.

TWO BUILD ENTRY POINTS, on purpose:

  * ``run_live_stream`` — the TRUE streaming path: a consumer group off
    ``signal.raw``, writing each event as it arrives and committing AFTER the
    write lands (crash-safe). Populates the real warm ``palimpsest`` graph.

  * ``build_canonical`` — sorts a record SET by log coordinate ``(partition,
    offset)`` and writes it in that fixed order. Used for BOTH sides of the
    replay parity check, so the sensing gate (whose novelty is order-dependent)
    makes the SAME decisions warm and cold and the digests match exactly. The
    replay invariant is "the log re-derives the identical graph" — canonical
    ordering is what makes that a clean equality rather than a coin flip.

``digest_graph`` hashes DOMAIN keys (Actor.name / Page.title / Wiki.code /
Event.id), never FalkorDB internal ids (which differ between graph keys), so two
independently-built graphs with the same content hash identically.

ATTRIBUTION IS IN THE DIGEST (the honest-receipt fix). The reproducibility claim
is "byte-identical ATTRIBUTED graph", so ``author_agent`` — the field that makes
"analyst asserted X, watcher contradicted it" a graph fact — is part of both the
node and the edge digest. Without it, a replay that re-derived the same topology
under a DIFFERENT author would false-pass the match. ``author_agent`` is
deterministic on the write path (the writer stamps a fixed selector), so a TRUE
same-attribution replay still matches exactly; only a PERTURBED author changes
the hash — which is what makes the digest load-bearing rather than decorative.
Wall-clock properties (``created_ts = timestamp()``) stay OUT, because they
differ per build and would make the equality a coin flip.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from app.bridge import graphstore

from . import consumer as consumer_mod
from . import gate as gate_mod
from . import graph_writer as gw
from . import laser_io


# ═══════════════════════════════════════════════════════════════════════════
# graph digest — the parity instrument
# ═══════════════════════════════════════════════════════════════════════════

_DOMAIN_KEY = "coalesce(n.name, n.title, n.code, n.id, '')"


def digest_graph(graph_key: str) -> Dict[str, Any]:
    """A deterministic node/edge digest of a graph key. Returns
    ``{node_count, edge_count, node_digest, edge_digest, digest}``.

    Independent of internal ids: nodes hash on ``(label, domain_key, author)``
    and edges on ``(src_key, rel_type, event_id?, dst_key, author)`` so the same
    ATTRIBUTED content in two different graph keys yields the same digest — and a
    change in ``author_agent`` alone changes it (the honest-receipt fix).
    """
    gk = graphstore.check_graph_key(graph_key)

    node_rows, _ms, _h = graphstore.query(
        "MATCH (n) RETURN labels(n)[0] AS label, " + _DOMAIN_KEY + " AS key, "
        "       coalesce(n.author_agent, '') AS author "
        "ORDER BY label, key, author",
        {},
        graph_key=gk,
        read_only=True,
    )
    node_lines = ["{0}\x1f{1}\x1f{2}".format(r[0], r[1], r[2]) for r in node_rows]

    edge_rows, _ms2, _h2 = graphstore.query(
        "MATCH (a)-[r]->(b) "
        "RETURN coalesce(a.name, a.title, a.code, a.id, '') AS ak, "
        "       type(r) AS rel, coalesce(r.event_id, r.relation, '') AS rk, "
        "       coalesce(b.name, b.title, b.code, b.id, '') AS bk, "
        "       coalesce(r.author_agent, '') AS author "
        "ORDER BY ak, rel, rk, bk, author",
        {},
        graph_key=gk,
        read_only=True,
    )
    edge_lines = [
        "{0}\x1f{1}\x1f{2}\x1f{3}\x1f{4}".format(r[0], r[1], r[2], r[3], r[4])
        for r in edge_rows
    ]

    node_digest = hashlib.sha256("\n".join(sorted(node_lines)).encode("utf-8")).hexdigest()
    edge_digest = hashlib.sha256("\n".join(sorted(edge_lines)).encode("utf-8")).hexdigest()
    combined = hashlib.sha256((node_digest + edge_digest).encode("utf-8")).hexdigest()
    return {
        "graph_key": gk,
        "node_count": len(node_lines),
        "edge_count": len(edge_lines),
        "node_digest": node_digest,
        "edge_digest": edge_digest,
        "digest": combined,
    }


# ═══════════════════════════════════════════════════════════════════════════
# builds
# ═══════════════════════════════════════════════════════════════════════════

async def build_canonical(
    records: List[Dict[str, Any]],
    *,
    graph_key: str,
    embedder_kind: str = "test",
    use_gate: bool = True,
    author_agent: str = gw.DEFAULT_AUTHOR,
    window_s: int = gw.CO_EDIT_WINDOW_S,
) -> Dict[str, Any]:
    """Build a graph from a record SET in canonical ``(partition, offset)`` order.
    Records are already drained (no live read here). Deterministic: the same set
    yields the same graph regardless of the order it was drained in."""
    gk = graphstore.check_graph_key(graph_key)
    embedder = gate_mod.make_embedder(embedder_kind)
    gate = gate_mod.SensingGate(embedder, graph_key=gk)
    writer = gw.GraphWriter(graph_key=gk, gate=gate, author_agent=author_agent, use_gate=use_gate)

    ordered = sorted(
        records,
        key=lambda r: (
            r.get("seq") or (r.get("partition") or 0, r.get("offset") or 0)
        ),
    )
    for rec in ordered:
        value = rec.get("value")
        if not isinstance(value, dict):
            continue
        await writer.write(value, offset=rec.get("offset"))

    derived = writer.finalize(window_s=window_s)
    out = {
        "graph_key": gk,
        "records": len(ordered),
        "embedder": embedder_kind,
        "use_gate": use_gate,
        "stats": writer.stats.as_dict(),
        "derived": derived,
        "digest": digest_graph(gk),
    }
    return out


async def run_live_stream(
    log: laser_io.LaserLog,
    *,
    topic: str,
    graph_key: str,
    group: str = "graph-writers",
    embedder_kind: str = "test",
    use_gate: bool = True,
    author_agent: str = gw.DEFAULT_AUTHOR,
    max_events: Optional[int] = None,
    idle_timeout: float = 3.0,
    emit_topic: Optional[str] = None,
    window_s: int = gw.CO_EDIT_WINDOW_S,
) -> Dict[str, Any]:
    """The real streaming path: consume ``topic`` from offset 0 in a consumer
    group, gate+write each event as it arrives, commit AFTER the write, then
    derive. Returns stats + the warm digest.

    ``max_events``/``idle_timeout`` bound it for a demo/verify run; omit
    ``max_events`` and set ``idle_timeout`` high for a long-lived writer.
    """
    gk = graphstore.check_graph_key(graph_key)
    embedder = gate_mod.make_embedder(embedder_kind)
    gate = gate_mod.SensingGate(embedder, graph_key=gk)
    writer = gw.GraphWriter(
        graph_key=gk, gate=gate, author_agent=author_agent,
        use_gate=use_gate, log=log, emit_topic=emit_topic,
    )
    consumer = await consumer_mod.open_consumer(log, topic, group, polling="first", idle_timeout=idle_timeout)

    processed = 0
    try:
        async for evt in consumer.events(stop_on_idle=True):
            value = evt.get("value")
            if isinstance(value, dict):
                await writer.write(value, offset=evt.get("offset"))
            # COMMIT AFTER the side effect lands (crash-safe).
            await consumer.commit(evt)
            processed += 1
            if max_events is not None and processed >= max_events:
                break
    finally:
        await consumer.stop()

    derived = writer.finalize(window_s=window_s)
    return {
        "graph_key": gk,
        "topic": topic,
        "group": group,
        "processed": processed,
        "consumer_stats": consumer.stats(),
        "writer_stats": writer.stats.as_dict(),
        "derived": derived,
        "digest": digest_graph(gk),
    }


__all__ = ["digest_graph", "build_canonical", "run_live_stream"]
