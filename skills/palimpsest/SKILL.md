---
name: palimpsest
description: PALIMPSEST — an attributed memory graph (FalkorDB) on a durable ordered log spine (LaserData), exposed as an MCP server. Gives an agent persistent memory of who the user is, a handover node telling it who IT is across restarts, and a constant datastream of what is happening right now — so it PULLS context instead of asking for it. HARNESS-AGNOSTIC — the same streamable-http MCP surface works from Claude Code, Claude Desktop, Codex, Cursor or any MCP client. Trigger when the user asks to remember or recall something across sessions, resume after a restart, hand work to another agent, ask "what happened / what is happening now", investigate a co-edit ring or coordinated-editing pattern, or mentions palimpsest / attributed memory / memory graph / handover / the ablation demo.
---

PALIMPSEST is a **memory substrate for agents that wake up amnesiac**. It answers
three questions no stateless model can:

| Question | Where the answer lives |
|---|---|
| **Who are you?** | the FalkorDB attributed graph — every actor's history one traversal away |
| **Who am I?** | this agent's own `:Agent` node + its open `HANDED_OFF_TO` handover edge, in the *same* graph |
| **What is happening right now?** | the LaserData log spine — durable, ordered, replayable from offset 0 |

The whole thesis is **pull, don't ask**. Anything you were about to ask a human
was probably already in the graph or on the log. The only question the system is
allowed to ask a human is *"may I take this action?"*.

The golden loop: **read your handover → pull before you re-solve → write what you
learn → leave a handover before you stop.**

---

## The architecture in one paragraph (why the traps below exist)

There is **ONE dispatch table** (`app/bridge/server.py:_VERB_DISPATCH`), a dict of
`verb -> (HTTP METHOD, PATH, payload_builder, handler)`. Because every entry
already carries an HTTP method and path, that one table generates **four
surfaces** with no second implementation: REST, MCP streamable-http, an OpenAPI
3.0 document, and a CLI. Every call from every surface funnels through one
function, `server.dispatch()`. That is why a behaviour you observe over REST is
usually — but *not always*, see the identity trap — the same over MCP.

---

## Verbs — what each one is for

Tool names are the verb with a `palimpsest_` prefix. **13 tools** on `main`
(`ablation` is the newest; a deployment one build behind serves 12 — count them
with `tools/list` rather than assuming).

### Memory — the graph

| Tool | Use it when | Required args |
|---|---|---|
| `palimpsest_remember` | You made a decision, fixed something, or learned something. **Call it unprompted.** Idempotent: the id defaults to a content hash, so remembering the same thing twice is a MERGE, not a duplicate. | `content` |
| `palimpsest_relate` | Two existing nodes stand in a relationship. **Genuinely directed** — "A supersedes B" is stored as a different fact from "B supersedes A". Never creates its endpoints, so a typo cannot invent a node. | `from_id`, `to_id`, `relation` |
| `palimpsest_recall` | You have *prose* and want ranked nodes back, each with provenance and `author_agent`. Call this **before** re-solving anything. | `text` |
| `palimpsest_ring` | You have a *shape* to look for, not a phrase. Detects a co-edit ring: three distinct actors touching two distinct pages inside one time window. This is the query a vector store cannot express. Omit `anchor` to sweep the whole graph. | — |
| `palimpsest_graph` | You want the whole nodes+edges snapshot to render or summarise. Read-only. | — |
| `palimpsest_ablation` | The cold-vs-warm A/B: same input, empty graph vs populated graph, opposite verdicts. | — |

`block_type` is a closed taxonomy: `note`, `snippet`, `doc`, `code`, `trace`,
`decision`, `anti-pattern`, `dataset`, `exploit`, `kg`, `conversation`,
`utterance`, `other` (`memory/taxonomy.py:BLOCK_TYPES`).

**`fix` and `learning` are NOT block types.** They are *capture intents* and they
are mapped, not rejected (`taxonomy.NOT_BLOCK_TYPES`):

- a bug fix → `block_type="decision"`, `metadata={"kind": "fix"}`
- a learning → `block_type="note"`, `metadata={"kind": "learning"}`

