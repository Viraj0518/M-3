from __future__ import annotations

from lme.graph.audit import GraphAudit


def test_graph_audit_serialization_is_stable_and_explicit():
    audit = GraphAudit(
        graph_key="lme_test",
        nodes={"Claim": 2},
        edges={"FROM_TURN": 2},
        extraction={"claims_without_entity": 1},
        retrieval={"returned_set_size": 2},
        warnings=["claims_missing_source_turn"],
    )
    assert audit.to_json() == {
        "graph_key": "lme_test",
        "nodes": {"Claim": 2},
        "edges": {"FROM_TURN": 2},
        "extraction": {"claims_without_entity": 1},
        "retrieval": {"returned_set_size": 2},
        "warnings": ["claims_missing_source_turn"],
    }
