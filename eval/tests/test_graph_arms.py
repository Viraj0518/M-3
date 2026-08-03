"""Graph mechanisms, the router, embedder/reader fallback discipline, manifest.

The FalkorDB tests are skipped (not silently passed) when no server answers
GRAPH.QUERY on the configured port -- and the probe asserts a REAL RESULT SET,
because a stray `redis-server` accepts the connection and only fails later.
"""

from __future__ import annotations

import numpy as np
import pytest

from lme import guards
from lme.dataset import Question, Session, Turn
from lme.embed import HashEmbedder, build_embedder, EmbedderConfigError
from lme.graph.ingest import Claim, HeuristicExtractor, supersede_pass
from lme.graph.retrieve import route_features, rrf_fuse
from lme.graph.schema import assert_directed_vocabulary, graph_key, normalize_entity
from lme.reader import ExtractiveTestReader, ReaderConfigError, build_reader, render_user_prompt


def _falkor_up() -> bool:
    try:
        from lme.graph.schema import GraphHandle

        g = GraphHandle("lme_pytest_probe")
        res = g.query("RETURN 1 AS x")
        ok = bool(res.result_set) and int(res.result_set[0][0]) == 1
        g.drop()
        return ok
    except Exception:
        return False


needs_falkor = pytest.mark.skipif(not _falkor_up(), reason="no FalkorDB answering GRAPH.QUERY")


# ── stub discipline: the whole point of embed.py / reader.py ────────────────
def test_stubs_are_opt_in_only_and_never_reached_by_a_missing_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # A missing key RAISES. It does not quietly become the hash embedder.
    with pytest.raises(EmbedderConfigError, match="will NOT silently fall back"):
        build_embedder(test_embedder=False)
    with pytest.raises(ReaderConfigError, match="NOT fall back"):
        build_reader(model="claude-sonnet-4-6-20260514", test_reader=False)
    # The explicit flag is the ONLY route.
    assert isinstance(build_embedder(test_embedder=True), HashEmbedder)
    assert isinstance(build_reader(test_reader=True), ExtractiveTestReader)


def test_test_embedder_self_labels_in_its_pin():
    """A dry run must be identifiable from the manifest alone."""
    assert build_embedder(test_embedder=True).pin.startswith("hash-test-embedder@")


def test_hash_embedder_is_deterministic_normalised_and_right_width():
    e = HashEmbedder(dim=256)
    a, b = e.embed_one("the user bought a Honda"), e.embed_one("the user bought a Honda")
    assert np.allclose(a, b)
    assert a.shape == (256,)
    assert abs(float(np.linalg.norm(a)) - 1.0) < 1e-5
    assert not np.allclose(a, e.embed_one("completely different text about pasta"))


def test_embed_cache_round_trips(tmp_path):
    e = build_embedder(test_embedder=True, cache_dir=tmp_path)
    e.embed(["alpha beta", "gamma delta"])
    assert e.stats["misses"] == 2 and e.stats["hits"] == 0
    e2 = build_embedder(test_embedder=True, cache_dir=tmp_path)
    e2.embed(["alpha beta", "gamma delta"])
    assert e2.stats["hits"] == 2 and e2.stats["misses"] == 0


# ── prompt rendering must survive braces ───────────────────────────────────
def test_render_user_prompt_is_brace_safe():
    out = render_user_prompt(
        "Today is {question_date}.\n{context}\n{date_index}\nQUESTION: {question}",
        question="what {is} this?",
        question_date="2023-01-01",
        context='user: {"json": [1,2]}',
        date_index="IDX",
    )
    assert '{"json": [1,2]}' in out and "what {is} this?" in out


# ── the router (T14) ────────────────────────────────────────────────────────
def test_router_signature_takes_a_string_so_gold_is_unreachable():
    import inspect

    params = list(inspect.signature(route_features).parameters)
    assert params == ["question_text"]


@pytest.mark.parametrize(
    "q,flag",
    [
        ("How long after my surgery did I start running?", "temporal"),
        ("When did I buy the car?", "temporal"),
        ("What restaurant would I prefer for dinner?", "preference"),
        ("Can you recommend a laptop for me?", "preference"),
        ("What is the best option for my commute?", "superlative"),
        ("What car do I drive now?", "update"),
    ],
)
def test_router_fires_on_question_text_features(q, flag):
    assert getattr(route_features(q), flag) is True


def test_router_is_quiet_on_a_plain_lookup():
    f = route_features("What is my dog's name")
    assert not f.temporal and not f.preference