`relation` is also closed: `supports`, `contradicts`, `derived_from`,
`supersedes`, `duplicates`, `references`. `relates` is a **reader-side fallback
only and is not writable** — passing it comes back `BAD_RELATION`.

### Stream — the log spine

| Tool | Use it when | Required args |
|---|---|---|
| `palimpsest_stream_tail` | You are about to make a **present-tense claim**. Your context window is stale by definition; the log is not. | `topic` |
| `palimpsest_stream_publish` | An observation, tool call, handoff or ruling happened and must be replayable. | `topic`, `payload` |
| `palimpsest_stream_replay` | Re-drive history from an explicit offset (0 = the beginning) through the same code path as the live tail. | `topic` |

`topic` is a closed enum of six (`memory/config.py:TOPICS`): `signal.raw`,
`signal.salient`, `case.opened`, `case.decision`, `action.executed`,
`agent.handoff`.

### Continuity — who am I, across a restart

| Tool | Use it when | Required args |
|---|---|---|
| `palimpsest_handover_read` | **First call of a restarted session.** Reads your own open handover back out of the graph: what you were doing, what is in flight, your committed log offset. `all=true` gives the latest open row for every agent — the team board. | `agent_id` (or `all=true`) |
| `palimpsest_handover_write` | Every meaningful state change *and* before you stop. Always recorded `status="open"`; writing a new one supersedes the previous open row into the same recipient. | `agent_id` |

Omit `to_agent` for a **self-handoff** — this session handing to the next session
of the same agent. That is the cold-resume path.

Put the **committed log offset** in `checkpoint`. Without it a resume is
approximate; with it, it is exact.

### Human + action

| Tool | Use it when | Required args |
|---|---|---|
| `palimpsest_ask` | **Only** to get approval to act. Every other question should have been a pull. Always supply `default` — it is returned on timeout so a run continues instead of freezing. | `question` |
| `palimpsest_act` | Fire a real external side effect (Discord message, GitHub issue) and write it back with clickable provenance. **Requires a prior approved `ask`.** Delivery upstream is at-least-once, so always pass `idempotency_key`. | `action`, `case_id` |

---

## Traps

Every item here was read out of the source or observed against a running bridge.
Where something is inferred rather than measured it says so.

### 1. HTTP 200 and `isError:false` are NOT success — parse the envelope

This is the trap that will bite you first, and it bites differently on each
surface.

**On MCP**, a domain error comes back as an HTTP 200, with the JSON-RPC result's
`isError` set to **`false`**, and the actual error sitting inside the text
content — which carries its *own* `isError: true`. Observed live:

```
HTTP/1.1 200 OK
{"jsonrpc":"2.0","id":5,"result":{
   "content":[{"type":"text","text":
      "{\"ok\": false, \"isError\": true, \"code\": \"MISSING_AGENT_ID\", ...}"}],
   "isError": false }}
```

The outer `isError` is false. The inner one is true. **Read the inner envelope.**

**On REST**, the same call is a real `400`, because `rest.py:status_for()` maps
an `err()` `code` onto a status — but *only* the codes in `_STATUS_BY_CODE`.
Anything else, including every honest not-implemented stub (which carries
`ok:false`, `status:"not_implemented"` and **no `code` at all**), rides on a
**200**. That is deliberate: the projector UI's `fetchJson` throws on any
non-2xx and `pollBridge` reads a throw on `/graph` as *bridge offline → show the
mock graph*, so a stub returning a non-2xx would put a MOCK badge on screen while
the graph was actually live.

So REST *does* return real 4xx/5xx — `MISSING_CONTENT` → 400, `UNKNOWN_TOOL` →
404, `HANDLER_ERROR` → 500, `PUBLIC_READONLY` → 403. What it never does is
derive a status from `ok`. (The repo's own comments in `rest.py` and
`deploy/README.md` say "`/health` is the only route that returns a non-2xx";
read literally that is wrong, and `status_for()` is the authority.)

