"""Aggregation -- micro/macro/per-type/abstention + retrieval recall@k.

TWO SEPARATE NAMESPACES, and they never share a dict (T6):

    qa.*         answer accuracy, judged
    retrieval.*  recall@k, returned-set size -- JUDGE-FREE, DETERMINISTIC

"recall@k quoted as accuracy is how phantom #1 was born." The separation is
structural here: `qa_metrics()` and `retrieval_metrics()` are different
functions returning different prefixes, and `combine()` runs `t6_namespaced`
over the union before anything is emitted.

`retrieval.*` is the GO/NO-GO gate precisely BECAUSE it has no judge in it --
all four historical phantoms were scoring artifacts, not capability gains.

The official aggregates replicated exactly (design doc S2.3/S2.4):
  * `Overall Accuracy`      -> qa.micro           (mean over all rows)
  * `Task-averaged Accuracy`-> qa.macro           (mean over 6 CONTAMINATED buckets)
  * per-type                -> qa.by_type.<t>     (contains the `_abs` rows)
  * `Abstention Accuracy`   -> qa.abstention.accuracy (the 30 `_abs` rows)

Plus, labelled `non-official`, the clean 7-way table where abstention is its own
bucket -- there is no clean 7-way breakdown in official code, so ours is
additional, never a substitute.

Metric keys are also SPLIT-TAGGED (T5): `qa.micro.s_cleaned@98d7416`. An oracle
number and an `_s` number cannot collide in one dict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from . import guards
from .dataset import QUESTION_TYPES


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


# ── QA ──────────────────────────────────────────────────────────────────────
@dataclass
class JudgedRow:
    """One judged answer. `label` is the judge's boolean."""

    question_id: str
    question_type: str
    is_abstention: bool
    label: bool
    #: True when the arm produced an empty/abstaining prediction, whatever the gold.
    predicted_abstain: bool = False

    @classmethod
    def from_mapping(cls, m: Mapping[str, Any]) -> "JudgedRow":
        return cls(
            question_id=str(m["question_id"]),
            question_type=str(m["question_type"]),
            is_abstention=bool(m.get("is_abstention", "_abs" in str(m["question_id"]))),
            label=bool(m["label"]),
            predicted_abstain=bool(m.get("predicted_abstain", False)),
        )


def qa_metrics(rows: Sequence[JudgedRow], *, split_id: str) -> Dict[str, Any]:
    """All four official aggregates + the non-official clean table.

    `split_id` is appended to every scalar key (T5). Nested tables carry it in
    their own `split_id` field.
    """
    if not rows:
        raise ValueError("qa_metrics on zero rows -- run t3_complete first")
    tag = f".{split_id}" if split_id else ""

    labels = [1.0 if r.label else 0.0 for r in rows]

    # OFFICIAL buckets: abstention rows are ALSO counted in their base type.
    official: Dict[str, List[float]] = {}
    for r in rows:
        official.setdefault(r.question_type, []).append(1.0 if r.label else 0.0)

    # NON-OFFICIAL clean 7-way: abstention gets its own bucket, removed from base.
    clean: Dict[str, List[float]] = {}
    for r in rows:
        bucket = "abstention" if r.is_abstention else r.question_type
        clean.setdefault(bucket, []).append(1.0 if r.label else 0.0)

    abs_rows = [r for r in rows if r.is_abstention]
    answerable = [r for r in rows if not r.is_abstention]

    out: Dict[str, Any] = {
        # Overall Accuracy -- the paper's headline, micro over every row
        f"qa.micro{tag}": _mean(labels),
        # Task-averaged Accuracy -- macro over the 6 CONTAMINATED buckets
        f"qa.macro{tag}": _mean([_mean(v) for _, v in sorted(official.items())]),
        f"qa.n{tag}": float(len(rows)),
        f"qa.by_type{tag}": {
            t: {"accuracy": _mean(official[t]), "n": len(official[t])}
            for t in QUESTION_TYPES
            if t in official
        },
        f"qa.by_type_clean_non_official{tag}": {
            t: {"accuracy": _mean(v), "n": len(v)} for t, v in sorted(clean.items())
        },
        # Abstention Accuracy -- the 30 `_abs` rows
        f"qa.abstention{tag}": {
            "accuracy": _mean([1.0 if r.label else 0.0 for r in abs_rows]),
            "n": len(abs_rows),
            # ALWAYS reported next to abstention accuracy. A one-sided abstention
            # number is gameable: over-abstention destroys preference+temporal,
            # under-abstention loses the 30 `_abs` rows. Report BOTH sides.
            "false_abstention_rate_on_answerable": _mean(
                [1.0 if r.predicted_abstain else 0.0 for r in answerable]
            ),
            "n_answerable": len(answerable),
        },
        f"qa.split_id{tag}": split_id,
    }

    notes: List[str] = []
    for key in (f"qa.micro{tag}", f"qa.macro{tag}"):
        note = guards.t11_noise_note(float(out[key]))
        if note:
            notes.append(f"{key}: {note}")
    for t, cell in out[f"qa.by_type{tag}"].items():
        note = guards.t11_noise_note(float(cell["accuracy"]))
        if note:
            notes.append(f"qa.by_type.{t}: {note}")
    if notes:
        out[f"qa.noise_floor_notes{tag}"] = notes

    return out


