# motion/PIPE_NOTES.md — RocketRide + LaserData, and the traps, written BEFORE anyone types code

Written at H0 on purpose. Every trap below was hit live by someone else on this
box before the sprint. Reading this is cheaper than rediscovering any of them at
hour 6, which is when the pipeline stops being debuggable.

Companion file: [`memory/SCHEMA.md`](../memory/SCHEMA.md) — same trap register,
graph schema and FalkorDB detail.

Runnable artifact: [`palimpsest.pipe`](./palimpsest.pipe). Validate it without
an LLM key using `python motion/client.py --validate-only`; run the full Wave
with `ANTHROPIC_API_KEY` set and optionally persist the trace with
`--trace plan/gates/rocketride-wave-trace.json` (the trace file is intentionally
left untracked until it has been reviewed for provider output and secrets).

Single source of constants: **`memory/config.py`**.

---

## 0. THE TRAP REGISTER (all ten, both lanes)

The complete list lives in BOTH files so neither reader can miss one. Deep
detail for traps 5-9 is here; 1-4 and 10 are detailed in `SCHEMA.md`.

| # | Trap | One-line rule | Detail |
|---|---|---|---|
| 1 | Vector score is a **DISTANCE** | **Sort ASC.** 0.0 = identical | SCHEMA §4 |
| 2 | Port **6401**, not 6399, not 6379 | 6379/6399 already taken; fails as a false-green | SCHEMA §5 |
| 3 | falkordblite persistence is **per-FILE-PATH** | One shared `DB_PATH` constant, committed | SCHEMA §6 |
| 4 | Handover read: **envelope-unwrap + `status=='open'` guard** | Two separate silent bugs | SCHEMA §7 |
| 5 | Provider is `mcp_client`, **NOT** `tool_mcp_client` | The repo directory name is not the registered service name | §2 below |
| 6 | `input:` is DATA FLOW, `control:` is TOOL ATTACHMENT | Declared **on the tool**, pointing back at the agent | §3 below |
| 7 | `ttl=0` on `use()` | Long fan-outs die mid-demo: "Your pipeline is not currently running." | §4 below |
| 8 | `iggy:laser@...`, **NOT** `iggy:iggy@...` | Use the exact string `./scripts/up` prints | §5 below |
| 9 | `producer.init()` **BEFORE** the first `send()` | Required, not optional | §6 below |
| 10 | `'fix'` and `'learning'` are **NOT block_types** | Carry them in `metadata.kind` | SCHEMA §8 |

---

## 1. The pipeline, in one paragraph

`motion/palimpsest.pipe` is ~130 lines of JSON; our Python is ~40 lines.

```
webhook_1 -> question_1 -> agent_rocketride "Commander" (Wave planner, max_waves=12)
                             |
              control-attached:  llm_anthropic (Claude Sonnet)
                                 memory_internal
                                 tool_falkordb (127.0.0.1:6401, graph=palimpsest, allow_writes=true)
                                 tool_http_request (Discord webhook + GitHub API)
                                 mcp_client (our bridge)
                                 researcher sub-agent (own llm_anthropic Haiku + memory_internal)
                             |
                          response_answers
```

`tool_falkordb` is a **first-party** RocketRide node, bundled in the stable
v3.3.1 runtime — FalkorDB↔RocketRide is not glue we invented. That is why the
agent *itself* chooses to call `falkordb.get_schema` and then `falkordb.query`,
and why the SSE trace proves it rather than us asserting it.

Engine: local, MIT, self-hosted, already booted (`127.0.0.1:5565`, dev key
`MYAPIKEY`). Confirm with `lsof -nP -iTCP:5565 -sTCP:LISTEN`.

---

## 2. TRAP 5 — the provider is `mcp_client`, NOT `tool_mcp_client`

Hit live on this box. The repo **directory** is named `tool_mcp_client`, but the
**registered service name** is `mcp_client`. Using the directory name gives you
an unknown-provider error that reads like the node doesn't exist at all.