**`GET /health` is the only route whose status is NOT derived from an `err()`
code** — it returns 503 with `code: "FALKORDB_UNAVAILABLE"` based on whether the
memory plane answered, because a container orchestrator needs that failure in the
status line rather than in the body.

Rule: **`ok === true` is the only success signal.** Not the HTTP status, not
`isError`.

### 2. A genuine miss is `ok: true`

`handover_read` on an agent with no row returns success with a null and a reason:

```json
{"ok": true, "handover": null,
 "reason": "no handover row for agent '__does_not_exist__' in graph 'palimpsest'"}
```

`handover: null` with `ok: true` means *asked and answered: nothing there*. It is
not an error and it is not a fabricated empty result. Two distinct null reasons
exist and they mean different things: `"no handover row for agent ..."` (nothing
was ever written) versus `"latest row is not open (status='superseded')"` (a row
exists but you may **not** inherit it). Only `status: "open"` may be resumed.

### 3. Over MCP, `author_agent` is `"unbound"` — and `x-palimpsest-agent` is ignored

The graph's whole point is attribution: every write is stamped `author_agent`
server-side, and the caller never gets to name its own author
(`identity.stamp()`). But **the MCP-over-HTTP path does not currently resolve a
per-session identity.**

`server.py`'s MCP handler calls `dispatch(..., is_stdio=True)` and passes **no
headers**. `identity.resolve()` therefore consults the single synthetic stdio
bucket, finds nothing bound, and returns `UNBOUND_AGENT`. Measured on a live
bridge, same header on both surfaces:

| surface | `x-palimpsest-agent: probe-*` | resolved `author_agent` |
|---|---|---|
| REST `GET /v1/stream/signal.raw/tail` | `probe-rest` | **`probe-rest`** |
| MCP `tools/call palimpsest_stream_tail` | `probe-mcp` | **`unbound`** |

`palimpsest_graph` on the same bridge returned `agents: {}` (the live binding
table is empty) and `contributors: {"unbound": 1}`.

Consequences you must plan around:

- **Everything you write over MCP is attributed to `unbound`.** If your
  demonstration depends on per-agent node colouring, drive the writes over REST
  with an `x-palimpsest-agent` header, or carry the identity in `metadata`
  yourself.
- There is **no bind verb.** `identity.bind()` exists in the module but no entry
  in the dispatch table calls it, so nothing can populate the binding table over
  any surface.
- Because the MCP path uses the *shared stdio bucket*, if anything ever does bind
  it, **every concurrent MCP session inherits that identity.** The per-session
  isolation `identity.py` is written to provide is real, but the HTTP MCP surface
  is not wired to it today.

### 4. `x-palimpsest-agent` is not authentication — it is a claim

On the REST surface the header is honoured with no verification whatsoever.
`identity.py`'s own docstring says it is a `session-id -> selector` map with no
auth. So `curl -H 'x-palimpsest-agent: commander' .../v1/remember` is recorded in
the graph as the commander.

The graph therefore records **a claim about who acted**, not who acted. Verified
identity (OAuth 2.1 + PKCE, EdDSA JWTs) exists on the `auth` branch and is
**not merged**. Until it is, treat `author_agent` as *attribution*, never as
*authorisation*.

### 5. `remember` never embeds for you

The bridge **never calls an embedding API** — there is no LLM and no network on
the live write path. `embedding` is an optional *precomputed* vector and must be
exactly `EMBED_DIM` floats (256 by default, `memory/config.py`). A width mismatch
is refused on purpose, because a wrong-width vector corrupts the HNSW index
**silently** — no error, just wrong neighbours forever.

With no embedding supplied, `recall` degrades to the property/fulltext lane and
tells you which lane answered in `mode` (`"vector"` vs `"lexical"`). **Check
`mode`** before concluding that semantic search is weak; you may simply not have
given it a vector.

### 6. FalkorDB vector scores are DISTANCES — 0.0 is the best match

`recall` results carry `score` and, in vector mode, `distance`. Smaller is
closer. Sorting descending hands you the *worst* matches in the database with no
error at all. The handler sorts ascending; if you post-process, keep it that way.

### 7. The sensing gate is opt-in and it will silently skip your write

