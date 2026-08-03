"""T1-T15 -- every trap from design doc S1.3 as an EXECUTABLE ASSERTION.

Prose in a README does not stop a phantom. Four published >=0.9 numbers on this
team were phantoms (judge-switch, stale-report, lucky single-run, small-n) plus a
fifth (post-hoc metric selection), and one cost a retraction. The root cause of
the 0.9010 phantom is worth restating because it is the shape of ALL of them:

    "Harness silent-failed on a missing SUPABASE_PG_URL and RE-JUDGED A STALE
     REPORT FILE instead of re-running. It looked 'deterministic' only because
     two runs read the same stale file." Real score: 0.61-0.68.

Every guard here FAILS LOUD. None of them logs a warning and continues. A guard
that can be ignored is not a guard.

Design principle inherited from prior art: VALIDATE THAT THE INSTRUMENT RETURNS
A POSITIVE BEFORE TRUSTING ITS ZERO. `tests/test_guards.py` therefore asserts
each guard both passes on good input AND raises on bad input -- an assertion
that never fires is indistinguishable from an assertion that cannot fire.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


class GuardViolation(RuntimeError):
    """A trap fired. The run is INVALID. Do not aggregate, do not print a score."""

    def __init__(self, trap: str, message: str) -> None:
        super().__init__(f"[{trap}] {message}")
        self.trap = trap
        self.message = message


TRAPS: Dict[str, str] = {
    "T1": "stale-artifact re-read",
    "T2": "silent reader fallback (served != pinned model)",
    "T3": "silent-kill / partial run",
    "T4": ">=0.90 disbelief",
    "T5": "oracle<->s split mixing",
    "T6": "recall@k quoted as accuracy",
    "T7": "reader family == judge family",
    "T8": "micro vs macro conflation",
    "T9": "dataset drift",
    "T10": "prompt confound",
    "T11": "gold-label noise floor",
    "T12": "ranking-not-serving law",
    "T13": "per-lever deltas are not summable",
    "T14": "router keyed on gold",
    "T15": "broken gate",
}


# ── T1: stale-artifact re-read ──────────────────────────────────────────────
def t1_fresh_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    run_id: str,
    started_at: str,
    artifact_paths: Iterable[Path] = (),
) -> None:
    """Every row must carry THIS run's id, and every artifact must post-date the
    run start. This is the guard that would have caught the 0.9010 phantom."""
    bad = [r.get("question_id", "<no-qid>") for r in rows if r.get("run_id") != run_id]
    if bad:
        raise GuardViolation(
            "T1",
            f"{len(bad)}/{len(rows)} row(s) carry a run_id != {run_id} "
            f"(first: {bad[:3]}). You are aggregating a STALE artifact.",
        )
    t0 = _parse_iso(started_at)
    for p in artifact_paths:
        p = Path(p)
        if not p.exists():
            raise GuardViolation("T1", f"expected artifact {p} does not exist")
        mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        if mtime < t0:
            raise GuardViolation(
                "T1",
                f"{p} mtime {mtime.isoformat()} predates run start {started_at} "
                "-- this file is from an earlier run.",
            )


def _parse_iso(s: str) -> datetime:
    s = s.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── T2: silent reader fallback ──────────────────────────────────────────────
def t2_served_model(served: Optional[str], *, pinned: str, question_id: str = "") -> None:
    """The correct check is `served == PINNED_CONSTANT`.

    NOT `served == requested`: providers rewrite BOTH the requested and the
    served field on fallback, so a requested/served comparison passes cleanly
    while you are silently talking to a different model.
    """
    if served is None:
        raise GuardViolation(
            "T2",
            f"{question_id or '<row>'}: the provider returned no model field. "
            "An unverifiable model is an INVALID row, not a pass.",
        )
    if served != pinned:
        raise GuardViolation(
            "T2",
            f"{question_id or '<row>'}: served={served!r} != pinned={pinned!r}. "
            "Row is INVALID-discard.",
        )


def t2_collect_violations(
    rows: Sequence[Mapping[str, Any]], *, pinned: str
) -> List[Dict[str, str]]:
    """Non-raising sweep -> `manifest.served_model_violations`. A NON-EMPTY list
    makes the whole run INVALID (manifest.finalize enforces that)."""
    out: List[Dict[str, str]] = []
    for r in rows:
        served = r.get("served_model")
        if served != pinned:
            out.append(
                {
                    "question_id": str(r.get("question_id", "")),
                    "served_model": str(served),
                    "pinned_model": pinned,
                }
            )
    return out


# ── T3: silent-kill / partial run ───────────────────────────────────────────
def t3_complete(rows: Sequence[Mapping[str, Any]], *, expected_n: int) -> None:
    """Asserted BEFORE any aggregate is computed or printed. A partial run whose
    mean is taken over the rows that happened to survive is a phantom."""
    if len(rows) != expected_n:
        raise GuardViolation(
            "T3",
            f"got {len(rows)} rows, manifest.expected_n={expected_n}. "
            "Refusing to aggregate a partial run.",
        )
    qids = [r.get("question_id") for r in rows]
    dupes = {q for q in qids if qids.count(q) > 1}
    if dupes:
        raise GuardViolation("T3", f"duplicate question_id(s): {sorted(dupes)[:5]}")


# ── T4: >=0.90 disbelief ────────────────────────────────────────────────────
PHANTOM_THRESHOLD: float = 0.90


@dataclass(frozen=True)
class PhantomFlag:
    metric: str
    value: float
    status: str = "SUSPECT -- requires refutation round"

    def to_json(self) -> Dict[str, Any]:
        return {"metric": self.metric, "value": round(self.value, 4), "status": self.status}


def t4_phantom_flags(
    scores: Mapping[str, float],
    *,
    threshold: float = PHANTOM_THRESHOLD,
    confirmed: Iterable[str] = (),
) -> List[PhantomFlag]:
    """Non-raising by design: it STAMPS. Any arm/category >= 0.90 is flagged
    SUSPECT and blocked from the summary table until a second seed AND a second
    judge family confirm it. Prior work is 4-for-4 on >=0.9 being a phantom.

    Small-n cells are flagged too: `single-session-preference` is n=30, where one
    item is 3.3 points -- "lucky single-run, small-n" was phantom #3 and #4.
    """
    confirmed_set = set(confirmed)
    return [
        PhantomFlag(metric=k, value=float(v))
        for k, v in sorted(scores.items())
        if isinstance(v, (int, float)) and float(v) >= threshold and k not in confirmed_set
    ]


def t4_assert_not_spoken(flags: Sequence[PhantomFlag]) -> None:
    """Call this at the point a number would be HEADLINED, not at compute time."""
    if flags:
        raise GuardViolation(
            "T4",
            "refusing to headline unrefuted >=0.90 cell(s): "
            + ", ".join(f"{f.metric}={f.value:.4f}" for f in flags)
            + ". Confirm with a second seed AND a second judge family first.",
        )


# ── T5: oracle<->s mixing ───────────────────────────────────────────────────
def t5_same_split(*split_ids: str) -> str:
    """Split identity is part of the metric key. Mixing is a category error."""
    uniq = sorted(set(s for s in split_ids if s))
    if len(uniq) > 1:
        raise GuardViolation(
            "T5",
            f"attempted to aggregate across split ids {uniq}. Oracle recall is "
            "1.0 by construction; an oracle number bounds the READER and is not "
            "comparable to any `_s` number.",
        )
    return uniq[0] if uniq else ""


# ── T6: recall@k quoted as accuracy ─────────────────────────────────────────
QA_NAMESPACE: str = "qa"
RETRIEVAL_NAMESPACE: str = "retrieval"


def t6_namespaced(metrics: Mapping[str, Any]) -> None:
    """Every emitted metric key must live under `qa.` or `retrieval.`.

    "That conflation is how phantom #1 was born." A bare `recall@5` key next to a
    bare `accuracy` key in one dict is one copy-paste away from a headline.
    """
    stray = [
        k
        for k in metrics
        if not (k.startswith(f"{QA_NAMESPACE}.") or k.startswith(f"{RETRIEVAL_NAMESPACE}."))
    ]
    if stray:
        raise GuardViolation(
            "T6",
            f"un-namespaced metric key(s) {stray[:5]}. Use `qa.*` for answer "
            f"accuracy and `retrieval.*` for recall@k -- never one flat dict.",
        )


# ── T7: reader family == judge family ───────────────────────────────────────
#: SUBSTRING-matched on purpose: a re-slug ("claude-opus-5-1m", "gpt4o-mini-v2")
#: must still be caught. Order matters only for readability.
MODEL_FAMILIES: Dict[str, tuple] = {
    "anthropic": ("claude", "anthropic", "sonnet", "opus", "haiku", "fable"),
    "openai": ("gpt-", "gpt4", "gpt3", "o1-", "o3-", "o4-", "openai", "davinci", "chatgpt"),
    "google": ("gemini", "palm", "bison", "gemma"),
    "meta": ("llama",),
    "mistral": ("mistral", "mixtral", "magistral"),
    "nvidia": ("nemotron",),
    "qwen": ("qwen",),
    "deepseek": ("deepseek",),
    "cohere": ("command-r", "cohere"),
}


def model_family(model: str) -> str:
    m = (model or "").lower()
    for fam, needles in MODEL_FAMILIES.items():
        if any(n in m for n in needles):
            return fam
    return f"unknown:{m}"


def t7_family_exclusion(reader_model: str, judge_models: Sequence[str]) -> None:
    """A judge may not share a family with the reader. Judge miscalibration is
    BIDIRECTIONAL -- truth is the cross-family-calibrated band, never one judge,
    and a same-family judge is a self-grade."""
    rfam = model_family(reader_model)
    clash = [j for j in judge_models if model_family(j) == rfam]
    if clash:
        raise GuardViolation(
            "T7",
            f"reader {reader_model!r} (family {rfam}) shares a family with "
            f"judge(s) {clash}. A same-family judge is a self-grade.",
        )
    if rfam.startswith("unknown:"):
        raise GuardViolation(
            "T7",
            f"reader model {reader_model!r} maps to no known family, so the "
            "exclusion check cannot be enforced. Add it to MODEL_FAMILIES.",
        )


def t7_panel_distinct(judge_models: Sequence[str], *, min_families: int = 3) -> None:
    """The strict panel's independence lives in the MODEL FAMILY, never in the
    rubric (the rubric is shared verbatim across lanes by design)."""
    fams = {model_family(j) for j in judge_models}
    unknown = sorted(f for f in fams if f.startswith("unknown:"))
    if unknown:
        raise GuardViolation("T7", f"panel member(s) map to no known family: {unknown}")
    if len(fams) < min_families:
        raise GuardViolation(
            "T7",
            f"strict panel spans {len(fams)} families {sorted(fams)}, need "
            f">= {min_families}. Same-family 'independent' lanes are one lane.",
        )


# ── T8: micro vs macro ──────────────────────────────────────────────────────
def t8_both_present(metrics: Mapping[str, Any]) -> None:
    """Emit BOTH, always. Mastra reports macro, the paper reports micro; they
    differ by many points because n ranges 30 -> 133. A single number labelled
    'accuracy' is unfalsifiable."""
    have_micro = any(".micro" in k for k in metrics)
    have_macro = any(".macro" in k for k in metrics)
    if not (have_micro and have_macro):
        raise GuardViolation(
            "T8",
            f"micro present={have_micro}, macro present={have_macro}. Both are "
            "mandatory (official print_qa_metrics.py emits both).",
        )


# ── T9: dataset drift ── enforced in dataset.assert_dataset_pin ─────────────
def t9_pinned(dataset_sha: str, *, expected: str, name: str = "") -> None:
    if dataset_sha != expected:
        raise GuardViolation(
            "T9", f"dataset {name} sha256 {dataset_sha} != pinned {expected}"
        )


# ── T10: prompt confound ────────────────────────────────────────────────────
def t10_prompt_hashed(config_hash_inputs: Mapping[str, str], *, required: Sequence[str]) -> None:
    """CoN+JSON formatting alone is worth UP TO 10 ABSOLUTE POINTS. If the
    answering prompt is not inside config_hash, a prompt edit silently becomes
    a 'mechanism win'."""
    missing = [k for k in required if k not in config_hash_inputs]
    if missing:
        raise GuardViolation(
            "T10",
            f"config_hash does not cover {missing}. A prompt change must be a "
            "NEW CONFIG, not an untracked edit.",
        )


# ── T11: gold-label noise floor ─────────────────────────────────────────────
NOISE_FLOOR: float = 0.96


def t11_noise_note(value: float) -> Optional[str]:
    """~12 open annotation-error issues upstream. Anything >= 96% is at or past
    the noise floor -- say so, in the report, next to the number."""
    if value >= NOISE_FLOOR:
        return (
            f"{value:.4f} is at/past the gold-label noise floor ({NOISE_FLOOR:.2f}); "
            "upstream has ~12 open annotation-error issues. This number cannot be "
            "distinguished from label noise."
        )
    return None


# ── T12: ranking-not-serving law ────────────────────────────────────────────
def t12_returned_set(size: int, *, budget: int, arm: str = "") -> None:
    """Triangulated x3 in prior art: RANK POSITION CONVERTS, VOLUME NEVER.
    Do not 'fix' a category by raising top-k. Every arm reports its returned-set
    size and the harness refuses a silent budget inflation."""
    if size > budget:
        raise GuardViolation(
            "T12",
            f"{arm or 'arm'} returned {size} items but the declared budget is "
            f"{budget}. Raising top-k is not a mechanism; it is a confound. "
            "Change the config (=> new config_hash) if you mean it.",
        )


def t12_compare_budgets(budgets: Mapping[str, int]) -> None:
    """Arms compared head-to-head must serve the SAME budget, else the delta is
    a volume delta wearing a mechanism's name."""
    uniq = sorted(set(budgets.values()))
    if len(uniq) > 1:
        raise GuardViolation(
            "T12",
            f"arms declare different serving budgets {dict(budgets)}. An equal-k "
            "comparison is mandatory; report production-k separately.",
        )