Same family of trap on the output node: it is `response_answers` /
`response_text` / `response_table` — **not** `response`.

## 3. TRAP 6 — `input:` is data flow, `control:` is tool attachment

Two different wiring keys, trivially conflated, and conflating them produces a
graph that *looks* connected and does nothing.

```jsonc
// DATA FLOW — what feeds what
"input":   [{ "lane": "text", "from": "<upstream_node_id>" }]

// TOOL ATTACHMENT — declared ON THE TOOL, pointing BACK at the agent
"control": [{ "classType": "llm" | "tool" | "memory", "from": "<agent_id>" }]
```

Note the direction: the control edge is declared on the **tool**, and it points
**back at the agent**. Not the other way round.

`agent_rocketride` requires **exactly one** `llm` and **exactly one**
`memory_internal` attached, or it will not start.

## 4. TRAP 7 — `ttl=0`, and keep fan-outs short

```python
r = await client.use(pipeline=PIPE, source="webhook_1", ttl=0,
                     pipelineTraceLevel="full")
```

With a non-zero TTL — or with a long agent fan-out that outlives it — the run
dies mid-demo with **"Your pipeline is not currently running."** The upstream
workshop code comments call this out explicitly. `max_waves=12` on the planner
is the other half of the same guard.

Also drive the trace:

```python
await client.set_events(token, ["task", "summary", "flow", "output", "sse"])
```

`thinking` frames then stream into the UI as live narration — which is the whole
"the agent is choosing tools, not following a script" beat.

### RocketRide SDK reality check

The docs site **lags the shipped code**. Ground truth from the installed 1.3.0
package, not `docs.rocketride.org`:

* `connect()` signature **changed** to `connect(credential=None, *, timeout=None)`.
  The old `connect(uri=..., auth=...)` kwargs are **gone** — pass `uri`/`auth`
  to the **constructor**.
* `__aenter__` **is** still present (the CHANGELOG is wrong about its removal).
* `use()` gained `name`, `env`, `team_id`.
* No `build_request` token-mirroring monkeypatch needed on 1.3.0 + server 3.3.1.
* Requires **Python >= 3.10**. On this Mac's system python 3.9.6,
  `pip install rocketride` **silently installs 1.0.0** — a materially different
  API — instead of failing. Use `/Users/tenzinyeshi/.local/bin/python3.12`.
* First engine start bootstraps ~100 wheels, takes ~4 minutes and **looks hung**.
  Do not kill it. Readiness signal: `Uvicorn running on http://localhost:5565`.
* `tool_python` is a RestrictedPython sandbox — no network, no filesystem, no
  subprocess. Do not plan to reach LaserData or FalkorDB through it; use
  `tool_http_request` or `tool_falkordb`.
* `db_neo4j` is a decoy: Bolt, and hard read-only by design. Use `tool_falkordb`.
* There is **no signup** and no self-serve API key. `MYAPIKEY` is the engine's
  documented built-in dev key. Don't hunt for a cloud key; self-hosting is MIT.

## 5. TRAP 8 — `iggy:laser@...`, NOT `iggy:iggy@...`

`laser-stack`'s `./scripts/up` prints an export line. **Copy that exact string.**

```bash
export LASER_CONNECTION_STRING='iggy:laser@127.0.0.1:8090'   # what ./scripts/up prints
```

The SDK examples' `_common.py` hardcodes `iggy:iggy@127.0.0.1:8090`. laser-stack's
default credentials are `iggy:laser`. The mismatch fails **as what looks like a
network problem**, so you will spend the next half hour debugging Docker
networking instead of a password.

Two env var names for one value — also a real trap. `.env.example` documents
`LASER_CONNECTION`; the SDK/laser-stack world uses `LASER_CONNECTION_STRING`.
`config.laser_connection()` honors **both**, SDK name first, and **raises** if
neither is set rather than inventing a default. Python has no `connect_env()`
(Rust/TS do) — you read the variable yourself.

## 6. TRAP 9 — `producer.init()` before the first `send()`