`remember` accepts `gate: true`. With an embedding supplied and the top-1
distance below `SALIENCE_THRESHOLD` (0.15), the write is **skipped** and you get
`ok: true, skipped: true` with a `reason` and the `nearest` node. That is a
success envelope for a write that did not happen. It is off by default precisely
because seed scripts and the eval harness use the same verb.

**Always check for `skipped`** on a gated write, and check `created` (true only
when the MERGE created a new node) to tell a fresh capture from a no-op.

### 8. Not every verb is implemented — and the stubs say so honestly

Some verbs return an honest not-implemented envelope: `ok: false`,
`status: "not_implemented"`, a `todo` naming what has to be built, and
`payload_echo` of what you sent. **They never fabricate a result** — a
plausible-looking empty answer is the most expensive lie this codebase could
tell. `palimpsest_ask` is a card *shape* with `pending: true`, `answer: null`
and `status: "stub"`; it does not block on a human and it never fabricates an
approval.

Which verbs are stubbed **changes between builds**. Do not trust this document —
call the verb and read `status`.

### 9. `recall` resolves supersede chains for you

Every hit is resolved to the HEAD of its supersede chain and served with the
lineage, so a stale claim can never come back as current. `superseded: true` plus
`superseded_by` names the winner, and `head` is the node you should actually act
on. If you read `node` and ignore `head`, you are reading the stale version.

### 10. `ring` has a time window, and the window is the whole discriminator

The same actors touching the same pages spread wider than `window_s` return
**zero** rings. That is the feature, not a bug: it is what makes a ring a
coordinated-editing signal rather than a coincidence. The envelope carries
`fired`, `ring_count`, `ring_score` (max closeness), `should_open_case`, and
FalkorDB's own `run_time_ms`.

### 11. Two graphs on one server — don't write to the cold one

`graph: "palimpsest"` is the warm graph (real memory). `graph:
"palimpsest_cold"` is **deliberately empty** and exists so the cold-vs-warm
ablation is a real A/B rather than a slide. Writing into it destroys that
demonstration. If you need a scratch graph, use a distinct key.

### 12. Streaming/log verbs depend on a spine that is often down

`/health` reports `laser.reachable`. On a stack without the log spine running it
is `false` and the stream verbs cannot tell you anything about *now*. Check
`/health` before you promise a user a live datastream. The spine requires a Linux
kernel ≳6.11 (see `deploy/docker-compose.yml`), so it is off by default on many
hosts.

### 13. The MCP endpoint string is exact — do not let a client follow a redirect

Bare `/mcp` is served by an explicit route inserted *ahead of* the mount, so it
does **not** 307 to `/mcp/`. Both spellings work, but the MCP Apps contract
derives a widget sandbox domain from a sha256 **of the endpoint string**, so a
client that silently follows a redirect computes a different domain than the one
it registered with. Configure clients with the exact URL, no trailing slash.

Also: `POST /mcp` **requires** `Accept: application/json, text/event-stream`.
Sending only `application/json` returns `406 Not Acceptable` with
`"Client must accept both application/json and text/event-stream"`. Real MCP
clients do this correctly; hand-rolled curl probes usually do not.

### 14. `mcp.mounted: false` means the SDK import failed, not that MCP is disabled

`/health` reports `mcp: {mounted, path, transport}` or `{mounted: false, reason}`.
The reason is always the same one: the `mcp` SDK is not importable in that
interpreter. Install it **pinned** — `mcp>=1.28,<2`. **The upper bound is
load-bearing**: SDK 2.x removes protocol-level sessions and the `Mcp-Session-Id`
header that `identity.py` keys on. Do not relax it to "fix" an install.

### 15. `serverInfo.version` is the SDK version, not the bridge version

The initialize handshake reports `serverInfo: {"name": "palimpsest-bridge",
"version": "1.29.0"}` — that is the MCP SDK's version leaking through, not
`SERVER_VERSION` (`0.1.0`). For the real build identity use `GET /health`
(`version` + `git_sha`). Note `git_sha` is `"unknown"` in a container built from
a tarball rather than a checkout, which is the normal case.

