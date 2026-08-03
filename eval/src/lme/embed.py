"""Content-addressed embedding cache.

Key: `sha256(model|dim|text)` -> `<cache>/<aa>/<hash>.npy`. A rerun is therefore
embedding-free and free of charge, which is what makes the four ablation arms
cost only reader+judge.

THE SILENT-FALLBACK RULE (T2's cousin, and the reason this file is longer than
it needs to be):

    There is a deterministic hash-embedder in here. It is selected ONLY by an
    EXPLICIT config flag `test_embedder: true`. It is NEVER selected because a
    key was missing, because a request failed, because the network was down, or
    because someone passed `--offline`. A missing OPENAI_API_KEY raises.

That asymmetry is the whole point. A silent fallback produces a run that looks
complete, scores plausibly, and is measuring nothing -- which is exactly the
shape of the 0.9010 phantom (a missing env var that degraded silently). The
embedder identity is written into the manifest pin string, so a test-embedder
run is labelled `hash-test-embedder@256` and can never be mistaken for a real
one downstream.

Dim is `memory.config.EMBED_DIM` (256) -- THE shared constant. A dim mismatch
between the embedder's `dimensions=` and the HNSW index's `dim:` corrupts the
vector index SILENTLY: no error, just wrong neighbours.
"""

from __future__ import annotations

import hashlib
import os
import struct
import threading
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np

from . import EVAL_ROOT

try:  # repo-root import, read-only
    from memory.config import EMBED_DIM as _REPO_EMBED_DIM
except Exception:  # pragma: no cover - repo layout guard
    _REPO_EMBED_DIM = 256

EMBED_DIM: int = int(_REPO_EMBED_DIM)
OPENAI_MODEL: str = "text-embedding-3-small"
TEST_EMBEDDER_ID: str = "hash-test-embedder"
CACHE_DIR: Path = Path(os.environ.get("LME_EMBED_CACHE", str(EVAL_ROOT / ".cache" / "embeddings")))


class EmbedderConfigError(RuntimeError):
    """Raised instead of silently degrading. See the module docstring."""


def cache_key(model: str, dim: int, text: str) -> str:
    h = hashlib.sha256()
    h.update(model.encode("utf-8"))
    h.update(b"|")
    h.update(str(dim).encode("utf-8"))
    h.update(b"|")
    h.update(text.encode("utf-8"))
    return h.hexdigest()


class _Cache:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.Lock()

    def path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.npy"

    def get(self, key: str) -> Optional[np.ndarray]:
        p = self.path(key)
        if not p.exists():
            return None
        try:
            return np.load(p)
        except Exception:
            # A truncated .npy from a killed run must not poison every rerun.
            p.unlink(missing_ok=True)
            return None

    def put(self, key: str, vec: np.ndarray) -> None:
        p = self.path(key)
        with self._lock:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".tmp")
            # Write through an OPEN HANDLE, not a path: `np.save(path, ...)`
            # APPENDS `.npy` when the name does not already end in it, so
            # `np.save("x.npy.tmp", v)` silently creates `x.npy.tmp.npy` and the
            # subsequent rename fails with a confusing FileNotFoundError.
            with open(tmp, "wb") as fh:
                np.save(fh, vec)
            tmp.replace(p)  # atomic -> no torn file on ^C


def _normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return (v / n).astype(np.float32) if n > 0 else v.astype(np.float32)


class Embedder:
    """Base. `model_id` is what goes in the manifest pin -- it must name the
    ACTUAL embedder used, so a test run is self-labelling."""

    model_id: str = "abstract"

    def __init__(self, *, dim: int = EMBED_DIM, cache: Optional[_Cache] = None) -> None:
        self.dim = dim
        self.cache = cache if cache is not None else _Cache(CACHE_DIR)
        self.stats: Dict[str, int] = {"hits": 0, "misses": 0, "calls": 0, "tokens_in": 0}

    @property
    def pin(self) -> str:
        return f"{self.model_id}@{self.dim}"

    def _compute(self, texts: Sequence[str]) -> List[np.ndarray]:  # pragma: no cover
        raise NotImplementedError

    def embed(self, texts: Sequence[str], *, batch: int = 128) -> List[np.ndarray]:
        keys = [cache_key(self.model_id, self.dim, t) for t in texts]
        out: List[Optional[np.ndarray]] = [self.cache.get(k) for k in keys]
        self.stats["hits"] += sum(1 for v in out if v is not None)

        todo = [i for i, v in enumerate(out) if v is None]
        self.stats["misses"] += len(todo)
        for start in range(0, len(todo), batch):
            chunk = todo[start : start + batch]
            vecs = self._compute([texts[i] for i in chunk])
            for i, vec in zip(chunk, vecs):
                v = _normalize(np.asarray(vec, dtype=np.float32))
                if v.shape[0] != self.dim:
                    raise EmbedderConfigError(
                        f"{self.model_id} returned dim {v.shape[0]}, expected {self.dim}. "
                        "A dim mismatch corrupts the HNSW index SILENTLY -- refusing."
                    )
                self.cache.put(keys[i], v)
                out[i] = v
        return [v for v in out if v is not None]

    def embed_one(self, text: str) -> np.ndarray:
        return self.embed([text])[0]


