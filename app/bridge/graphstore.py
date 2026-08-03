"""PALIMPSEST memory plane — the ONE place FalkorDB is spoken to.

Everything the bridge handlers need to talk Cypher, and nothing else. Kept in a
separate module from ``server.py`` for exactly one reason: **server.py must
import cleanly on the system Python 3.9 with no venv hot**, so the dispatch
table, the tool schemas and the OpenAPI generator stay testable in a bare
interpreter. The ``falkordb`` client is therefore imported HERE, GUARDED, and
this module imports cleanly without it too — you only get an honest error when
you actually try to open a connection.

════════════════════════════════════════════════════════════════════════════
VERIFIED AGAINST THE LIVE CONTAINER (palimpsest-falkordb, 127.0.0.1:6401)
════════════════════════════════════════════════════════════════════════════
Module version on the box: ``graph v42001``. Two facts that differ from what
memory/SCHEMA.md §3 was written against, both verified by round-trip:

  1. ``CALL db.idx.vector.createNodeIndex(...)`` is **GONE** in this build
     ("Procedure `db.idx.vector.createNodeIndex` is not registered"). The index
     is created with DDL instead::

         CREATE VECTOR INDEX FOR (n:Claim) ON (n.emb)
         OPTIONS {dimension: 256, similarityFunction: 'cosine'}

  2. The **query** procedure is unchanged and still POSITIONAL 4-arg::

         CALL db.idx.vector.queryNodes('Claim', 'emb', $k, vecf32($q))

     The newer map form is rejected ("requires 4 arguments, got 1").

TRAP 1 (memory/SCHEMA.md §4) RE-VERIFIED ON THIS BOX TODAY: querying with the
exact stored vector returned **0.0**, its near neighbour **0.0061**. The score
is a DISTANCE. **ORDER BY score ASC.** Sorting DESC returns the worst matches
in the database with no error at all.

TRAP 2: port 6401 comes from ``memory/config.py``. Never write a literal here.

FalkorDB property types: primitives + arrays of primitives only. There is no
map property, so every dict-shaped field (``metadata``, ``artifacts``,
``checkpoint``) is stored JSON-ENCODED in a ``*_json`` property and decoded on
read. One helper does it in both directions so the two sides cannot drift.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from memory import config
from memory.taxonomy import NODE_LABELS

# ─── the client, GUARDED ────────────────────────────────────────────────────
try:  # pragma: no cover - import-environment dependent
    from falkordb import FalkorDB  # type: ignore[import-not-found]

    FALKORDB_AVAILABLE = True
except ImportError:  # pragma: no cover - import-environment dependent
    FALKORDB_AVAILABLE = False
    FalkorDB = None  # type: ignore[assignment,misc]


class GraphUnavailable(RuntimeError):
    """The memory plane cannot be reached. Raised, never swallowed — a silent
    empty result is indistinguishable from a genuine miss, and 'the graph
    actually remembers' is the entire thesis."""


#: Graph keys this process will open. `palimpsest` (warm) and `palimpsest_cold`
#: (the ablation target) come from config; the pattern additionally admits
#: `palimpsest_test` and friends so the pytest suite gets its own teardownable
#: key WITHOUT widening this into "any string the caller sends" (that would be
#: a graph-key injection straight off the REST surface).
_GRAPH_KEY_RE = re.compile(r"^palimpsest(_[a-z0-9]+)*$")

#: Node labels that may be MERGEd. Allowlisted because a label cannot be a
#: Cypher parameter — it is string-interpolated, so it must never be caller text.
_WRITABLE_LABELS = frozenset(NODE_LABELS)

#: Properties never echoed back to a caller or the UI: 256 floats per node
#: would be ~90% of every /graph payload and mean nothing on screen.
_HEAVY_PROPS = ("emb",)

#: Field separator for the deterministic id hash. A control character, so
#: stable_id("blk", "a b", "c") and stable_id("blk", "a", "b c") cannot collide
#: the way a space separator would let them.
_SEP = "\x1f"

_DB: Any = None


# ═══════════════════════════════════════════════════════════════════════════
# connection
# ═══════════════════════════════════════════════════════════════════════════

def check_graph_key(graph: Optional[str]) -> str:
    key = graph or config.GRAPH_WARM
    if not _GRAPH_KEY_RE.match(key):
        raise ValueError(
            "refusing graph key {0!r}: must match {1} (the warm graph is {2!r}, "
            "the ablation graph is {3!r})".format(
                key, _GRAPH_KEY_RE.pattern, config.GRAPH_WARM, config.GRAPH_COLD
            )
        )
    return key


