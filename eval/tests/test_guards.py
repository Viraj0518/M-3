"""Every guard, in BOTH directions.

"Validate that the instrument returns a positive before trusting its zero."
A guard that only ever gets bad input is indistinguishable from a guard that
raises unconditionally; a guard that only ever gets good input is
indistinguishable from a `pass` statement. So every trap gets one of each.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from lme import guards


def _rows(n=5, run_id="RID", served="claude-sonnet-4-6-20260514"):
    return [
        {"run_id": run_id, "question_id": f"q{i}", "served_model": served} for i in range(n)
    ]


NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
PAST = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── T1 ──────────────────────────────────────────────────────────────────────
def test_t1_accepts_fresh_rows():
    guards.t1_fresh_rows(_rows(), run_id="RID", started_at=PAST)


def test_t1_rejects_stale_run_id():
    with pytest.raises(guards.GuardViolation) as e:
        guards.t1_fresh_rows(_rows(run_id="OLD"), run_id="RID", started_at=PAST)
    assert e.value.trap == "T1"


def test_t1_rejects_artifact_older_than_run(tmp_path):
    p = tmp_path / "rows.jsonl"
    p.write_text("{}")
    import os
    old = (datetime.now(timezone.utc) - timedelta(days=2)).timestamp()
    os.utime(p, (old, old))
    with pytest.raises(guards.GuardViolation) as e:
        guards.t1_fresh_rows(_rows(), run_id="RID", started_at=NOW, artifact_paths=[p])
    assert e.value.trap == "T1"


# ── T2 ──────────────────────────────────────────────────────────────────────
def test_t2_accepts_pinned_model():
    guards.t2_served_model("claude-sonnet-4-6-20260514", pinned="claude-sonnet-4-6-20260514")


@pytest.mark.parametrize("served", [None, "claude-haiku-4-5-20251001", "gpt-4o-mini", ""])
def test_t2_rejects_anything_but_the_pin(served):
    with pytest.raises(guards.GuardViolation) as e:
        guards.t2_served_model(served, pinned="claude-sonnet-4-6-20260514")
    assert e.value.trap == "T2"


def test_t2_collect_is_non_raising_but_complete():
    rows = _rows(3) + _rows(2, served="gpt-4o-mini")
    v = guards.t2_collect_violations(rows, pinned="claude-sonnet-4-6-20260514")
    assert len(v) == 2 and all(x["served_model"] == "gpt-4o-mini" for x in v)


# ── T3 ──────────────────────────────────────────────────────────────────────
def test_t3_accepts_complete_run():
    guards.t3_complete(_rows(5), expected_n=5)


def test_t3_rejects_partial_run():
    with pytest.raises(guards.GuardViolation) as e:
        guards.t3_complete(_rows(4), expected_n=5)
    assert e.value.trap == "T3"


def test_t3_rejects_duplicate_question_ids():
    rows = _rows(3)
    rows.append(dict(rows[0]))
    with pytest.raises(guards.GuardViolation):
        guards.t3_complete(rows, expected_n=4)


# ── T4 ──────────────────────────────────────────────────────────────────────
def test_t4_flags_only_at_or_above_threshold():
    flags = guards.t4_phantom_flags({"qa.micro": 0.8999, "qa.macro": 0.90, "qa.x": 0.95})
    assert {f.metric for f in flags} == {"qa.macro", "qa.x"}


def test_t4_blocks_headline_but_allows_confirmed():
    with pytest.raises(guards.GuardViolation) as e:
        guards.t4_assert_not_spoken(guards.t4_phantom_flags({"qa.micro": 0.94}))
    assert e.value.trap == "T4"
    guards.t4_assert_not_spoken(guards.t4_phantom_flags({"qa.micro": 0.94}, confirmed=["qa.micro"]))


# ── T5 ──────────────────────────────────────────────────────────────────────
def test_t5_allows_one_split_rejects_two():
    assert guards.t5_same_split("oracle@98d7416", "oracle@98d7416") == "oracle@98d7416"
    with pytest.raises(guards.GuardViolation) as e:
        guards.t5_same_split("oracle@98d7416", "s_cleaned@98d7416")
    assert e.value.trap == "T5"


# ── T6 ──────────────────────────────────────────────────────────────────────
def test_t6_accepts_namespaced_rejects_flat():
    guards.t6_namespaced({"qa.micro.oracle": 0.8, "retrieval.recall@5.oracle": 1.0})
    with pytest.raises(guards.GuardViolation) as e:
        guards.t6_namespaced({"accuracy": 0.9, "recall@5": 0.9})
    assert e.value.trap == "T6"


# ── T7 ──────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "model,family",
    [
        ("claude-sonnet-4-6-20260514", "anthropic"),
        ("claude-opus-5[1m]", "anthropic"),
        ("gpt-4o-2024-08-06", "openai"),
        ("gemini-1.5-pro-002", "google"),
        ("meta-llama/Meta-Llama-3.1-70B-Instruct", "meta"),
        ("nemotron-4-340b", "nvidia"),
    ],
)
def test_model_family_substring_matching_survives_reslug(model, family):
    assert guards.model_family(model) == family


def test_t7_accepts_cross_family_rejects_same_family():
    guards.t7_family_exclusion("claude-sonnet-4-6-20260514", ["gpt-4o-2024-08-06"])
    with pytest.raises(guards.GuardViolation) as e:
        guards.t7_family_exclusion("claude-sonnet-4-6-20260514", ["claude-opus-4-1-20250805"])
    assert e.value.trap == "T7"


def test_t7_rejects_unknown_reader_family():
    """An unmappable model means the exclusion CANNOT be enforced, which must
    fail loudly rather than pass by default."""
    with pytest.raises(guards.GuardViolation):
        guards.t7_family_exclusion("some-private-model-v3", ["gpt-4o-2024-08-06"])


def test_t7_panel_needs_three_distinct_families():
    guards.t7_panel_distinct(
        ["gpt-4o-2024-08-06", "gemini-1.5-pro-002", "meta-llama/Meta-Llama-3.1-70B-Instruct"]
    )
    with pytest.raises(guards.GuardViolation):
        guards.t7_panel_distinct(["gpt-4o-2024-08-06", "gpt-4o-mini", "gpt-4.1"])


# ── T8 ──────────────────────────────────────────────────────────────────────
def test_t8_requires_both_micro_and_macro():
    guards.t8_both_present({"qa.micro.x": 0.8, "qa.macro.x": 0.7})
    with pytest.raises(guards.GuardViolation) as e:
        guards.t8_both_present({"qa.micro.x": 0.8})
    assert e.value.trap == "T8"


# ── T9 ──────────────────────────────────────────────────────────────────────
def test_t9_pin():
    guards.t9_pinned("abc", expected="abc")
    with pytest.raises(guards.GuardViolation) as e:
        guards.t9_pinned("abc", expected="def")
    assert e.value.trap == "T9"


# ── T10 ─────────────────────────────────────────────────────────────────────
def test_t10_requires_prompt_in_config_hash():
    guards.t10_prompt_hashed({"config_yaml": "a", "reader_prompt": "b"}, required=["reader_prompt"])
    with pytest.raises(guards.GuardViolation) as e:
        guards.t10_prompt_hashed({"config_yaml": "a"}, required=["reader_prompt"])
    assert e.value.trap == "T10"


# ── T11 ─────────────────────────────────────────────────────────────────────
def test_t11_noise_floor_note_only_at_or_above_96pct():
    assert guards.t11_noise_note(0.9599) is None
    assert "noise floor" in (guards.t11_noise_note(0.96) or "")


# ── T12 ─────────────────────────────────────────────────────────────────────
def test_t12_budget_and_equal_k():
    guards.t12_returned_set(12, budget=12)
    with pytest.raises(guards.GuardViolation) as e:
        guards.t12_returned_set(40, budget=12)
    assert e.value.trap == "T12"
    guards.t12_compare_budgets({"a1": 12, "a3": 12})
    with pytest.raises(guards.GuardViolation):
        guards.t12_compare_budgets({"a1": 12, "a3": 40})


# ── T13 ─────────────────────────────────────────────────────────────────────
def test_t13_flags_non_additive_levers():
    assert guards.t13_no_summing({"a": 0.05, "b": 0.05}, composed_delta=0.10) is None
    note = guards.t13_no_summing({"a": 0.05, "b": 0.05}, composed_delta=0.07)
    assert note and "NOT summable" in note


# ── T14 ─────────────────────────────────────────────────────────────────────
def test_t14_rejects_gold_features_allows_runtime_features():
    guards.t14_router_features({"temporal": True, "n_tokens": 9})
    with pytest.raises(guards.GuardViolation) as e:
        guards.t14_router_features({"question_type": "temporal-reasoning"})
    assert e.value.trap == "T14"


def test_t14_oracle_routed_arm_may_opt_in():
    guards.t14_router_features({"question_type": "x"}, allow_oracle=True)


def test_t14_source_scan_catches_attr_and_subscript():
    with pytest.raises(guards.GuardViolation):
        guards.t14_scan_source("if q.question_type == 'x': pass")
    with pytest.raises(guards.GuardViolation):
        guards.t14_scan_source("if entry['answer_session_ids']: pass")
    guards.t14_scan_source("if feats.temporal: pass")


def test_t14_live_router_source_is_gold_free():
    """The shipped router, scanned as source. Survives a refactor that renames
    the feature dict."""
    from pathlib import Path

    import lme.graph.retrieve as r

    guards.t14_scan_source(Path(r.__file__).read_text(), symbol="graph/retrieve.py")


# ── T15 ─────────────────────────────────────────────────────────────────────
def test_t15_fires_when_sabotage_passes():
    guards.t15_negative_fixture(gate_result_on_sabotage=False)
    with pytest.raises(guards.GuardViolation) as e:
        guards.t15_negative_fixture(gate_result_on_sabotage=True)
    assert e.value.trap == "T15"


def test_gate_runs_green_end_to_end():
    """The gate itself, executed. Both directions, 15 saboteurs + 6 controls."""
    from lme.gate import run_gate

    res = run_gate(verbose=False)
    assert res.ok, res.failures
    assert res.saboteurs_caught == res.saboteurs_total > 0
    assert res.controls_passed == res.controls_total > 0