class OpenAIEmbedder(Embedder):
    """`text-embedding-3-small` with `dimensions=EMBED_DIM`.

    Raises on a missing key. Does not fall back. Does not retry into a different
    model. If the provider serves a different model, that is a T2 event and the
    caller must discard, not average.
    """

    model_id = OPENAI_MODEL

    def __init__(self, *, dim: int = EMBED_DIM, cache: Optional[_Cache] = None) -> None:
        super().__init__(dim=dim, cache=cache)
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise EmbedderConfigError(
                "OPENAI_API_KEY is unset. The harness will NOT silently fall back "
                "to the hash embedder -- that is how a run scores plausibly while "
                "measuring nothing. Either export the key, or pass "
                "`test_embedder: true` in the config to opt in EXPLICITLY."
            )
        from openai import OpenAI  # imported lazily so the dry path needs no key

        self._client = OpenAI(api_key=key)

    def _compute(self, texts: Sequence[str]) -> List[np.ndarray]:
        resp = self._client.embeddings.create(
            model=self.model_id, input=list(texts), dimensions=self.dim
        )
        served = getattr(resp, "model", None)
        # T2 shape: the SERVED model must match the PINNED constant, not merely
        # match what we requested (providers rewrite both fields on fallback).
        if served and not str(served).startswith(self.model_id):
            raise EmbedderConfigError(
                f"served embedding model {served!r} != pinned {self.model_id!r}. "
                "Embeddings from a different model are not comparable; refusing."
            )
        self.stats["calls"] += 1
        self.stats["tokens_in"] += int(getattr(getattr(resp, "usage", None), "total_tokens", 0) or 0)
        return [np.asarray(d.embedding, dtype=np.float32) for d in resp.data]


class HashEmbedder(Embedder):
    """Deterministic, offline, free. **Opt-in only.**

    Not a semantic model: it is a stable random projection of token hashes. It
    exercises every code path (cache, HNSW index, dim contract, retrieval
    plumbing, ranking) at zero cost and with bit-identical results across boxes,
    which is what makes the dry smoke gate meaningful as a PLUMBING test.

    It is NOT a retrieval-quality test and the harness says so in the manifest:
    any run pinned to `hash-test-embedder@256` is stamped `test_embedder=true`
    and its qa.* numbers are labelled non-citable.
    """

    model_id = TEST_EMBEDDER_ID

    def _compute(self, texts: Sequence[str]) -> List[np.ndarray]:
        self.stats["calls"] += 1
        return [self._hash_vec(t) for t in texts]

    def _hash_vec(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        # word-level hashing so lexically-overlapping texts land near each other
        tokens = [t for t in "".join(c.lower() if c.isalnum() else " " for c in text).split() if t]
        if not tokens:
            tokens = ["\x00empty"]
        for tok in tokens:
            d = hashlib.sha256(tok.encode("utf-8")).digest()
            # 8 deterministic (index, sign) draws per token
            for j in range(8):
                idx = struct.unpack_from(">I", d, j * 4)[0] % self.dim
                sign = 1.0 if (d[j] & 1) else -1.0
                vec[idx] += sign
        return _normalize(vec)


def build_embedder(
    *,
    test_embedder: bool = False,
    dim: int = EMBED_DIM,
    cache_dir: Optional[Path] = None,
) -> Embedder:
    """THE factory. `test_embedder` is the ONLY route to `HashEmbedder`.

    There is deliberately no `try openai / except -> hash` anywhere in this
    module. If you find one, delete it: a silent fallback is indistinguishable
    from a working system right up until the number is published.
    """
    cache = _Cache(Path(cache_dir) if cache_dir else CACHE_DIR)
    if test_embedder:
        return HashEmbedder(dim=dim, cache=cache)
    return OpenAIEmbedder(dim=dim, cache=cache)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine SIMILARITY (1.0 = identical).

    NOTE the sign convention difference from FalkorDB: `db.idx.vector.queryNodes`
    returns a DISTANCE (0.0 = identical, sort ASC). memory/SCHEMA.md trap #1.
    """
    return float(np.dot(a, b))


__all__ = [
    "CACHE_DIR",
    "EMBED_DIM",
    "Embedder",
    "EmbedderConfigError",
    "HashEmbedder",
    "OPENAI_MODEL",
    "OpenAIEmbedder",
    "TEST_EMBEDDER_ID",
    "build_embedder",
    "cache_key",
    "cosine",
]