def _client() -> Any:
    global _DB
    if not FALKORDB_AVAILABLE:
        raise GraphUnavailable(
            "the `falkordb` client is not installed — the memory plane cannot "
            "be opened. Install it: pip install -r app/bridge/requirements.txt "
            "(needs Python >= 3.12)."
        )
    if _DB is None:
        _DB = FalkorDB(host=config.FALKORDB_HOST, port=config.FALKORDB_PORT)
    return _DB


def graph(graph_key: Optional[str] = None) -> Any:
    """A handle on one graph key. Cheap; the client pools the connection."""
    return _client().select_graph(check_graph_key(graph_key))


def reset_client() -> None:
    """Drop the cached client (tests, and a future reconnect hook)."""
    global _DB
    _DB = None


def _run(cypher: str, params: Optional[dict], graph_key: Optional[str], read_only: bool) -> Any:
    g = graph(graph_key)
    try:
        return g.ro_query(cypher, params or {}) if read_only else g.query(cypher, params or {})
    except Exception as exc:  # noqa: BLE001
        raise GraphUnavailable(
            "FalkorDB at {0}:{1} rejected the query: {2}: {3}".format(
                config.FALKORDB_HOST, config.FALKORDB_PORT, type(exc).__name__, exc
            )
        ) from exc


def query(
    cypher: str,
    params: Optional[dict] = None,
    *,
    graph_key: Optional[str] = None,
    read_only: bool = False,
) -> Tuple[List[list], float, list]:
    """Run one Cypher statement. Returns ``(rows, run_time_ms, header)``.

    ``run_time_ms`` is FalkorDB's OWN measured execution time, not a wall clock
    we computed — that is the number the projector puts on screen next to the
    ring verdict, and it has to be the database's, not ours.
    """
    res = _run(cypher, params, graph_key, read_only)
    return list(res.result_set or []), float(res.run_time_ms or 0.0), list(res.header or [])


def mutate(
    cypher: str,
    params: Optional[dict] = None,
    *,
    graph_key: Optional[str] = None,
) -> Tuple[List[list], float, Dict[str, int]]:
    """A write. Returns ``(rows, run_time_ms, stats)`` where ``stats`` carries
    the counters that make a MERGE honest about whether it CREATED or MATCHED —
    which is the difference between "captured" and "already knew that"."""
    res = _run(cypher, params, graph_key, False)
    stats = {
        "nodes_created": int(getattr(res, "nodes_created", 0) or 0),
        "nodes_deleted": int(getattr(res, "nodes_deleted", 0) or 0),
        "relationships_created": int(getattr(res, "relationships_created", 0) or 0),
        "relationships_deleted": int(getattr(res, "relationships_deleted", 0) or 0),
        "properties_set": int(getattr(res, "properties_set", 0) or 0),
    }
    return list(res.result_set or []), float(res.run_time_ms or 0.0), stats


def drop_graph(graph_key: str) -> bool:
    """GRAPH.DELETE, tolerant of a key that was never created (pytest teardown
    runs even when a test failed before it wrote anything)."""
    try:
        graph(graph_key).delete()
        return True
    except Exception:  # noqa: BLE001 - "Invalid graph operation on empty key"
        return False


# ═══════════════════════════════════════════════════════════════════════════
# indexes — idempotent, at startup, per memory/SCHEMA.md §3
# ═══════════════════════════════════════════════════════════════════════════

#: (statement, human name). Range first, then fulltext, then HNSW.
def _index_statements() -> List[Tuple[str, str]]:
    dim = config.EMBED_DIM
    sim = config.VECTOR_SIMILARITY
    vec = (
        "CREATE VECTOR INDEX FOR (n:{label}) ON (n.emb) "
        "OPTIONS {{dimension: %d, similarityFunction: '%s'}}" % (dim, sim)
    )
    return [
        ("CREATE INDEX FOR (a:Actor) ON (a.name)", "range:Actor.name"),
        ("CREATE INDEX FOR (p:Page) ON (p.title)", "range:Page.title"),
        ("CREATE INDEX FOR (c:Case) ON (c.id)", "range:Case.id"),
        ("CREATE INDEX FOR (c:Claim) ON (c.id)", "range:Claim.id"),
        ("CREATE INDEX FOR (e:Event) ON (e.id)", "range:Event.id"),
        ("CREATE INDEX FOR (g:Agent) ON (g.agent_id)", "range:Agent.agent_id"),
        ("CREATE FULLTEXT INDEX FOR (c:Claim) ON (c.text)", "fulltext:Claim.text"),
        ("CREATE FULLTEXT INDEX FOR (e:Event) ON (e.summary)", "fulltext:Event.summary"),
        (vec.format(label="Claim"), "vector:Claim.emb"),
        (vec.format(label="Event"), "vector:Event.emb"),
    ]


