"""Arm interface + the shared answer path.

An Arm does ONE thing: turn a Question into a context string plus a ranked
session-id list. The reader call, the T2 assert, the row shape, and the
retrieval bookkeeping all live HERE so no arm can accidentally differ in a way
that shows up as a mechanism win.

`ranked_session_ids` is what `retrieval.jsonl` scores. It is JUDGE-FREE and
DETERMINISTIC, and it is the GO/NO-GO gate: "the gate that decides GO must have
no judge in it at all, because all four historical phantoms were scoring
artifacts, not capability gains."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ..dataset import Question
from ..reader import Reader, ReaderResult, render_user_prompt


@dataclass
class ArmOutput:
    """What an arm hands to the reader, plus its retrieval receipts."""

    context: str
    date_index: str = ""
    ranked_session_ids: List[str] = field(default_factory=list)
    retrieved_block_ids: List[str] = field(default_factory=list)
    returned_set_size: int = 0
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    #: An arm may pre-empt the reader entirely (A2 serves its own answer).
    direct_prediction: Optional[str] = None


class Arm:
    """Base. `name` is the manifest's `arm` field and the runs/ directory prefix."""

    name: str = "abstract"
    #: Declared serving budget -- T12. Arms compared head-to-head must match.
    budget: int = 12

    def prepare(self, question: Question) -> None:
        """Per-question setup (graph build, index build). Default: nothing."""

    def teardown(self, question: Question) -> None:
        """Per-question cleanup. Default: nothing."""

    def retrieve(self, question: Question) -> ArmOutput:  # pragma: no cover
        raise NotImplementedError

    # ── the shared answer path ──────────────────────────────────────────────
    def answer(
        self,
        question: Question,
        out: ArmOutput,
        *,
        reader: Reader,
        system: str,
        user_template: str,
    ) -> ReaderResult:
        if out.direct_prediction is not None:
            return ReaderResult(
                text=out.direct_prediction,
                served_model=getattr(reader, "model_id", "external"),
                latency_ms=0,
                raw=out.direct_prediction,
            )
        user = render_user_prompt(
            user_template,
            question=question.question,
            question_date=question.question_date,
            context=out.context,
            date_index=out.date_index,
        )
        return reader.answer(system, user, question=question.question, context=out.context)


def build_arm(name: str, cfg: Dict[str, Any], **deps: Any) -> Arm:
    """Arm registry. Imports are local so that, e.g., the A2 arm's httpx client
    is never constructed on a run that does not use it."""
    if name == "a0_fullcontext":
        from .fullcontext import FullContextArm

        return FullContextArm(cfg, **deps)
    if name == "a1_naive_rag":
        from .naive_rag import NaiveRagArm

        return NaiveRagArm(cfg, **deps)
    if name.startswith("a3_palimpsest") or name.startswith("a3_ablate"):
        from .palimpsest import PalimpsestArm

        return PalimpsestArm(cfg, **deps)
    if name == "a2_unblock":
        from .unblock import UnblockArm

        return UnblockArm(cfg, **deps)
    raise ValueError(f"unknown arm {name!r}")


__all__ = ["Arm", "ArmOutput", "build_arm"]
