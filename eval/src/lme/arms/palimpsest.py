"""A3 -- PALIMPSEST. THE CLAIM.

Typed graph, dated sessions, supersede lineage, S1-S5 retrieval, per-question
graph isolation. Plus the four ablations, each of which deletes exactly one
mechanism:

    a3_ablate_supersede    S3 off, `[:RELATES{supersedes}]` never written
                           -> removal test for knowledge-update
    a3_ablate_temporal     Session.date_iso and [:NEXT] never written, S4 off,
                           no date index in the prompt
                           -> removal test for temporal-reasoning
    a3_ablate_expansion    S2 off, vector entry only
                           -> removal test for multi-session
    a3_ablate_sufficiency  entity-coverage gate off
                           -> removal test for abstention

"Best score only has receipts if the mechanism that produced it can be deleted
and the score moves." The ablations reuse cached embeddings AND cached
extractions, so each costs only reader+judge (~$5, ~7 min) -- eight arms is
cheap and the receipts are the product. NEVER cut the ablation matrix: a number
without removal tests is exactly the artifact this team has retracted before.

TWO ABLATIONS ACT AT INGEST TIME, NOT QUERY TIME (`supersede`, `temporal`). That
distinction is load-bearing. Disabling only the query stage while the data stays
dated is not a removal test, because S5 still hands the reader dated provenance
and the prompt still carries a date index -- the mechanism would keep working
through a different door and the ablation would understate its own effect.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..dataset import Question
from ..embed import Embedder
from ..graph.ingest import Extractor, IngestReport, ingest_question
from ..graph.retrieve import FINAL_TOP_K, S2_FANOUT, render_context, retrieve
from ..graph.schema import GraphHandle, assert_directed_vocabulary
from .base import Arm, ArmOutput


class PalimpsestArm(Arm):
    name = "a3_palimpsest"

    def __init__(
        self,
        cfg: Dict[str, Any],
        *,
        embedder: Embedder,
        extractor: Extractor,
        split_id: str = "",
        **_: Any,
    ) -> None:
        self.cfg = cfg
        self.name = str(cfg.get("arm", "a3_palimpsest"))
        self.embedder = embedder
        self.extractor = extractor
        self.split_id = split_id
        self.budget = int(cfg.get("final_top_k", FINAL_TOP_K))
        self.fanout = int(cfg.get("s2_fanout", S2_FANOUT))

        ab = cfg.get("ablate", {}) or {}
        self.ablate_supersede = bool(ab.get("supersede", False))
        self.ablate_temporal = bool(ab.get("temporal", False))
        self.ablate_expansion = bool(ab.get("expansion", False))
        self.ablate_sufficiency = bool(ab.get("sufficiency", False))
        #: keep the per-question graph after the run (debugging); default False
        self.keep_graph = bool(cfg.get("keep_graph", False))

        # Positive assertion that the shared vocabulary import is LIVE and that
        # `supersedes` is still marked directed. Without this, schema.py's
        # `except ImportError` fallback could quietly give the eval a private
        # copy of the taxonomy while it appears to share the demo's -- a
        # one-identifier-two-objects false green.
        assert_directed_vocabulary()

        self._g: Optional[GraphHandle] = None
        self._report: Optional[IngestReport] = None

    def prepare(self, question: Question) -> None:
        self._g, self._report = ingest_question(
            question,
            split_id=self.split_id,
            embedder=self.embedder,
            extractor=self.extractor,
            ablate_supersede=self.ablate_supersede,
            ablate_temporal=self.ablate_temporal,
            fresh=True,
        )

    def teardown(self, question: Question) -> None:
        if self._g is not None and not self.keep_graph:
            self._g.drop()  # GRAPH.DELETE -- clean teardown, cheap per-question graphs
        self._g, self._report = None, None

    def retrieve(self, question: Question) -> ArmOutput:
        if self._g is None:
            raise RuntimeError("prepare() must run before retrieve()")
        qvec = self.embedder.embed_one(question.question)
        res = retrieve(
            self._g,
            question_text=question.question,
            qvec=qvec,
            final_top_k=self.budget,
            ablate_expansion=self.ablate_expansion,
            ablate_supersede=self.ablate_supersede,
            ablate_temporal=self.ablate_temporal,
            ablate_sufficiency=self.ablate_sufficiency,
            fanout=self.fanout,
        )
        # The date index is part of the TEMPORAL mechanism, so the temporal
        # ablation must remove it from the prompt too -- see module docstring.
        session_dates = (
            []
            if self.ablate_temporal
            else [s.date_iso for s in question.sessions if s.date_iso]
        )
        context, date_index = render_context(res, session_dates=session_dates)

        diag: Dict[str, Any] = {
            "stages": res.stage_counts,
            "router": res.features.to_json(),
            "missing_entities": res.missing_entities,
            "ablate": {
                "supersede": self.ablate_supersede,
                "temporal": self.ablate_temporal,
                "expansion": self.ablate_expansion,
                "sufficiency": self.ablate_sufficiency,
            },
        }
        if self._report is not None:
            diag["ingest"] = self._report.to_json()

        return ArmOutput(
            context=context,
            date_index=date_index,
            ranked_session_ids=res.ranked_session_ids,
            retrieved_block_ids=[i.claim_id for i in res.items],
            returned_set_size=res.returned_set_size,
            diagnostics=diag,
        )


__all__ = ["PalimpsestArm"]