### 16. A public deployment may be running in read-only mode

If the bridge is started with `PALIMPSEST_PUBLIC_MODE=readonly`, every mutating
verb is refused with `code: "PUBLIC_READONLY"` (HTTP 403 on REST) and reads
answer normally. `closed` refuses everything with `PUBLIC_CLOSED`. `GET /health`
reports the posture under `public_mode`, so **check it before you conclude that
writes are broken.** Unset (the default) means no guard at all.

`palimpsest_ablation` is classified as a **write** and is refused under
`readonly` — it drops and re-seeds two dedicated graphs on every call, so it is
not the read it appears to be.

### 17. `ablation` is destructive, and it is not cheap

`palimpsest_ablation` drops (`GRAPH.DELETE`) and re-seeds
`palimpsest_ablation_warm` and `palimpsest_ablation_cold` on **every single
call**, then runs the ring verdict against both. It never touches the warm or
cold demo graphs, so it is safe in that sense — but do not call it in a loop, and
do not treat it as a read.

---

## Auth model

**Today, on `main`: there is none.** Anyone who can reach the URL can call every
verb, and `x-palimpsest-agent` lets them choose whose name is on the write. If
you are pointed at a public PALIMPSEST URL, assume the graph is world-writable
and treat its contents accordingly — do not put anything in it you would not post
publicly.

**On the `auth` branch (PR #2, not merged):** an OAuth 2.1 + PKCE service issuing
EdDSA JWTs, plus a bridge-side verifier (`app/bridge/auth.py`). From a client's
side it is an ordinary OAuth flow — you authenticate, you get a token, the token
goes in `Authorization: Bearer …`. The verifier is a precedence rung *above*
`identity.py`, not a replacement: no token behaves exactly as today, a bad token
also behaves exactly as today (a forged token cannot *remove* an identity you
would otherwise have had), and a valid token yields a selector that outranks any
header claim. With `AUTH_JWKS_URL` unset the whole module is inert.

Client-side setup, the OAuth routes, and the discovery requirements are in
[`docs/MCP.md`](../../docs/MCP.md).

---

## Reflexes — do these without being asked

- **RESUME** — first call of a restarted session is
  `palimpsest_handover_read(agent_id=<you>)`. Find out who you are and what you
  were doing before you do anything else.
- **PULL** — before re-explaining or re-solving anything: `palimpsest_recall`
  (you have prose) or `palimpsest_ring` (you have an entity and want its
  neighbourhood).
- **NOW** — before any present-tense claim: `palimpsest_stream_tail`. Your
  context is stale by construction.
- **CAPTURE** — after a decision, a fix, or a learning: `palimpsest_remember`.
  Cheap and idempotent. Check the response for `skipped`.
- **HANDOVER** — on every meaningful state change and before you stop:
  `palimpsest_handover_write`, with your committed log offset in `checkpoint`.
- **ASK LAST** — `palimpsest_ask` is for approval to act, and nothing else.

## Session arc

1. `GET /health` — is the graph reachable, is `mcp.mounted` true, is the log
   spine up, what is `public_mode`?
2. `palimpsest_handover_read(agent_id=<you>)` — who am I, what was I doing,
   what offset am I resuming from?
3. `palimpsest_stream_tail` — what happened while I was gone?
4. Work: `recall`/`ring` before solving · `remember` as you learn ·
   `stream_publish` so the run stays replayable.
5. `palimpsest_ask` only when you need approval, then `palimpsest_act` with an
   `idempotency_key`.
6. `palimpsest_handover_write` before you stop.

## Safety

- The graph is **shared and attributed**. Anything you write is visible to every
  other agent and human on that graph.
- Without the auth lane merged, a public bridge is an **unauthenticated write
  surface**. Do not put secrets, credentials or private data into it — ever.
- `palimpsest_act` fires **real, externally-visible** side effects. It requires a
  prior approved `ask`; never call it on your own authority, and always pass
  `idempotency_key` because upstream delivery is at-least-once.
- Never write to `palimpsest_cold` — you would destroy the ablation.
