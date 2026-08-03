"""The answering step. Single-turn, temperature=0, served-model asserted (T2).

ONE call per question. No retries into a different model, no multi-turn repair
loop, no self-consistency voting -- every one of those is a confound that would
show up as a "mechanism win". The prompt is `prompts/reader_v1.md`, byte-identical
across all arms; only `{context}` differs, which is what makes an arm-vs-arm
delta a retrieval delta.

T2 IN ITS CORRECT FORM: the check is `served == PINNED_MODEL_CONSTANT`. It is NOT
`served == requested`. Providers rewrite BOTH the requested and the served field
when they fall back, so a requested/served comparison passes cleanly while you
are talking to another model. `served_model` is persisted PER QUESTION so the
violation list is reconstructible from `rows.jsonl` alone.

The extractive test reader (`test_reader: true`) is opt-in ONLY, exactly like the
hash embedder. It never activates because a key is missing.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import EVAL_ROOT, guards

PROMPTS_DIR: Path = EVAL_ROOT / "prompts"
READER_PROMPT_PATH: Path = PROMPTS_DIR / "reader_v1.md"

TEST_READER_ID: str = "extractive-test-reader"
ABSTAIN_SENTINEL: str = "I don't have enough information to answer that"


class ReaderConfigError(RuntimeError):
    pass


def load_reader_prompt(path: Path = READER_PROMPT_PATH) -> Tuple[str, str]:
    """Split `prompts/reader_v1.md` into (system, user_template).

    The file's leading prose is documentation for humans and is NOT sent to the
    model -- but it IS inside the file that gets hashed into `config_hash`, so
    editing the rationale still marks a new config. That is intentional: a
    rationale that drifts from the prompt is its own failure mode.
    """
    text = path.read_text(encoding="utf-8")
    if "---SYSTEM---" not in text or "---USER---" not in text:
        raise ReaderConfigError(f"{path} is missing ---SYSTEM--- / ---USER--- markers")
    _, rest = text.split("---SYSTEM---", 1)
    system, user = rest.split("---USER---", 1)
    return system.strip(), user.strip()


def render_user_prompt(
    template: str,
    *,
    question: str,
    question_date: str,
    context: str,
    date_index: str = "",
) -> str:
    """Sentinel-free, brace-safe substitution.

    Uses `.replace`, not `.format`: retrieved conversation text routinely
    contains braces (JSON, code snippets, emoji-adjacent markup) and `.format`
    would read them as replacement fields. That is precisely the bug class that
    killed `judge_canonical_lane.py`'s live path, and it is even more likely
    here because the substituted values are USER-SUPPLIED TEXT.
    """
    out = template
    for token, value in (
        ("{question_date}", question_date),
        ("{context}", context),
        ("{date_index}", date_index),
        ("{question}", question),
    ):
        out = out.replace(token, value)
    return out


@dataclass
class ReaderResult:
    text: str
    served_model: str
    latency_ms: int
    tokens_in: int = 0
    tokens_out: int = 0
    raw: str = ""

    @property
    def abstained(self) -> bool:
        """Detected on the SERVED TEXT, not on gold. Feeds
        `false_abstention_rate_on_answerable` -- the other half of the abstention
        number, without which abstention accuracy is gameable."""
        t = self.text.strip().lower()
        if not t:
            return True
        needles = (
            "i don't have enough information",
            "i do not have enough information",
            "not enough information",
            "cannot be determined from",
            "isn't mentioned in",
            "is not mentioned in",
            "no information about",
        )
        return any(n in t for n in needles)


class Reader:
    model_id: str = "abstract"

    def __init__(self, *, temperature: float = 0.0, max_tokens: int = 512) -> None:
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.stats: Dict[str, int] = {"calls": 0, "tokens_in": 0, "tokens_out": 0}

    def answer(self, system: str, user: str, *, question: str = "", context: str = "") -> ReaderResult:  # pragma: no cover
        raise NotImplementedError


class AnthropicReader(Reader):
    """Pinned Claude reader. Raises on a missing key; never degrades silently."""

    def __init__(self, model: str, **kw: Any) -> None:
        super().__init__(**kw)
        self.model_id = model
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ReaderConfigError(
                "ANTHROPIC_API_KEY is unset. The harness will NOT fall back to the "
                "extractive test reader -- pass `test_reader: true` to opt in "
                "EXPLICITLY, and accept that the run is stamped non-citable."
            )
        import anthropic

        self._client = anthropic.Anthropic(api_key=key)

    def answer(self, system: str, user: str, **_: Any) -> ReaderResult:
        t0 = time.perf_counter()
        resp = self._client.messages.create(
            model=self.model_id,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        dt = int((time.perf_counter() - t0) * 1000)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        served = str(getattr(resp, "model", "") or "")
        # T2 at the call site: assert against the PINNED CONSTANT.
        guards.t2_served_model(served, pinned=self.model_id)
        usage = getattr(resp, "usage", None)
        ti = int(getattr(usage, "input_tokens", 0) or 0)
        to = int(getattr(usage, "output_tokens", 0) or 0)
        self.stats["calls"] += 1
        self.stats["tokens_in"] += ti
        self.stats["tokens_out"] += to
        return ReaderResult(text=text, served_model=served, latency_ms=dt,
                            tokens_in=ti, tokens_out=to, raw=text)


class OpenAIReader(Reader):
    """Provided so A0/A1 can be run under a GPT reader for a cross-family check.
    NOT the default: the default judge is gpt-4o and T7 forbids the overlap."""

    def __init__(self, model: str, **kw: Any) -> None:
        super().__init__(**kw)
        self.model_id = model
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ReaderConfigError("OPENAI_API_KEY is unset; no silent fallback.")
        from openai import OpenAI

        self._client = OpenAI(api_key=key)

    def answer(self, system: str, user: str, **_: Any) -> ReaderResult:
        t0 = time.perf_counter()
        resp = self._client.chat.completions.create(
            model=self.model_id,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        dt = int((time.perf_counter() - t0) * 1000)
        text = (resp.choices[0].message.content or "").strip()
        served = str(getattr(resp, "model", "") or "")
        guards.t2_served_model(served, pinned=self.model_id)
        usage = getattr(resp, "usage", None)
        ti = int(getattr(usage, "prompt_tokens", 0) or 0)
        to = int(getattr(usage, "completion_tokens", 0) or 0)
        self.stats["calls"] += 1
        self.stats["tokens_in"] += ti
        self.stats["tokens_out"] += to
        return ReaderResult(text=text, served_model=served, latency_ms=dt,
                            tokens_in=ti, tokens_out=to, raw=text)


class ExtractiveTestReader(Reader):
    """No-LLM, no-network, deterministic. **Opt-in via `test_reader: true` only.**

    What it is FOR: proving the harness plumbing end-to-end without spending
    money -- retrieval runs, context is built, rows are written, guards fire,
    metrics aggregate, the gate evaluates. It exercises every path except the
    model call.

    What it is NOT: a reader. It picks the served context sentence with the
    highest lexical overlap with the question. Its qa.* numbers are meaningless
    and the manifest stamps the run `test_reader=true` / non-citable so they can
    never be quoted by accident.

    Its `served_model` is `extractive-test-reader`, which is ALSO the pinned
    model for a test run -- so T2 still passes structurally and the T2 code path
    is exercised rather than skipped.
    """

    model_id = TEST_READER_ID

    _STOP = frozenset(
        """a an the and or but if of to in on at for with about from by as is are was were be been
        being do does did what when where who whom which how why my i me you your it its that this
        these those there their they them he she his her had has have will would could should can
        may might not no yes s t""".split()
    )

    def answer(self, system: str, user: str, *, question: str = "", context: str = "") -> ReaderResult:
        t0 = time.perf_counter()
        q_terms = self._terms(question or self._extract_question(user))
        body = context or user
        best, best_score = "", 0.0
        for cand in self._candidates(body):
            score = self._overlap(q_terms, self._terms(cand))
            if score > best_score:
                best, best_score = cand, score
        text = best.strip() if best_score > 0 else ABSTAIN_SENTINEL + " (test reader: no lexical overlap)"
        self.stats["calls"] += 1
        return ReaderResult(
            text=text[:800],
            served_model=self.model_id,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            raw=text,
        )

    @staticmethod
    def _extract_question(user: str) -> str:
        m = re.search(r"QUESTION:\s*(.+?)\s*(?:\n\s*Answer:|\Z)", user, re.S)
        return m.group(1).strip() if m else user[-400:]

    @staticmethod
    def _candidates(body: str) -> List[str]:
        out: List[str] = []
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith(("SESSION DATE INDEX", "CONVERSATION HISTORY", "QUESTION:")):
                continue
            for sent in re.split(r"(?<=[.!?])\s+", line):
                sent = sent.strip()
                if len(sent) > 12:
                    out.append(sent)
        return out

    @classmethod
    def _terms(cls, text: str) -> set:
        toks = "".join(c.lower() if c.isalnum() else " " for c in text).split()
        return {t for t in toks if t not in cls._STOP and len(t) > 2}

    @staticmethod
    def _overlap(a: set, b: set) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / (len(a) ** 0.5 * len(b) ** 0.5)


def build_reader(
    *,
    model: str = "",
    test_reader: bool = False,
    temperature: float = 0.0,
    max_tokens: int = 512,
) -> Reader:
    """THE factory. `test_reader` is the ONLY route to the extractive reader.

    As in embed.py, there is deliberately NO `try live / except -> stub` branch.
    """
    if test_reader:
        return ExtractiveTestReader(temperature=temperature, max_tokens=max_tokens)
    if not model:
        raise ReaderConfigError("reader model is unset and test_reader is false")
    fam = guards.model_family(model)
    if fam == "anthropic":
        return AnthropicReader(model, temperature=temperature, max_tokens=max_tokens)
    if fam == "openai":
        return OpenAIReader(model, temperature=temperature, max_tokens=max_tokens)
    raise ReaderConfigError(
        f"no reader client for model {model!r} (family {fam}). Add one explicitly "
        "rather than routing it to whatever client happens to be importable."
    )


__all__ = [
    "ABSTAIN_SENTINEL",
    "AnthropicReader",
    "ExtractiveTestReader",
    "OpenAIReader",
    "READER_PROMPT_PATH",
    "Reader",
    "ReaderConfigError",
    "ReaderResult",
    "TEST_READER_ID",
    "build_reader",
    "load_reader_prompt",
    "render_user_prompt",
]
