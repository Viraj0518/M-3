"""Dataset loading, split locks, and the official metric replication."""

from __future__ import annotations

import json

import pytest

from lme import dataset as ds
from lme import guards, metrics


# ── dataset ─────────────────────────────────────────────────────────────────
def test_oracle_pin_and_taxonomy_match_the_design_doc():
    """The counts are machine-verified in design doc §2.3. If this drifts, the
    dataset changed under us and every prior number is on different data."""
    d = ds.load_dataset(ds.DATA_DIR / "longmemeval_oracle.json")
    assert len(d) == ds.EXPECTED_TOTAL
    from collections import Counter

    counts = Counter(q.question_type for q in d.questions)
    assert dict(counts) == ds.EXPECTED_TYPE_COUNTS
    assert sum(1 for q in d.questions if q.is_abstention) == ds.EXPECTED_ABSTENTION
    assert d.split_id == f"oracle@{ds.HF_REVISION_SHORT}"


def test_dataset_pin_refuses_wrong_bytes(tmp_path):
    p = tmp_path / "longmemeval_oracle.json"
    p.write_text("[]")
    with pytest.raises(ds.DatasetPinError):
        ds.assert_dataset_pin(p)


def test_abstention_is_an_id_substring_check():
    q = ds.Question("x_abs_1", "temporal-reasoning", "q", "a", "", [], [])
    assert q.is_abstention is True
    assert ds.Question("x1", "temporal-reasoning", "q", "a", "", [], []).is_abstention is False


def test_abstention_row_keeps_its_base_bucket_officially_and_splits_out_cleanly():
    """The official contamination, replicated: `official_bucket` keeps the base
    type (so the reported temporal number CONTAINS 6 abstention items), while
    `clean_bucket` is the non-official 7-way view."""
    q = ds.Question("x_abs", "temporal-reasoning", "q", "a", "", [], [])
    assert q.official_bucket == "temporal-reasoning"
    assert q.clean_bucket == "abstention"


def test_parse_date_iso_is_lexically_sortable_and_degrades_not_raises():
    assert ds.parse_date_iso("2023/04/10 (Mon) 17:50") == "2023-04-10T17:50"
    assert ds.parse_date_iso("2023/4/5 (Wed) 09:01") == "2023-04-05T09:01"
    assert ds.parse_date_iso("garbage") == ""
    assert ds.parse_date_iso("") == ""
    assert ds.parse_date_iso("2023/04/10 (Mon) 09:00") < ds.parse_date_iso("2023/04/10 (Mon) 17:50")


def test_sessions_are_date_sorted_even_when_the_file_is_not():
    """ORACLE HAYSTACKS SHIP UNSORTED. Assuming file order is chronological is a
    real trap; the loader sorts so no arm can inherit it."""
    d = ds.load_dataset(ds.DATA_DIR / "longmemeval_oracle.json")
    for q in d.questions[:50]:
        dated = [s.date_iso for s in q.sessions if s.date_iso]
        assert dated == sorted(dated)
        assert [s.ordinal for s in q.sessions] == list(range(len(q.sessions)))


def test_strip_gold_removes_has_answer():
    """`has_answer` is GOLD and must not reach any arm."""
    s = ds.Session("s1", "raw", "2023-01-01", 0, [ds.Turn("user", "hi", 0, has_answer=True)])
    out = ds.strip_gold(s)
    assert out == [{"role": "user", "text": "hi"}]
    assert "has_answer" not in out[0]


# ── splits ──────────────────────────────────────────────────────────────────
def test_all_splits_lock_against_the_registry():
    for name, n in (("smoke-30", 30), ("smoke-40", 40), ("dev-150", 150), ("full-500", 500)):
        sp = ds.load_split(name)
        assert sp.n == n
        assert sp.ids_sha256 == ds.sha256_ids(sp.ids)
        assert sp.seed == 20260803