```python
laser    = await ls.Laser.connect(config.laser_connection(), stream="live")
topic    = laser.topic("signal.raw")
await topic.ensure(partitions=4)
producer = topic.producer(batch_length=200, linger_ms=50, retries=3)
await producer.init()          # REQUIRED before send()
```

Publish with `key=<wiki>` so per-wiki ordering survives parallel consumers.

---

## 7. LaserData: scope rule, invariants, and the honest answer

### HARD SCOPE RULE: Log primitive ONLY

Views / Changes / State / Graph / Memory are **never called**, and every
capability beyond Log is gated:

```python
caps = await laser.capabilities()
```

Those managed primitives require the `laser-plane` sidecar; on bare Apache Iggy
they raise `UnsupportedError`. Gating on `capabilities()` is exactly what makes
local-stack → LaserData Cloud → bare Apache Iggy a **one-env-var swap**.

There is a second reason, and it matters more: LaserData's SDK ships
`laser.graph()` and `laser.memory()`, which **directly overlap FalkorDB's
mandated role**. Do not let Laser be the knowledge graph. FalkorDB owns "what
ever happened"; LaserData owns "what is happening now" plus the message spine.
Mixing them muddies both sponsor stories.

### The four consumer invariants (ported from unblock `comms/monitor.py:28-42`)

LaserData is explicitly **at-least-once**, so these are precisely the four bugs
we would otherwise only discover on stage:

1. **enqueue-before-ack** — append to the buffer *before* `ack()`, so a crash in
   between is a harmless at-least-once redelivery, never silent loss.
2. **dedup on STREAM SEQUENCE, not `message_id`** — the common frames lack a
   message id, so id-based dedup silently degrades to no dedup.
3. **backpressure, not drop** — when the bounded buffer fills, stop
   fetching/acking and leave frames unacked + durable on the broker. Never
   ack-then-drop.
4. **cancellation-safe, idempotent teardown** — `stop()` cancels *and awaits*
   the task, and is safe to call twice.

Consumer group `graph-writers`, `auto_commit=disabled`, **commit AFTER the side
effects land**. Any RocketRide action with a real side effect must be
idempotent or fenced (the SDK has KV compare-and-swap + fenced leases for
exactly this) — hence the `idempotency_key` on the bridge's `act` verb.

### Six topics

`signal.raw` · `signal.salient` · `case.opened` · `case.decision` ·
`action.executed` · `agent.handoff` — spelled once, in `config.TOPICS`.

4 partitions, `batch_length=200`, `linger_ms=50`.

### If a judge asks "what real-time data did LaserData give you?"

The honest answer: **none, and that is not what it is.** LaserData is transport
and memory infrastructure — the nervous system carrying a live feed you point at
it. There is no Kafka/webhook/WebSocket/SSE **source** connector (the four that
exist are PostgreSQL, Elasticsearch, InfluxDB, Random, and connectors are
cloud-only). We bring the source: a ~25-line producer on the Wikimedia
EventStreams SSE firehose. There **is** an HTTP *sink*, which is the clean
no-code way to fire a RocketRide webhook off a stream.

Other rough edges — everything is version 0.0.1, first published 2026-08-02:
* Free-tier throughput is tiny and the docs contradict the console (100 KB/s vs
  200 KB/s). A firehose demo **will** throttle on Cloud. Run on local
  laser-stack (unthrottled); keep Cloud as the "same code, hosted" proof point.
* Free-tier accounts may be **activated by a human at LaserData** after SSO
  signup. Start signup in the background; build against laser-stack in the
  foreground. `laserdata.cloud` 403s non-browser clients — sign up in a real browser.
* VSR (Viewstamped Replication) is unconditional. A generic Iggy client
  configured for classic framing is rejected.
* The TypeScript producer lacks client-side `batchLength`/`linger` (use
  `publishBatch`/`sendBatch`); Python has no background producer mode.