#: graph keys whose indexes this process has already ensured.
_ENSURED: set = set()


def ensure_indexes(graph_key: Optional[str] = None, *, force: bool = False) -> Dict[str, str]:
    """Create every index from SCHEMA.md §3, IDEMPOTENTLY.

    FalkorDB has no ``IF NOT EXISTS`` for these, and a re-create raises
    ``Attribute 'x' is already indexed``. That specific error is the SUCCESS
    case on a second boot, so it is matched narrowly and reported as
    ``"exists"`` — anything else still propagates. A blanket except here would
    hide a genuinely broken vector index, which fails SILENTLY at query time
    (trap: dimension mismatch = wrong neighbours, no error).
    """
    key = check_graph_key(graph_key)
    out: Dict[str, str] = {}
    if key in _ENSURED and not force:
        return {"status": "cached", "graph": key}
    for stmt, name in _index_statements():
        try:
            query(stmt, graph_key=key)
            out[name] = "created"
        except GraphUnavailable as exc:
            msg = str(exc)
            if "already indexed" in msg or "already exists" in msg:
                out[name] = "exists"
            else:
                raise
    _ENSURED.add(key)
    return out


def forget_ensured(graph_key: Optional[str] = None) -> None:
    """Test hook: make the next :func:`ensure_indexes` do real work again."""
    if graph_key is None:
        _ENSURED.clear()
    else:
        _ENSURED.discard(graph_key)


# ═══════════════════════════════════════════════════════════════════════════
# encoding helpers — FalkorDB has NO map property type
# ═══════════════════════════════════════════════════════════════════════════

def encode_map(value: Optional[dict]) -> str:
    """dict -> a JSON string property. '{}' when absent, never NULL, so a
    reader never has to branch on missing-vs-empty."""
    return json.dumps(value or {}, sort_keys=True, default=str)


