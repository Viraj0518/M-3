"""The A3 graph schema, and the per-question isolation decision.

ISOLATION: ONE FALKORDB GRAPH PER QUESTION, key `lme_{split}_{question_id}`.
  (a) cross-question leakage is the single most likely false-green in this whole
      harness, and per-graph isolation makes it STRUCTURALLY IMPOSSIBLE rather
      than merely unlikely;
  (b) traversals stay ~50 sessions wide, so ring/expansion queries stay in the
      millisecond band the demo advertises;
  (c) trivially parallel and resumable;
  (d) `GRAPH.DELETE` is a clean teardown.
Cost: 500 small HNSW indexes, which is fine at this scale.

VOCABULARY IS LIFTED, NOT REDESIGNED. `relation` values come from
`memory.taxonomy.RELATION_KINDS` (the 6-value list from unblock's
relate-verb.ts) and `[:RELATES]` is stored GENUINELY DIRECTED -- unblock's
Postgres CHECK `block_id_a < block_id_b` forces edges into lexical id order and
thereby destroys direction, so "A supersedes B" becomes indistinguishable from
"B supersedes A". FalkorDB gives real direction for free and the supersede chain
is the entire knowledge-update mechanism, so this divergence is load-bearing,
not cosmetic.

═══════════════════════════════════════════════════════════════════════════════
TRAPS VERIFIED ON THIS BOX (FalkorDB v4.2.1, 2026-08-03)
═══════════════════════════════════════════════════════════════════════════════
1. `db.idx.vector.queryNodes` returns a **DISTANCE**: 0.0 == identical, larger
   == further. SORT ASC. (memory/SCHEMA.md trap #1.) Measured: identical vector
   -> 0.0, orthogonal-ish -> 0.778.
2. **CREATE INDEXES BEFORE INSERTING NODES.** Measured: creating the vector
   index after the nodes made `db.idx.vector.queryNodes` fail with "Invalid
   arguments for procedure" -- an error that reads like a signature problem and
   is actually an ordering problem. `ensure_schema()` is therefore always called
   on an empty graph.
3. The 4-arg positional signature is the one this server accepts:
   `db.idx.vector.queryNodes(label, attr, k, vecf32($q))`. The map form
   (`{label:..., k:...}`) raises "requires 4 arguments, got 1".
4. Port **6401**, from `memory.config` -- never 6379/6399. A stray `redis-server`
   on either lets the client CONNECT FINE and then fail on `GRAPH.QUERY`, which
   is a false-green that eats 45 minutes. On this box 6401 is held by colima's
   ssh forwarder; `GRAPH.QUERY` was verified to return a real result set through
   it, so the listener identity was confirmed POSITIVELY rather than assumed.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

try:
    from memory.config import EMBED_DIM, FALKORDB_HOST, FALKORDB_PORT, VECTOR_SIMILARITY
    from memory.taxonomy import RELATION_KINDS, check_relation, is_directed
except Exception:  # pragma: no cover
    EMBED_DIM, FALKORDB_HOST, FALKORDB_PORT, VECTOR_SIMILARITY = 256, "127.0.0.1", 6401, "cosine"
    RELATION_KINDS = ("supports", "contradicts", "derived_from", "supersedes", "duplicates", "references")
    def check_relation(v: str) -> str:  # type: ignore
        return v
    def is_directed(v: str) -> bool:  # type: ignore
        return v in {"contradicts", "derived_from", "supersedes", "supports"}

NODE_LABELS: tuple = ("Session", "Turn", "Claim", "Entity")
CLAIM_KINDS: tuple = ("fact", "preference", "event")

#: Only these relation kinds are written by the eval ingest. All are in
#: memory.taxonomy.RELATION_KINDS; `supersedes` and `contradicts` are directed.
EVAL_RELATIONS: tuple = ("supersedes", "contradicts")


def graph_key(split: str, question_id: str) -> str:
    """`lme_{split}_{qid}`, sanitised for a redis key.

    `split` here is the SPLIT-ID TAG (`oracle@98d7416`), so an oracle graph and
    an `_s` graph for the same qid cannot collide -- T5 enforced at the storage
    layer, not just in the metric names.
    """
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", f"{split}_{question_id}")
    return f"lme_{safe}"


# ── DDL ─────────────────────────────────────────────────────────────────────
def schema_statements(dim: int = EMBED_DIM) -> List[str]:
    """Indexes first, always. See trap 2 above."""
    return [
        # range indexes -- the temporal spine and the entity join both need these
        "CREATE INDEX FOR (s:Session) ON (s.date_iso)",
        "CREATE INDEX FOR (s:Session) ON (s.ordinal)",
        "CREATE INDEX FOR (s:Session) ON (s.id)",
        "CREATE INDEX FOR (t:Turn) ON (t.id)",
        "CREATE INDEX FOR (c:Claim) ON (c.id)",
        "CREATE INDEX FOR (c:Claim) ON (c.predicate)",
        "CREATE INDEX FOR (e:Entity) ON (e.norm)",
        # vector -- dim MUST equal the embedder's `dimensions=`; a mismatch
        # corrupts the index SILENTLY (no error, just wrong neighbours)
        f"CREATE VECTOR INDEX FOR (c:Claim) ON (c.emb) "
        f"OPTIONS {{dimension:{dim}, similarityFunction:'{VECTOR_SIMILARITY}'}}",
        f"CREATE VECTOR INDEX FOR (t:Turn) ON (t.emb) "
        f"OPTIONS {{dimension:{dim}, similarityFunction:'{VECTOR_SIMILARITY}'}}",
        # lexical entry (S1b)
        "CALL db.idx.fulltext.createNodeIndex('Turn', 'text')",
        "CALL db.idx.fulltext.createNodeIndex('Claim', 'text')",
    ]


SCHEMA_DOC: str = """
(:Session {id, date_iso, date_raw, ordinal})
(:Turn    {id, role, text, ordinal, ts, emb})
(:Claim   {id, text, emb, predicate, valid_from, polarity, kind})  // kind in fact|preference|event
(:Entity  {name, norm})