def test_smoke30_is_stratified_five_per_type():
    from collections import Counter

    d = ds.load_dataset(ds.DATA_DIR / "longmemeval_oracle.json")
    _, qs = ds.resolve(d, "smoke-30")
    assert dict(Counter(q.question_type for q in qs)) == {t: 5 for t in ds.QUESTION_TYPES}


def test_smoke40_is_six_per_type_plus_four_largest_buckets():
    """40 does not divide by 6. The allocation is a FLOOR of 6 per type, then +1
    to each of the 4 largest buckets by population -- so every type keeps a
    workable floor and no mechanism goes under-sampled."""
    from collections import Counter

    d = ds.load_dataset(ds.DATA_DIR / "longmemeval_oracle.json")
    _, qs = ds.resolve(d, "smoke-40")
    got = dict(Counter(q.question_type for q in qs))
    assert got == {
        "multi-session": 7,          # pop 133
        "temporal-reasoning": 7,     # pop 133
        "knowledge-update": 7,       # pop  78
        "single-session-user": 7,    # pop  70
        "single-session-assistant": 6,
        "single-session-preference": 6,
    }
    assert sum(got.values()) == 40
    assert set(got) == set(ds.QUESTION_TYPES)  # every type represented


def test_top_up_to_is_deterministic_and_does_not_disturb_plain_per_type():
    """The regression that matters: adding `top_up_to` must leave the EXISTING
    locked splits byte-identical, because `top_up_to=None` has to reproduce the
    original rng call sequence exactly."""
    d = ds.load_dataset(ds.DATA_DIR / "longmemeval_oracle.json")

    a = ds.make_split("tmp", d, per_type=6, top_up_to=40, seed=20260803)
    b = ds.make_split("tmp", d, per_type=6, top_up_to=40, seed=20260803)
    assert a.ids_sha256 == b.ids_sha256 == ds.load_split("smoke-40").ids_sha256
    assert a.strategy == "stratified/6-per-type+4-largest-buckets"
    assert len(set(a.ids)) == 40  # no duplicates across the top-up pass

    # a different seed is a different split
    assert ds.make_split("tmp", d, per_type=6, top_up_to=40, seed=1).ids_sha256 != a.ids_sha256

    # and the un-topped-up path still reproduces the committed smoke-30 lock
    plain = ds.make_split("tmp", d, per_type=5, seed=20260803)
    assert plain.ids_sha256 == ds.load_split("smoke-30").ids_sha256
    assert plain.strategy == "stratified/5-per-type"


def test_top_up_to_refuses_incoherent_allocations():
    d = ds.load_dataset(ds.DATA_DIR / "longmemeval_oracle.json")
    with pytest.raises(ValueError):  # below the floor
        ds.make_split("tmp", d, per_type=6, top_up_to=30, seed=20260803)
    with pytest.raises(ValueError):  # more extras than buckets -> ambiguous
        ds.make_split("tmp", d, per_type=6, top_up_to=50, seed=20260803)
    with pytest.raises(ValueError):  # top_up_to is a floor modifier, not a total
        ds.stratified_ids(d.questions, total=40, top_up_to=40, seed=20260803)


def test_split_generation_is_deterministic_under_the_seed():
    d = ds.load_dataset(ds.DATA_DIR / "longmemeval_oracle.json")
    a = ds.make_split("tmp", d, per_type=5, seed=20260803)
    b = ds.make_split("tmp", d, per_type=5, seed=20260803)
    c = ds.make_split("tmp", d, per_type=5, seed=1)
    assert a.ids_sha256 == b.ids_sha256
    assert a.ids_sha256 != c.ids_sha256


def test_split_lock_detects_tampering(tmp_path, monkeypatch):
    body = json.loads((ds.SPLITS_DIR / "smoke-30.json").read_text())
    body["ids"][0] = "tampered"
    monkeypatch.setattr(ds, "SPLITS_DIR", tmp_path)
    (tmp_path / "smoke-30.json").write_text(json.dumps(body))
    with pytest.raises(ds.SplitLockError, match="self-hash mismatch"):
        ds.load_split("smoke-30")


