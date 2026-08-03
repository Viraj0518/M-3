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
import sys
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from memory import config
from memory.taxonomy import (
    BLOCK_TYPES,
    HANDOVER_STATUSES,
    MESSAGE_INTENTS,
    RELATION_KINDS,
)

from . import identity

# ─── MCP SDK, guarded ───────────────────────────────────────────────────────
# The module must IMPORT CLEANLY with or without the SDK installed, so the
# taxonomy/config/table/OpenAPI surfaces stay testable in a bare interpreter
# and `python -c "import app.bridge.server"` never depends on a venv being hot.
# When the SDK is absent we substitute minimal structural stand-ins; the MCP
# transport itself refuses to boot with an honest error (see _main).
try:  # pragma: no cover - import-environment dependent
    from mcp.server import Server  # type: ignore[import-not-found]
    from mcp.server.stdio import stdio_server  # type: ignore[import-not-found]
    from mcp.types import TextContent, Tool  # type: ignore[import-not-found]

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
        "role",
        "session_id",
        "summary",
        "in_flight",
        "next_steps",
        "blockers",
        "artifacts",
        "checkpoint",
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
# handlers  —  element 4 of the tuple. TODO bodies, honest envelopes.
# ═══════════════════════════════════════════════════════════════════════════
# Each one names EXACTLY what has to be built and which lane owns it. Nothing
# below fabricates a result.

async def _h_remember(ctx: RequestCtx) -> dict:
    return todo(
        ctx,
        "MEMORY LANE. Embed content (EMBED_DIM=256, must match the HNSW index "
        "or the vector index corrupts SILENTLY); run the sensing gate "
        "(db.idx.vector.queryNodes, top-1 DISTANCE < SALIENCE_THRESHOLD => "
        "skip); then MERGE a (:Claim) / (:Event) with "
        "normalize_block_type(block_type, metadata) applied and "
        "SET n.author_agent = $author_agent. Emit the delta to the UI topic.",
    )


async def _h_relate(ctx: RequestCtx) -> dict:
    return todo(
        ctx,
        "MEMORY LANE. check_relation(relation) then MERGE "
        "(a)-[:RELATES {relation, author_agent}]->(b) — GENUINELY DIRECTED. Do "
        "NOT canonicalize the endpoints into id order: that is unblock's a<b "
        "CHECK, and it is exactly the bug we dropped.",
    )


async def _h_recall(ctx: RequestCtx) -> dict:
    return todo(
        ctx,
        "MEMORY LANE. Hybrid GraphRAG entry: db.idx.vector.queryNodes over "
        "Claim.emb/Event.emb, then expand. SORT ASCENDING — the score is a "
        "DISTANCE (0.0 = identical). Sorting DESC shows the judges the WORST "
        "matches with no error at all.",
    )


async def _h_ring(ctx: RequestCtx) -> dict:
    return todo(
        ctx,
        "MEMORY LANE. The n-hop neighbourhood traversal around an anchor node "
        "(MATCH p=(a)-[*1..3]->(b), verified working on FalkorDB). Returns the "
        "Path objects the UI animates, plus a ring_score that decides whether "
        "to publish case.opened.",
    )


async def _h_graph(ctx: RequestCtx) -> dict:
    return todo(
        ctx,
        "UI LANE. The nodes+edges projection payload — shape lifted from "
        "fetchBrainGraph (unblock_substrate .../roster-graph.ts:413-460), fed "
        "from Cypher instead of SQL. FalkorDB exposes Node .id/.labels/"
        ".properties and Edge .relation/.src_node/.dest_node. Include the "
        "per-agent contribution stats (identity.contributors) — that is what "
        "colours the nodes by author_agent.",
    )


async def _h_stream_publish(ctx: RequestCtx) -> dict:
    return todo(
        ctx,
        "STREAM LANE. laser topic producer. producer.init() BEFORE the first "
        "send() or it fails. key=<partition key> so per-key ordering survives "
        "parallel consumers. Log primitive ONLY — gate anything beyond Log "
        "behind `await laser.capabilities()`.",
    )


async def _h_stream_tail(ctx: RequestCtx) -> dict:
    return todo(
        ctx,
        "STREAM LANE. Bounded read of the newest N records. Consumer "
        "invariants (ported from unblock comms/monitor.py:28-42): "
        "enqueue-before-ack; dedup on STREAM SEQUENCE not message_id; "
        "backpressure, never drop; cancellation-safe idempotent teardown.",
    )


