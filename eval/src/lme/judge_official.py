"""LANE 1 -- THE CITABLE JUDGE. `gpt-4o-2024-08-06`, upstream prompts VERBATIM.

`get_anscheck_prompt` below is transcribed CHARACTER-FOR-CHARACTER from
    https://github.com/xiaowu0162/LongMemEval  ->  src/evaluation/evaluate_qa.py
(fetched 2026-08-03; that file sha256
 ecce9c4c79dc89d99534ac17b383a5cbb5b9f0c69ee98adaf0684742e3d95251).

DO NOT IMPROVE THESE STRINGS. Not the double space, not the trailing space on
the abstention branch, not the ` \n\n` before "Question:". Every published
LongMemEval number was produced by these exact bytes; an "obvious" cleanup makes
our number non-comparable to all of them, which is the single criticism §2.5 of
the design doc levels at other vendors. `PROMPT_TEMPLATES_SHA256` below pins the
transcription so a well-meaning edit is caught by a test rather than by a
reviewer.

Protocol, also upstream-exact:
    model=gpt-4o-2024-08-06, n=1, temperature=0, max_tokens=10,
    label = 'yes' in response.lower()

Note the label rule is a SUBSTRING check on the lowercased reply, so "Yes." and
"yes, correct" both count -- and so, notably, does "yes" appearing inside a
longer refusal. That is upstream behaviour and we replicate it rather than
fixing it; the strict panel (lane 2) is where a stricter parse lives.

ABSTENTION OVERRIDE: `abstention='_abs' in entry['question_id']` -- a substring
check on the id, which overrides the per-type variant entirely.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from . import guards

#: The pinned official judge. Not configurable -- it is what "official" means.
OFFICIAL_JUDGE_MODEL: str = "gpt-4o-2024-08-06"
OFFICIAL_TEMPERATURE: float = 0.0
OFFICIAL_MAX_TOKENS: int = 10
OFFICIAL_N: int = 1


# ═══════════════════════════════════════════════════════════════════════════
# BEGIN VERBATIM TRANSCRIPTION -- xiaowu0162/LongMemEval src/evaluation/evaluate_qa.py
# ═══════════════════════════════════════════════════════════════════════════
def get_anscheck_prompt(task, question, answer, response, abstention=False):
    if not abstention:
        if task in ['single-session-user', 'single-session-assistant', 'multi-session']:
            template = "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no. \n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
            prompt = template.format(question, answer, response)
        elif task == 'temporal-reasoning':
            template = "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no. In addition, do not penalize off-by-one errors for the number of days. If the question asks for the number of days/weeks/months, etc., and the model makes off-by-one errors (e.g., predicting 19 days when the answer is 18), the model's response is still correct. \n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
            prompt = template.format(question, answer, response)
        elif task == 'knowledge-update':
            template = "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response contains some previous information along with an updated answer, the response should be considered as correct as long as the updated answer is the required answer.\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
            prompt = template.format(question, answer, response)
        elif task == 'single-session-preference':
            template = "I will give you a question, a rubric for desired personalized response, and a response from a model. Please answer yes if the response satisfies the desired response. Otherwise, answer no. The model does not need to reflect all the points in the rubric. The response is correct as long as it recalls and utilizes the user's personal information correctly.\n\nQuestion: {}\n\nRubric: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
            prompt = template.format(question, answer, response)
        else:
            raise NotImplementedError
    else:
        template = "I will give you an unanswerable question, an explanation, and a response from a model. Please answer yes if the model correctly identifies the question as unanswerable. The model could say that the information is incomplete, or some other information is given but the asked information is not.\n\nQuestion: {}\n\nExplanation: {}\n\nModel Response: {}\n\nDoes the model correctly identify the question as unanswerable? Answer yes or no only."
        prompt = template.format(question, answer, response)
    return prompt
# ═══════════════════════════════════════════════════════════════════════════
# END VERBATIM TRANSCRIPTION
# ═══════════════════════════════════════════════════════════════════════════


def _probe_templates() -> str:
    """Render all five branches with fixed inputs and hash the concatenation.

    A transcription pin that checks the RENDERED OUTPUT, not the source text, so
    it survives reformatting but catches any change to a single character of
    prompt content.
    """
    parts: List[str] = []
    for task in (
        "single-session-user",
        "single-session-assistant",
        "multi-session",
        "temporal-reasoning",
        "knowledge-update",
        "single-session-preference",
    ):
        parts.append(get_anscheck_prompt(task, "Q", "A", "R", abstention=False))
    parts.append(get_anscheck_prompt("temporal-reasoning", "Q", "A", "R", abstention=True))
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


#: Pinned by tests/test_judge_official.py. Update ONLY when upstream changes,
#: and say so out loud in the report -- it makes the number non-comparable.
PROMPT_TEMPLATES_SHA256: str = "e40ecdea6cffec84e2b3c04f5a1a7c5a1e0f9c1b0b96a68bff5e8b2f9a24e5e5"


def official_label(eval_response: str) -> bool:
    """Upstream, verbatim: `label = 'yes' in eval_response.lower()`."""
    return "yes" in eval_response.strip().lower()


@dataclass
class OfficialVerdict:
    question_id: str
    question_type: str
    is_abstention: bool
    label: bool
    raw_response: str
    served_model: str
    prompt_sha256: str = ""

    def to_json(self) -> Dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question_type": self.question_type,
            "is_abstention": self.is_abstention,
            "label": self.label,
            "raw_response": self.raw_response,
            "autoeval_label": {"model": self.served_model, "label": self.label},
            "lane": "official",
            "prompt_sha256": self.prompt_sha256,
        }


class OfficialJudge:
    """The one citable lane. Model is not a parameter."""

    def __init__(self, *, client: Any = None, model: str = OFFICIAL_JUDGE_MODEL) -> None:
        self.model = model
        self.stats: Dict[str, int] = {"calls": 0, "tokens_in": 0, "tokens_out": 0}
        if client is not None:
            self._client = client  # injected -> unit-testable with canned responses
            return
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "OPENAI_API_KEY is unset. The official judge is gpt-4o-2024-08-06 "
                "and there is no substitute -- a different judge is a different "
                "number (judge miscalibration is BIDIRECTIONAL)."
            )
        from openai import OpenAI

        self._client = OpenAI(api_key=key)

    def judge_one(
        self,
        *,
        question_id: str,
        question_type: str,
        question: str,
        gold: Any,
        prediction: str,
        max_retries: int = 5,
    ) -> OfficialVerdict:
        abstention = "_abs" in question_id  # upstream rule, verbatim
        prompt = get_anscheck_prompt(
            question_type, question, gold, prediction, abstention=abstention
        )
        last: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    n=OFFICIAL_N,
                    temperature=OFFICIAL_TEMPERATURE,
                    max_tokens=OFFICIAL_MAX_TOKENS,
                )
                break
            except Exception as exc:  # exponential backoff, as upstream
                last = exc
                if attempt == max_retries - 1:
                    raise
                time.sleep(min(2 ** attempt, 30))
        else:  # pragma: no cover
            raise RuntimeError(str(last))

        text = (resp.choices[0].message.content or "").strip()
        served = str(getattr(resp, "model", "") or "")
        # T2 applies to the judge too: a judge silently swapped for gpt-4o-mini
        # is the "lax judge family" half of the 0.9010 phantom.
        guards.t2_served_model(served, pinned=self.model, question_id=question_id)
        self.stats["calls"] += 1
        return OfficialVerdict(
            question_id=question_id,
            question_type=question_type,
            is_abstention=abstention,
            label=official_label(text),
            raw_response=text,
            served_model=served,
            prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        )

    def judge_rows(self, rows: Sequence[Dict[str, Any]]) -> List[OfficialVerdict]:
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


__all__ = [
    "OFFICIAL_JUDGE_MODEL",
    "OFFICIAL_MAX_TOKENS",
    "OFFICIAL_N",
    "OFFICIAL_TEMPERATURE",
    "OfficialJudge",
    "OfficialVerdict",
    "PROMPT_TEMPLATES_SHA256",
    "get_anscheck_prompt",
    "official_label",
]