def test_resolve_refuses_split_dataset_mismatch():
    """T5 at the split layer: a split built on `_s` cannot be run on oracle."""
    d = ds.load_dataset(ds.DATA_DIR / "longmemeval_oracle.json")
    with pytest.raises(ds.SplitLockError, match="SPLIT/DATASET MISMATCH"):
        ds.resolve(d, "dev-150")


# ── metrics ─────────────────────────────────────────────────────────────────
def _jrows():
    rows = []
    # 6 buckets; temporal carries 1 abstention row -> official contamination
    for i in range(4):
        rows.append(metrics.JudgedRow(f"t{i}", "temporal-reasoning", False, i < 2))
    rows.append(metrics.JudgedRow("t_abs", "temporal-reasoning", True, True))
    for t in ("multi-session", "knowledge-update", "single-session-user",
              "single-session-assistant", "single-session-preference"):
        rows.append(metrics.JudgedRow(f"{t}0", t, False, True))
    return rows


def test_micro_and_macro_differ_and_both_are_emitted():
    m = metrics.qa_metrics(_jrows(), split_id="oracle@98d7416")
    micro = m["qa.micro.oracle@98d7416"]
    macro = m["qa.macro.oracle@98d7416"]
    assert micro != macro  # they differ because n ranges per bucket
    guards.t8_both_present(m)
    guards.t6_namespaced(m)


def test_official_buckets_contain_abstention_rows_clean_table_does_not():
    m = metrics.qa_metrics(_jrows(), split_id="oracle@98d7416")
    official = m["qa.by_type.oracle@98d7416"]["temporal-reasoning"]
    clean = m["qa.by_type_clean_non_official.oracle@98d7416"]
    assert official["n"] == 5              # 4 answerable + 1 `_abs`
    assert clean["temporal-reasoning"]["n"] == 4
    assert clean["abstention"]["n"] == 1


def test_abstention_is_reported_two_sided():
    """A one-sided abstention number is gameable: over-abstention destroys
    preference and temporal, under-abstention loses the 30 `_abs` rows."""
    rows = _jrows()
    rows[0].predicted_abstain = True
    m = metrics.qa_metrics(rows, split_id="oracle@98d7416")
    cell = m["qa.abstention.oracle@98d7416"]
    assert cell["accuracy"] == 1.0
    assert cell["false_abstention_rate_on_answerable"] > 0
    assert cell["n"] == 1 and cell["n_answerable"] == 9


def test_retrieval_and_qa_live_in_separate_namespaces():
    rr = [metrics.RetrievalRow("q1", "multi-session", ["s1"], ["s1", "s2"], 2)]
    rm = metrics.retrieval_metrics(rr, split_id="oracle@98d7416")
    qm = metrics.qa_metrics(_jrows(), split_id="oracle@98d7416")
    combined = metrics.combine(rm, qm)          # runs T6 + T8
    assert all(k.startswith(("qa.", "retrieval.")) for k in combined)
    with pytest.raises(guards.GuardViolation):
        metrics.combine({"accuracy": 0.9})


def test_recall_is_returned_set_recall_at_k():
    r = metrics.RetrievalRow("q", "t", ["a", "b"], ["x", "a", "y", "b"], 4)
    assert r.recall_at(1) == 0.0
    assert r.recall_at(2) == 0.5
    assert r.recall_at(4) == 1.0


def test_oracle_recall_carries_its_caveat_inline():
    rr = [metrics.RetrievalRow("q1", "t", ["s1"], ["s1"], 1)]
    m = metrics.retrieval_metrics(rr, split_id="oracle@98d7416")
    assert "by construction" in m["retrieval.caveat.oracle@98d7416"]


def test_render_board_masks_phantom_cells():
    m = {"qa.micro.x": 0.94, "qa.macro.x": 0.71}
    out = "\n".join(metrics.render_board({"a3": m}))
    assert "SUSPECT(T4)" in out and "0.7100" in out
