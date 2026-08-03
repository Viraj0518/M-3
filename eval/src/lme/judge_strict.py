"""LANE 2 -- the anti-phantom band. Cross-family panel under shared rubric v1.1.

NEVER the headline. Lane 1 (judge_official) is the citable number; this lane
exists to answer "is that number an artifact of one judge's calibration?" The
0.9010 phantom had TWO mechanisms and a lax judge family was the second one:
"judge miscalibration is BIDIRECTIONAL -- truth is the cross-family-calibrated
band, never one judge."

DESIGN, lifted from `~/unblock-eval/judge_canonical_lane.py`:
  * shared rubric, verbatim, across every family -- INDEPENDENCE LIVES IN THE
    MODEL FAMILY, NEVER IN THE RUBRIC. Varying the rubric per family would
    confound family disagreement with rubric disagreement.
  * 3 families x 5 votes, majority within family, then majority across families
  * `floor = min over families` is reported alongside `maj3`
  * divergence from lane 1 > 0.02 => JUDGE-BAND, never a point (metrics.LaneBand)
  * family-exclusion guard (T7): no panel member may share the reader's family

THE FIXED BUG (design doc S1.1). The lifted code built its prompt with
`ENTAIL_PROMPT.format(...)` while the rubric text contains a literal brace group
naming the three judge inputs, so `str.format` parsed it as a replacement field
and the live path raised `KeyError: 'question, gold, prediction'` on its first
real call. `--selftest` passed for weeks because it never reached `call_judge`.

The fix is structural: `render_prompt()` uses `str.replace` on `<<SENTINEL>>`
tokens and `.format()` is never called on rubric text anywhere in this module.
Escaping the braces would have fixed that one crash; removing `.format()`
removes the class -- which matters because the substituted values (question,
gold, prediction) are attacker-shaped free text that regularly contains braces.
`tests/test_judge_strict.py` asserts a brace-laden prediction renders fine.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import EVAL_ROOT, guards

RUBRIC_PATH: Path = EVAL_ROOT / "prompts" / "strict_rubric_v1_1.md"
RUBRIC_VERSION: str = "v1.1"
DEFAULT_VOTES: int = 5

#: Default 3-family panel. None may share the reader's family (T7). These are
#: pins, not suggestions -- swapping a family changes the band.
DEFAULT_PANEL: tuple = (
    "gpt-4o-2024-08-06",
    "gemini-1.5-pro-002",
    "meta-llama/Meta-Llama-3.1-70B-Instruct",
)

SENTINELS: tuple = (
    "<<QUESTION>>",
    "<<GOLD>>",
    "<<GOLD_LABEL>>",
    "<<PREDICTION>>",
    "<<ABSTENTION_NOTE>>",
)

ABSTENTION_NOTE = (
    "NOTE: this row is an ABSTENTION row. The GOLD above is an EXPLANATION of why "
    "the question is unanswerable, not an answer. Apply R4: the prediction is "
    "CORRECT only if it correctly identifies the question as unanswerable."
)


class RubricError(RuntimeError):
    pass


def load_rubric(path: Path = RUBRIC_PATH) -> str:
    text = path.read_text(encoding="utf-8")
    missing = [s for s in SENTINELS if s not in text]
    if missing:
        raise RubricError(f"{path} is missing sentinel(s) {missing}")
    return text


def render_prompt(
    rubric: str,
    *,
    question: str,
    gold: Any,
    prediction: str,
    is_abstention: bool,
) -> str:
    """Sentinel substitution by `str.replace`. NO `.format()`. See module docstring.

    Substitution order matters: `<<GOLD_LABEL>>` is replaced BEFORE `<<GOLD>>`
    would otherwise be a prefix match -- `str.replace` is literal so `<<GOLD>>`
    does not match inside `<<GOLD_LABEL>>`, but the explicit order documents the
    intent for anyone adding a sentinel later.
    """
    out = rubric
    out = out.replace("<<GOLD_LABEL>>", "explanation of unanswerability" if is_abstention else "correct answer")
    out = out.replace("<<QUESTION>>", str(question))
    out = out.replace("<<GOLD>>", str(gold))
    out = out.replace("<<PREDICTION>>", str(prediction) if str(prediction).strip() else "(empty response)")
    out = out.replace("<<ABSTENTION_NOTE>>", ABSTENTION_NOTE if is_abstention else "")
    leftover = [s for s in SENTINELS if s in out]
    if leftover:
        raise RubricError(f"unsubstituted sentinel(s) after render: {leftover}")
    return out


def parse_verdict(raw: str) -> Optional[bool]:
    """Tolerant parse of the JSON output contract.

    Returns None on an unparseable reply -- which is counted as an ABSTAINING
    VOTE, not as a False. Silently converting a parse failure into a `False`
    deflates the strict floor and would look like judge strictness, which is
    exactly the kind of scoring artifact this lane exists to catch.
    """
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            v = obj.get("verdict")
            if isinstance(v, bool):
                return v
            if isinstance(v, str) and v.strip().lower() in ("true", "false"):
                return v.strip().lower() == "true"
        except Exception:
            pass
    t = raw.strip().lower()
    if re.search(r'"?verdict"?\s*[:=]\s*true', t):
        return True
    if re.search(r'"?verdict"?\s*[:=]\s*false', t):
        return False
    return None


@dataclass
class FamilyVerdict:
    model: str
    family: str
    votes: List[Optional[bool]] = field(default_factory=list)

    @property
    def label(self) -> Optional[bool]:
        """Majority of the PARSEABLE votes. None if a majority failed to parse."""
        cast = [v for v in self.votes if v is not None]
        if not cast or len(cast) * 2 <= len(self.votes) - len(cast):
            return None
        c = Counter(cast)
        if c[True] == c[False]:
            return None
        return c[True] > c[False]

    def to_json(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "family": self.family,
            "votes": self.votes,
            "label": self.label,
            "n_unparsed": sum(1 for v in self.votes if v is None),
        }


@dataclass
class StrictVerdict:
    question_id: str
    question_type: str
    is_abstention: bool
    families: List[FamilyVerdict] = field(default_factory=list)

    @property
    def maj3(self) -> Optional[bool]:
        labels = [f.label for f in self.families if f.label is not None]
        if not labels:
            return None
        c = Counter(labels)
        if c[True] == c[False]:
            return None  # a tie is UNDECIDED, never silently False
        return c[True] > c[False]

    @property
    def floor(self) -> bool:
        """The pessimistic reading: correct only if EVERY family says so.
        `floor = min over families` -- the strict lane's lower bound."""
        return all(f.label is True for f in self.families) if self.families else False

    @property
    def unanimous(self) -> bool:
        labels = [f.label for f in self.families]
        return len(set(labels)) == 1 and labels[0] is not None

    def to_json(self) -> Dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question_type": self.question_type,
            "is_abstention": self.is_abstention,
            "maj3": self.maj3,
            "floor": self.floor,
            "unanimous": self.unanimous,
            "families": [f.to_json() for f in self.families],
            "lane": "strict",
            "rubric_version": RUBRIC_VERSION,
        }


