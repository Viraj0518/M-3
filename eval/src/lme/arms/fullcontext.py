"""A0 -- full context. The bar everyone must clear. Paper: 0.606 on `_s`.

No retrieval at all: the entire haystack goes in the prompt, timestamp-labelled,
in DATE ORDER. `Dataset._build_question` already sorts sessions by date, which
matters on the oracle split where the shipped haystack is UNSORTED -- a
full-context arm that serves the file order is silently a different (and worse)
arm on temporal questions.

`ranked_session_ids` is every session, so `retrieval.recall@k` is 1.0 for any k
>= the haystack size. That is CORRECT and it is also why A0's recall number
means nothing: A0 has no retrieval to score. It appears in `retrieval.jsonl`
only so the file has a row per question for every arm.

On `_s` this arm is ~115k tokens per question and is the most expensive thing on
the board per point. It exists as a BAR, not as a candidate.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..dataset import Question, strip_gold
from .base import Arm, ArmOutput


class FullContextArm(Arm):
    name = "a0_fullcontext"

    def __init__(self, cfg: Dict[str, Any], **_: Any) -> None:
        self.cfg = cfg
        #: A0's "budget" is the whole haystack; it is exempt from the equal-k
        #: comparison and reported separately, never inside a T12 equal-k table.
        self.budget = int(cfg.get("final_top_k", 10_000))
        self.max_chars = int(cfg.get("max_context_chars", 0))  # 0 = unbounded

    def retrieve(self, question: Question) -> ArmOutput:
        lines: List[str] = []
        for s in question.sessions:
            date = s.date_iso[:10] or "undated"
            lines.append(f"--- session {s.session_id} — {date} ---")
            for t in strip_gold(s):
                lines.append(f"    {t['role']}: {t['text']}")
        context = "\n".join(lines)
        truncated = False
        if self.max_chars and len(context) > self.max_chars:
            # TRUNCATION IS A CONFOUND and is recorded as one. A truncated A0 is
            # not "full context"; the diagnostic makes that visible in the rows
            # rather than leaving it as an unstated property of the config.
            context = context[: self.max_chars]
            truncated = True

        dates = sorted({s.date_iso for s in question.sessions if s.date_iso})
        date_index = (
            f"SESSION DATE INDEX: {', '.join(d[:10] for d in dates)} "
            f"({len(dates)} sessions, {dates[0][:10]} → {dates[-1][:10]})"
            if dates
            else ""
        )
        sids = [s.session_id for s in question.sessions]
        return ArmOutput(
            context=context,
            date_index=date_index,
            ranked_session_ids=sids,
            retrieved_block_ids=[],
            returned_set_size=len(sids),
            diagnostics={
                "n_sessions": len(sids),
                "context_chars": len(context),
                "truncated": truncated,
                "note": "A0 has no retrieval; its recall@k is 1.0 by construction.",
            },
        )


__all__ = ["FullContextArm"]