# ── RRF ─────────────────────────────────────────────────────────────────────
def test_rrf_is_rank_based_and_rewards_agreement():
    # An item both lists rank highly beats items either list ranks alone.
    fused = rrf_fuse(["a", "b"], ["a", "c"])
    assert fused[0] == "a"
    assert set(fused) == {"a", "b", "c"}
    # Within a single list, order is preserved.
    assert rrf_fuse(["x", "y", "z"]) == ["x", "y", "z"]
    # Fusion is score-blind: only RANK enters, so a distance and a BM25 score
    # never need a common scale (which is why we do not normalise them).
    assert rrf_fuse(["p", "q"], ["q", "p"])[0] in {"p", "q"}


# ── supersede pass ──────────────────────────────────────────────────────────
def _claim(cid, subject, pred, text, vf):
    return Claim(id=cid, text=text, predicate=pred, kind="fact", entities=[subject],
                 subject=subject, from_turn=0, session_id="s", valid_from=vf)


def test_supersede_chains_by_subject_predicate_in_time_order():
    lins = supersede_pass([
        _claim("c1", "car", "car_model", "user drives a Civic", "2023-01-01"),
        _claim("c2", "car", "car_model", "user drives a Tesla", "2023-06-01"),
        _claim("c3", "car", "car_model", "user drives a Rivian", "2023-09-01"),
    ])
    assert [(l.newer, l.older, l.relation) for l in lins] == [
        ("c2", "c1", "supersedes"),
        ("c3", "c2", "supersedes"),
    ]


def test_same_timestamp_conflict_is_contradicts_not_supersedes():
    """With no time ordering there is no 'newer'; inventing one would fabricate
    the very signal the mechanism claims to measure."""
    lins = supersede_pass([
        _claim("c1", "car", "car_model", "a Civic", "2023-01-01"),
        _claim("c2", "car", "car_model", "a Tesla", "2023-01-01"),
    ])
    assert [l.relation for l in lins] == ["contradicts"]


def test_repeated_identical_statement_is_not_an_update():
    assert supersede_pass([
        _claim("c1", "car", "car_model", "a Civic", "2023-01-01"),
        _claim("c2", "car", "car_model", "a Civic", "2023-06-01"),
    ]) == []


def test_different_predicates_never_chain():
    assert supersede_pass([
        _claim("c1", "car", "car_model", "a Civic", "2023-01-01"),
        _claim("c2", "car", "car_colour", "blue", "2023-06-01"),
    ]) == []


def test_shared_taxonomy_is_live_and_directed():
    """Guards schema.py's ImportError fallback: if the eval quietly carried a
    private copy of the vocabulary it would LOOK like it shares the demo's."""
    assert_directed_vocabulary()
    from memory.taxonomy import is_directed

    assert is_directed("supersedes") and is_directed("contradicts")


def test_graph_key_is_split_scoped():
    a = graph_key("oracle@98d7416", "q1")
    b = graph_key("s_cleaned@98d7416", "q1")
    assert a != b and a.startswith("lme_")


def test_normalize_entity_is_deliberately_boring():
    assert normalize_entity("  The Sugar Factory!! ") == "the sugar factory"
    assert normalize_entity("University of Melbourne") == "university of melbourne"


# ── live graph ──────────────────────────────────────────────────────────────
def _question():
    turns_a = [
        Turn("user", "I just bought a Honda Civic for my commute to Seattle.", 0),
        Turn("assistant", "Congratulations on the Honda Civic!", 1),
    ]
    turns_b = [
        Turn("user", "I sold the Honda Civic and bought a Tesla Model 3 instead.", 0),
        Turn("assistant", "Nice upgrade to the Tesla Model 3.", 1),
    ]
    return Question(
        question_id="qtest1",
        question_type="knowledge-update",
        question="What car do I drive now?",
        answer="Tesla Model 3",
        question_date="2023/09/01 (Fri) 10:00",
        sessions=[
            Session("sess_a", "2023/01/01 (Sun) 10:00", "2023-01-01T10:00", 0, turns_a),
            Session("sess_b", "2023/06/01 (Thu) 10:00", "2023-06-01T10:00", 1, turns_b),
        ],
        gold_session_ids=["sess_b"],
    )


@needs_falkor
def test_ingest_builds_a_real_graph_with_dates_and_provenance():
    from lme.graph.ingest import ingest_question

    g, rep = ingest_question(
        _question(), split_id="test@0000000",
        embedder=build_embedder(test_embedder=True),
        extractor=HeuristicExtractor(),
    )
    try:
        assert rep.n_sessions == 2 and rep.n_turns == 4
        assert rep.n_claims > 0 and rep.n_entities > 0
        assert rep.dated_sessions == 2
        assert g.edge_count("NEXT") == 1          # the temporal spine
        assert g.edge_count("IN_SESSION") == 4
        assert g.edge_count("FROM_TURN") > 0      # S5 provenance is reachable
    finally:
        g.drop()