class StrictPanel:
    """3 families x 5 votes under the shared rubric.

    `clients` maps model-id -> an object with `.complete(prompt) -> str`. Passing
    it explicitly is what makes this lane unit-testable with canned responses
    (`tests/test_judge_strict.py`) -- and note that BOTH the pass and the fail
    direction are tested, because an assertion that never fires is
    indistinguishable from one that cannot.
    """

    def __init__(
        self,
        *,
        panel: Sequence[str] = DEFAULT_PANEL,
        votes: int = DEFAULT_VOTES,
        reader_model: str = "",
        clients: Optional[Mapping[str, Any]] = None,
        rubric: Optional[str] = None,
    ) -> None:
        self.panel = list(panel)
        self.votes = votes
        self.rubric = rubric if rubric is not None else load_rubric()
        self.clients = dict(clients or {})
        # T7, both prongs: distinct families, and none of them the reader's.
        guards.t7_panel_distinct(self.panel, min_families=min(3, len(self.panel)))
        if reader_model:
            guards.t7_family_exclusion(reader_model, self.panel)
        self.stats: Dict[str, int] = {"calls": 0}

    def _complete(self, model: str, prompt: str) -> str:
        client = self.clients.get(model)
        if client is None:
            raise RuntimeError(
                f"no client registered for strict-panel member {model!r}. The "
                "strict lane requires an explicit client per family -- it will "
                "not route a missing family to whatever is importable."
            )
        self.stats["calls"] += 1
        return client.complete(prompt)

    def judge_one(
        self,
        *,
        question_id: str,
        question_type: str,
        question: str,
        gold: Any,
        prediction: str,
    ) -> StrictVerdict:
        is_abs = "_abs" in question_id
        prompt = render_prompt(
            self.rubric,
            question=question,
            gold=gold,
            prediction=prediction,
            is_abstention=is_abs,
        )
        families: List[FamilyVerdict] = []
        for model in self.panel:
            fv = FamilyVerdict(model=model, family=guards.model_family(model))
            for _ in range(self.votes):
                fv.votes.append(parse_verdict(self._complete(model, prompt)))
            families.append(fv)
        return StrictVerdict(
            question_id=question_id,
            question_type=question_type,
            is_abstention=is_abs,
            families=families,
        )

    def judge_rows(self, rows: Sequence[Mapping[str, Any]]) -> List[StrictVerdict]:
        return [
            self.judge_one(
                question_id=r["question_id"],
                question_type=r["question_type"],
                question=r["question"],
                gold=r["gold"],
                prediction=r.get("prediction", ""),
            )
            for r in rows
        ]


def summarize(verdicts: Sequence[StrictVerdict]) -> Dict[str, Any]:
    """`maj3`, `floor`, and per-family accuracy -> metrics.LaneBand inputs.

    An UNDECIDED maj3 (tie or all-unparsed) counts as FALSE in the aggregate but
    is also counted separately, so an eroding panel is visible rather than
    silently depressing the score.
    """
    n = len(verdicts)
    if not n:
        return {"n": 0, "maj3": 0.0, "floor": 0.0, "per_family": {}, "undecided": 0}
    undecided = sum(1 for v in verdicts if v.maj3 is None)
    per_family: Dict[str, float] = {}
    for i, model in enumerate(verdicts[0].families and [f.model for f in verdicts[0].families] or []):
        vals = [1.0 if (v.families[i].label is True) else 0.0 for v in verdicts]
        per_family[model] = sum(vals) / n
    return {
        "n": n,
        "maj3": sum(1.0 for v in verdicts if v.maj3 is True) / n,
        "floor": sum(1.0 for v in verdicts if v.floor) / n,
        "per_family": per_family,
        "undecided": undecided,
        "unanimous_rate": sum(1.0 for v in verdicts if v.unanimous) / n,
        "rubric_version": RUBRIC_VERSION,
    }


__all__ = [
    "ABSTENTION_NOTE",
    "DEFAULT_PANEL",
    "DEFAULT_VOTES",
    "FamilyVerdict",
    "RUBRIC_PATH",
    "RUBRIC_VERSION",
    "RubricError",
    "SENTINELS",
    "StrictPanel",
    "StrictVerdict",
    "load_rubric",
    "parse_verdict",
    "render_prompt",
    "summarize",
]
