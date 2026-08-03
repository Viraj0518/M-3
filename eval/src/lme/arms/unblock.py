"""A2 -- the unblock substrate arm. **OPTIONAL. NOT ON THE GATE PATH.**

═══════════════════════════════════════════════════════════════════════════════
STATUS: STUB / BLACK BOX. Behind `--arm a2_unblock`. Never in `make eval-smoke`,
never in the default `make eval` set (A0/A1/A3), and ITS ABSENCE NEVER FAILS A
GATE. Nothing in this file has been executed against a live backend from this
repo; it is the HTTP shape from design doc §3.5, not a verified path.
═══════════════════════════════════════════════════════════════════════════════

WHY IT IS OPTIONAL, stated plainly because burying it would be the exact
non-comparability this harness criticises in other vendors:

 1. It needs the LIVE KAEVA BACKEND, which violates GOAL.md's "zero runtime
    dependency on the live Kaeva backend". So it cannot be part of the
    reproducible claim -- a third party cannot rerun it.
 2. Its reader prompt lives SERVER-SIDE. That makes the reader an UNCONTROLLED
    VARIABLE: an A2-vs-A3 delta is not a retrieval delta, because the two arms
    are not sharing a reader or a prompt. Recorded in the manifest as such.
 3. Neither substrate arm attaches `haystack_dates` or `session_id` to blocks,
    so its temporal-reasoning ceiling is STRUCTURAL, not a tuning failure. Do
    not report a low A2 temporal number as evidence about graphs-vs-substrates.

Isolation: one eval api_key per question, `purpose='eval'`, with
`eval_tenant_id` and `eval_run_id='q-<qid>'` -- the EF's scope predicate then
gives zero cross-question and zero prod leakage. Env var NAMES only appear here;
no value is ever read into a log, an error message, or an artifact.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from ..dataset import Question, strip_gold
from .base import Arm, ArmOutput

#: ENV NAMES ONLY. Never a value, never a default, never echoed.
ENV_BASE = "UNBLOCK_EF_BASE"
ENV_API_KEY = "UNBLOCK_EVAL_API_KEY"
ENV_PG_URL = "SUPABASE_PG_URL"  # key minting only; unused by this stub


class UnblockArmUnavailable(RuntimeError):
    """Raised loudly rather than degrading. A2 is optional; a BROKEN A2 that
    silently returns empty predictions would score ~0 and read as a result."""


class UnblockArm(Arm):
    name = "a2_unblock"

    def __init__(self, cfg: Dict[str, Any], **_: Any) -> None:
        self.cfg = cfg
        self.budget = int(cfg.get("final_top_k", 10))
        self.base = os.environ.get(ENV_BASE, "")
        self.api_key = os.environ.get(ENV_API_KEY, "")
        self.timeout = float(cfg.get("timeout_s", 60.0))
        self._client: Optional[Any] = None
        self._run_id = ""

        if not self.base or not self.api_key:
            raise UnblockArmUnavailable(
                f"A2 requires {ENV_BASE} and {ENV_API_KEY}. This arm is OPTIONAL "
                "and off the gate path -- run without `--arm a2_unblock`. It is "
                "not skipped silently because a silently-skipped arm that later "
                "appears in a table as 0.00 is worse than an absent one."
            )

    # ── plumbing ────────────────────────────────────────────────────────────
    def _http(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.Client(
                base_url=self.base,
                timeout=self.timeout,
                headers={
                    "authorization": f"Bearer {self.api_key}",
                    "content-type": "application/json",
                },
            )
        return self._client

    def _scope(self, question: Question) -> Dict[str, str]:
        """Per-question isolation envelope. `eval_run_id` is what the EF's scope
        predicate keys on, so cross-question leakage is refused server-side."""
        return {
            "purpose": "eval",
            "eval_run_id": f"q-{question.question_id}",
            "eval_tenant_id": self.cfg.get("eval_tenant_id", "lme"),
        }

    # ── arm interface ───────────────────────────────────────────────────────
    def prepare(self, question: Question) -> None:
        """Ingest: `POST /v1/remember` per turn, `{content: "<role>: <text>"}`."""
        client = self._http()
        scope = self._scope(question)
        for s in question.sessions:
            for t in strip_gold(s):
                resp = client.post(
                    "/v1/remember",
                    json={
                        "content": f"{t['role']}: {t['text']}",
                        "scope": "member",
                        "metadata": {
                            **scope,
                            # NOTE: the substrate does not attach these to the
                            # block in a retrievable way -- see honesty note 3.
                            "session_id": s.session_id,
                            "session_date": s.date_iso,
                        },
                    },
                )
                resp.raise_for_status()

    def retrieve(self, question: Question) -> ArmOutput:
        """Retrieve: `POST /v1/query` -> `{answer, abstained, hits[]}`.

        `direct_prediction` short-circuits our reader entirely: the substrate
        synthesises server-side, so there is no local prompt to apply. That is
        the uncontrolled variable, made explicit in the ArmOutput rather than
        hidden inside a context string.
        """
        client = self._http()
        resp = client.post(
            "/v1/query",
            json={
                "text": question.question,
                "final_top_k": self.budget,
                "skip_synth": False,
                "metadata": self._scope(question),
            },
        )
        resp.raise_for_status()
        body = resp.json()

        hits: List[Dict[str, Any]] = list(body.get("hits") or [])
        ranked: List[str] = []
        for h in hits:
            sid = str((h.get("metadata") or {}).get("session_id", ""))
            if sid and sid not in ranked:
                ranked.append(sid)

        # Prediction rule from `longmemeval-live-lib.mjs`: abstained -> "".
        prediction = "" if body.get("abstained") else str(body.get("answer", ""))

        return ArmOutput(
            context="\n".join(str(h.get("content", "")) for h in hits),
            date_index="",
            ranked_session_ids=ranked,
            retrieved_block_ids=[str(h.get("id", "")) for h in hits],
            returned_set_size=len(hits),
            direct_prediction=prediction,
            diagnostics={
                "abstained": bool(body.get("abstained")),
                "n_hits": len(hits),
                "uncontrolled_variables": [
                    "reader prompt lives server-side",
                    "reranker/synth model not pinned by this harness",
                    "no session_date on blocks -> structural temporal ceiling",
                ],
            },
        )

    def teardown(self, question: Question) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


__all__ = ["ENV_API_KEY", "ENV_BASE", "UnblockArm", "UnblockArmUnavailable"]