@needs_falkor
def test_temporal_ablation_removes_dates_at_ingest_time_not_query_time():
    """An ablation that only disables the query stage is not a removal test:
    S5 would still hand the reader dated provenance."""
    from lme.graph.ingest import ingest_question

    g, rep = ingest_question(
        _question(), split_id="test@0000000",
        embedder=build_embedder(test_embedder=True),
        extractor=HeuristicExtractor(),
        ablate_temporal=True,
    )
    try:
        assert rep.dated_sessions == 0
        assert g.edge_count("NEXT") == 0
        res = g.ro_query("MATCH (s:Session) RETURN s.date_iso")
        assert all((r[0] or "") == "" for r in res.result_set)
    finally:
        g.drop()


@needs_falkor
def test_supersede_ablation_removes_the_edges():
    from lme.graph.ingest import ingest_question

    for ablate, want in ((False, True), (True, False)):
        g, _ = ingest_question(
            _question(), split_id="test@0000000",
            embedder=build_embedder(test_embedder=True),
            extractor=HeuristicExtractor(),
            ablate_supersede=ablate,
        )
        try:
            n = g.relation_count("supersedes") + g.relation_count("contradicts")
            assert (n > 0) is want, f"ablate_supersede={ablate} gave {n} edges"
        finally:
            g.drop()


@needs_falkor
def test_retrieve_runs_all_five_stages_and_respects_the_budget():
    from lme.graph.ingest import ingest_question
    from lme.graph.retrieve import render_context, retrieve

    emb = build_embedder(test_embedder=True)
    g, _ = ingest_question(
        _question(), split_id="test@0000000", embedder=emb, extractor=HeuristicExtractor()
    )
    try:
        res = retrieve(g, question_text="What car do I drive now?",
                       qvec=emb.embed_one("What car do I drive now?"), final_top_k=12)
        assert res.stage_counts["s1a"] > 0
        assert res.stage_counts["s5"] > 0
        assert "s1b_error" not in res.stage_counts     # lexical stage is healthy
        assert len(res.items) <= 12                    # T12 budget respected
        ctx, idx = render_context(res, session_dates=["2023-01-01T10:00", "2023-06-01T10:00"])
        assert "SESSION DATE INDEX" in idx
        assert "session sess_" in ctx
        # S5: the VERBATIM source turn is co-served, not just the paraphrase
        assert "user:" in ctx or "assistant:" in ctx
    finally:
        g.drop()


@needs_falkor
def test_s1b_lexical_returns_hits_for_a_natural_question():
    """REGRESSION: space-joined terms are an AND intersection and returned 0 for
    every real question, silently reducing S1 to semantic-only."""
    from lme.graph.ingest import ingest_question
    from lme.graph.retrieve import s1b_lexical

    emb = build_embedder(test_embedder=True)
    g, _ = ingest_question(
        _question(), split_id="test@0000000", embedder=emb, extractor=HeuristicExtractor()
    )
    try:
        hits, err = s1b_lexical(g, "What car do I drive now, the Honda or the Tesla?")
        assert err == ""
        assert len(hits) > 0
    finally:
        g.drop()


# ── manifest ────────────────────────────────────────────────────────────────
def test_manifest_refuses_a_run_with_served_model_violations():
    from lme.manifest import Manifest, Pins

    man = Manifest(arm="a", dataset_file="f", dataset_sha256="s", hf_repo="r", hf_revision="v",
                   split_name="smoke-30", split_n=2, split_ids_sha256="i", seed=1, expected_n=2,
                   pins=Pins(reader="claude-sonnet-4-6-20260514"))
    rows = [{"run_id": man.run_id, "question_id": "q1", "served_model": "claude-sonnet-4-6-20260514"},
            {"run_id": man.run_id, "question_id": "q2", "served_model": "gpt-4o-mini"}]
    with pytest.raises(guards.GuardViolation) as e:
        man.finalize(rows, enforce_served_model=True)
    assert e.value.trap == "T2"
    assert man.valid is False


def test_manifest_config_hash_requires_the_reader_prompt():
    from lme.manifest import Manifest

    man = Manifest(arm="a", dataset_file="f", dataset_sha256="s", hf_repo="r", hf_revision="v",
                   split_name="s", split_n=1, split_ids_sha256="i", seed=1, expected_n=1)
    man.compute_config_hash({"config_yaml": "x", "code_version": "y"})
    with pytest.raises(guards.GuardViolation) as e:
        man.check_config_hash()
    assert e.value.trap == "T10"


def test_assert_quotable_blocks_phantom_flagged_runs():
    from lme.manifest import assert_quotable

    with pytest.raises(guards.GuardViolation) as e:
        assert_quotable({"run_id": "r", "valid": True, "served_model_violations": [],
                         "guards": {}, "phantom_flags": [{"metric": "qa.micro", "value": 0.94}]})
    assert e.value.trap == "T4"
