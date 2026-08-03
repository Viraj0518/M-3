"""T15 -- THE NEGATIVE FIXTURE. These runs MUST FAIL the gate.

"A deliberately-sabotaged arm must FAIL the gate; if it passes, the gate is
broken." Every green this harness has ever emitted is worthless if it cannot
produce a red on demand.

This matters more here than it sounds. `~/unblock-eval/judge_canonical_lane.py`
shipped a `--selftest` that passed for weeks while the live path had never
executed once -- the instrument returned a zero and nobody had ever checked it
could return a positive. The `uv run eval gate` command runs every saboteur
below and FAILS IF ANY OF THEM PASSES.

Each saboteur reproduces a REAL documented failure, not an invented one:

  stale_run_id       the 0.9010 phantom itself -- re-judging a stale report file
                     (T1)
  partial_run        a silent-kill that drops rows, then means over survivors
                     (T3)
  served_model_swap  a provider fallback to a cheaper model (T2)
  phantom_score      a >=0.90 cell quoted without a refutation round (T4)
  split_mixing       oracle rows aggregated into an `_s` number (T5)
  recall_as_accuracy recall@k presented in the qa namespace (T6)
  same_family_judge  the reader grading itself (T7)
  micro_only         a lone unlabelled "accuracy" (T8)
  dataset_drift      the deprecated pre-cleaning `_s` bytes (T9)
  gold_routed        a router keyed on gold `question_type` (T14)
  budget_inflation   top-k quietly raised to buy recall (T12)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Tuple

from lme import guards, metrics
from lme.manifest import Manifest, Pins

RUN_ID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
STARTED = "2026-08-03T18:00:00Z"


def _rows(n: int = 10, *, run_id: str = RUN_ID, served: str = "claude-sonnet-4-6-20260514") -> List[Dict[str, Any]]:
    return [
        {
            "run_id": run_id,
            "question_id": f"q{i}",
            "question_type": "multi-session",
            "served_model": served,
            "label": True,
        }
        for i in range(n)
    ]


# ── the saboteurs ───────────────────────────────────────────────────────────
def stale_run_id() -> None:
    """T1: rows carry a PREVIOUS run's id. The actual 0.9010 mechanism."""
    rows = _rows(10, run_id="staleaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    guards.t1_fresh_rows(rows, run_id=RUN_ID, started_at=STARTED)


def partial_run() -> None:
    """T3: 7 rows delivered where the manifest promised 10."""
    guards.t3_complete(_rows(7), expected_n=10)


def served_model_swap() -> None:
    """T2: the provider quietly served a cheaper model."""
    guards.t2_served_model("claude-haiku-4-5-20251001", pinned="claude-sonnet-4-6-20260514")


def served_model_swap_manifest() -> None:
    """T2 at the manifest layer: finalize() must refuse the whole run."""
    man = Manifest(
        arm="sabotage", dataset_file="longmemeval_oracle.json", dataset_sha256="x",
        hf_repo="r", hf_revision="v", split_name="smoke-30", split_n=10,
        split_ids_sha256="s", seed=1, expected_n=10,
        pins=Pins(reader="claude-sonnet-4-6-20260514"),
    )
    man.run_id = RUN_ID
    man.started_at = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    man.finalize(_rows(10, served="gpt-4o-mini"), enforce_served_model=True)


def phantom_score() -> None:
    """T4: a 0.94 cell headlined without a refutation round."""
    flags = guards.t4_phantom_flags({"qa.micro.s_cleaned@98d7416": 0.94})
    guards.t4_assert_not_spoken(flags)


def split_mixing() -> None:
    """T5: oracle and `_s` rows folded into one number."""
    guards.t5_same_split("oracle@98d7416", "s_cleaned@98d7416")


def recall_as_accuracy() -> None:
    """T6: "that conflation is how phantom #1 was born"."""
    guards.t6_namespaced({"accuracy": 0.9, "recall@5": 0.9})


def same_family_judge() -> None:
    """T7: the reader grades its own homework."""
    guards.t7_family_exclusion(
        "claude-sonnet-4-6-20260514", ["claude-opus-4-1-20250805", "gpt-4o-2024-08-06"]
    )


def same_family_panel() -> None:
    """T7: a 3-model "cross-family" panel that is really one family."""
    guards.t7_panel_distinct(["gpt-4o-2024-08-06", "gpt-4o-mini", "gpt-4.1"], min_families=3)


def micro_only() -> None:
    """T8: one unlabelled number. Mastra reports macro, the paper micro."""
    guards.t8_both_present({"qa.micro.oracle@98d7416": 0.8})


def dataset_drift() -> None:
    """T9: the deprecated pre-cleaning `_s` bytes."""
    guards.t9_pinned(
        "08d8dad4be43000000000000000000000000000000000000000000000000dead",
        expected="d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442",
        name="longmemeval_s_cleaned.json",
    )


def gold_routed() -> None:
    """T14: the router peeks at gold `question_type`."""
    guards.t14_router_features({"question_type": "temporal-reasoning", "n_tokens": 9})


def gold_routed_source() -> None:
    """T14 static backstop: gold field referenced in a router body."""
    guards.t14_scan_source(
        "def route(q):\n    if q.question_type == 'temporal-reasoning':\n        return 'S4'\n",
        symbol="sabotaged_router",
    )


def budget_inflation() -> None:
    """T12: top-k quietly raised to buy recall. Volume never converts."""
    guards.t12_returned_set(40, budget=12, arm="a3_sabotage")


def unequal_budgets() -> None:
    """T12: arms compared head-to-head at different k."""
    guards.t12_compare_budgets({"a1_naive_rag": 12, "a3_palimpsest": 40})


#: name -> (callable, trap it must trip). `uv run eval gate` runs every one.
SABOTEURS: Dict[str, Tuple[Callable[[], None], str]] = {
    "stale_run_id": (stale_run_id, "T1"),
    "partial_run": (partial_run, "T3"),
    "served_model_swap": (served_model_swap, "T2"),
    "served_model_swap_manifest": (served_model_swap_manifest, "T2"),
    "phantom_score": (phantom_score, "T4"),
    "split_mixing": (split_mixing, "T5"),
    "recall_as_accuracy": (recall_as_accuracy, "T6"),
    "same_family_judge": (same_family_judge, "T7"),
    "same_family_panel": (same_family_panel, "T7"),
    "micro_only": (micro_only, "T8"),
    "dataset_drift": (dataset_drift, "T9"),
    "gold_routed": (gold_routed, "T14"),
    "gold_routed_source": (gold_routed_source, "T14"),
    "budget_inflation": (budget_inflation, "T12"),
    "unequal_budgets": (unequal_budgets, "T12"),
}


# ── the POSITIVE controls ───────────────────────────────────────────────────
# "Validate that the instrument returns a POSITIVE before trusting its zero."
# A gate made only of saboteurs would also pass if every guard raised
# unconditionally. These clean inputs MUST NOT raise.
def clean_rows() -> None:
    rows = _rows(10)
    guards.t3_complete(rows, expected_n=10)
    guards.t1_fresh_rows(rows, run_id=RUN_ID, started_at=STARTED)
    for r in rows:
        guards.t2_served_model(r["served_model"], pinned="claude-sonnet-4-6-20260514")


def clean_namespaces() -> None:
    guards.t6_namespaced({"qa.micro.oracle@98d7416": 0.8, "retrieval.recall@5.oracle@98d7416": 1.0})
    guards.t8_both_present({"qa.micro.oracle@98d7416": 0.8, "qa.macro.oracle@98d7416": 0.7})


def clean_families() -> None:
    guards.t7_family_exclusion(
        "claude-sonnet-4-6-20260514",
        ["gpt-4o-2024-08-06", "gemini-1.5-pro-002", "meta-llama/Meta-Llama-3.1-70B-Instruct"],
    )
    guards.t7_panel_distinct(
        ["gpt-4o-2024-08-06", "gemini-1.5-pro-002", "meta-llama/Meta-Llama-3.1-70B-Instruct"]
    )


def clean_router() -> None:
    guards.t14_router_features({"temporal": True, "preference": False, "n_tokens": 9})


def clean_budget() -> None:
    guards.t12_returned_set(12, budget=12, arm="a3_palimpsest")
    guards.t12_compare_budgets({"a1_naive_rag": 12, "a3_palimpsest": 12})


def clean_no_phantom() -> None:
    guards.t4_assert_not_spoken(guards.t4_phantom_flags({"qa.micro.s_cleaned@98d7416": 0.71}))


POSITIVE_CONTROLS: Dict[str, Callable[[], None]] = {
    "clean_rows": clean_rows,
    "clean_namespaces": clean_namespaces,
    "clean_families": clean_families,
    "clean_router": clean_router,
    "clean_budget": clean_budget,
    "clean_no_phantom": clean_no_phantom,
}


__all__ = ["POSITIVE_CONTROLS", "SABOTEURS"]