# ── T13: per-lever deltas are not summable ──────────────────────────────────
def t13_no_summing(lever_deltas: Mapping[str, float], *, composed_delta: float,
                   tolerance: float = 1e-9) -> Optional[str]:
    """Only the measured composed stack is citable. Returns an advisory string
    when someone's arithmetic implies additivity; the overlap matrix (each
    failure row reclaimable EXACTLY ONCE) is what settles it."""
    naive = sum(lever_deltas.values())
    if abs(naive - composed_delta) > tolerance:
        return (
            f"sum(per-lever deltas)={naive:+.4f} != measured composed "
            f"delta={composed_delta:+.4f}. Per-lever deltas are NOT summable "
            "(T13) -- publish the lever x failure-bucket overlap matrix; only "
            "the composed number is citable."
        )
    return None


# ── T14: router keyed on gold ───────────────────────────────────────────────
#: Field names that are GOLD. A router that reads any of these is scoring itself.
GOLD_FIELDS: tuple = (
    "question_type",
    "answer",
    "answer_session_ids",
    "has_answer",
    "gold",
    "gold_session_ids",
    "is_abstention",
)


def t14_router_features(features: Mapping[str, Any], *, allow_oracle: bool = False) -> None:
    """The feature router keys on QUESTION TEXT ONLY -- date/duration regexes,
    "how long/when/before/after/since", preference verbs, superlatives.

    The `a3_oracle_routed` arm may pass allow_oracle=True, and its results are
    reported ONLY under the header `ORACLE-ROUTED -- BOUND, NOT A RESULT`.
    """
    if allow_oracle:
        return
    leaked = [k for k in features if k in GOLD_FIELDS]
    if leaked:
        raise GuardViolation(
            "T14",
            f"router read gold field(s) {leaked}. A router must key on "
            "RUNTIME-COMPUTABLE question features only.",
        )