* laser-stack needs **Docker Engine 25+ / Compose 2.20+**. Docker was NOT
  installed on the build Mac — this is a pre-event blocker, not a sprint task.
  **HARD ABORT at T+0:45** if Docker is not up: stop fighting it and take the
  ladder (Cloud free tier → bare Iggy).

---

## 8. The MCP surface (our bridge, mounted via `mcp_client`)

The bridge is `app/bridge/server.py` — ONE `_VERB_DISPATCH` table generating
four surfaces (REST, MCP, OpenAPI 3.0, CLI). RocketRide consumes surface (b);
Guild consumes surface (c) as its single Integration.

**PIN `mcp>=1.28,<2` — the upper bound is load-bearing.** MCP spec revision
2026-07-28 (SDK 2.x) removes protocol-level sessions and the `Mcp-Session-Id`
header that `app/bridge/identity.py` keys per-agent identity on. If a newer SDK
is ever forced, switch to the per-request header selector path
(`identity.selector_from_headers`), which survives the change.

**Write a shape test alongside EVERY payload builder, on day one, before the
OpenAPI spec is published to Guild.** Pre-registered bug class: in unblock,
`unblock_ingest` was not a passthrough — the tool spoke
`{utterances:[{role,text,ts}]}` while the backend spoke
`{items:[{content,metadata}]}`. The mismatch silently 400'd every call, cost
unblock agent self-ingest entirely, and was invisible because the failure was a
well-formed error envelope rather than a crash.

Live verification, kept open on a spare terminal as Q&A card #3: adapt
`vendor/unblock-reuse/unblock_mcp/scripts/mcp_smoke.py` (`--list` and
`'<tool> <json>'` modes over streamable-http). When a skeptical judge asks "is
any of this mocked?", call a bridge tool live in front of them. Same harness
produces the H5 gate artifacts — receipts, not claims.

## 9. Guild notes that belong here

* Guild's sandbox **bans `fetch`, npm, and Node built-ins**. An Integration
  (from our OpenAPI spec) is the **only legal door**. Three integrations would
  eat the sprint; we ship one.
* Never spread a whole toolset into an `llmAgent` — use `pick()`. Models cap
  tool counts, and narrow non-overlapping surfaces are what make the
  `guild session events` trace readable (each agent visibly owns a different
  sponsor).
* Set `useWorkspaceAgents: false` **explicitly**. It defaults to `true`, which
  makes the coordination look accidental to a judge.
* `npm i -g @guildai/cli` — it is **npm-only**. `pip install guildai` is an
  UNRELATED legacy ML tool that even shares the guild.ai domain.
* The `ui_prompt` decision card shape is lifted from unblock's `AskOpts`:
  IN `{question, options[], recommendation, timeoutSec, default}`,
  OUT `{answer, timedOut, questionId, responder}`.
  **`default`-on-timeout is the single most important borrowed field in the
  demo** — it is what stops the pitch freezing at second 60 if nobody taps
  approve. And copy the **subscribe-BEFORE-publish** ordering
  (`nats_client.py:1318-1400`) so a fast responder can never race ahead of the
  listener.

## 10. Fallback ladder, pre-agreed

If the engine dies mid-demo: cut to the recorded clip and keep narrating. If
`tool_falkordb` misbehaves, route the agent through the bridge's
`recall`/`ring` verbs instead — the agent still **chooses** the tool, the trace
still proves it, and the RocketRide story survives intact.

Cut order for a long rehearsal, agreed at hour −1 so nobody argues at 7:35:
**kill-and-resume → judge-taps-approve → hybrid vector query.**
**Never the rewind A/B.**

## 11. Repo hygiene (non-negotiable)

No credentials in this repo, ever. `ROCKETRIDE_API_KEY=MYAPIKEY` is the
upstream-published local dev default for an MIT self-hosted engine with no
signup — it is not a credential, and it is the only key-shaped literal allowed
anywhere in this tree. Everything else is bring-your-own-key via `.env.example`.
Secret-scan before every commit; a leaked key in git history cannot be un-leaked.