# ── RETRIEVAL ───────────────────────────────────────────────────────────────
@dataclass
class RetrievalRow:
    """One question's retrieval outcome. NO judge anywhere in this path."""

    question_id: str
    question_type: str
    gold_session_ids: List[str]
    ranked_session_ids: List[str]
    returned_set_size: int = 0

    def recall_at(self, k: int) -> float:
        """RETURNED-SET recall, not NDCG.

        The rule lifted from `rerank-recall-check-methodology.md`: what matters
        is whether the gold session is IN THE SET THAT WAS SERVED, at equal-k and
        at production-k. A rank-weighted metric hides the case where the gold
        arrives at position 40 of a 40-item set.
        """
        if not self.gold_session_ids:
            return 0.0
        top = set(self.ranked_session_ids[:k])
        hit = sum(1 for g in self.gold_session_ids if g in top)
        return hit / len(self.gold_session_ids)


DEFAULT_KS: tuple = (1, 5, 10)


def retrieval_metrics(
    rows: Sequence[RetrievalRow],
    *,
    split_id: str,
    ks: Sequence[int] = DEFAULT_KS,
    final_top_k: Optional[int] = None,
) -> Dict[str, Any]:
    """Judge-free GO/NO-GO numbers.

    ORACLE CAVEAT, emitted inline so it cannot be dropped in transcription:
    oracle recall is 1.0 BY CONSTRUCTION (the haystack is the gold sessions), so
    an oracle recall number bounds the READER and never the system.
    """
    if not rows:
        raise ValueError("retrieval_metrics on zero rows")
    tag = f".{split_id}" if split_id else ""
    all_ks = list(dict.fromkeys(list(ks) + ([final_top_k] if final_top_k else [])))

    out: Dict[str, Any] = {f"retrieval.n{tag}": float(len(rows))}
    for k in all_ks:
        out[f"retrieval.recall@{k}{tag}"] = _mean([r.recall_at(k) for r in rows])

    by_type: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        by_type.setdefault(r.question_type, {"_rows": []})["_rows"].append(r)
    out[f"retrieval.by_type{tag}"] = {
        t: {
            **{f"recall@{k}": _mean([x.recall_at(k) for x in cell["_rows"]]) for k in all_ks},
            "n": len(cell["_rows"]),
        }
        for t, cell in sorted(by_type.items())
    }

    # T12: report the SIZE of the returned set per arm. Volume never converts;
    # a recall gain bought with a bigger set is not a retrieval gain.
    sizes = [float(r.returned_set_size) for r in rows]
    out[f"retrieval.returned_set_size{tag}"] = {
        "mean": _mean(sizes),
        "max": max(sizes) if sizes else 0.0,
        "min": min(sizes) if sizes else 0.0,
    }

    if split_id.startswith("oracle"):
        out[f"retrieval.caveat{tag}"] = (
            "ORACLE SPLIT: recall is 1.0 by construction (the haystack IS the gold "
            "sessions). This number bounds the READER, never the system."
        )
    return out


# ── combine ─────────────────────────────────────────────────────────────────
def combine(*metric_dicts: Mapping[str, Any]) -> Dict[str, Any]:
    """Union + enforce T6 (namespacing) and T8 (micro AND macro present)."""
    merged: Dict[str, Any] = {}
    for d in metric_dicts:
        for k, v in d.items():
            if k in merged and merged[k] != v:
                raise ValueError(f"metric key collision on {k!r} with differing values")
            merged[k] = v
    guards.t6_namespaced(merged)
    if any(k.startswith("qa.") for k in merged):
        guards.t8_both_present(merged)
    return merged


