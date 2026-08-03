"""Machine-readable extraction and retrieval audit for a PALIMPSEST graph.

The audit is intentionally separate from scoring. It answers *why* a row
failed: whether the graph contains claims, whether claims have entities and
provenance, and which retrieval stages actually returned candidates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .retrieve import RetrievalResult
from .schema import GraphHandle


@dataclass
class GraphAudit:
    graph_key: str
    nodes: Dict[str, int] = field(default_factory=dict)
    edges: Dict[str, int] = field(default_factory=dict)
    extraction: Dict[str, int] = field(default_factory=dict)
    retrieval: Dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return {
            "graph_key": self.graph_key,
            "nodes": dict(self.nodes),
            "edges": dict(self.edges),
            "extraction": dict(self.extraction),
            "retrieval": dict(self.retrieval),
            "warnings": list(self.warnings),
        }


def _count(g: GraphHandle, cypher: str) -> int:
    res = g.ro_query(cypher)
    if not res.result_set:
        return 0
    return int(res.result_set[0][0] or 0)


def audit_graph(
    g: GraphHandle,
    *,
    retrieval: Optional[RetrievalResult] = None,
    ingest_report: Optional[Any] = None,
) -> GraphAudit:
    """Inspect graph shape and retrieval stage output without changing state."""
    audit = GraphAudit(graph_key=g.key)
    for label in ("Session", "Turn", "Claim", "Entity"):
        audit.nodes[label] = _count(g, f"MATCH (n:{label}) RETURN count(n)")
    for relation in ("NEXT", "IN_SESSION", "FROM_TURN", "MENTIONS", "RELATES"):
        audit.edges[relation] = _count(g, f"MATCH ()-[r:{relation}]->() RETURN count(r)")
    audit.edges["supersedes"] = _count(
        g, "MATCH ()-[r:RELATES {relation:'supersedes'}]->() RETURN count(r)"
    )
    audit.edges["contradicts"] = _count(
        g, "MATCH ()-[r:RELATES {relation:'contradicts'}]->() RETURN count(r)"
    )

    audit.extraction.update(
        {
            "claims_without_predicate": _count(
                g, "MATCH (c:Claim) WHERE c.predicate IS NULL OR c.predicate = '' RETURN count(c)"
            ),
            "claims_without_entity": _count(
                g, "MATCH (c:Claim) WHERE NOT (c)-[:MENTIONS]->(:Entity) RETURN count(c)"
            ),
            "claims_without_provenance": _count(
                g, "MATCH (c:Claim) WHERE NOT (c)-[:FROM_TURN]->(:Turn) RETURN count(c)"
            ),
        }
    )
    if ingest_report is not None:
        audit.extraction.update(
            {
                "sessions": int(getattr(ingest_report, "n_sessions", 0)),
                "turns": int(getattr(ingest_report, "n_turns", 0)),
                "claims_reported": int(getattr(ingest_report, "n_claims", 0)),
                "empty_extractions": int(getattr(ingest_report, "empty_extractions", 0)),
            }
        )

    if retrieval is not None:
        audit.retrieval = {
            "stage_counts": dict(retrieval.stage_counts),
            "returned_set_size": retrieval.returned_set_size,
            "graph_items_served": len(retrieval.items),
            "preference_pack_size": len(retrieval.preference_pack),
            "missing_entities": list(retrieval.missing_entities),
        }

    if audit.nodes["Claim"] == 0:
        audit.warnings.append("no_claims_extracted")
    if audit.extraction["claims_without_provenance"]:
        audit.warnings.append("claims_missing_source_turn")
    if retrieval is not None and not retrieval.items:
        audit.warnings.append("retrieval_returned_no_graph_items")
    if retrieval is not None and not retrieval.stage_counts.get("s1a", 0):
        audit.warnings.append("semantic_entry_returned_no_hits")
    if retrieval is not None and not retrieval.stage_counts.get("s5", 0):
        audit.warnings.append("provenance_hydration_returned_no_hits")
    return audit


__all__ = ["GraphAudit", "audit_graph"]
