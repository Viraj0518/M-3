"""PALIMPSEST bridge — ONE dispatch table, FOUR surfaces.

PORTED SKELETON. The shape of this file is lifted from unblock's MCP server
(``unblock_mcp/src/unblock_mcp/server.py``, 5446 LOC) — specifically:

    * the 4-tuple ``_VERB_DISPATCH`` table            (server.py:3478-3495)
    * the ~35-LOC generic ``call_tool`` router tail   (server.py:5231-5268)
    * the tool-schema style                           (server.py:897-1092)
    * the server ``instructions`` reflex text          (server.py:772-796)
    * ``_main``                                        (server.py:5403+)

A reference copy of the original is at
``vendor/unblock-reuse/unblock_mcp/src/unblock_mcp/server.py``.

WHAT WAS STRIPPED: everything. ~80% of the original file is macaroon minting,
DID resolution, Supabase/Kaeva Edge-Function wire, device registration, api-key
format guards, NATS credential minting, rate limiting, generated tools, and
surface gating. NONE of it is here. PALIMPSEST takes ZERO runtime dependency on
the live Kaeva backend — a prod outage during the pitch would be unrecoverable,
so there is nothing to outage. The backend is FalkorDB, on localhost.

════════════════════════════════════════════════════════════════════════════
WHY THE TABLE IS THE WHOLE ARCHITECTURE
════════════════════════════════════════════════════════════════════════════
Each entry is ``verb -> (METHOD, PATH, payload_builder, handler)``.

Because every entry already carries an HTTP (METHOD, PATH), ONE table generates
FOUR surfaces with no second implementation:

    (a) REST          — the projector UI and the seed script
    (b) MCP           — streamable-http, mounted into RocketRide's `mcp_client`
    (c) OpenAPI 3.0   — the ONE Guild Integration (Guild's sandbox bans
                        fetch/npm/Node built-ins, so an Integration is the only
                        legal door; three of them would eat the sprint)
    (d) CLI           — rehearsal + gate-artifact capture

Element 4 is the DIVERGENCE from unblock. Upstream it is a `queueable_verb`
string pointing at a Kaeva Edge Function. Here it is a HANDLER COROUTINE that
talks to FalkorDB / LaserData / RocketRide directly. Same table, no network
mystery.

════════════════════════════════════════════════════════════════════════════
PRE-REGISTERED BUG CLASS — READ BEFORE EDITING A payload_builder
════════════════════════════════════════════════════════════════════════════
In unblock, ``unblock_ingest`` was NOT a passthrough: the TOOL spoke
``{utterances:[{role,text,ts}]}`` while the BACKEND spoke
``{items:[{content,metadata}]}``. The mismatch silently 400'd every call and
cost unblock agent self-ingest ENTIRELY — nobody noticed because the failure
was a well-formed error envelope, not a crash.

RULE: every payload_builder below gets a SHAPE TEST alongside it, written on
day one, BEFORE the OpenAPI spec is published to Guild. A builder is the only
place the tool-facing shape becomes the handler-facing shape, so it is the only
place that class of bug can hide.

MCP VERSION PIN (load-bearing): ``mcp>=1.28,<2``. Spec revision 2026-07-28
(SDK 2.x) removes protocol-level sessions and the ``Mcp-Session-Id`` header
that ``identity.py`` keys on. Do not relax the upper bound.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from memory import config
from memory.taxonomy import (
    BLOCK_TYPES,
    HANDOVER_OPEN,
    HANDOVER_STATUSES,
    MESSAGE_INTENTS,
    NODE_LABELS,
    RELATION_KINDS,
    check_relation,
    is_directed,
    normalize_block_type,
)

from . import graphstore, guard, identity, widget_apps

# ─── MCP SDK, guarded ───────────────────────────────────────────────────────
# The module must IMPORT CLEANLY with or without the SDK installed, so the
# taxonomy/config/table/OpenAPI surfaces stay testable in a bare interpreter
# and `python -c "import app.bridge.server"` never depends on a venv being hot.
# When the SDK is absent we substitute minimal structural stand-ins; the MCP
# transport itself refuses to boot with an honest error (see _main).
try:  # pragma: no cover - import-environment dependent
    from mcp.server import Server  # type: ignore[import-not-found]
    from mcp.server.lowlevel.server import (  # type: ignore[import-not-found]
        ReadResourceContents,
    )
    from mcp.server.stdio import stdio_server  # type: ignore[import-not-found]
    from mcp.types import Resource, TextContent, Tool  # type: ignore[import-not-found]

    MCP_AVAILABLE = True
except ImportError:  # pragma: no cover - import-environment dependent
    MCP_AVAILABLE = False
    Server = None  # type: ignore[assignment,misc]
    stdio_server = None  # type: ignore[assignment]

    class TextContent:  # type: ignore[no-redef]
        """Structural stand-in for mcp.types.TextContent."""

        def __init__(self, type: str = "text", text: str = "") -> None:
            self.type = type
            self.text = text

        def __repr__(self) -> str:
            return f"TextContent(type={self.type!r}, text={self.text!r})"

    class Tool:  # type: ignore[no-redef]
        """Structural stand-in for mcp.types.Tool."""

        def __init__(self, name: str, description: str, inputSchema: dict) -> None:
            self.name = name
            self.description = description
            self.inputSchema = inputSchema

        def __repr__(self) -> str:
            return f"Tool(name={self.name!r})"


SERVER_NAME = "palimpsest-bridge"
SERVER_VERSION = "0.1.0"

#: MCP tool names are the verb with this prefix. The TABLE is keyed on the bare
#: verb so REST/CLI/OpenAPI stay clean; exactly one function converts between
#: them, so a rename can never desync two surfaces.
TOOL_PREFIX = "palimpsest_"

#: The author_agent stamped on an MCP-surface write when the host neither binds
#: a session nor forwards an ``x-palimpsest-agent`` header — the stdio mount,
#: which is exactly the surface we hand judges. Without it every MCP write was
#: attributed to ``identity.UNBOUND_AGENT`` ('unbound'), silently undercutting
#: GOAL's "the agent knows who it is" pillar on that surface. It is a stable,
#: NON-SECRET, env-overridable name (never a credential), and it is the LOWEST
#: rung of the identity ladder in ``identity.resolve`` — an explicit bind, a
#: forwarded header, and any verified-token selector all still outrank it.
MCP_DEFAULT_AGENT: str = os.environ.get("PALIMPSEST_MCP_AGENT") or "palimpsest-mcp"


# ─── Agent reflex text ──────────────────────────────────────────────────────
# Lifted from unblock server.py:772-796 — battle-tested phrasing for "write
# memory without being asked, query before re-solving". THE VERB NAMES ARE
# REWRITTEN TO OURS: leaving unblock's names in would make our agents
# hallucinate unblock tool calls at the worst possible moment. This same text
# feeds the Guild agent `systemPrompt` fields and RocketRide's `instructions[]`.
INSTRUCTIONS = (
    "You are wired into PALIMPSEST — a live attributed memory graph (FalkorDB) "
    "sitting on a durable ordered event log (LaserData). You are never "
    "stateless: before you ask a human anything, PULL. "
    "CAPTURE REFLEX: before finalizing any turn where you made a decision, "
    "fixed a bug, or learned a pattern — call palimpsest_remember with an "
    "in-taxonomy block_type and carry the capture intent in metadata.kind "
    "('fix' and 'learning' are NOT block_types): a decision -> "
    "block_type=decision; a bug fix -> block_type=decision + "
    "metadata={'kind': 'fix'}; a learning -> block_type=note + "
    "metadata={'kind': 'learning'}. Include what you did and whether it worked. "
    "Do not wait to be asked. The sensing gate rejects non-novel writes for "
    "you, so capturing is cheap. "
    "QUERY REFLEX: before re-explaining something or re-solving a class of "
    "problem, call palimpsest_recall or palimpsest_ring first — a prior "
    "session, or another agent, may have solved it already. Ring first when "
    "you have an entity and want its neighbourhood; recall when you have prose. "
    "STREAM REFLEX: what is happening RIGHT NOW is on the log, not in your "
    "context. Call palimpsest_stream_tail before you claim anything about the "
    "present tense. "
    "HANDOFF REFLEX: call palimpsest_handover_write on every meaningful state "
    "change (started X, shipped Y, blocked on Z) and before you finish. A "
    "restarted agent reads its own handover node back out of the graph and "
    "resumes from its committed log offset — that only works if you wrote one. "
    "ASK LAST: palimpsest_ask is for ONE thing only — approval to act. Every "
    "other question should have been a pull."
)


# ═══════════════════════════════════════════════════════════════════════════
# Request context
# ═══════════════════════════════════════════════════════════════════════════

class RequestCtx:
    """Everything a handler needs and nothing it does not.

    ONE object threaded through ONE chokepoint, so retries, error envelopes,
    correlation ids and headers live in one place. Per the upstream rationale:
    if you need to add a header or a timeout you change one function, not
    seventeen call sites.
    """

    __slots__ = ("verb", "method", "path", "payload", "agent", "session_id", "surface")

    def __init__(
        self,
        verb: str,
        method: str,
        path: str,
        payload: dict,
        agent: str,
        session_id: Optional[str] = None,
        surface: str = "mcp",
    ) -> None:
        self.verb = verb
        self.method = method
        self.path = path
        self.payload = payload
        self.agent = agent
        self.session_id = session_id
        self.surface = surface

    def stamped(self) -> dict:
        """The payload with ``author_agent`` applied. Handlers write with THIS,
        never with the raw body — the caller must not get to name its author."""
        return identity.stamp(self.payload, self.agent)

    def __repr__(self) -> str:
        return (
            f"RequestCtx(verb={self.verb!r}, method={self.method!r}, "
            f"path={self.path!r}, agent={self.agent!r}, surface={self.surface!r})"
        )


Handler = Callable[[RequestCtx], Awaitable[dict]]
Builder = Callable[[dict], dict]


def ok(**fields: Any) -> dict:
    payload: Dict[str, Any] = {"ok": True}
    payload.update(fields)
    return payload


def err(code: str, message: str, **fields: Any) -> dict:
    payload: Dict[str, Any] = {"ok": False, "isError": True, "code": code, "error": message}
    payload.update(fields)
    return payload


def todo(ctx: RequestCtx, what: str) -> dict:
    """The HONEST not-yet-implemented envelope.

    Deliberately NOT a fake success and NOT a silent empty result. A stub that
    returns `{}` is indistinguishable from a real miss, and the whole demo
    thesis is "the graph actually remembers" — a plausible-looking empty answer
    is the single most expensive lie this codebase could tell. Every stub says
    so out loud, names what has to be built, and echoes back the resolved
    routing so the table/router/identity wiring is verifiable end-to-end TODAY.
    """
    return {
        "ok": False,
        "status": "not_implemented",
        "verb": ctx.verb,
        "todo": what,
        "routing": {
            "method": ctx.method,
            "path": ctx.path,
            "surface": ctx.surface,
        },
        "author_agent": ctx.agent,
        "payload_echo": ctx.payload,
    }


# ═══════════════════════════════════════════════════════════════════════════
# payload builders  —  EVERY ONE OF THESE NEEDS A SHAPE TEST (see header)
# ═══════════════════════════════════════════════════════════════════════════

def _passthrough(args: dict) -> dict:
    """The tool shape IS the handler shape. Only legal when that is TRUE."""
    return dict(args)


def _strip_keys(*keys: str) -> Builder:
    """Drop path-bound params from the body (they are already in the path)."""

    def build(args: dict) -> dict:
        return {k: v for k, v in args.items() if k not in keys}

    return build


def _build_handover_write(args: dict) -> dict:
    """Handover WRITE body. Ported from ``_handover_write_body``
    (unblock server.py:4674-4699): omit-undefined, and ALWAYS status='open'.

    A session may only ever write an OPEN handover; superseding is the reader's
    job, never the writer's. That invariant is what makes the status guard on
    the read side meaningful.
    """
    body: Dict[str, Any] = {"status": "open"}
    for key in (
        "agent_id",
        "to_agent",
        "role",
        "session_id",
        "summary",
        "in_flight",
        "next_steps",
        "blockers",
        "artifacts",
        "checkpoint",
        "graph",
    ):
        val = args.get(key)
        if val is not None:
            body[key] = val
    return body


def _build_ask(args: dict) -> dict:
    """Decision-card shape, lifted from unblock's AskOpts
    (``unblock_comms/src/client.ts:872-1030``,
    ``comms/nats_client.py:1318-1400``).

    IN : {question, options[], recommendation, timeout_sec, default}
    OUT: {answer, timed_out, question_id, responder}

    ``default`` IS THE SINGLE MOST IMPORTANT BORROWED FIELD IN THE DEMO. It is
    what stops the pitch freezing at second 60 when nobody taps approve. Never
    make it optional in practice — the builder defaults it explicitly.
    """
    body = {
        "question": args.get("question"),
        "options": args.get("options") or [],
        "recommendation": args.get("recommendation"),
        "timeout_sec": args.get("timeout_sec", 60.0),
        "default": args.get("default"),
        "intent": args.get("intent", "ASK"),
    }
    if body["default"] is None and body["options"]:
        # Fail SAFE, and say so in the payload so the UI can render "auto-chose
        # X on timeout" rather than pretending a human answered.
        body["default"] = body["options"][-1]
        body["default_source"] = "last_option_fallback"
    return body


# ═══════════════════════════════════════════════════════════════════════════
# MEMORY PLANE — live handlers against FalkorDB
# ═══════════════════════════════════════════════════════════════════════════
# Element 4 of the tuple. The stream/act verbs below are still honest `todo`
# envelopes (a separate lane); everything in THIS block talks to the database.
#
# ── THE UI-ENVELOPE TRAP (found reading app/web/index.html, not by guessing) ─
# The projector unwraps every response as
#
#     const root = payload?.result ?? payload?.data ?? payload?.graph ?? payload
#
# so a top-level key named `result`, `data` or **`graph`** is treated as THE
# PAYLOAD. Returning `{"ok": true, "nodes": [...], "graph": "palimpsest"}` makes
# `root` the STRING "palimpsest", `root.nodes` undefined, zero nodes parsed, and
# the UI silently falls back to its mock graph — a perfect false-green on stage.
# Every handler below therefore names the graph key `graph_key`, never `graph`.
#
# ── async over a sync client ────────────────────────────────────────────────
# The falkordb client is synchronous. Every call is pushed onto a worker thread
# via asyncio.to_thread so one slow query cannot stall the MCP/SSE event loop.

_RING_WINDOW_S_DEFAULT = 720          # 12 minutes — the synthesis ring beat
_RING_SCORE_THRESHOLD = 0.5           # above this, publish case.opened
_MAX_TS = 2 ** 53                     # "no upper bound" as a real number


def _graph_key(ctx: RequestCtx) -> str:
    """The graph this call reads/writes. Validated (allowlist pattern), never
    interpolated raw — the key arrives from a REST query string."""
    return graphstore.check_graph_key(ctx.payload.get("graph") or ctx.payload.get("graph_key"))


async def _ensure(graph_key: str) -> None:
    """Idempotent index bootstrap, on the first touch of a graph key.

    SCHEMA.md §3 calls for these at startup; doing it per-first-touch instead
    means a graph created mid-run (the ablation's cold graph, a test key) is
    never left without its HNSW index — which fails SILENTLY, as wrong
    neighbours rather than an error.
    """
    await asyncio.to_thread(graphstore.ensure_indexes, graph_key)


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, out))


# ─── remember ───────────────────────────────────────────────────────────────

async def _h_remember(ctx: RequestCtx) -> dict:
    """MERGE one typed node into the graph, stamped with the acting agent.

    * ``block_type`` goes through ``taxonomy.normalize_block_type`` — so
      ``'fix'`` becomes ``block_type='decision' + metadata.kind='fix'`` instead
      of silently degrading to ``'note'`` (SCHEMA.md trap 10).
    * ``author_agent`` is stamped SERVER-SIDE from the resolved identity. The
      request body never gets to name its own author.
    * ``embedding`` is an OPTIONAL PRECOMPUTED vector. This handler NEVER calls
      an embedding API — embeddings come from ``OPENAI_API_KEY``, which is the
      gate's/harness's business, and a network call on the live write path is
      exactly the thing that hangs on stage.
    * The id is a content hash by default, so calling remember twice with the
      same content is a genuine no-op MERGE rather than a duplicate node.
    """
    p = ctx.payload
    content = p.get("content")
    if content is None:
        return err("MISSING_CONTENT", "remember requires `content`")

    graph_key = _graph_key(ctx)
    block_type, metadata = normalize_block_type(p.get("block_type"), p.get("metadata"))
    labels = p.get("labels") or []
    label = graphstore.check_label(labels[0] if labels else None, "Claim")
    emb = graphstore.check_vector(p.get("embedding"))
    text = graphstore.flat_text(content)
    node_id = p.get("id") or graphstore.stable_id("blk", label, block_type, text)
    tags = [str(t) for t in (p.get("tags") or [])]

    await _ensure(graph_key)

    # ── sensing gate (OPT-IN) ───────────────────────────────────────────────
    # Ported threshold from unblock's sensing-gate.ts. Applied to the top-1
    # DISTANCE: BELOW the threshold means "too close to something we already
    # know" => skip. It is opt-in here because `remember` is also the primitive
    # the seed scripts and the eval harness use, and a gate that silently eats
    # a deliberate write is worse than no gate. realtime/gate.py owns the
    # always-on firehose path.
    if p.get("gate") and emb is not None:
        near = await asyncio.to_thread(_knn, graph_key, label, emb, 1)
        if near and near[0][1] < config.SALIENCE_THRESHOLD:
            return ok(
                skipped=True,
                reason="sensing gate: top-1 distance {0:.4f} < SALIENCE_THRESHOLD "
                "{1} — not novel".format(near[0][1], config.SALIENCE_THRESHOLD),
                nearest=graphstore.project_node(near[0][0]),
                distance=near[0][1],
                graph_key=graph_key,
            )

    sets = [
        "n.text = $text",
        "n.block_type = $block_type",
        "n.metadata_json = $metadata_json",
        "n.tags = $tags",
        "n.updated_ts = timestamp()",
        identity.author_clause("n"),
    ]
    params: Dict[str, Any] = {
        "id": node_id,
        "text": text,
        "block_type": block_type,
        "metadata_json": graphstore.encode_map(metadata),
        "tags": tags,
        identity.AUTHOR_PROP: ctx.agent,
    }
    if label == "Event":
        # SCHEMA.md §1: :Event carries `summary`, :Claim carries `text`.
        sets.append("n.summary = $text")
    if emb is not None:
        sets.append("n.emb = vecf32($emb)")
        params["emb"] = emb
    if p.get("ts") is not None:
        sets.append("n.ts = $ts")
        params["ts"] = p["ts"]

    cypher = (
        "MERGE (n:{label} {{id: $id}}) "
        "ON CREATE SET n.created_ts = timestamp() "
        "SET {sets} "
        "RETURN n"
    ).format(label=label, sets=", ".join(sets))

    rows, run_ms, stats = await asyncio.to_thread(
        graphstore.mutate, cypher, params, graph_key=graph_key
    )
    node = graphstore.project_node(rows[0][0]) if rows else None

    linked = None
    if p.get("case_id"):
        link = (
            "MATCH (n:{label} {{id: $id}}) "
            "MERGE (c:Case {{id: $case_id}}) "
            "ON CREATE SET c.status = 'open', c.opened_ts = timestamp() "
            "MERGE (c)-[r:IMPLICATES]->(n) SET r.why = $why "
            "RETURN c.id"
        ).format(label=label)
        await asyncio.to_thread(
            graphstore.mutate,
            link,
            {"id": node_id, "case_id": p["case_id"], "why": p.get("why") or "remember"},
            graph_key=graph_key,
        )
        linked = p["case_id"]

    return ok(
        id=node_id,
        node=node,
        label=label,
        block_type=block_type,
        metadata=metadata,
        created=bool(stats["nodes_created"]),
        embedded=emb is not None,
        case_id=linked,
        author_agent=ctx.agent,
        run_time_ms=run_ms,
        graph_key=graph_key,
    )


# ─── relate ─────────────────────────────────────────────────────────────────

async def _h_relate(ctx: RequestCtx) -> dict:
    """A GENUINELY DIRECTED [:RELATES] edge.

    The endpoints are written in the order the caller gave them and are NEVER
    canonicalized into id order. unblock's ``contradiction_edges`` carries
    ``CHECK (block_id_a < block_id_b)``, a Postgres dedup trick that makes
    "A supersedes B" and "B supersedes A" the same row. We dropped it, and
    ``taxonomy.is_directed()`` is returned on the envelope so the property is
    assertable by a test rather than promised by a comment.
    """
    p = ctx.payload
    from_id, to_id = p.get("from_id"), p.get("to_id")
    if not from_id or not to_id:
        return err("MISSING_ENDPOINT", "relate requires `from_id` and `to_id`")
    try:
        relation = check_relation(p.get("relation") or "")
    except ValueError as exc:
        return err("BAD_RELATION", str(exc), valid=list(RELATION_KINDS))

    graph_key = _graph_key(ctx)
    await _ensure(graph_key)

    # ── ACYCLICITY GUARD for supersedes (Bug: cycles + self-edges accepted) ──
    # We write (newer)-[:RELATES{relation:'supersedes'}]->(older) and resolve
    # "current truth" by walking INCOMING supersedes edges to a head (a node
    # with no incoming supersedes). A self-edge, or an edge that closes a
    # reachability cycle a→…→a, makes that walk undefined: EVERY node on the
    # loop has an incoming supersedes, so `_supersede_head` finds NO head and
    # recall reports superseded:false / head:null — a silent, wrong answer.
    # Reject at write time so the invariant "supersede lineage is a DAG" holds.
    # Only supersedes is direction-critical this way; the other relations are
    # free to form cycles (supports/references/… legitimately can).
    if relation == "supersedes":
        if from_id == to_id:
            return err(
                "SUPERSEDE_CYCLE",
                "a node cannot supersede itself ({0!r}); supersede lineage "
                "must be acyclic".format(from_id),
                from_id=from_id,
                to_id=to_id,
                relation=relation,
                graph_key=graph_key,
            )
        # Adding from→to closes a cycle iff `to` can ALREADY reach `from`
        # through existing supersedes edges — then from→to→…→from is a loop.
        cyc, _cyc_ms, _cyc_h = await asyncio.to_thread(
            graphstore.query,
            "MATCH path = (t {id: $to_id})-[:RELATES* {relation: 'supersedes'}]->"
            "(f {id: $from_id}) RETURN count(path) AS n",
            {"from_id": from_id, "to_id": to_id},
            graph_key=graph_key,
            read_only=True,
        )
        if cyc and cyc[0][0]:
            return err(
                "SUPERSEDE_CYCLE",
                "refusing '{0!r} supersedes {1!r}': {1!r} already supersedes "
                "{0!r} (directly or transitively), so this edge would close a "
                "cycle and leave the supersede chain without a head".format(
                    from_id, to_id
                ),
                from_id=from_id,
                to_id=to_id,
                relation=relation,
                graph_key=graph_key,
            )

    cypher = (
        "MATCH (a {id: $from_id}), (b {id: $to_id}) "
        "MERGE (a)-[r:RELATES {relation: $relation}]->(b) "
        "ON CREATE SET r.created_ts = timestamp() "
        "SET r.note = $note, r.updated_ts = timestamp(), " + identity.author_clause("r") + " "
        "RETURN a, r, b"
    )
    params = {
        "from_id": from_id,
        "to_id": to_id,
        "relation": relation,
        "note": p.get("note") or "",
        identity.AUTHOR_PROP: ctx.agent,
    }
    rows, run_ms, stats = await asyncio.to_thread(
        graphstore.mutate, cypher, params, graph_key=graph_key
    )
    if not rows:
        return err(
            "NODE_NOT_FOUND",
            "no node with id {0!r} and/or {1!r} in graph {2!r} — relate never "
            "creates its endpoints, so a typo cannot invent a node".format(
                from_id, to_id, graph_key
            ),
            from_id=from_id,
            to_id=to_id,
            graph_key=graph_key,
        )

    a, rel, b = rows[0][0], rows[0][1], rows[0][2]
    return ok(
        edge=graphstore.project_edge(rel),
        src=graphstore.project_node(a),
        dest=graphstore.project_node(b),
        relation=relation,
        directed=is_directed(relation),
        from_id=from_id,
        to_id=to_id,
        created=bool(stats["relationships_created"]),
        author_agent=ctx.agent,
        run_time_ms=run_ms,
        graph_key=graph_key,
    )


# ─── recall ─────────────────────────────────────────────────────────────────

def _knn(graph_key: str, label: str, vector: List[float], k: int) -> List[Tuple[Any, float]]:
    """Vector KNN entry. **ORDER BY score ASC** — the score is a DISTANCE.

    Re-verified against this container today: querying with the exact stored
    vector returns 0.0, its near neighbour 0.0061. Sorting DESC would hand the
    judges the WORST matches in the database, with no error and no warning.

    ``label`` and ``k`` are interpolated (both validated: label against the
    ten-label allowlist, k coerced to a bounded int) because this build's
    procedure signature is positional and rejects a map form.
    """
    cypher = (
        "CALL db.idx.vector.queryNodes('{label}', 'emb', {k}, vecf32($q)) "
        "YIELD node, score RETURN node, score ORDER BY score ASC"
    ).format(label=graphstore.check_label(label), k=int(k))
    try:
        rows, _ms, _h = graphstore.query(cypher, {"q": vector}, graph_key=graph_key)
    except graphstore.GraphUnavailable:
        # No vector index on that label yet, or no node carries `emb`. An empty
        # KNN is the honest answer; the property/fulltext lane still runs.
        return []
    return [(r[0], float(r[1])) for r in rows]


def _lexical(graph_key: str, text: str, labels: List[str], k: int) -> List[Tuple[Any, float]]:
    """Property/fulltext entry — the no-embedding lane.

    Fulltext first (a real index, ranked); on any rejection (no fulltext index
    on that label yet, or a query string the tokenizer refuses) it degrades to
    a CONTAINS scan, and `recall` reports WHICH lane answered in `mode` so a
    silent degradation is visible instead of invisible.
    """
    out: List[Tuple[Any, float]] = []
    fields = {"Claim": "text", "Event": "summary"}
    for label in labels:
        field = fields.get(label)
        if not field:
            continue
        try:
            rows, _ms, _h = graphstore.query(
                "CALL db.idx.fulltext.queryNodes('{label}', $q) YIELD node, score "
                "RETURN node, score ORDER BY score DESC LIMIT {k}".format(
                    label=graphstore.check_label(label), k=int(k)
                ),
                {"q": text},
                graph_key=graph_key,
            )
            out.extend((r[0], float(r[1])) for r in rows)
        except graphstore.GraphUnavailable:
            continue
    if out:
        return out[:k]

    for label in labels:
        field = fields.get(label, "text")
        rows, _ms, _h = graphstore.query(
            "MATCH (n:{label}) WHERE toLower(coalesce(n.{field}, '')) CONTAINS toLower($q) "
            "RETURN n, 0.0 LIMIT {k}".format(
                label=graphstore.check_label(label), field=field, k=int(k)
            ),
            {"q": text},
            graph_key=graph_key,
        )
        out.extend((r[0], 0.0) for r in rows)
    return out[:k]


def _supersede_head(graph_key: str, internal_id: int) -> Tuple[Optional[Any], List[Any], int]:
    """Resolve one seed to the HEAD of its supersede chain, plus the lineage.

    Direction matters and is the whole point: we write
    ``(newer)-[:RELATES {relation:'supersedes'}]->(older)``, so the CURRENT
    truth is reached by walking INCOMING supersedes edges until a node has
    none left. "You told me X, then Y — here is the current truth and the chain
    that got there." An undirected edge store cannot answer this at all.
    """
    # DETERMINISTIC head selection. A FORK — new_a→old←new_b — has two valid
    # heads at the SAME path length, so `ORDER BY length DESC LIMIT 1` alone
    # picks one ARBITRARILY (a different head across runs). We keep the longest
    # chain as the primary key, then break every tie with a STABLE key: newest
    # head first (created_ts, then ts), and finally the head's own id — a value
    # that is identical across repeated runs of the same seed, so the resolved
    # head is reproducible rather than nondeterministic.
    rows, _ms, _h = graphstore.query(
        "MATCH (s) WHERE id(s) = $sid "
        "MATCH path = (s)<-[:RELATES* {relation: 'supersedes'}]-(head) "
        "WHERE NOT (head)<-[:RELATES {relation: 'supersedes'}]-() "
        "RETURN head, nodes(path), length(path) "
        "ORDER BY length(path) DESC, "
        "         coalesce(head.created_ts, head.ts, 0) DESC, "
        "         coalesce(head.id, toString(id(head))) ASC "
        "LIMIT 1",
        {"sid": int(internal_id)},
        graph_key=graph_key,
    )
    if not rows:
        return None, [], 0
    return rows[0][0], list(rows[0][1] or []), int(rows[0][2] or 0)


async def _h_recall(ctx: RequestCtx) -> dict:
    """Hybrid retrieval: semantic entry -> symbolic expansion -> lineage.

    Three stages, one envelope:

      1. ENTRY — ``db.idx.vector.queryNodes`` when the caller supplies a
         precomputed ``embedding``; otherwise the property/fulltext lane.
      2. EXPANSION — one hop through shared ``:Entity`` nodes
         (``[:ABOUT|MENTIONS]``). This is the half a vector store cannot do:
         "semantically near" plus "structurally adjacent", one round trip.
      3. SUPERSEDE RESOLUTION — every hit is resolved to the HEAD of its
         supersede chain and served WITH the lineage, so a stale claim can
         never be returned as current. `superseded_by` names the winner.
    """
    p = ctx.payload
    text = p.get("text") or ""
    if not text and p.get("embedding") is None:
        return err("MISSING_QUERY", "recall requires `text` (or a precomputed `embedding`)")

    graph_key = _graph_key(ctx)
    top_k = _int(p.get("top_k"), 10, 1, 100)
    labels = [graphstore.check_label(x) for x in (p.get("labels") or ["Claim", "Event"])]
    emb = graphstore.check_vector(p.get("embedding"))
    await _ensure(graph_key)

    def _work() -> dict:
        seeds: List[Tuple[Any, float]] = []
        if emb is not None:
            for label in labels:
                seeds.extend(_knn(graph_key, label, emb, top_k))
            seeds.sort(key=lambda pair: pair[1])       # ASC. Always ASC.
            mode = "vector"
        else:
            seeds = _lexical(graph_key, text, labels, top_k)
            mode = "lexical"
        seeds = seeds[:top_k]

        seed_ids = [int(getattr(n, "id")) for n, _s in seeds]

        # (2) one-hop entity expansion over the seed set
        expanded: List[dict] = []
        if seed_ids:
            rows, _ms, _h = graphstore.query(
                "MATCH (s) WHERE id(s) IN $ids "
                "MATCH (s)-[:ABOUT|MENTIONS]->(e:Entity)<-[:ABOUT|MENTIONS]-(nb) "
                "WHERE id(nb) <> id(s) AND NOT id(nb) IN $ids "
                "RETURN DISTINCT nb, e, id(s) LIMIT $lim",
                {"ids": seed_ids, "lim": top_k * 4},
                graph_key=graph_key,
            )
            for nb, ent, via in rows:
                expanded.append(
                    {
                        "node": graphstore.project_node(nb),
                        "via_entity": graphstore.project_node(ent),
                        "via_seed_id": str(via),
                        "hop": 1,
                    }
                )

        # (3) supersede resolution — serve the chain HEAD + lineage
        results: List[dict] = []
        for node, score in seeds:
            nid = int(getattr(node, "id"))
            head, lineage, depth = _supersede_head(graph_key, nid)
            item = {
                "node": graphstore.project_node(node),
                "score": score,
                "distance": score if mode == "vector" else None,
                "superseded": head is not None,
                "supersede_depth": depth,
                "head": graphstore.project_node(head) if head is not None else None,
                "lineage": [graphstore.project_node(n) for n in lineage],
            }
            if head is not None:
                item["superseded_by"] = (head.properties or {}).get("id")
            results.append(item)
        return {"mode": mode, "results": results, "expanded": expanded}

    started = time.perf_counter()
    out = await asyncio.to_thread(_work)
    return ok(
        query=text,
        mode=out["mode"],
        top_k=top_k,
        labels=labels,
        count=len(out["results"]),
        results=out["results"],
        expanded=out["expanded"],
        elapsed_ms=round((time.perf_counter() - started) * 1000.0, 3),
        graph_key=graph_key,
    )


# ─── ring ───────────────────────────────────────────────────────────────────

#: The load-bearing query (plan/synthesis.json sponsor_integration.falkordb §1).
#: Three DISTINCT actors, two DISTINCT pages, four :EDITED edges inside one time
#: window. No single edit crosses any threshold; the SHAPE is the signal. A
#: vector store cannot express "similar to a four-step causal chain".
_RING_CYPHER = (
    "MATCH p=(a:Actor)-[e1:EDITED]->(x:Page)<-[e2:EDITED]-(b:Actor)"
    "-[e3:EDITED]->(y:Page)<-[e4:EDITED]-(c:Actor) "
    "WHERE a.name <> b.name AND b.name <> c.name AND a.name <> c.name "
    "AND x.title <> y.title "
    "{anchor}"
    "WITH p, a, b, c, x, y, [e1.ts, e2.ts, e3.ts, e4.ts] AS tss "
    "WITH p, a, b, c, x, y, tss, "
    "     reduce(mn = tss[0], t IN tss | CASE WHEN t < mn THEN t ELSE mn END) AS t_min, "
    "     reduce(mx = tss[0], t IN tss | CASE WHEN t > mx THEN t ELSE mx END) AS t_max "
    "WHERE (t_max - t_min) <= $window_s AND t_min >= $since AND t_max <= $until "
    "RETURN p, a.name, b.name, c.name, x.title, y.title, t_min, t_max, "
    "       (t_max - t_min) AS span_s "
    "ORDER BY span_s ASC LIMIT $limit"
)


async def _h_ring(ctx: RequestCtx) -> dict:
    """The 3-hop co-edit ring — the verdict the rest of the stack cannot express.

    Returns FalkorDB ``Path`` objects (projected to ``{nodes, edges, ids}``,
    which is what the projector animates) and ``run_time_ms`` taken from
    FalkorDB's OWN execution time, not a wall clock — that number goes on
    screen next to the verdict, so it has to be the database's.

    The window is the discriminator, and it is what makes the ablation honest:
    the SAME actors and pages with edits spread beyond ``window_s`` return zero
    rings. Cold does not fire.
    """
    p = ctx.payload
    graph_key = _graph_key(ctx)
    window_s = _int(p.get("window_s"), _RING_WINDOW_S_DEFAULT, 1, 86400 * 30)
    limit = _int(p.get("limit"), 50, 1, 500)
    since = p.get("since")
    until = p.get("until")
    anchor = p.get("anchor")

    await _ensure(graph_key)

    anchor_clause = ""
    params: Dict[str, Any] = {
        "window_s": window_s,
        "since": since if isinstance(since, (int, float)) else 0,
        "until": until if isinstance(until, (int, float)) else _MAX_TS,
        "limit": limit,
    }
    if anchor:
        anchor_clause = "AND (a.name = $anchor OR b.name = $anchor OR c.name = $anchor) "
        params["anchor"] = anchor

    cypher = _RING_CYPHER.format(anchor=anchor_clause)
    rows, run_ms, _h = await asyncio.to_thread(
        graphstore.query, cypher, params, graph_key=graph_key
    )

    # The traversal is symmetric: (A,B,C) and (C,B,A) are the same ring walked
    # backwards. Keep the first, drop the mirror — a doubled ring count would
    # inflate ring_score and open a case twice.
    rings: List[dict] = []
    paths: List[dict] = []
    seen = set()
    for path, a, b, c, x, y, t_min, t_max, span in rows:
        fingerprint = (tuple(sorted((a, b, c))), tuple(sorted((x, y))))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        rings.append(
            {
                "actors": [a, b, c],
                "pages": [x, y],
                "t_min": t_min,
                "t_max": t_max,
                "span_s": span,
                "closeness": round(1.0 - (float(span) / float(window_s)), 4),
            }
        )
        paths.append(graphstore.project_path(path))

    ring_score = round(max([r["closeness"] for r in rings] or [0.0]), 4)
    return ok(
        fired=bool(rings),
        ring_count=len(rings),
        ring_score=ring_score,
        should_open_case=ring_score >= _RING_SCORE_THRESHOLD and bool(rings),
        rings=rings,
        paths=paths,
        ids=paths[0]["ids"] if paths else [],
        window_s=window_s,
        anchor=anchor,
        run_time_ms=run_ms,
        graph_key=graph_key,
    )


# ─── ringleader (native graph algorithms a vector store cannot compute) ──────

#: Projects the bipartite ``(:Actor)-[:EDITED]->(:Page)<-[:EDITED]-(:Actor)``
#: co-edit structure into a weighted unipartite
#: ``(:Actor)-[:CO_EDITED_WITH]->(:Actor)`` graph — the substrate the native
#: FalkorDB graph algorithms run on. One canonical directed edge per unordered
#: pair (``a.name < b.name``); ``weight`` = the number of pages both actors
#: touched. Idempotent MERGE, so it is safe to re-run on every call, and it
#: NEVER touches the :EDITED edges the /ring verb reads — the ring beat is
#: unchanged, this is a purely additive derived layer.
_COEDIT_PROJECT = (
    "MATCH (a:Actor)-[:EDITED]->(pg:Page)<-[:EDITED]-(b:Actor) "
    "WHERE a.name < b.name "
    "WITH a, b, count(DISTINCT pg) AS shared "
    "MERGE (a)-[r:CO_EDITED_WITH]->(b) "
    "SET r.weight = shared"
)

#: PageRank NAMES the ringleader — the highest structural influence in the
#: co-edit graph. The positional form is CORRECT for pageRank (verified live);
#: the map-config requirement below is specific to WCC / labelPropagation /
#: betweenness, which silently return [] positionally.
_COEDIT_PAGERANK = (
    "CALL algo.pageRank('Actor', 'CO_EDITED_WITH') YIELD node, score "
    "RETURN node.name AS name, score ORDER BY score DESC LIMIT $k"
)
_COEDIT_PAGERANK_BASELINE = (
    "CALL algo.pageRank('Actor', 'CO_EDITED_WITH') YIELD node, score "
    "RETURN avg(score)"
)

#: WCC (Weakly Connected Components) AUTO-DISCOVERS the collusion cell: the
#: connected component the ringleader sits in. Unsupervised — nobody tells it
#: how many cells exist or who is in them — it recovers EXACTLY the ring and
#: leaves an unrelated co-edit pair (the bystander) in its own component.
#: MUST use the MAP-CONFIG form: the positional
#: ``algo.WCC('Actor','CO_EDITED_WITH')`` silently returns nothing (verified
#: live), the same trap that bites labelPropagation and betweenness.
_COEDIT_WCC = (
    "CALL algo.WCC({nodeLabels: ['Actor'], relationshipTypes: ['CO_EDITED_WITH']}) "
    "YIELD node, componentId "
    "RETURN node.name AS name, componentId"
)

#: Label propagation is the fuzzy-community refinement of the same map-config
#: form. It is included for transparency, NOT as the load-bearing detector: on a
#: star/hub cell it is unstable and fragments the hub actor out of its own
#: community (verified live — it excludes the ringleader), which is precisely why
#: WCC above is what the demo cites for the cell. Kept in map-config form
#: (positional returns []).
_COEDIT_LABELPROP = (
    "CALL algo.labelPropagation({nodeLabels: ['Actor'], relationshipTypes: ['CO_EDITED_WITH']}) "
    "YIELD node, communityId "
    "RETURN node.name AS name, communityId"
)


def _cells_from(rows: List[list], min_size: int = 2) -> List[dict]:
    """Group ``(name, component/community id)`` rows into non-trivial cells,
    largest first. A cell of one actor is not a cell — those are the isolated
    singletons the algorithm correctly leaves alone, and folding them in would
    bury the signal (the ring) under 150-odd bystanders."""
    grouped: Dict[Any, List[str]] = {}
    for name, cid in rows:
        grouped.setdefault(cid, []).append(name)
    cells = [
        {"id": cid, "members": sorted(members), "size": len(members)}
        for cid, members in grouped.items()
        if len(members) >= min_size
    ]
    cells.sort(key=lambda c: c["size"], reverse=True)
    return cells


async def _h_ringleader(ctx: RequestCtx) -> dict:
    """NAME the ringleader and AUTO-DISCOVER the collusion cell — pure in-engine
    graph topology, no LLM, no key, no vector store.

    The /ring verb proves a co-edit ring EXISTS. This verb answers the two
    questions a vector store fundamentally cannot: WHO is the ringleader, and
    WHICH actors form the cell — both computed by FalkorDB's own compiled
    ``algo.*`` procedures over a co-edit projection:

    1. Project ``(:Actor)-[:EDITED]->(:Page)<-[:EDITED]-(:Actor)`` into a
       weighted ``(:Actor)-[:CO_EDITED_WITH]->(:Actor)`` graph (idempotent).
    2. ``algo.pageRank`` -> the RINGLEADER (highest structural influence).
    3. ``algo.WCC`` (map-config) -> the CELL: the ringleader's connected
       component, recovered unsupervised, with unrelated pairs left out.
    4. ``algo.labelPropagation`` (map-config) -> the fuzzy-community view, for
       transparency (unstable on a hub cell; WCC is load-bearing).

    ``run_time_ms`` is the SUM of FalkorDB's OWN measured execution times across
    every phase — the database's number, not a wall clock — so it can sit on
    screen next to the ring verb's own run_time_ms.
    """
    p = ctx.payload
    graph_key = _graph_key(ctx)
    k = _int(p.get("k"), 5, 1, 100)

    await _ensure(graph_key)

    # 1. Project the co-edit graph the algorithms run on (idempotent MERGE).
    _proj_rows, proj_ms, proj_stats = await asyncio.to_thread(
        graphstore.mutate, _COEDIT_PROJECT, graph_key=graph_key
    )

    # 2. PageRank NAMES the ringleader (+ a baseline avg for the "Nx baseline").
    pr_rows, pr_ms, _h1 = await asyncio.to_thread(
        graphstore.query, _COEDIT_PAGERANK, {"k": k}, graph_key=graph_key, read_only=True
    )
    base_rows, base_ms, _h2 = await asyncio.to_thread(
        graphstore.query, _COEDIT_PAGERANK_BASELINE, graph_key=graph_key, read_only=True
    )
    baseline = float(base_rows[0][0]) if base_rows and base_rows[0][0] is not None else 0.0
    influence_ranking = [
        {"name": name, "score": round(float(score), 6)} for name, score in pr_rows
    ]
    ringleader: Optional[dict] = None
    if influence_ranking:
        top = influence_ranking[0]
        ringleader = {
            "name": top["name"],
            "score": top["score"],
            "score_vs_baseline": round(top["score"] / baseline, 2) if baseline else None,
        }

    # 3. WCC AUTO-DISCOVERS the cell (MAP-CONFIG form — positional is silent).
    wcc_rows, wcc_ms, _h3 = await asyncio.to_thread(
        graphstore.query, _COEDIT_WCC, graph_key=graph_key, read_only=True
    )
    communities = _cells_from(wcc_rows)
    largest_cell = communities[0] if communities else None
    if ringleader is not None:
        name = ringleader["name"]
        ringleader["cell"] = next(
            (c["members"] for c in communities if name in c["members"]), []
        )

    # 4. Label propagation — the fuzzy-community refinement, for transparency.
    lp_rows, lp_ms, _h4 = await asyncio.to_thread(
        graphstore.query, _COEDIT_LABELPROP, graph_key=graph_key, read_only=True
    )
    label_propagation = _cells_from(lp_rows)

    run_time_ms = round(proj_ms + pr_ms + base_ms + wcc_ms + lp_ms, 6)
    return ok(
        ringleader=ringleader,
        influence_ranking=influence_ranking,
        communities=communities,
        cell_count=len(communities),
        largest_cell=largest_cell,
        label_propagation=label_propagation,
        coedit_edges_created=int(proj_stats.get("relationships_created", 0)),
        method={
            "ringleader": "algo.pageRank",
            "cell": "algo.WCC (map-config)",
            "refinement": "algo.labelPropagation (map-config)",
        },
        run_time_ms=run_time_ms,
        timings={
            "projection": round(proj_ms, 6),
            "pageRank": round(pr_ms + base_ms, 6),
            "wcc": round(wcc_ms, 6),
            "labelPropagation": round(lp_ms, 6),
        },
        graph_key=graph_key,
    )


# ─── ablation (the opposite-verdict proof) ───────────────────────────────────

async def _h_ablation(ctx: RequestCtx) -> dict:
    """THE REAL ABLATION — same new event, two differently-seeded graphs,
    OPPOSITE mechanical verdict (GOAL victory condition 1, NEVER cut).

    Runs ``realtime.ablation.run_ablation``: seeds a WARM graph with the full
    historical co-edit corpus + one new event, and a COLD graph with ONLY that
    new event, then runs the SAME ``_h_ring`` verdict against both. WARM fires
    (escalate); COLD does not (dismiss). Topology-only — no LLM, no key, pure
    Cypher — so this is safe to call live from the UI/demo.

    Distinct from ``stream_replay`` (which proves REPRODUCIBILITY: replay-from-0
    re-derives the identical graph). Reproducibility is graph EQUALITY; the
    ablation is the opposite verdict. Both are real; they are different claims.

    Uses dedicated, NON-DESTRUCTIVE keys (``palimpsest_ablation_warm`` /
    ``palimpsest_ablation_cold``) so the live demo graphs are never touched.
    """
    from realtime import ablation  # lazy: keeps server.py importable without the realtime deps hot

    p = ctx.payload
    window_s = _int(p.get("window_s"), _RING_WINDOW_S_DEFAULT, 1, 86400 * 30)
    res = await ablation.run_ablation(window_s=window_s)
    res.setdefault("author_agent", ctx.agent)
    return res


# ─── graph (the UI projection) ──────────────────────────────────────────────

async def _h_graph(ctx: RequestCtx) -> dict:
    """The nodes+edges snapshot the projector renders.

    THE UI WAS BUILT FIRST AND THIS CONFORMS TO IT — the contract below was
    read out of ``app/web/index.html`` (``normalizeGraph``), not designed here:

        node  -> {id, labels: [...], properties: {...}}
                 id is the FalkorDB INTERNAL node id as a string, because
                 Edge.src_node/.dest_node are internal ids and the UI drops any
                 edge whose endpoints are not in the node id set.
                 properties.snippet is synthesised: the UI's label chain is
                 name/title/display_name/snippet/content/summary and our
                 :Claim's own field is `text`, which is NOT in that chain.
        edge  -> {relation, src, dest}   (its first-choice keys)
        meta  -> {node_count, edge_count, capped, real_edges, derived_edges}
                 — the same five fields as fetchBrainGraph's BrainGraph.meta
                 (roster-graph.ts), with real = the typed [:RELATES] vocabulary
                 and derived = the structural edges.

    ``emb`` is stripped from every projected node: 256 floats per node would be
    ~90% of the payload and render as nothing.
    """
    p = ctx.payload
    graph_key = _graph_key(ctx)
    limit = _int(p.get("limit"), 500, 1, 5000)
    since = p.get("since")
    await _ensure(graph_key)

    def _work() -> dict:
        where = ""
        params: Dict[str, Any] = {"lim": limit + 1}
        if isinstance(since, (int, float)):
            where = "WHERE coalesce(n.updated_ts, n.ts, n.created_ts, 0) >= $since "
            params["since"] = since

        rows, run_ms, _h = graphstore.query(
            "MATCH (n) {where}RETURN n "
            "ORDER BY coalesce(n.updated_ts, n.ts, n.created_ts, 0) DESC "
            "LIMIT $lim".format(where=where),
            params,
            graph_key=graph_key,
        )
        capped = len(rows) > limit
        kept = rows[:limit]
        nodes = [graphstore.project_node(r[0]) for r in kept]
        ids = [int(getattr(r[0], "id")) for r in kept]

        edges: List[dict] = []
        if ids:
            erows, _ms, _hh = graphstore.query(
                "MATCH (a)-[e]->(b) WHERE id(a) IN $ids AND id(b) IN $ids RETURN e",
                {"ids": ids},
                graph_key=graph_key,
            )
            edges = [graphstore.project_edge(r[0]) for r in erows]

        total, _ms2, _h2 = graphstore.query(
            "MATCH (n) RETURN count(n)", {}, graph_key=graph_key
        )
        return {
            "nodes": nodes,
            "edges": edges,
            "capped": capped,
            "total": int(total[0][0]) if total else len(nodes),
            "run_time_ms": run_ms,
        }

    out = await asyncio.to_thread(_work)
    nodes, edges = out["nodes"], out["edges"]
    real = sum(1 for e in edges if e["type"] == "RELATES")

    return ok(
        nodes=nodes,
        edges=edges,
        meta={
            "node_count": len(nodes),
            "edge_count": len(edges),
            "capped": out["capped"],
            "real_edges": real,
            "derived_edges": len(edges) - real,
            "total_nodes": out["total"],
            "run_time_ms": out["run_time_ms"],
            "graph_key": graph_key,
        },
        contributors=graphstore.contributors_of(nodes),
        agents=identity.bindings(),
        graph_key=graph_key,
    )


def _topic_from_path(ctx: RequestCtx, *, tail: bool) -> Optional[str]:
    """The {topic} path segment, resolved off the RESOLVED path (the builder
    strips `topic` from the body into the path). Falls back to the payload for
    a direct handler call."""
    parts = ctx.path.split("/")
    if tail and len(parts) >= 2:
        return parts[-2]  # /v1/stream/<topic>/tail  |  /replay
    if not tail and parts:
        return parts[-1]  # /v1/stream/<topic>
    return ctx.payload.get("topic")


async def _h_stream_publish(ctx: RequestCtx) -> dict:
    """Append one durable record to a topic. LIVE against the laser log spine
    (Log primitive only; producer.init() is handled inside realtime.laser_io).
    """
    from . import stream as stream_mod  # lazy: keeps server.py py3.9-importable

    topic = _topic_from_path(ctx, tail=False)
    payload = ctx.payload.get("payload")
    if payload is None:
        return err("MISSING_PAYLOAD", "stream_publish requires `payload`")
    res = await stream_mod.publish(topic, payload, key=ctx.payload.get("key"))
    res.setdefault("author_agent", ctx.agent)
    return res


async def _h_stream_tail(ctx: RequestCtx) -> dict:
    """The ONE stub with a rendered consumer, so it gets a rendered SHAPE.

    Still an honest not-implemented envelope (``ok:false``,
    ``status:'not_implemented'``, and it never invents a record), but it
    additionally carries the three fields the projector's stream strip reads —
    ``events: []`` / ``offset: null`` / ``stub: true``.

    THE REASON IS THE UI, NOT POLITENESS. ``app/web/index.html``'s
    ``pollBridge`` calls /graph, /stream_tail and /ring through
    ``Promise.allSettled``, and ``fetchJson`` THROWS on any non-2xx. A REJECTED
    /stream_tail leaves the graph live but the strip on a connection error; an
    EMPTY-BUT-DECLARED tail is parsed by ``flattenRecords`` (which checks
    ``records``/``events``/``items``/``messages`` in that order), yields zero
    records, and leaves the strip untouched while the graph renders LIVE. The
    difference between those two is the difference between a stubbed strip and
    a MOCK badge on stage.
    """
    # NOW LIVE (stream lane shipped): real records + real offsets off the log,
    # with the honest degraded envelope preserved. When the laser spine is
    # unreachable, `stream.tail` returns ok:true + declared-empty records/offset,
    # so the projector's stream strip stays live instead of throwing to the mock
    # graph. Consumer invariants (enqueue-before-ack; dedup on STREAM SEQUENCE
    # not message_id; backpressure; cancellation-safe teardown) live in
    # realtime/consumer.py; the bounded-tail read is documented in stream.tail.
    from . import stream as stream_mod  # lazy: keeps server.py py3.9-importable

    topic = _topic_from_path(ctx, tail=True)
    limit = _int(ctx.payload.get("limit"), 25, 1, 500)
    since_offset = ctx.payload.get("since_offset")
    res = await stream_mod.tail(topic, limit=limit, since_offset=since_offset)
    res.setdefault("author_agent", ctx.agent)
    return res


async def _h_stream_replay(ctx: RequestCtx) -> dict:
    """THE REWIND — replay a topic from offset 0 and re-derive the graph into a
    target key. Log primitive only. NEVER cut (GOAL victory condition 1).

    ``target_graph`` re-derives the whole graph from the durable log (the A/B);
    omit it to fetch the replayed records. Same code path a
    ``--source file://demo/seed_replay.ndjson`` producer run feeds, so conference
    wifi is never on the critical path.
    """
    from . import stream as stream_mod  # lazy

    topic = _topic_from_path(ctx, tail=True)
    from_offset = _int(ctx.payload.get("from_offset"), 0, 0, _MAX_TS)
    target_graph = ctx.payload.get("target_graph")
    res = await stream_mod.stream_replay(
        topic,
        from_offset=from_offset,
        target_graph=target_graph,
        embedder_kind=ctx.payload.get("embedder_kind", "test"),
        use_gate=bool(ctx.payload.get("use_gate", True)),
    )
    res.setdefault("author_agent", ctx.agent)
    return res


async def _h_act(ctx: RequestCtx) -> dict:
    return todo(
        ctx,
        "ACTION LANE. Fire the real side effect (Discord webhook + GitHub "
        "issue) and write it BACK as (:Action)-[:RELATES "
        "{relation:'derived_from'}]->(:Event) so every action has clickable "
        "provenance. LaserData delivery is AT-LEAST-ONCE, so this MUST be "
        "idempotent or fenced — an action_key + compare-and-swap, not a bare "
        "POST. Then publish action.executed.",
    )


# ═══════════════════════════════════════════════════════════════════════════
# CONTINUITY — the cold-resume path, with BOTH inherited traps closed
# ═══════════════════════════════════════════════════════════════════════════

#: Edge properties that are dict-shaped and therefore stored JSON-encoded
#: (FalkorDB has no map property type).
_HANDOVER_MAPS = ("artifacts", "checkpoint")

#: Flat string properties carried on the edge.
_HANDOVER_FLAT = ("summary", "in_flight", "next_steps", "blockers", "role", "session_id")


def _handover_row(from_agent: Any, to_agent: Any, edge: Any) -> dict:
    """One [:HANDED_OFF_TO] edge -> the row shape the reader guards."""
    props = dict(getattr(edge, "properties", None) or {})
    row: Dict[str, Any] = {
        "handover_id": props.get("handover_id"),
        "status": props.get("status"),
        "from_agent": from_agent,
        "agent_id": to_agent,
        "updated_at": props.get("updated_ts"),
        "created_at": props.get("created_ts"),
    }
    for key in _HANDOVER_FLAT:
        row[key] = props.get(key)
    for key in _HANDOVER_MAPS:
        row[key] = graphstore.decode_map(props.get(key + "_json"))
    return row


async def _h_handover_write(ctx: RequestCtx) -> dict:
    """Write an OPEN handover edge, superseding the recipient's prior open rows.

    Invariants (SCHEMA.md §7):
      * status is ALWAYS ``'open'`` on write — ``_build_handover_write``
        hard-codes it and this handler never reads a caller-supplied status.
        Superseding is the WRITER's transaction job, never a caller's choice;
        that is what makes the reader's status guard meaningful.
      * the supersede and the insert happen in ONE Cypher statement (verified
        on this build: ``OPTIONAL MATCH ... SET`` over zero rows is a no-op,
        and ``WITH DISTINCT`` stops the optional match multiplying the CREATE).
      * ``checkpoint`` carries the COMMITTED LaserData offset. That is what
        makes cold-resume exact rather than approximate.
    """
    p = ctx.payload
    agent_id = p.get("agent_id")
    if not agent_id:
        return err("MISSING_AGENT_ID", "handover_write requires `agent_id`")

    graph_key = _graph_key(ctx)
    # Default is a SELF-handoff: this session hands to the next session of the
    # same agent. `to_agent` makes it a genuine delegation to a peer.
    to_agent = p.get("to_agent") or agent_id
    from_agent = agent_id
    await _ensure(graph_key)

    handover_id = graphstore.stable_id("hov", from_agent, to_agent, time.time_ns())
    params: Dict[str, Any] = {
        "from_agent": from_agent,
        "to_agent": to_agent,
        "handover_id": handover_id,
        identity.AUTHOR_PROP: ctx.agent,
    }
    sets = [
        "h.handover_id = $handover_id",
        "h.status = '{0}'".format(HANDOVER_OPEN),
        "h.created_ts = timestamp()",
        "h.updated_ts = timestamp()",
        identity.author_clause("h"),
    ]
    for key in _HANDOVER_FLAT:
        params[key] = p.get(key) or ""
        sets.append("h.{0} = ${0}".format(key))
    for key in _HANDOVER_MAPS:
        params[key + "_json"] = graphstore.encode_map(p.get(key))
        sets.append("h.{0}_json = ${0}_json".format(key))

    cypher = (
        "MERGE (a:Agent {agent_id: $from_agent}) "
        "ON CREATE SET a.created_ts = timestamp(), " + identity.author_clause("a") + " "
        "MERGE (b:Agent {agent_id: $to_agent}) "
        "ON CREATE SET b.created_ts = timestamp(), " + identity.author_clause("b") + " "
        "WITH a, b "
        # supersede every prior OPEN row into the recipient, in this same query
        "OPTIONAL MATCH (:Agent)-[old:HANDED_OFF_TO {status: 'open'}]->(b) "
        "SET old.status = 'superseded', old.superseded_ts = timestamp() "
        "WITH DISTINCT a, b "
        "CREATE (a)-[h:HANDED_OFF_TO]->(b) "
        "SET " + ", ".join(sets) + " "
        "RETURN a.agent_id, b.agent_id, h"
    )
    rows, run_ms, _stats = await asyncio.to_thread(
        graphstore.mutate, cypher, params, graph_key=graph_key
    )
    if not rows:
        return err("HANDOVER_WRITE_FAILED", "the handover edge was not created")

    row = _handover_row(rows[0][0], rows[0][1], rows[0][2])
    return ok(
        handover_id=handover_id,
        handover=row,
        status=HANDOVER_OPEN,
        from_agent=from_agent,
        agent_id=to_agent,
        author_agent=ctx.agent,
        run_time_ms=run_ms,
        graph_key=graph_key,
    )


async def _h_handover_read(ctx: RequestCtx) -> dict:
    """Read an agent's OPEN handover. BOTH inherited traps are closed HERE.

    ── TRAP (a): UNWRAP THE ENVELOPE EXPLICITLY ────────────────────────────
    The by-agent read produces ``{"handovers": [row?]}`` — an envelope with
    zero or one rows. ``if parsed:`` treats a GENUINE MISS AS A HIT, because
    ``{"handovers": []}`` is itself a truthy dict. The unwrap below is the
    fix from unblock server.py:4608-4672, kept verbatim in shape.

    ── TRAP (b): STATUS-GUARD ON status == 'open' ──────────────────────────
    The by-agent query deliberately carries NO status filter (upstream's does
    not either — only the all-agents path filters). Without the Python guard a
    cold-resume RESURRECTS a superseded handover and the agent confidently
    redoes finished work. The guard is what makes the query's missing filter
    safe; removing either one re-opens the bug.
    """
    p = ctx.payload
    graph_key = _graph_key(ctx)
    want_all = bool(p.get("all"))
    await _ensure(graph_key)

    if want_all:
        # The ALL path filters status IN THE QUERY (upstream parity): latest
        # OPEN row per agent, the whole team's board.
        rows, run_ms, _h = await asyncio.to_thread(
            graphstore.query,
            "MATCH (a:Agent)-[h:HANDED_OFF_TO {status: '" + HANDOVER_OPEN + "'}]->(b:Agent) "
            "RETURN a.agent_id, b.agent_id, h ORDER BY h.updated_ts DESC",
            {},
            graph_key=graph_key,
        )
        best: Dict[str, dict] = {}
        for a, b, edge in rows:
            if b not in best:
                best[b] = _handover_row(a, b, edge)
        return ok(
            handovers=list(best.values()),
            count=len(best),
            all=True,
            run_time_ms=run_ms,
            graph_key=graph_key,
        )

    agent_id = p.get("agent_id")
    if not agent_id:
        return err("MISSING_AGENT_ID", "handover_read requires `agent_id` (or all=true)")

    # NOTE THE ABSENCE OF A STATUS FILTER. It is deliberate and it is the trap.
    rows, run_ms, _h = await asyncio.to_thread(
        graphstore.query,
        "MATCH (a:Agent)-[h:HANDED_OFF_TO]->(b:Agent {agent_id: $agent_id}) "
        "RETURN a.agent_id, b.agent_id, h ORDER BY h.updated_ts DESC LIMIT 1",
        {"agent_id": agent_id},
        graph_key=graph_key,
        read_only=True,
    )
    parsed: Dict[str, Any] = {
        "handovers": [_handover_row(a, b, edge) for a, b, edge in rows]
    }

    # ── TRAP (a) ────────────────────────────────────────────────────────────
    handover_rows = parsed.get("handovers") if isinstance(parsed, dict) else None
    row = None
    if isinstance(handover_rows, list) and handover_rows:
        row = handover_rows[0]
    if row is None:
        return ok(
            handover=None,
            reason="no handover row for agent {0!r} in graph {1!r}".format(agent_id, graph_key),
            agent_id=agent_id,
            run_time_ms=run_ms,
            graph_key=graph_key,
        )

    # ── TRAP (b) ────────────────────────────────────────────────────────────
    if isinstance(row, dict) and row.get("status") == HANDOVER_OPEN:
        return ok(
            handover=row,
            agent_id=agent_id,
            run_time_ms=run_ms,
            graph_key=graph_key,
        )
    return ok(
        handover=None,
        reason="latest row is not open (status={0!r})".format(row.get("status")),
        agent_id=agent_id,
        rejected_handover_id=row.get("handover_id"),
        run_time_ms=run_ms,
        graph_key=graph_key,
    )


async def _h_ask(ctx: RequestCtx) -> dict:
    """The decision card — SHAPE ONLY, no Guild/NATS wiring yet.

    This returns the card the projector and an MCP host render; it does NOT
    block on a human, because the responder plane (Guild ui_prompt / the NATS
    reply subject) is a different lane. It is a STUB and says so on the
    envelope (`pending=True`, `answer=None`) — never a fabricated approval.

    WHEN THE RESPONDER PLANE LANDS, port the ordering from unblock's ask()
    (comms/nats_client.py:1318-1400): SUBSCRIBE THE REPLY SUBJECT FIRST, then
    publish the question, THEN race the deadline. A fast responder that answers
    before the listener exists loses the answer entirely.

    TODO (plan/research/mcp-widgets-guide.md §2.6, tier 2 — the elicitation
    branch): in a terminal host that declares ``elicitation: {}`` (Claude Code
    does; verified in-process on mcp 1.29), this same verb must call
    ``await ctx.elicit(message=..., schema=Approval)`` with a primitives-only
    pydantic model and map ``r.action == 'accept'`` to the answer, falling back
    to this text/card payload otherwise. One verb carries BOTH ``_meta.ui`` and
    the elicitation branch — no fork — because no host today declares both MCP
    Apps and elicitation. Tier 1 (a meaningful ``content:[{type:'text'}]``) is
    already satisfied by this envelope; tier 3 (``ctx.report_progress``) belongs
    to stream_tail, not here.
    """
    p = ctx.payload
    timeout_sec = p.get("timeout_sec", 60.0)
    card = {
        "question": p.get("question"),
        "options": p.get("options") or [],
        "recommendation": p.get("recommendation"),
        # BOTH spellings on purpose: `timeoutSec` is unblock's AskOpts field
        # name (the shape this was lifted from and the one the card renderer
        # reads); `timeout_sec` is the snake_case wire field the builder emits.
        "timeoutSec": timeout_sec,
        "timeout_sec": timeout_sec,
        "default": p.get("default"),
        "intent": p.get("intent", "ASK"),
    }
    if p.get("default_source"):
        card["default_source"] = p["default_source"]
    return ok(
        status="stub",
        pending=True,
        card=card,
        question_id=graphstore.stable_id("ask", card["question"], time.time_ns()),
        answer=None,
        timed_out=False,
        responder=None,
        note=(
            "decision-card SHAPE only — no responder plane wired. This verb "
            "never fabricates an approval; `act` must refuse a stub approval_id."
        ),
        author_agent=ctx.agent,
        **{k: v for k, v in card.items() if k in ("question", "options", "recommendation", "default")}
    )


# ═══════════════════════════════════════════════════════════════════════════
# THE TABLE
# ═══════════════════════════════════════════════════════════════════════════
# verb -> (HTTP METHOD, PATH template, payload_builder, handler coroutine)
#
# For GET/DELETE the built body is dropped on the wire and becomes the query
# string instead (see `dispatch`), exactly as upstream. Path params are
# substituted from the raw arguments BEFORE the builder strips them.

_VERB_DISPATCH: Dict[str, Tuple[str, str, Builder, Handler]] = {
    # ── memory ──────────────────────────────────────────────────────────────
    "remember": ("POST", "/v1/remember", _passthrough, _h_remember),
    "relate": ("POST", "/v1/relate", _passthrough, _h_relate),
    "recall": ("POST", "/v1/recall", _passthrough, _h_recall),
    "ring": ("POST", "/v1/ring", _passthrough, _h_ring),
    "ringleader": ("POST", "/v1/ringleader", _passthrough, _h_ringleader),
    "ablation": ("GET", "/v1/ablation", _passthrough, _h_ablation),
    "graph": ("GET", "/v1/graph", _passthrough, _h_graph),
    # ── stream ──────────────────────────────────────────────────────────────
    "stream_publish": ("POST", "/v1/stream/{topic}", _strip_keys("topic"), _h_stream_publish),
    "stream_tail": ("GET", "/v1/stream/{topic}/tail", _strip_keys("topic"), _h_stream_tail),
    "stream_replay": ("POST", "/v1/stream/{topic}/replay", _strip_keys("topic"), _h_stream_replay),
    # ── motion ──────────────────────────────────────────────────────────────
    "act": ("POST", "/v1/act", _passthrough, _h_act),
    # ── continuity ──────────────────────────────────────────────────────────
    "handover_write": ("POST", "/v1/handover", _build_handover_write, _h_handover_write),
    "handover_read": ("GET", "/v1/handover", _passthrough, _h_handover_read),
    # ── human ───────────────────────────────────────────────────────────────
    "ask": ("POST", "/v1/ask", _build_ask, _h_ask),
}

VERBS: Tuple[str, ...] = tuple(_VERB_DISPATCH)


def tool_name(verb: str) -> str:
    return f"{TOOL_PREFIX}{verb}"


def verb_of(name: str) -> str:
    """MCP tool name -> table key. Accepts the bare verb too, so the CLI and
    the MCP surface can share the router without a second lookup path."""
    return name[len(TOOL_PREFIX):] if name.startswith(TOOL_PREFIX) else name


# ═══════════════════════════════════════════════════════════════════════════
# Tool schemas  (surface b + the source of surface c)
# ═══════════════════════════════════════════════════════════════════════════
# Descriptions carry the taxonomy guidance and failure modes INLINE — upstream
# does the same, and it is why unblock's agents pick the right verb without a
# system prompt saying so.

_ID = {"type": "string", "minLength": 1, "maxLength": 256}
_TOPIC = {"type": "string", "enum": list(config.TOPICS)}

#: Which graph key a memory verb reads/writes. The cold graph is deliberately
#: empty and exists so the ablation is a real A/B, not a slide.
_GRAPH = {
    "type": "string",
    "enum": [config.GRAPH_WARM, config.GRAPH_COLD],
    "default": config.GRAPH_WARM,
    "description": (
        "Which graph to read/write. The cold graph is deliberately empty and "
        "exists for the ablation beat."
    ),
}

#: A PRECOMPUTED embedding. The bridge never calls an embedding API — there is
#: no LLM and no network on the live write path. Width is enforced against
#: config.EMBED_DIM because a mismatch corrupts the HNSW index SILENTLY.
_EMBEDDING = {
    "type": "array",
    "items": {"type": "number"},
    "minItems": config.EMBED_DIM,
    "maxItems": config.EMBED_DIM,
    "description": (
        "Optional precomputed embedding, exactly {0} floats (config.EMBED_DIM). "
        "The bridge NEVER calls an embedding API; supply the vector or accept "
        "the property/fulltext lane. A width mismatch is refused, because it "
        "corrupts the vector index with no error at all."
    ).format(config.EMBED_DIM),
}


# ─── MCP-Apps widget metadata (surface b+) ──────────────────────────
# The ENTIRE widget delta on the tool surface: attach ``_meta.ui`` to exactly
# the verbs that declare a UiSpec (``widget_apps.UI_SPECS``); every other tool
# is untouched. REST/OpenAPI/CLI never read ``.meta``, so this moves ONE
# surface. ``model_copy(update=...)`` sets the field AND marks it in
# ``__pydantic_fields_set__`` so it survives serialization when nested in a
# ListToolsResult/ServerResult (a plain attribute assignment is dropped there);
# the stand-in Tool (bare interpreter, no ``model_copy``) falls back to a plain
# assignment, which is all the non-MCP surfaces ever need.
def _attach_widget_meta(tools: List[Tool]) -> List[Tool]:
    out: List[Tool] = []
    for tool in tools:
        spec = widget_apps.UI_SPECS.get(verb_of(tool.name))
        if spec is None:
            out.append(tool)
            continue
        meta = widget_apps.tool_ui_meta(spec)
        try:
            out.append(tool.model_copy(update={"meta": meta}))
        except AttributeError:  # structural stand-in without model_copy
            tool.meta = meta
            out.append(tool)
    return out


def _tools() -> List[Tool]:
    tools = [
        Tool(
            name=tool_name("remember"),
            description=(
                "Capture a discrete block of knowledge — a decision, a claim, "
                "an observation — into the attributed memory graph. Returns a "
                "stable id you can cite later from relate/recall/ring. This is "
                "the core write verb. It is CHEAP to call: the sensing gate "
                "drops non-novel writes for you, so capture first and ask "
                "questions never."
            ),
            inputSchema={
                "type": "object",
                "required": ["content"],
                "properties": {
                    "content": {
                        "description": "Any JSON-serializable payload. String for prose, object for structured data.",
                    },
                    "block_type": {
                        "type": "string",
                        "enum": list(BLOCK_TYPES),
                        "default": "note",
                        "description": (
                            "In-taxonomy block type. 'fix' and 'learning' are "
                            "NOT block_types — capture a bug fix as "
                            "block_type='decision' with metadata.kind='fix', "
                            "and a learning as block_type='note' with "
                            "metadata.kind='learning'."
                        ),
                    },
                    "labels": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(NODE_LABELS)},
                        "description": (
                            "Node labels; the FIRST is the node's label. "
                            "Defaults to ['Claim']; use ['Event'] for an "
                            "observed edit off the log."
                        ),
                    },
                    "tags": {"type": "array", "items": {"type": "string", "maxLength": 64}, "maxItems": 32},
                    "metadata": {"type": "object", "additionalProperties": True},
                    "case_id": {"type": "string", "description": "Attach this write to an open case."},
                    "id": dict(
                        _ID,
                        description=(
                            "Explicit node id. Omit and one is derived from a "
                            "content hash, which makes remember idempotent."
                        ),
                    ),
                    "embedding": _EMBEDDING,
                    "ts": {"type": "number", "description": "Observation timestamp (epoch seconds)."},
                    "gate": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Run the sensing gate: with an embedding supplied, "
                            "skip the write when the top-1 DISTANCE is below "
                            "SALIENCE_THRESHOLD (too close to something we "
                            "already know)."
                        ),
                    },
                    "why": {"type": "string", "description": "Reason text for a case_id link."},
                    "graph": _GRAPH,
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name=tool_name("relate"),
            description=(
                "Assert a DIRECTED relationship between two nodes. Direction "
                "is meaning: 'A supersedes B' is not 'B supersedes A'. "
                "(PALIMPSEST stores these genuinely directed; the SQL system "
                "this vocabulary came from canonicalized endpoints into id "
                "order and lost direction entirely.)"
            ),
            inputSchema={
                "type": "object",
                "required": ["from_id", "to_id", "relation"],
                "properties": {
                    "from_id": dict(_ID, description="Source node id — the subject of the claim."),
                    "to_id": dict(_ID, description="Target node id — the object of the claim."),
                    "relation": {
                        "type": "string",
                        "enum": list(RELATION_KINDS),
                        "description": (
                            "supports | contradicts | derived_from | supersedes "
                            "| duplicates | references. 'relates' is a reader-"
                            "side fallback only and is NOT writable."
                        ),
                    },
                    "note": {"type": "string", "maxLength": 2000},
                    "graph": _GRAPH,
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name=tool_name("recall"),
            description=(
                "Semantic search over the memory graph — prose in, ranked "
                "nodes out, each with its provenance and author_agent. Call "
                "this BEFORE re-solving anything: another agent or an earlier "
                "session may already have the answer."
            ),
            inputSchema={
                "type": "object",
                "required": ["text"],
                "properties": {
                    "text": {"type": "string", "minLength": 1, "maxLength": 4000},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                    "labels": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(NODE_LABELS)},
                        "description": "Labels to search. Default ['Claim', 'Event'].",
                    },
                    "embedding": _EMBEDDING,
                    "graph": _GRAPH,
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name=tool_name("ring"),
            description=(
                "Detect a CO-EDIT RING: three distinct actors touching two "
                "distinct pages inside one time window — a four-step chain in "
                "which no individual edit crosses any threshold. This is the "
                "verdict nothing else in the stack can express; a vector store "
                "cannot ask for 'similar to a four-step causal chain'. Returns "
                "the paths the UI animates plus FalkorDB's own run_time_ms. "
                "Omit `anchor` to sweep the whole graph."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "anchor": dict(
                        _ID,
                        description=(
                            "Optional Actor.name that must appear in the ring. "
                            "Omit for a whole-graph sweep (this is what the "
                            "projector polls)."
                        ),
                    ),
                    "window_s": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 2592000,
                        "default": _RING_WINDOW_S_DEFAULT,
                        "description": (
                            "The ring must close within this many seconds. THE "
                            "discriminator: the same actors and pages spread "
                            "wider than the window return zero rings."
                        ),
                    },
                    "since": {"type": "number", "description": "Lower ts bound (epoch seconds)."},
                    "until": {"type": "number", "description": "Upper ts bound (epoch seconds)."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
                    "graph": _GRAPH,
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name=tool_name("ringleader"),
            description=(
                "NAME the ringleader and AUTO-DISCOVER the collusion cell with "
                "FalkorDB's own compiled graph algorithms — no LLM, no key, no "
                "vector store. Where /ring proves a co-edit ring EXISTS, this "
                "answers WHO leads it and WHICH actors form the cell: it "
                "projects the co-edit graph, runs algo.pageRank to name the "
                "ringleader (highest structural influence), and algo.WCC to "
                "recover the ringleader's connected cell unsupervised — leaving "
                "unrelated bystander pairs in their own component. Returns the "
                "influence ranking, the discovered cells, and FalkorDB's own "
                "run_time_ms. This is topology a vector store cannot compute."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "k": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 5,
                        "description": (
                            "How many actors to return in the PageRank influence "
                            "ranking. The top one is the named ringleader."
                        ),
                    },
                    "graph": _GRAPH,
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name=tool_name("ablation"),
            description=(
                "Run the cold-vs-warm ablation LIVE and return the "
                "opposite-verdict proof. Seeds a WARM graph with the historical "
                "co-edit corpus plus one new event, and a COLD graph with ONLY "
                "that same new event, then runs the identical 3-hop ring query "
                "against both: WARM fires (escalate), COLD does not (dismiss). "
                "Topology-only — no LLM, no key, pure Cypher, milliseconds. This "
                "is the headline beat (GOAL victory condition 1); it is a "
                "DIFFERENT claim from stream_replay's reproducibility digest. "
                "Non-destructive: uses dedicated palimpsest_ablation_* keys."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window_s": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 2592000,
                        "default": _RING_WINDOW_S_DEFAULT,
                        "description": (
                            "The ring window handed to BOTH graphs — the same "
                            "discriminator the /ring verb uses."
                        ),
                    },
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name=tool_name("graph"),
            description=(
                "The nodes+edges projection for rendering — a whole-graph or "
                "filtered snapshot with per-agent contribution stats, ready for "
                "any force-graph library. Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "graph": _GRAPH,
                    "since": {
                        "type": "number",
                        "description": (
                            "Epoch-seconds floor; only nodes touched at or "
                            "after this are projected."
                        ),
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 500},
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name=tool_name("stream_publish"),
            description=(
                "Append a durable, ordered record to the log spine. Every "
                "memory write, tool call, handoff and human ruling goes on the "
                "log — that is what makes the whole run replayable from "
                "offset 0."
            ),
            inputSchema={
                "type": "object",
                "required": ["topic", "payload"],
                "properties": {
                    "topic": dict(_TOPIC, description="One of the six PALIMPSEST topics."),
                    "payload": {"description": "JSON-serializable record body."},
                    "key": {
                        "type": "string",
                        "description": (
                            "Partition key. Same key => same partition => "
                            "ordering preserved across parallel consumers."
                        ),
                    },
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name=tool_name("stream_tail"),
            description=(
                "Read the newest records off a topic — WHAT IS HAPPENING RIGHT "
                "NOW. Your context window is stale by definition; call this "
                "before making any present-tense claim."
            ),
            inputSchema={
                "type": "object",
                "required": ["topic"],
                "properties": {
                    "topic": dict(_TOPIC),
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 25},
                    "since_offset": {"type": "integer", "minimum": 0},
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name=tool_name("stream_replay"),
            description=(
                "Replay a topic from an explicit offset (0 = from the very "
                "beginning) through the same code path as the live tail. This "
                "is the rewind: same events, cold graph vs warm graph, "
                "opposite verdicts."
            ),
            inputSchema={
                "type": "object",
                "required": ["topic"],
                "properties": {
                    "topic": dict(_TOPIC),
                    "from_offset": {"type": "integer", "minimum": 0, "default": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100000, "default": 1000},
                    "target_graph": {
                        "type": "string",
                        "enum": [config.GRAPH_WARM, config.GRAPH_COLD],
                        "description": "Which graph the replay writes into.",
                    },
                    "speed": {"type": "number", "minimum": 0, "description": "0 = as fast as possible."},
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name=tool_name("act"),
            description=(
                "Fire a real, externally-visible action (Discord message, "
                "GitHub issue) and write it back into the graph with clickable "
                "provenance. REQUIRES a prior approved `ask` — never call this "
                "on your own authority. Delivery upstream is at-least-once, so "
                "always pass an idempotency_key."
            ),
            inputSchema={
                "type": "object",
                "required": ["action", "case_id"],
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["discord_message", "github_issue"],
                    },
                    "case_id": dict(_ID, description="The case this action discharges."),
                    "approval_id": dict(_ID, description="question_id of the approving `ask`."),
                    "idempotency_key": dict(
                        _ID,
                        description="Fences the side effect against at-least-once redelivery.",
                    ),
                    "body": {"type": "object", "additionalProperties": True},
                    "derived_from": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Event/Claim ids this action is derived from — the provenance chain.",
                    },
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name=tool_name("handover_write"),
            description=(
                "Leave continuity for the next session: what you did, what is "
                "still in flight, what should happen next, what is blocking, "
                "and a checkpoint carrying your committed stream offset. Write "
                "one on every meaningful state change AND before you finish — "
                "a restarted agent can only resume from what you wrote. Always "
                "recorded as status='open'."
            ),
            inputSchema={
                "type": "object",
                "required": ["agent_id"],
                "properties": {
                    "agent_id": dict(_ID, description="Your agent id."),
                    "to_agent": dict(
                        _ID,
                        description=(
                            "Recipient agent id. Omit for a SELF-handoff (this "
                            "session hands to the next session of the same "
                            "agent) — that is the cold-resume path."
                        ),
                    ),
                    "role": {"type": "string", "description": "Optional role label."},
                    "session_id": {"type": "string"},
                    "summary": {"type": "string", "description": "What this session did."},
                    "in_flight": {"type": "string", "description": "Work still in progress."},
                    "next_steps": {"type": "string", "description": "What the next session should do."},
                    "blockers": {"type": "string", "description": "Anything blocking progress."},
                    "artifacts": {
                        "type": "object",
                        "additionalProperties": True,
                        "description": "Structured artifacts (ids, urls, file paths, ...).",
                    },
                    "checkpoint": {
                        "type": "object",
                        "additionalProperties": True,
                        "description": (
                            "Resumable state. MUST carry the COMMITTED "
                            "LaserData offset — that is what makes cold-resume "
                            "exact rather than approximate."
                        ),
                    },
                    "graph": _GRAPH,
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name=tool_name("handover_read"),
            description=(
                "Read an agent's OPEN handover back out of the graph — the "
                "cold-resume path. A restarted agent calls this FIRST, before "
                "anything else, to find out who it is and what it was doing. "
                "Returns null with an explicit reason on a genuine miss; it "
                "will never hand you a superseded row."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": dict(_ID, description="Whose handover to read. Omit with all=true."),
                    "all": {
                        "type": "boolean",
                        "description": "Latest OPEN row per agent — the whole team.",
                    },
                    "status": {
                        "type": "string",
                        "enum": list(HANDOVER_STATUSES),
                        "default": "open",
                        "description": (
                            "Only 'open' may be inherited. Reading another "
                            "status is a forensic read, never a resume."
                        ),
                    },
                    "graph": _GRAPH,
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name=tool_name("ask"),
            description=(
                "Put a decision card in front of a human and BLOCK until they "
                "answer or the timer expires. This is the ONLY question the "
                "system is allowed to ask, and it is always 'approve this "
                "action?'. Everything else you needed to know was already "
                "pullable from the graph or the log. Always supply `default` — "
                "on timeout it is returned with timed_out=true so the run "
                "continues instead of freezing."
            ),
            inputSchema={
                "type": "object",
                "required": ["question"],
                "properties": {
                    "question": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "The tappable choices, e.g. ['approve', 'dismiss'].",
                    },
                    "recommendation": {
                        "type": "string",
                        "description": "What the system thinks, and why. Shown above the options.",
                    },
                    "timeout_sec": {"type": "number", "minimum": 1, "maximum": 3600, "default": 60},
                    "default": {
                        "type": "string",
                        "description": (
                            "Returned when the timer expires with no answer. "
                            "THE most important field on this card — without "
                            "it a live demo hangs on an absent human."
                        ),
                    },
                    "intent": {"type": "string", "enum": list(MESSAGE_INTENTS), "default": "ASK"},
                },
                "additionalProperties": False,
            },
        ),
    ]
    return _attach_widget_meta(tools)


def all_tools() -> List[Tool]:
    return _tools()


# ═══════════════════════════════════════════════════════════════════════════
# THE GENERIC ROUTER  (ported from unblock server.py:5231-5268)
# ═══════════════════════════════════════════════════════════════════════════

async def dispatch(
    name: str,
    arguments: Optional[dict] = None,
    *,
    session_id: Optional[str] = None,
    headers: Optional[dict] = None,
    surface: str = "mcp",
    is_stdio: bool = False,
    default_agent: Optional[str] = None,
) -> dict:
    """Resolve one call to one handler. THE chokepoint — every surface enters
    here, so retries, error envelopes, identity resolution and header handling
    live in exactly one function.

    Order matters and mirrors upstream:
      1. resolve the verb,
      2. substitute path params from the RAW arguments (before the builder
         strips them),
      3. build the payload,
      4. resolve identity per-request (never from frozen session state),
      5. hand a RequestCtx to the handler.
    """
    arguments = arguments or {}
    verb = verb_of(name)

    entry = _VERB_DISPATCH.get(verb)
    if entry is None:
        return err(
            "UNKNOWN_TOOL",
            f"unknown tool: {name}",
            known=[tool_name(v) for v in VERBS],
        )

    method, path_template, builder, handler = entry

    # PUBLIC-EXPOSURE GUARD. Inert unless PALIMPSEST_PUBLIC_MODE is set, so this
    # is a no-op on every existing deployment. It sits HERE — before the builder,
    # before identity resolution, before any handler work — because a refused
    # verb should touch nothing. Guarding at this chokepoint is what makes ONE
    # check cover every NETWORK surface; an HTTP middleware would cover REST
    # only, since an MCP tool call's verb lives in a JSON-RPC body, not the path.
    # `surface` is hardcoded by each entry point, never caller-supplied, so the
    # local-surface exemption (which keeps seed_demo working at boot) is safe.
    refused = guard.refusal(verb, surface=surface)
    if refused is not None:
        return refused

    try:
        path = path_template.format(**arguments)
    except KeyError as e:
        return err("MISSING_PATH_PARAM", f"missing required path param: {e!s}")

    payload = builder(arguments)

    # GET/DELETE: the body is dropped on the wire and becomes the query string.
    # Drop Nones so we never emit `?foo=None`.
    if method.upper() in {"GET", "DELETE"}:
        payload = {k: v for k, v in payload.items() if v is not None}

    hdr_session, hdr_agent = identity.selector_from_headers(headers)
    agent = identity.resolve(
        session_id or hdr_session,
        is_stdio=is_stdio,
        header_selector=hdr_agent,
        default=default_agent,
    )

    ctx = RequestCtx(
        verb=verb,
        method=method,
        path=path,
        payload=payload,
        agent=agent,
        session_id=session_id or hdr_session,
        surface=surface,
    )

    try:
        return await handler(ctx)
    except Exception as e:  # noqa: BLE001 — one place turns a crash into an envelope
        return err(
            "HANDLER_ERROR",
            f"{type(e).__name__}: {e}",
            verb=verb,
            author_agent=agent,
        )


# ═══════════════════════════════════════════════════════════════════════════
# SURFACE (c): OpenAPI 3.0 — generated from THE SAME TABLE
# ═══════════════════════════════════════════════════════════════════════════
# This is the ONE Guild Integration. It exists because the table already
# carries (METHOD, PATH); there is no second spec to keep in sync.

def openapi_spec(base_url: Optional[str] = None) -> dict:
    base = base_url or f"http://{config.BRIDGE_HOST}:{config.BRIDGE_PORT}"
    by_name = {t.name: t for t in _tools()}
    paths: Dict[str, dict] = {}

    for verb, (method, path_template, _builder, _handler) in _VERB_DISPATCH.items():
        tool = by_name[tool_name(verb)]
        schema = dict(tool.inputSchema)
        props: dict = dict(schema.get("properties") or {})
        required = list(schema.get("required") or [])

        # Path params come out of the body and become path parameters.
        param_names = [
            seg[1:-1]
            for seg in path_template.split("/")
            if seg.startswith("{") and seg.endswith("}")
        ]
        parameters = []
        for pname in param_names:
            parameters.append(
                {
                    "name": pname,
                    "in": "path",
                    "required": True,
                    "schema": props.get(pname, {"type": "string"}),
                }
            )
            props.pop(pname, None)
            if pname in required:
                required.remove(pname)

        operation: Dict[str, Any] = {
            "operationId": verb,
            "summary": tool.description,
            "responses": {
                "200": {
                    "description": "Result envelope",
                    "content": {"application/json": {"schema": {"type": "object"}}},
                }
            },
        }

        if method.upper() in {"GET", "DELETE"}:
            for pname, pschema in props.items():
                parameters.append(
                    {
                        "name": pname,
                        "in": "query",
                        "required": pname in required,
                        "schema": pschema,
                    }
                )
        else:
            body_schema: Dict[str, Any] = {
                "type": "object",
                "properties": props,
                "additionalProperties": schema.get("additionalProperties", False),
            }
            if required:
                body_schema["required"] = required
            operation["requestBody"] = {
                "required": bool(required),
                "content": {"application/json": {"schema": body_schema}},
            }

        if parameters:
            operation["parameters"] = parameters

        paths.setdefault(path_template, {})[method.lower()] = operation

    return {
        "openapi": "3.0.3",
        "info": {
            "title": "PALIMPSEST bridge",
            "version": SERVER_VERSION,
            "description": INSTRUCTIONS,
        },
        "servers": [{"url": base}],
        "paths": paths,
    }


# ═══════════════════════════════════════════════════════════════════════════
# SURFACE (b): MCP
# ═══════════════════════════════════════════════════════════════════════════

server = None


def _mcp_request_identity() -> Tuple[Optional[str], Optional[dict]]:
    """Best-effort ``(session_id, headers)`` for the MCP surface.

    The stdio mount carries NO HTTP headers, so this returns ``(None, None)``
    and :func:`dispatch` falls through the identity ladder to
    :data:`MCP_DEFAULT_AGENT` — a real author, never 'unbound'. On a
    streamable-http mount the host MAY forward ``Mcp-Session-Id`` and/or
    ``x-palimpsest-agent``; when the SDK exposes them on its per-request
    context we thread them through the SAME header ladder the REST surface
    uses, so a forwarded agent still wins over the default (and an explicit
    binding, or a future verified-token selector, still wins over the header).

    It is deliberately defensive: the SDK's ``request_context`` is a contextvar
    that raises when read outside a request and whose ``request`` shape varies
    by transport, so every access is guarded and any failure degrades to the
    configured default rather than crashing a tool call.
    """
    srv = server
    if srv is None:
        return (None, None)
    try:
        ctx = srv.request_context
    except Exception:  # noqa: BLE001 - LookupError outside a request, etc.
        return (None, None)
    headers: Optional[dict] = None
    request = getattr(ctx, "request", None)
    raw_headers = getattr(request, "headers", None)
    if raw_headers:
        try:
            headers = dict(raw_headers)
        except Exception:  # noqa: BLE001 - unknown header container shape
            headers = None
    session_id: Optional[str] = None
    if headers:
        session_id, _ = identity.selector_from_headers(headers)
    return (session_id, headers)


if MCP_AVAILABLE:  # pragma: no cover - requires the SDK
    server = Server(SERVER_NAME, instructions=INSTRUCTIONS)

    @server.list_tools()
    async def list_tools() -> List[Tool]:
        return all_tools()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> List[TextContent]:
        # BUG FIX: bind identity from the request BEFORE the write verb runs, so
        # an MCP-surface write carries a real author_agent instead of 'unbound'.
        # A forwarded x-palimpsest-agent/Mcp-Session-Id is honored via the same
        # header ladder as REST; the stdio mount (no headers) falls to the
        # configured MCP_DEFAULT_AGENT. is_stdio stays true when no session is
        # forwarded, preserving the one-process-one-session stdio semantics.
        session_id, headers = _mcp_request_identity()
        result = await dispatch(
            name,
            arguments or {},
            session_id=session_id,
            headers=headers,
            surface="mcp",
            is_stdio=session_id is None,
            default_agent=MCP_DEFAULT_AGENT,
        )
        return [TextContent(type="text", text=json.dumps(result, default=str))]

    # ── MCP-Apps widget resources (the ui:// bundles) ───────────────
    # Serve the predeclared widget HTML the three tools reference via
    # `_meta.ui.resourceUri`. The `_meta.ui` block (render-gate domain + border
    # hint + any CSP) rides on BOTH resources/list AND resources/read, so an
    # `--app-info` probe reads the same declaration wherever it looks; mimeType
    # is the ratified `text/html;profile=mcp-app` on both. Registering these
    # advertises the `resources` capability — purely ADDITIVE to the MCP
    # surface, touching no verb, the pinned table, or the plain-text fallback.
    @server.list_resources()
    async def list_resources() -> List[Resource]:
        out: List[Resource] = []
        for spec, meta in widget_apps.resource_rows():
            res = Resource(
                uri=spec.uri,
                name=spec.name,
                title=spec.title,
                description=spec.description,
                mimeType=widget_apps.MIME,
            )
            res = res.model_copy(update={"meta": meta})  # `_meta` on the wire
            out.append(res)
        return out

    @server.read_resource()
    async def read_resource(uri: Any) -> List["ReadResourceContents"]:
        spec = widget_apps.spec_for_uri(str(uri))
        if spec is None:
            raise ValueError("unknown widget resource: {0}".format(uri))
        # ReadResourceContents carries `meta`, which the SDK propagates onto
        # the TextResourceContents `_meta` — that is how resources/read gets
        # the `ui.domain`/`ui.csp` render gate (guide §2.3).
        return [
            ReadResourceContents(
                content=widget_apps.read_widget_html(spec),
                mime_type=widget_apps.MIME,
                meta=widget_apps.resource_ui_meta(spec),
            )
        ]


# ═══════════════════════════════════════════════════════════════════════════
# SURFACE (d): CLI  +  _main
# ═══════════════════════════════════════════════════════════════════════════

async def _main() -> None:
    """Serve the MCP stdio loop.

    Upstream's _main resolves a device macaroon, an agent sub-credential and a
    persona binding before serving. NONE of that exists here: PALIMPSEST is
    local and keyless, so boot is just 'serve'. That is the point.
    """
    if not MCP_AVAILABLE:
        raise RuntimeError(
            "the `mcp` SDK is not installed — cannot serve the MCP surface. "
            "Install it PINNED: pip install 'mcp>=1.28,<2'. The upper bound is "
            "load-bearing: MCP 2.x removes the Mcp-Session-Id header that "
            "app/bridge/identity.py keys per-agent identity on."
        )
    print(f"[{SERVER_NAME}] config: {json.dumps(config.describe())}", file=sys.stderr)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main(argv: Optional[List[str]] = None) -> int:
    """Tiny CLI: rehearsal + gate-artifact capture, over the SAME router."""
    args = list(sys.argv[1:] if argv is None else argv)

    if not args or args[0] in {"-h", "--help"}:
        print(f"{SERVER_NAME} {SERVER_VERSION}")
        print("  --list-tools            list the MCP tool surface")
        print("  --verbs                 list the dispatch table")
        print("  --openapi               print the generated OpenAPI 3.0 spec")
        print("  --config                print the effective (secret-free) config")
        print("  --serve                 serve the MCP stdio loop")
        print("  <verb> '<json>'         call one verb through the router")
        return 0

    cmd = args[0]

    if cmd == "--list-tools":
        for t in all_tools():
            print(t.name)
        return 0

    if cmd == "--verbs":
        for verb, (method, path, _b, _h) in _VERB_DISPATCH.items():
            print(f"{verb:<16} {method:<6} {path}")
        return 0

    if cmd == "--openapi":
        print(json.dumps(openapi_spec(), indent=2))
        return 0

    if cmd == "--config":
        print(json.dumps(config.describe(), indent=2))
        return 0

    if cmd == "--serve":
        asyncio.run(_main())
        return 0

    payload = json.loads(args[1]) if len(args) > 1 else {}
    result = asyncio.run(dispatch(cmd, payload, surface="cli", is_stdio=True))
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