async def _h_stream_replay(ctx: RequestCtx) -> dict:
    return todo(
        ctx,
        "STREAM LANE. Replay from an explicit offset (0 = from the beginning) "
        "— this is the rewind A/B, which is NEVER cut. Same code path as the "
        "live tail, and the same path a --source file://demo/seed_replay.ndjson "
        "run drives, so conference wifi is never on the critical path.",
    )


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


async def _h_handover_write(ctx: RequestCtx) -> dict:
    return todo(
        ctx,
        "HANDOVER LANE. MERGE (:Agent)-[:HANDED_OFF_TO {handover_id, status, "
        "summary, in_flight, next_steps, blockers, artifacts, checkpoint}]->"
        "(:Agent), with checkpoint carrying the COMMITTED LaserData offset. "
        "Always status='open' on write (the builder enforces it); supersede "
        "prior open rows for the same agent in the same transaction.",
    )


async def _h_handover_read(ctx: RequestCtx) -> dict:
    return todo(
        ctx,
        "HANDOVER LANE — INHERIT BOTH DOCUMENTED TRAPS (unblock server.py:"
        "4608-4672). (1) UNWRAP the {'handovers': [row?]} envelope EXPLICITLY: "
        "a bare truthiness check treats a genuine MISS as a HIT, because "
        "{'handovers': []} is itself truthy. (2) STATUS-GUARD on "
        "status=='open': the by-agent read has no status filter upstream, so "
        "an unguarded read RESURRECTS a superseded row. Fold the row ONLY when "
        "open; otherwise report the honest miss with a reason.",
    )


async def _h_ask(ctx: RequestCtx) -> dict:
    return todo(
        ctx,
        "HUMAN-IN-THE-LOOP. Port the ordering from unblock's ask() "
        "(comms/nats_client.py:1318-1400): SUBSCRIBE THE REPLY SUBJECT FIRST, "
        "then publish the question, THEN race the deadline — otherwise a fast "
        "responder races ahead of the listener and the answer is lost. On "
        "timeout return the `default` with timed_out=True; never block "
        "forever. This is the ONLY question the system is allowed to ask a "
        "human, and it is always 'approve this action?'.",
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


def _tools() -> List[Tool]:
    return [
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
                        "items": {"type": "string"},
                        "description": "Extra node labels, e.g. ['Claim'] or ['Event'].",
                    },
                    "tags": {"type": "array", "items": {"type": "string", "maxLength": 64}, "maxItems": 32},
                    "metadata": {"type": "object", "additionalProperties": True},
                    "case_id": {"type": "string", "description": "Attach this write to an open case."},
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
                    "labels": {"type": "array", "items": {"type": "string"}},
                    "graph": {
                        "type": "string",
                        "enum": [config.GRAPH_WARM, config.GRAPH_COLD],
                        "default": config.GRAPH_WARM,
                        "description": (
                            "Which graph to read. The cold graph is "
                            "deliberately empty and exists for the ablation."
                        ),
                    },
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name=tool_name("ring"),
            description=(
                "Traverse the neighbourhood around an anchor node — 'everything "
                "this actor/page/entity has ever been involved in, N hops out'. "
                "Use this when you HAVE an entity; use recall when you only "
                "have prose. Returns paths, which the UI animates."
            ),
            inputSchema={
                "type": "object",
                "required": ["anchor"],
                "properties": {
                    "anchor": dict(_ID, description="Node id or unique name to traverse from."),
                    "hops": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
                    "edge_types": {"type": "array", "items": {"type": "string"}},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
                    "graph": {
                        "type": "string",
                        "enum": [config.GRAPH_WARM, config.GRAPH_COLD],
                        "default": config.GRAPH_WARM,
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
                    "graph": {
                        "type": "string",
                        "enum": [config.GRAPH_WARM, config.GRAPH_COLD],
                        "default": config.GRAPH_WARM,
                    },
                    "since": {"type": "string", "description": "ISO-8601; only nodes/edges newer than this."},
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
if MCP_AVAILABLE:  # pragma: no cover - requires the SDK
    server = Server(SERVER_NAME, instructions=INSTRUCTIONS)

    @server.list_tools()
    async def list_tools() -> List[Tool]:
        return all_tools()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> List[TextContent]:
        result = await dispatch(name, arguments or {}, is_stdio=True, surface="mcp")
        return [TextContent(type="text", text=json.dumps(result, default=str))]


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
