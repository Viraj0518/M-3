"""A1 -- naive RAG. THE HONEST BASELINE.

Flat vectors over session chunks. Vector top-k only. No graph, no entity
expansion, no supersede lineage, no date index, no preference pack.

SAME reader, SAME prompt file, SAME judge, SAME `final_top_k` as A3. That
equality is the entire value of this arm: any A3-vs-A1 delta is then a
RETRIEVAL delta and not a prompt, model, or budget delta. `t12_compare_budgets`
enforces the k equality across arms at report time.

Deliberately NOT included here, even though each would help:
  * dates on served chunks (that is A3's temporal mechanism -- giving it to A1
    would make the A3-A1 delta unattributable)
  * assistant-turn weighting
  * any reranker
A1 is meant to be the honest floor, not a strawman AND not a stealth A3.

Chunking is per-TURN-WINDOW rather than per-session because whole-session chunks
on `_s` (~40-50 sessions, ~115k tokens) would blow the context budget at k=12
and quietly turn A1 into a degraded A0. Window size is in the config and hashed.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .. import guards
from ..dataset import Question, strip_gold
from ..embed import Embedder
from .base import Arm, ArmOutput


class NaiveRagArm(Arm):
    name = "a1_naive_rag"

    def __init__(self, cfg: Dict[str, Any], *, embedder: Embedder, **_: Any) -> None:
        self.cfg = cfg
        self.embedder = embedder
        self.budget = int(cfg.get("final_top_k", 12))
        self.window = int(cfg.get("chunk_turns", 4))
        self.stride = int(cfg.get("chunk_stride", 2))
        self._chunks: List[Tuple[str, str]] = []  # (session_id, text)
        self._mat: Optional[np.ndarray] = None

    def prepare(self, question: Question) -> None:
        chunks: List[Tuple[str, str]] = []
        for s in question.sessions:
            turns = strip_gold(s)
            if not turns:
                continue
            step = max(1, self.stride)
            for start in range(0, max(1, len(turns)), step):
                win = turns[start : start + self.window]
                if not win:
                    continue
                body = "\n".join(f"{t['role']}: {t['text']}" for t in win)
                chunks.append((s.session_id, body))
                if start + self.window >= len(turns):
                    break
        self._chunks = chunks
        vecs = self.embedder.embed([c[1] for c in chunks]) if chunks else []
        self._mat = np.vstack(vecs) if vecs else None

    def teardown(self, question: Question) -> None:
        self._chunks, self._mat = [], None

    def retrieve(self, question: Question) -> ArmOutput:
        if self._mat is None or not self._chunks:
            return ArmOutput(context="", ranked_session_ids=[], returned_set_size=0)
        qv = self.embedder.embed_one(question.question)
        # Vectors are L2-normalised in embed.py, so a dot product IS cosine
        # SIMILARITY here (1.0 = identical) -- the OPPOSITE sign convention from
        # FalkorDB's queryNodes, which returns a DISTANCE. Sort DESC here, ASC
        # there. Getting this backwards is memory/SCHEMA.md trap #1 and it
        # presents as "retrieval just doesn't work", not as an error.
        sims = self._mat @ qv
        order = np.argsort(-sims)[: self.budget]

        lines: List[str] = []
        ranked: List[str] = []
        block_ids: List[str] = []
        for n, idx in enumerate(order, start=1):
            sid, body = self._chunks[int(idx)]
            lines.append(f"[{n}] session {sid}")
            for ln in body.splitlines():
                lines.append(f"    {ln}")
            block_ids.append(f"{sid}::chunk{int(idx)}")
            if sid not in ranked:
                ranked.append(sid)

        guards.t12_returned_set(len(order), budget=self.budget, arm=self.name)
        return ArmOutput(
            context="\n".join(lines),
            # NO date index -- that is A3's mechanism. See module docstring.
            date_index="",
            ranked_session_ids=ranked,
            retrieved_block_ids=block_ids,
            returned_set_size=int(len(order)),
            diagnostics={
                "n_chunks": len(self._chunks),
                "top_sim": float(sims[order[0]]) if len(order) else 0.0,
            },
        )


__all__ = ["NaiveRagArm"]
