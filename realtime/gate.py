"""[3] SENSING GATE — embedding-novelty write filter, between consumer and graph.

Ported from ``vendor/unblock-reuse/unblock_substrate/src/brain/sensing-gate.ts``
(``decideSensingEmbedded``, the EMBEDDING-cosine path, NOT the trigram fallback):

    novelty  = 1 - maxCosineSimilarity(candidate, nearest neighbour)
    salience = clamp01(piBottomUp * novelty)
    admit    = salience >= threshold           (DEFAULT_SALIENCE_THRESHOLD = 0.15)
    cold start (no neighbour at all) => fully novel => admit

FalkorDB's vector index scores a **DISTANCE**, not a similarity (SCHEMA.md trap
1, re-verified on this box: exact vector -> 0.0, near neighbour -> 0.0061). For a
cosine index that distance IS the novelty (``1 - cosine_sim``), so we **sort
ASC** and gate on the top-1 distance directly:

    admit if top1_distance >= threshold   (novel enough)
    skip  if top1_distance <  threshold   (too close to something we already know)

Without the gate a 50-100 ev/s firehose buries the ring query in near-dupes in
four minutes and the projector graph becomes an unreadable hairball. Judge-
visible stat: "N events in, M nodes — the gate rejected X% as non-novel."

NO-KEY PATH (the demo runs with no OpenAI key): a DETERMINISTIC test-embedder,
selected EXPLICITLY (``kind='test'``), never silently. It hashes char n-grams
into ``config.EMBED_DIM`` buckets and L2-normalizes, so an identical edit summary
maps to an identical vector (distance 0 -> gated as a dup) while distinct
summaries separate — a real novelty signal with zero network and full
determinism (which the replay-parity invariant needs). The OpenAI path exists
for the keyed run and is guarded; asking for it without a key raises, never
degrades in silence.
"""

from __future__ import annotations

import hashlib
import math
import os
from dataclasses import dataclass
from typing import List, Optional, Protocol

from memory import config

from app.bridge import graphstore


# ═══════════════════════════════════════════════════════════════════════════
# embedders — chosen EXPLICITLY, never silently
# ═══════════════════════════════════════════════════════════════════════════

class Embedder(Protocol):
    kind: str
    dim: int

    def embed(self, text: str) -> List[float]: ...


class DeterministicEmbedder:
    """A hash-based, network-free, fully deterministic embedder for the no-key
    path. Same text -> same vector (byte-for-byte), so it is safe for the
    replay-parity invariant and gives exact-duplicate detection for free."""

    kind = "test"

    def __init__(self, dim: int = config.EMBED_DIM) -> None:
        self.dim = dim

    def embed(self, text: str) -> List[float]:
        text = (text or "").lower().strip()
        vec = [0.0] * self.dim
        if not text:
            # a stable non-zero vector for empty text, so KNN never sees NaN
            vec[0] = 1.0
            return vec
        padded = "  " + text + "  "
        for i in range(len(padded) - 2):
            gram = padded[i : i + 3]
            h = int.from_bytes(hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest(), "big")
            bucket = h % self.dim
            sign = 1.0 if (h >> 63) & 1 else -1.0
            vec[bucket] += sign
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


class OpenAIEmbedder:
    """The keyed path. Guarded: constructing it WITHOUT ``OPENAI_API_KEY`` raises
    — we never silently fall back, and we never default a key-shaped value."""

    kind = "openai"

    def __init__(self, dim: int = config.EMBED_DIM, model: str = "text-embedding-3-small") -> None:
        key = config.secret("OPENAI_API_KEY", required=True)  # raises honestly if unset
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "OpenAI embedder requested but the `openai` package is not "
                "installed. Use kind='test' for the no-key path."
            ) from exc
        self.dim = dim
        self.model = model
        self._client = OpenAI(api_key=key)

    def embed(self, text: str) -> List[float]:
        resp = self._client.embeddings.create(
            model=self.model, input=text or " ", dimensions=self.dim
        )
        return list(resp.data[0].embedding)


def make_embedder(kind: str = "test", *, dim: int = config.EMBED_DIM) -> Embedder:
    """Build an embedder by EXPLICIT kind. Env ``PALIMPSEST_EMBEDDER`` may set a
    default but the choice is always logged by the caller — never silent."""
    kind = (kind or os.environ.get("PALIMPSEST_EMBEDDER") or "test").lower()
    if kind == "test":
        return DeterministicEmbedder(dim)
    if kind == "openai":
        return OpenAIEmbedder(dim)
    raise ValueError("unknown embedder kind {0!r}: use 'test' or 'openai'".format(kind))


# ═══════════════════════════════════════════════════════════════════════════
# the gate
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class GateDecision:
    admit: bool
    novelty: float          # the top-1 DISTANCE (== 1 - cosine_sim)
    threshold: float
    cold_start: bool        # no neighbour yet -> fully novel -> admit
    nearest_id: Optional[str]
    method: str
    embedding: List[float]


class SensingGate:
    """Embedding-novelty gate over a FalkorDB vector index (label ``Event`` by
    default). Stateless except for its embedder + config; the novelty signal is
    read live from the target graph, so the SAME gate serves warm and cold."""

    def __init__(
        self,
        embedder: Embedder,
        *,
        graph_key: str,
        threshold: float = config.SALIENCE_THRESHOLD,
        label: str = "Event",
    ) -> None:
        self.embedder = embedder
        self.graph_key = graph_key
        self.threshold = threshold
        self.label = graphstore.check_label(label)

    def embed(self, text: str) -> List[float]:
        return self.embedder.embed(text)

    def _knn_top1(self, vector: List[float], *, graph_key: str) -> Optional[tuple]:
        """Top-1 (node, DISTANCE) from the Event vector index. **ORDER BY score
        ASC** — the score is a distance, 0.0 == identical. Returns None when
        there is no vector index yet or no embedded neighbour (cold start)."""
        cypher = (
            "CALL db.idx.vector.queryNodes('{label}', 'emb', 1, vecf32($q)) "
            "YIELD node, score RETURN node, score ORDER BY score ASC LIMIT 1"
        ).format(label=self.label)
        try:
            rows, _ms, _h = graphstore.query(cypher, {"q": vector}, graph_key=graph_key, read_only=True)
        except graphstore.GraphUnavailable:
            return None
        if not rows:
            return None
        node, score = rows[0][0], float(rows[0][1])
        return node, score

    def decide(self, summary: str, *, graph_key: Optional[str] = None, embedding: Optional[List[float]] = None) -> GateDecision:
        """Admit or skip a candidate edit by novelty. ``embedding`` may be
        supplied precomputed (so the writer and the gate share one vector)."""
        gk = graph_key or self.graph_key
        vec = embedding if embedding is not None else self.embed(summary)
        near = self._knn_top1(vec, graph_key=gk)
        if near is None:
            return GateDecision(
                admit=True, novelty=1.0, threshold=self.threshold,
                cold_start=True, nearest_id=None,
                method="embedding:{0}".format(self.embedder.kind), embedding=vec,
            )
        node, distance = near
        nearest_id = None
        try:
            nearest_id = (getattr(node, "properties", None) or {}).get("id")
        except Exception:  # noqa: BLE001
            pass
        admit = distance >= self.threshold
        return GateDecision(
            admit=admit, novelty=distance, threshold=self.threshold,
            cold_start=False, nearest_id=nearest_id,
            method="embedding:{0}".format(self.embedder.kind), embedding=vec,
        )


__all__ = [
    "Embedder",
    "DeterministicEmbedder",
    "OpenAIEmbedder",
    "make_embedder",
    "SensingGate",
    "GateDecision",
]
