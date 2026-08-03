"""[4] GRAPH WRITER — gated events -> FalkorDB (:Actor/:Page/:Wiki/:Event/[:EDITED]).

Consumes gated recentchange events and MERGEs them into a FalkorDB graph key,
reusing the EXACT idempotency patterns the memory verbs use
(``app/bridge/graphstore``: ``mutate`` with CREATE/MATCH stats, ``stable_id``,
``encode_map``, the ``identity.author_clause`` stamp). Then derives
``[:CO_EDITED_WITH]`` and ``Actor.edit_count`` as PURE post-passes over the graph
so the whole result is a function of the admitted event SET — order-independent
and idempotent, which is what makes the replay A/B a digest MATCH rather than a
coincidence.

NO LLM ON THE LIVE PATH (SCHEMA.md §9): gate -> Cypher MERGE -> delta. The only
vector is the gate's own embedding, stored on ``Event.emb`` so the next
novelty query has a neighbour to measure against.

AT-LEAST-ONCE SAFE: every write MERGEs on a domain id (``Event.id`` = the
recentchange ``meta.id``; ``Actor.name``; ``Page.title``), so a redelivered
event is a no-op, not a duplicate node.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.bridge import graphstore, identity

from . import gate as gate_mod
from . import laser_io

#: The agent this writer stamps on every node/edge. The watcher owns LaserData
#: ops on the bridge (agents/roster: watcher = the log/ingest specialist).
DEFAULT_AUTHOR = "watcher"

#: Co-edit derivation window (seconds). 12 minutes — the synthesis ring beat.
CO_EDIT_WINDOW_S = 720


# ═══════════════════════════════════════════════════════════════════════════
# recentchange -> the graph model
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class MappedEvent:
    event_id: str
    actor: str
    is_bot: bool
    title: str
    wiki: str
    url: str
    ts: float
    bytes_delta: int
    summary: str
    kind: str
    offset: Optional[int] = None


def map_event(raw: Dict[str, Any], *, offset: Optional[int] = None) -> Optional[MappedEvent]:
    """One raw recentchange dict -> a MappedEvent, or None if it is unusable
    (no user or no title — e.g. some ``log`` rows). Robust to missing fields:
    the captured firehose mixes edit / new / categorize / log types."""
    if not isinstance(raw, dict):
        return None
    user = raw.get("user")
    title = raw.get("title")
    if not user or not title:
        return None
    meta = raw.get("meta") or {}
    event_id = str(meta.get("id") or raw.get("id") or graphstore.stable_id("evt", user, title, raw.get("timestamp")))
    length = raw.get("length") or {}
    try:
        bytes_delta = int(length.get("new", 0)) - int(length.get("old", 0)) if isinstance(length, dict) else 0
    except (TypeError, ValueError):
        bytes_delta = 0
    try:
        ts = float(raw.get("timestamp") or 0)
    except (TypeError, ValueError):
        ts = 0.0
    return MappedEvent(
        event_id=event_id,
        actor=str(user),
        is_bot=bool(raw.get("bot")),
        title=str(title),
        wiki=str(raw.get("wiki") or raw.get("server_name") or "unknown"),
        url=str(raw.get("title_url") or raw.get("notify_url") or ""),
        ts=ts,
        bytes_delta=bytes_delta,
        summary=str(raw.get("comment") or ""),
        kind=str(raw.get("type") or "edit"),
        offset=offset if offset is not None else (meta.get("offset") if isinstance(meta, dict) else None),
    )


# ═══════════════════════════════════════════════════════════════════════════
# the writer
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class WriteStats:
    ingested: int = 0
    admitted: int = 0
    gated_out: int = 0
    unmapped: int = 0
    nodes_created: int = 0
    edges_created: int = 0

    def as_dict(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        d["gate_reject_pct"] = round(100.0 * self.gated_out / self.ingested, 1) if self.ingested else 0.0
        return d


class GraphWriter:
    """Applies the gate then MERGEs one event at a time. Collects UI deltas and,
    optionally, publishes them to ``signal.salient`` on the log."""

    def __init__(
        self,
        *,
        graph_key: str,
        gate: Optional[gate_mod.SensingGate] = None,
        author_agent: str = DEFAULT_AUTHOR,
        use_gate: bool = True,
        log: Optional[laser_io.LaserLog] = None,
        emit_topic: Optional[str] = None,
    ) -> None:
        self.graph_key = graphstore.check_graph_key(graph_key)
        self.gate = gate
        self.author_agent = author_agent
        self.use_gate = use_gate and gate is not None
        self.log = log
        self.emit_topic = emit_topic
        self.stats = WriteStats()
        self.deltas: List[Dict[str, Any]] = []
        graphstore.ensure_indexes(self.graph_key)

    async def write(
        self,
        raw: Dict[str, Any],
        *,
        offset: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """MERGE one event. ``headers`` is the record's out-of-band attribution
        (``rec['headers']`` from ``laser_io.normalize_record``): when it carries an
        ``author_agent`` that stamps the node/edge instead of this writer's
        default, so provenance rides the log record rather than being re-derived
        from the body. ``headers=None`` (the current pipeline call) is unchanged."""
        self.stats.ingested += 1
        mapped = map_event(raw, offset=offset)
        if mapped is None:
            self.stats.unmapped += 1
            return None

        # ── SENSING GATE (novelty) ───────────────────────────────────────────
        embedding: Optional[List[float]] = None
        if self.use_gate and self.gate is not None:
            decision = self.gate.decide(mapped.summary, graph_key=self.graph_key)
            embedding = decision.embedding
            if not decision.admit:
                self.stats.gated_out += 1
                return {
                    "admitted": False, "event_id": mapped.event_id, "wiki": mapped.wiki,
                    "actor": mapped.actor, "page": mapped.title,
                    "novelty": round(decision.novelty, 4), "reason": "gate:non-novel",
                }
        elif self.gate is not None:
            # gate disabled for parity, but still embed so the vector index is
            # populated identically in warm and cold.
            embedding = self.gate.embed(mapped.summary)

        self.stats.admitted += 1
        delta = self._merge(mapped, embedding, author=self._resolve_author(headers))
        self.deltas.append(delta)
        if self.log is not None and self.emit_topic:
            await self.log.publish(self.emit_topic, delta, key=mapped.wiki)
        return delta

    def _resolve_author(self, headers: Optional[Dict[str, Any]]) -> str:
        """Prefer an ``author_agent`` carried on the record HEADERS (out-of-band
        attribution) over this writer's default. Falls back to
        ``self.author_agent`` when no header is present, so the existing pipeline
        call path (which passes no headers) stamps exactly as before."""
        if headers:
            a = headers.get("author_agent")
            if a:
                return str(a)
        return self.author_agent

    def _merge(
        self,
        m: MappedEvent,
        embedding: Optional[List[float]],
        *,
        author: Optional[str] = None,
    ) -> Dict[str, Any]:
        author = author if author is not None else self.author_agent
        params: Dict[str, Any] = {
            "wiki": m.wiki,
            "actor": m.actor,
            "is_bot": m.is_bot,
            "title": m.title,
            "url": m.url,
            "event_id": m.event_id,
            "ts": m.ts,
            "bytes": m.bytes_delta,
            "summary": m.summary,
            "kind": m.kind,
            "offset": m.offset if m.offset is not None else -1,
            identity.AUTHOR_PROP: author,
        }
        event_sets = [
            "e.ts = $ts",
            "e.bytes_delta = $bytes",
            "e.summary = $summary",
            "e.kind = $kind",
            "e.offset = $offset",
            identity.author_clause("e"),
        ]
        if embedding is not None:
            event_sets.append("e.emb = vecf32($emb)")
            params["emb"] = graphstore.check_vector(embedding, field="event embedding")

        cypher = (
            "MERGE (w:Wiki {code: $wiki}) ON CREATE SET w.created_ts = timestamp() "
            "MERGE (a:Actor {name: $actor}) "
            "  ON CREATE SET a.created_ts = timestamp(), a.first_seen = $ts, " + identity.author_clause("a") + " "
            "  SET a.is_bot = $is_bot "
            "MERGE (p:Page {title: $title}) "
            "  ON CREATE SET p.created_ts = timestamp(), " + identity.author_clause("p") + " "
            "  SET p.wiki = $wiki, p.url = $url "
            "MERGE (p)-[:ON]->(w) "
            "MERGE (e:Event {id: $event_id}) "
            "  ON CREATE SET e.created_ts = timestamp() "
            "  SET " + ", ".join(event_sets) + " "
            "MERGE (a)-[ed:EDITED {event_id: $event_id}]->(p) "
            "  SET ed.ts = $ts, ed.bytes = $bytes, ed.summary = $summary "
            # Connect the reified :Event into the graph so it is not an orphan in
            # the projector. Reuses the schema's [:MENTIONS] edge (Event->X);
            # recall's entity expansion filters (:Entity), so an Event->Page
            # MENTIONS is inert there — no new edge TYPE is introduced.
            "MERGE (e)-[:MENTIONS]->(p) "
            "RETURN a.name, p.title, e.id"
        )
        _rows, run_ms, stats = graphstore.mutate(cypher, params, graph_key=self.graph_key)
        self.stats.nodes_created += stats["nodes_created"]
        self.stats.edges_created += stats["relationships_created"]
        return {
            "admitted": True,
            "event_id": m.event_id,
            "wiki": m.wiki,
            "actor": m.actor,
            "page": m.title,
            "ts": m.ts,
            "bytes_delta": m.bytes_delta,
            "created": bool(stats["nodes_created"]),
            "run_time_ms": run_ms,
            "author_agent": author,
        }

    # ── derived, order-independent post-passes ───────────────────────────────
    def finalize(self, *, window_s: int = CO_EDIT_WINDOW_S) -> Dict[str, Any]:
        """Derive ``Actor.edit_count`` and ``[:CO_EDITED_WITH]`` — pure functions
        of the current graph, so warm and cold converge to the identical result.
        Idempotent: re-running recomputes the same values."""
        return finalize_graph(self.graph_key, window_s=window_s)


def finalize_graph(graph_key: str, *, window_s: int = CO_EDIT_WINDOW_S) -> Dict[str, Any]:
    gk = graphstore.check_graph_key(graph_key)

    # edit_count = a pure count of outgoing EDITED edges.
    graphstore.mutate(
        "MATCH (a:Actor) OPTIONAL MATCH (a)-[e:EDITED]->() "
        "WITH a, count(e) AS c SET a.edit_count = c",
        {},
        graph_key=gk,
    )

    # CO_EDITED_WITH: two actors editing the SAME page within the window. Stored
    # ONE directed edge in name order (a.name < b.name) so it is deterministic.
    _rows, run_ms, stats = graphstore.mutate(
        "MATCH (a:Actor)-[e1:EDITED]->(p:Page)<-[e2:EDITED]-(b:Actor) "
        "WHERE a.name < b.name AND abs(e1.ts - e2.ts) <= $window_s "
        "WITH a, b, count(DISTINCT p) AS shared, min(abs(e1.ts - e2.ts)) AS closest "
        "MERGE (a)-[c:CO_EDITED_WITH]->(b) "
        "SET c.window_s = $window_s, c.count = shared, c.closest_s = closest",
        {"window_s": window_s},
        graph_key=gk,
    )
    return {
        "co_edited_edges": stats["relationships_created"],
        "window_s": window_s,
        "run_time_ms": run_ms,
        "graph_key": gk,
    }


__all__ = [
    "MappedEvent",
    "map_event",
    "WriteStats",
    "GraphWriter",
    "finalize_graph",
    "DEFAULT_AUTHOR",
    "CO_EDIT_WINDOW_S",
]