def decode_map(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        out = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def flat_text(content: Any) -> str:
    """Any content -> one flat string. Mirrors roster-graph.ts ``flatSnippet``:
    the graph stores something a human and a fulltext index can both read."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (int, float, bool)):
        return str(content)
    return json.dumps(content, sort_keys=True, default=str)


def stable_id(prefix: str, *parts: Any) -> str:
    """A deterministic id, so ``remember`` is genuinely a MERGE and calling it
    twice with the same content is a no-op rather than a duplicate node."""
    digest = hashlib.sha256(_SEP.join(flat_text(p) for p in parts).encode("utf-8"))
    return "{0}_{1}".format(prefix, digest.hexdigest()[:16])


def check_label(label: Optional[str], default: str = "Claim") -> str:
    """A Cypher label cannot be a $param — it is interpolated, so it is
    allowlisted against the ten labels in memory/SCHEMA.md §1."""
    lab = label or default
    if lab not in _WRITABLE_LABELS:
        raise ValueError(
            "unknown node label {0!r}. In-schema labels: {1}".format(
                lab, ", ".join(sorted(_WRITABLE_LABELS))
            )
        )
    return lab


def check_vector(vec: Any, *, field: str = "embedding") -> Optional[List[float]]:
    """Validate a PRECOMPUTED embedding. The bridge NEVER calls an embedding
    API — embeddings arrive from the caller (the gate/eval harness owns that
    key). Width is enforced because a mismatch corrupts the HNSW index SILENTLY:
    no error, just wrong neighbours, which on stage is indistinguishable from
    'the memory doesn't work'."""
    if vec is None:
        return None
    if not isinstance(vec, (list, tuple)):
        raise ValueError("{0} must be a list of floats".format(field))
    out = [float(x) for x in vec]
    if len(out) != config.EMBED_DIM:
        raise ValueError(
            "{0} has width {1} but the HNSW index is dim {2} "
            "(config.EMBED_DIM). A width mismatch corrupts the vector index "
            "silently — refusing the write.".format(field, len(out), config.EMBED_DIM)
        )
    return out


# ═══════════════════════════════════════════════════════════════════════════
# projection — FalkorDB objects -> the shapes the UI already parses
# ═══════════════════════════════════════════════════════════════════════════

def node_props(node: Any) -> Dict[str, Any]:
    props = dict(getattr(node, "properties", None) or {})
    for heavy in _HEAVY_PROPS:
        props.pop(heavy, None)
    return props


def snippet_of(props: Dict[str, Any]) -> str:
    """The one human-readable line for a node.

    The UI resolves a node's on-screen label as
    ``props.name ?? props.title ?? props.display_name ?? props.snippet ??
    props.content ?? props.summary ?? props.id`` (app/web/index.html
    normalizeGraph). Our :Claim carries ``text``, which is NOT in that chain —
    so the projection synthesises ``snippet``, which IS. Without this every
    Claim renders as its opaque id.
    """
    for key in (
        "name",
        "title",
        "display_name",
        "snippet",
        "content",
        "summary",
        "text",
        "agent_id",
    ):
        val = props.get(key)
        if isinstance(val, str) and val.strip():
            return val[:180]
        if val not in (None, "", [], {}):
            return flat_text(val)[:180]
    return str(props.get("id") or "")


def project_node(node: Any) -> Dict[str, Any]:
    """One FalkorDB Node -> the UI's node shape: ``{id, labels, properties}``.

    ``id`` is the FalkorDB INTERNAL node id as a string, because that is the
    only identifier edges can reference (``Edge.src_node``/``dest_node`` are
    internal ids). The domain id survives untouched in ``properties.id``.
    """
    props = node_props(node)
    props.setdefault("snippet", snippet_of(props))
    return {
        "id": str(getattr(node, "id", "")),
        "labels": list(getattr(node, "labels", None) or []),
        "properties": props,
    }


def project_edge(edge: Any) -> Dict[str, Any]:
    """One FalkorDB Edge -> the UI's edge shape: ``{relation, src, dest}``.

    ``relation`` prefers the edge PROPERTY ``relation`` (the 6-value directed
    vocabulary on ``[:RELATES]``) and falls back to the edge TYPE for the
    structural edges (``EDITED``, ``ON``, ``HANDED_OFF_TO``, ...). The UI reads
    ``raw.relation ?? raw.type ?? ...`` and ``raw.src ?? raw.source ??
    raw.src_node``, so these three keys hit its first choice every time.
    """
    props = dict(getattr(edge, "properties", None) or {})
    rel = props.get("relation") or getattr(edge, "relation", None) or "RELATES"
    return {
        "relation": str(rel),
        "type": str(getattr(edge, "relation", "") or rel),
        "src": str(getattr(edge, "src_node", "")),
        "dest": str(getattr(edge, "dest_node", "")),
        "properties": props,
    }


def project_path(path: Any) -> Dict[str, Any]:
    """One FalkorDB Path -> ``{nodes, edges, ids}``.

    The UI's ``normalizeRing`` takes ``payload.paths[0]``, sees a ``.nodes``
    array, and maps it through ``endpointId`` — which reads ``.id``. So the
    node ids in a path MUST be the same internal ids the /graph projection
    emits, or the ring animation highlights nothing.
    """
    nodes = [project_node(n) for n in path.nodes()]
    edges = [project_edge(e) for e in path.edges()]
    return {"nodes": nodes, "edges": edges, "ids": [n["id"] for n in nodes]}


def contributors_of(nodes: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    """author_agent -> write count, for the per-agent node colouring."""
    from . import identity

    counts: Dict[str, int] = {}
    for n in nodes:
        who = (n.get("properties") or {}).get(identity.AUTHOR_PROP) or identity.UNBOUND_AGENT
        counts[who] = counts.get(who, 0) + 1
    return counts


__all__ = [
    "FALKORDB_AVAILABLE",
    "GraphUnavailable",
    "check_graph_key",
    "check_label",
    "check_vector",
    "contributors_of",
    "decode_map",
    "drop_graph",
    "encode_map",
    "ensure_indexes",
    "flat_text",
    "forget_ensured",
    "graph",
    "mutate",
    "node_props",
    "project_edge",
    "project_node",
    "project_path",
    "query",
    "reset_client",
    "snippet_of",
    "stable_id",
]