# ── judge-lane agreement (anti-phantom band) ────────────────────────────────
AGREEMENT_BAND: float = 0.02


@dataclass
class LaneBand:
    """Lane 1 (official) vs Lane 2 (strict panel).

    Divergence > 0.02 is reported as a JUDGE-BAND -- both lanes' numbers -- and
    NEVER collapsed to a false point. `<= 0.02` is DUAL-CONFIRMED.
    """

    official: float
    strict_maj3: float
    strict_floor: float
    per_family: Dict[str, float] = field(default_factory=dict)

    @property
    def divergence(self) -> float:
        return abs(self.official - self.strict_maj3)

    @property
    def dual_confirmed(self) -> bool:
        return self.divergence <= AGREEMENT_BAND

    def render(self) -> str:
        if self.dual_confirmed:
            return (
                f"DUAL-CONFIRMED {self.official:.4f} "
                f"(strict maj3 {self.strict_maj3:.4f}, floor {self.strict_floor:.4f}, "
                f"divergence {self.divergence:.4f} <= {AGREEMENT_BAND})"
            )
        lo, hi = sorted((self.official, self.strict_maj3))
        return (
            f"JUDGE-BAND [{lo:.4f}, {hi:.4f}] "
            f"(official {self.official:.4f} | strict maj3 {self.strict_maj3:.4f} | "
            f"strict floor {self.strict_floor:.4f}; divergence {self.divergence:.4f} "
            f"> {AGREEMENT_BAND}) -- NOT a point estimate"
        )

    def to_json(self) -> Dict[str, Any]:
        return {
            "official": round(self.official, 4),
            "strict_maj3": round(self.strict_maj3, 4),
            "strict_floor": round(self.strict_floor, 4),
            "per_family": {k: round(v, 4) for k, v in self.per_family.items()},
            "divergence": round(self.divergence, 4),
            "dual_confirmed": self.dual_confirmed,
            "rendered": self.render(),
        }


def compute_agreement(a: Sequence[bool], b: Sequence[bool]) -> float:
    """Row-wise agreement rate between two judge lanes (lifted from prior art)."""
    if len(a) != len(b):
        raise ValueError(f"lane length mismatch {len(a)} vs {len(b)}")
    if not a:
        return 0.0
    return _mean([1.0 if x == y else 0.0 for x, y in zip(a, b)])


# ── scalar view for the phantom guard ───────────────────────────────────────
def scalar_scores(metrics: Mapping[str, Any]) -> Dict[str, float]:
    """Flatten every `qa.*` accuracy to a flat name->float map for t4."""
    out: Dict[str, float] = {}
    for k, v in metrics.items():
        if not k.startswith("qa."):
            continue
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if ".n" in k:
                continue
            out[k] = float(v)
        elif isinstance(v, dict):
            for sub, cell in v.items():
                if isinstance(cell, dict) and "accuracy" in cell:
                    out[f"{k}.{sub}"] = float(cell["accuracy"])
                elif sub == "accuracy" and isinstance(cell, (int, float)):
                    out[f"{k}.accuracy"] = float(cell)
    return out


def render_board(arm_metrics: Mapping[str, Mapping[str, Any]]) -> List[str]:
    """A per-arm summary table. Cells flagged by T4 are printed as `SUSPECT`
    rather than as a number -- the guard is enforced at RENDER time, which is the
    point at which a number would actually be believed."""
    lines: List[str] = []
    for arm, m in sorted(arm_metrics.items()):
        flags = {f.metric for f in guards.t4_phantom_flags(scalar_scores(m))}
        micro_k = next((k for k in m if k.startswith("qa.micro")), None)
        macro_k = next((k for k in m if k.startswith("qa.macro")), None)
        def cell(k: Optional[str]) -> str:
            if k is None:
                return "-"
            return "SUSPECT(T4)" if k in flags else f"{float(m[k]):.4f}"
        lines.append(f"{arm:24s} micro={cell(micro_k)}  macro={cell(macro_k)}")
    return lines


__all__ = [
    "AGREEMENT_BAND",
    "DEFAULT_KS",
    "JudgedRow",
    "LaneBand",
    "RetrievalRow",
    "combine",
    "compute_agreement",
    "qa_metrics",
    "render_board",
    "retrieval_metrics",
    "scalar_scores",
]