def t14_scan_source(source: str, *, symbol: str = "<router>") -> None:
    """Static backstop: catch `q.question_type` / `entry['answer_session_ids']`
    inside a router body. Cheap, and it survives a refactor that renames the
    feature dict."""
    hits = sorted(
        {
            f
            for f in GOLD_FIELDS
            if re.search(rf"""(\.{f}\b)|(\[['"]{f}['"]\])""", source)
        }
    )
    if hits:
        raise GuardViolation(
            "T14", f"{symbol} source references gold field(s) {hits}"
        )


# ── T15: broken gate ────────────────────────────────────────────────────────
def t15_negative_fixture(gate_result_on_sabotage: bool) -> None:
    """A deliberately-sabotaged arm MUST FAIL the gate. If it passes, the gate is
    broken and every green it ever produced is meaningless.

    `judge_canonical_lane.py --selftest` passed for weeks while its live path had
    never executed once. Validate that the instrument returns a POSITIVE before
    trusting its zero.
    """
    if gate_result_on_sabotage:
        raise GuardViolation(
            "T15",
            "the sabotaged negative fixture PASSED the gate. The gate is broken; "
            "no green it has ever emitted can be trusted.",
        )


__all__ = [
    "GOLD_FIELDS",
    "GuardViolation",
    "MODEL_FAMILIES",
    "NOISE_FLOOR",
    "PHANTOM_THRESHOLD",
    "PhantomFlag",
    "QA_NAMESPACE",
    "RETRIEVAL_NAMESPACE",
    "TRAPS",
    "model_family",
    "t1_fresh_rows",
    "t2_collect_violations",
    "t2_served_model",
    "t3_complete",
    "t4_assert_not_spoken",
    "t4_phantom_flags",
    "t5_same_split",
    "t6_namespaced",
    "t7_family_exclusion",
    "t7_panel_distinct",
    "t8_both_present",
    "t9_pinned",
    "t10_prompt_hashed",
    "t11_noise_note",
    "t12_compare_budgets",
    "t12_returned_set",
    "t13_no_summing",
    "t14_router_features",
    "t14_scan_source",
    "t15_negative_fixture",
]