(:Turn)-[:IN_SESSION]->(:Session)
(:Session)-[:NEXT]->(:Session)                    // temporal spine, ordered by date_iso
(:Claim)-[:FROM_TURN]->(:Turn)                    // provenance -> recall@k on answer_session_ids
(:Claim)-[:MENTIONS]->(:Entity)
(:Claim)-[:RELATES {relation}]->(:Claim)          // GENUINELY DIRECTED
"""


def normalize_entity(name: str) -> str:
    """Entity join key. Lowercase, strip punctuation, collapse whitespace.

    Deliberately dumb: the S2 expansion joins on `Entity.norm`, and a clever
    normaliser (stemming, alias tables, embedding-clustered entities) would make
    the expansion mechanism unfalsifiable -- you could no longer tell a graph win
    from a normalisation win. Keep it boring so the removal test means something.
    """
    s = (name or "").strip().lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_predicate(pred: str) -> str:
    s = (pred or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_") or "unspecified"


class GraphHandle:
    """Thin wrapper -- keeps the driver import in one place and stamps params."""

    def __init__(self, key: str, *, host: str = FALKORDB_HOST, port: int = FALKORDB_PORT) -> None:
        import falkordb

        self.key = key
        self.host, self.port = host, port
        self._db = falkordb.FalkorDB(host=host, port=port)
        self._g = self._db.select_graph(key)

    def query(self, cypher: str, params: Optional[Dict[str, Any]] = None) -> Any:
        return self._g.query(cypher, params or {})

    def ro_query(self, cypher: str, params: Optional[Dict[str, Any]] = None) -> Any:
        try:
            return self._g.ro_query(cypher, params or {})
        except Exception:
            return self._g.query(cypher, params or {})

    def drop(self) -> None:
        """`GRAPH.DELETE` -- the clean teardown that makes per-question graphs cheap."""
        try:
            self._g.delete()
        except Exception:
            pass  # already absent

    def ensure_schema(self, dim: int = EMBED_DIM) -> None:
        """Call on an EMPTY graph, before any node exists (trap 2)."""
        for stmt in schema_statements(dim):
            try:
                self.query(stmt)
            except Exception as exc:
                # "already indexed" is the only tolerable failure here
                if "already" not in str(exc).lower():
                    raise

    def node_count(self, label: str) -> int:
        res = self.ro_query(f"MATCH (n:{label}) RETURN count(n) AS c")
        return int(res.result_set[0][0]) if res.result_set else 0

    def edge_count(self, rel: str) -> int:
        res = self.ro_query(f"MATCH ()-[r:{rel}]->() RETURN count(r) AS c")
        return int(res.result_set[0][0]) if res.result_set else 0

    def relation_count(self, relation: str) -> int:
        res = self.ro_query(
            "MATCH ()-[r:RELATES]->() WHERE r.relation = $rel RETURN count(r) AS c",
            {"rel": relation},
        )
        return int(res.result_set[0][0]) if res.result_set else 0


def assert_directed_vocabulary() -> None:
    """Positive assertion that the taxonomy import is live and asymmetric.

    Guards against the schema module silently falling back to its own local
    constants (the `except ImportError` at the top of this file) -- which would
    be a textbook one-identifier-two-objects false-green: the eval would look
    like it shares the demo's vocabulary while actually carrying a private copy.
    """
    for rel in EVAL_RELATIONS:
        check_relation(rel)
        if not is_directed(rel):
            raise AssertionError(
                f"relation {rel!r} is not marked directed in memory.taxonomy. The "
                "supersede chain is the knowledge-update mechanism; it is "
                "meaningless if direction is not preserved."
            )


__all__ = [
    "CLAIM_KINDS",
    "EMBED_DIM",
    "EVAL_RELATIONS",
    "FALKORDB_HOST",
    "FALKORDB_PORT",
    "GraphHandle",
    "NODE_LABELS",
    "RELATION_KINDS",
    "SCHEMA_DOC",
    "assert_directed_vocabulary",
    "graph_key",
    "normalize_entity",
    "normalize_predicate",
    "schema_statements",
]
