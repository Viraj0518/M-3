from __future__ import annotations

from lme.graph.audit import GraphAudit, audit_graph

from test_graph_arms import _question, needs_falkor


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


def test_audit_failure_degrades_to_a_warning_not_a_crash():
    """The audit rides along inside a paid eval run; a diagnostics query that
    throws must surface as a warning on a partial audit, never abort retrieve."""

    class _BrokenHandle:
        key = "lme_broken"

        def ro_query(self, cypher):
            raise RuntimeError("connection lost mid-audit")

    audit = audit_graph(_BrokenHandle())
    assert audit.graph_key == "lme_broken"
    assert any(w.startswith("audit_failed:") for w in audit.warnings)
    # partial state still serializes -- rows.jsonl must not lose the row
    assert audit.to_json()["warnings"] == audit.warnings


@needs_falkor
def test_audit_counts_a_real_graph_and_flags_unspecified_predicates():
    from lme.embed import build_embedder
    from lme.graph.ingest import HeuristicExtractor, ingest_question
    from lme.graph.retrieve import retrieve

    emb = build_embedder(test_embedder=True)
    g, rep = ingest_question(
        _question(), split_id="test@0000000", embedder=emb,
        extractor=HeuristicExtractor(),
    )
    try:
        res = retrieve(g, question_text="What car do I drive now?",
                       qvec=emb.embed_one("What car do I drive now?"), final_top_k=12)
        audit = audit_graph(g, retrieval=res, ingest_report=rep)

        assert audit.nodes["Session"] == 2 and audit.nodes["Turn"] == 4
        assert audit.nodes["Claim"] == rep.n_claims > 0
        assert audit.edges["FROM_TURN"] > 0
        assert audit.extraction["claims_reported"] == rep.n_claims
        assert audit.retrieval["stage_counts"] == dict(res.stage_counts)
        assert audit.retrieval["graph_items_served"] == len(res.items)
        assert not any(w.startswith("audit_failed:") for w in audit.warnings)

        # normalize_predicate() maps empty predicates to 'unspecified'; the
        # audit must count that spelling, not the NULL that can never occur
        g.query(
            "CREATE (:Claim {id: 'audit_probe::c0', text: 'probe',"
            " predicate: 'unspecified'})"
        )
        audit2 = audit_graph(g)
        assert audit2.extraction["claims_without_predicate"] >= 1
    finally:
        g.drop()
