# SPONSOR-GITHUB PLAYBOOK — final sprint

Synthesized from the 4 sponsor-GitHub findings + live ground-truth probes on this box (2026-08-03).
Verified this session: `rocketride` is NOT installed in `.venv`; engine PID **9858** live on `127.0.0.1:5565` (`/services` → 200); all **10** FalkorDB `algo.*` procedures compiled into the live `:6401` build; laser-sdk 0.0.1 + iggy live on `:8090`.

> **KEY CAVEAT for the build agent:** the brief points at `M-3-lme/.env` for the Nebius key — **that path does NOT exist on this box**, and the running engine (9858) has **no** `ROCKETRIDE_NEBIUS_*` in its env. Before RocketRide step 3 you MUST locate the real Nebius key (OpenAI-compat) and export it into the ENGINE's process env, or `${ROCKETRIDE_*}` substitution resolves empty and the wave LLM call fails. Do not inline the key in the `.pipe` — env-substitution only.

---

## 1) ROCKETRIDE — EXACT ordered steps to fire a real parallel wave (the $1000)

Ground truth: the 401 was **two** bugs — (a) `/api/*` is a phantom prefix (auth catch-all → 401 or route-absent → 404); the real REST surface is `/task`, `/task/data`, `/task/process`, `/webhook`, `/status`, `/services`, `/version`, and the SDK talks **WebSocket** to `/task/service`. (b) The credential is read from `Authorization: Bearer <key>` (or `?auth=`), NOT `X-API-Key`. OSS mode compares one shared secret `ROCKETRIDE_APIKEY` (currently `MYAPIKEY`) with `hmac.compare_digest`. **Fix = drive the engine with the SDK over WS; never hand-roll `/api/*`.**

### Step 0 — locate the Nebius key (PRECONDITION, brief path is stale)
```
# brief said M-3-lme/.env — it does NOT exist. Find the real key first:
grep -rl NEBIUS /Users/tenzinyeshi 2>/dev/null | grep -v node_modules | head
# capture it as $NEBIUS_KEY in your shell (do NOT echo it into any committed file)
```

### Step 1 — install the SDK into the project venv (fixes ModuleNotFoundError)
Version-exact path (guarantees client 1.3.0 ↔ engine 3.3.1 parity, dodges the py3.9→1.0.0 silent-mismatch trap):
```
/Users/tenzinyeshi/.local/bin/uv pip install \
  --python /Users/tenzinyeshi/memory-meets-motion/.venv/bin/python \
  "/private/tmp/claude-501/-Users-tenzinyeshi/b39df6cb-a185-4f35-bfcc-2988940d3e3d/scratchpad/rr-engine/static/clients/python/rocketride-1.3.0-py3-none-any.whl"
# fallback: /Users/tenzinyeshi/memory-meets-motion/.venv/bin/pip install "rocketride==1.3.0"
# verify:
/Users/tenzinyeshi/memory-meets-motion/.venv/bin/python -c "import rocketride; print(rocketride.__version__)"   # -> 1.3.0
```

### Step 2 — prove auth on a REAL endpoint (not `/api/*`)
```
/usr/bin/curl -sS -o /dev/null -w '%{http_code}\n' -H 'Authorization: Bearer MYAPIKEY' http://127.0.0.1:5565/services   # -> 200
```

### Step 3 — put LLM keys in the ENGINE's env and relaunch so `${ROCKETRIDE_*}` resolves
The wave LLM key must live in the **engine process** env (not the client, not the .pipe):
```
kill 9858
cd /private/tmp/claude-501/-Users-tenzinyeshi/b39df6cb-a185-4f35-bfcc-2988940d3e3d/scratchpad/rr-engine
export ROCKETRIDE_APIKEY=MYAPIKEY
export ROCKETRIDE_NEBIUS_KEY="$NEBIUS_KEY"     # from Step 0; NEVER inline in the pipe
./engine ai/eaas.py &
# READY when it logs: Uvicorn running on http://localhost:5565
# FIRST BOOT bootstraps ~100 wheels (~4 min) and LOOKS HUNG — DO NOT KILL.
```

### Step 4 — write `motion/palimpsest.pipe` (minimal single-wave parallel fan-out)
One `agent_rocketride` with control-attached `llm_nebius` + `memory_internal` + `tool_falkordb` (`:6401`, graph `palimpsest`, `allow_writes:true`) + `tool_http_request` (serverName `act`). Output node is `response_answers`. **Wiring law:** each component = `{id, provider, config, EITHER input OR control}`; DATA = `input:[{lane,from}]`; TOOL/LLM/MEMORY attach = `control:[{classType,from:<AGENT_id>}]` declared **on the tool/llm/memory node pointing back at the agent**. `agent_rocketride` needs exactly one llm + one memory.
```json
{ "pipeline": { "project_id": "palimpsest-0001", "source": "webhook_1", "components": [
  {"id":"webhook_1","provider":"webhook","config":{}},
  {"id":"question_1","provider":"question","config":{},"input":[{"lane":"text","from":"webhook_1"}]},
  {"id":"agent_1","provider":"agent_rocketride","config":{"profile":"default","default":{
     "max_waves":12,
     "agent_description":"Palimpsest commander: decides escalate|dismiss on a wiki edit.",
     "instructions":["In ONE wave, call BOTH tools in parallel: `act.http_request` to fetch the live page (NOW) AND `graph.query` with a 3-hop co-edit Cypher (EVER). Then in the next wave decide.","Return done=true with answer 'ESCALATE' or 'DISMISS' and the provenance."]}},
   "input":[{"lane":"questions","from":"question_1"}]},
  {"id":"llm_1","provider":"llm_nebius","config":{"profile":"llama-3-3-70b","name":"Nebius Llama 3.3 70B"},
   "control":[{"classType":"llm","from":"agent_1"}]},
  {"id":"mem_1","provider":"memory_internal","config":{},
   "control":[{"classType":"memory","from":"agent_1"}]},
  {"id":"graph","provider":"tool_falkordb","config":{"type":"tool_falkordb","tool_falkordb":{
     "host":"127.0.0.1","port":6401,"graph":"palimpsest","allow_writes":true,"max_rows":250}},
   "control":[{"classType":"tool","from":"agent_1"}]},
  {"id":"act","provider":"tool_http_request","config":{"http_request":{"serverName":"act",
     "allowGET":true,"allowPOST":true,"urlWhitelist":[]}},
   "control":[{"classType":"tool","from":"agent_1"}]},
  {"id":"out_1","provider":"response_answers","config":{"laneName":"answers"},
   "input":[{"lane":"answers","from":"agent_1"}]}
] } }
```

### Step 5 — write `motion/run_wave.py` (validate → use(ttl=0,trace=full) → stream SSE → send → read trace → terminate)
```python
import asyncio, json
from rocketride import RocketRideClient
def on_event(msg):
    b = msg.get("body") or {}
    if msg.get("event") == "apaevt_sse":
        print("THINK:", b.get("type"), b.get("message") or b.get("text"))
async def main():
    async with RocketRideClient(uri="ws://127.0.0.1:5565", auth="MYAPIKEY", on_event=on_event) as c:
        pipe = json.load(open("motion/palimpsest.pipe"))
        await c.validate(pipeline=pipe.get("pipeline", pipe))          # validate BEFORE every use()
        r = await c.use(filepath="motion/palimpsest.pipe", source="webhook_1",
                        ttl=0, pipelineTraceLevel="full")               # ttl=0 no idle-death; full captures _trace
        token = r["token"]
        await c.set_events(token, ["apaevt_sse","apaevt_node_started","apaevt_node_finished","apaevt_node_error"])
        out = await c.send(token, json.dumps({"case_id":"c1","edit":{}}), mimetype="application/json")
        for w in (out.get("_trace") or {}).get("waves", []):
            print("WAVE", w["wave_num"], "PARALLEL:", [tc["tool"] for tc in w["calls"]])
        print("VERDICT:", out)
        await c.terminate(token)
asyncio.run(main())
```

### Step 6 — run + capture the GATE-ROCKET artifact
```
/Users/tenzinyeshi/memory-meets-motion/.venv/bin/python motion/run_wave.py
```
GATE = some `out["_trace"]["waves"][k]["calls"]` has **≥2 tools** (`act.http_request` + `graph.query` in ONE wave) AND `answer ∈ {ESCALATE, DISMISS}`. Screenshot the `_trace` + the `apaevt_sse` thinking log into `plan/gates/GATE-ROCKET.md`.

### Step 7 (post-gate) — make it load-bearing in the bridge
`app/bridge/server.py` `_h_act` (~line 1128) is an intentional stub pinned by `test_surfaces.py::test_act_is_still_an_honest_stub` (`STUB_VERBS={"act"}`). On an ESCALATE decision: (i) invoke `motion/run_wave.py` / POST the case to the engine, (ii) `MERGE (:Action)-[:RELATES{relation:'derived_from'}]->(:Event)` into FalkorDB, (iii) publish `action.executed` to LaserData with an `idempotency_key`; then move `"act"` from `STUB_VERBS` → `LIVE_VERBS` with a real assertion.

**Fallback** if experimental `tool_falkordb` won't complete a live Cypher round-trip: add an `mcp_client` node (transport Streamable HTTP) at our bridge and instruct the agent to use its `recall`/`ring` verb instead — the trace still proves tool choice, RocketRide story intact.

---

## 2) Single highest-leverage move per remaining sponsor

### FalkorDB — SHIP THE `ringleader` VERB (native graph algorithms)
Highest leverage: we use FalkorDB deeply but call **zero** of its 10 compiled `algo.*` procedures. Add ONE verb that PageRank-names the ringleader + LabelPropagation-auto-discovers the collusion cell — pure topology, no LLM, no key. Removal test: without it the demo shows a ring but cannot name its leader or cluster the cell.

Diff in `app/bridge/server.py` — paste `_COEDIT_PROJECT` + `async def _h_ringleader` right after `_h_ring` (after line 921), register in `_VERB_DISPATCH` (~1413) under `"ring"`:
```python
"ringleader": ("POST", "/v1/ringleader", _passthrough, _h_ringleader),
```
`_h_ringleader`: `graphstore.mutate(_COEDIT_PROJECT)` then two read-only queries — `CALL algo.pageRank('Actor','CO_EDITED_WITH') YIELD node,score ...` and **the map-config form** `CALL algo.labelPropagation({nodeLabels:['Actor'],relationshipTypes:['CO_EDITED_WITH']}) YIELD node,communityId ...`. **TRAP (verified): labelPropagation / WCC / betweenness SILENTLY return `[]` in the positional form — MUST use map-config.** Reuses `graphstore.mutate/query`, `_graph_key`, `_ensure`, `_int`, `ok`, `asyncio.to_thread` verbatim — no new imports. Verify:
```
.venv/bin/python -c "from falkordb import FalkorDB; g=FalkorDB(host='127.0.0.1',port=6401).select_graph('palimpsest'); print(g.ro_query(\"CALL algo.pageRank('Actor','CO_EDITED_WITH') YIELD node,score RETURN node.name ORDER BY score DESC LIMIT 3\").result_set)"
.venv/bin/python -m pytest app/bridge/tests/test_ring.py app/bridge/tests/test_graph_projection.py -q
```
For the crispest on-camera cell, call with the ablation warm key: `{"graph":"palimpsest_ablation_warm"}`; put its `run_time_ms` on screen next to the ring's 2.45ms.

### LaserData — FIX THE TAIL + WALL-CLOCK TIME-SCRUB
Highest leverage = one correctness fix that unlocks the headline beat. `app/bridge/stream.py::tail()` is **NOT a tail** — it calls `drain_from_zero(topic, max_records=10_000)` then returns `records[-limit:]`, i.e. the last `limit` of the FIRST 10k records from offset 0. At 50–100 ev/s it goes permanently stale (~offset 9,975) in 2–3 min while the tool doc promises "what is happening RIGHT NOW." Back it with a live consumer across the 4 partitions and take the newest `limit`:
```python
c = await log.topic(topic).consumer("ui-tail", partition=p, polling="last",
                                    batch_length=limit, auto_commit="disabled")
```
Then thread `timestamp_micros=` through `realtime/laser_io.py` consumer helpers to unlock the money beat: `consumer(polling="timestamp", timestamp_micros=T, allow_replay=True)` — wall-clock rewind ("rewind the NOW to the instant BEFORE the ring's 4th edit; same agent says DISMISS; step forward one record → flips to ESCALATE"). **`Topic.replay()` has NO timestamp seek — wall-clock lives ONLY on `Topic.consumer`.** Conn string is `iggy:laser@127.0.0.1:8090` (**NOT `iggy:iggy`** — the mismatch masquerades as a network error). Verify:
```
python -c "import laser_sdk as L; print(L.Topic.consumer.__text_signature__)"   # confirm kwargs on THIS wheel
python -m realtime.laser_smoke
```

### Guild.ai — HONEST CEILING + free-tier path
**Ceiling (definitive):** there is NO local/offline/mock runtime — Guild agent code runs ONLY in Guild's server-side sandbox; the SDK `@guildai/agents-sdk` can't even `npm install` without `guild auth login` (private-registry token). This box has NO guild CLI, NO `~/.guild`, and `agents/` is empty. So a live Guild demo has a **hard network+login dependency** we cannot pre-bake.
**BUT it is NOT paid-seat-gated:** Guild is in open beta with a self-serve **free tier** (no credit card); a single browser `guild auth login` → personal workspace → `init/test/publish/run` a full multi-agent orchestration solo. The 500-LLM-call / 50M-token limits are universal guardrails, not tier gates; "seats" only gate shared/team workspaces.
Highest-leverage move IF you commit ~30 min + network: `npm install -g @guildai/cli@0.17.0 && guild auth login && guild workspace select <ws>`, then publish specialists BEFORE the orchestrator (the `/tool` package only exists post-publish) and demo `guild session events <id>` as the coordination receipt. **Do NOT `pip install guildai`** (unrelated legacy ML decoy).
**HONEST FALLBACK (recommend for the room):** drop Guild live and let the **RocketRide** orchestrator (127.0.0.1:5565) carry the multi-agent story — `agent_rocketride`'s wave planner delegating to a researcher sub-wave IS real multi-agent coordination — with the bridge `ask` verb as the human gate. State the substitution plainly: "Guild is our intended control plane; the live coordination shown is RocketRide sub-agent delegation."

---

## 3) One-line honest load-bearing claim per sponsor (for submission)

- **RocketRide:** A live wave in RocketRide's own engine (build 3.3.1) fires `act.http_request` (live page = NOW) and `tool_falkordb.query` (co-edit ring = EVER) **concurrently in a single wave**, then decides ESCALATE/DISMISS — proven, not asserted, by `_trace.waves[k].calls` containing ≥2 tool_calls.
- **FalkorDB:** FalkorDB doesn't just prove a ring exists — its native `algo.pageRank` **names the ringleader** and `algo.labelPropagation` **auto-discovers the collusion cell** (unsupervised, recovers exactly {ring}, excludes the bystander) — pure in-engine topology no vector store can compute.
- **LaserData:** A wall-clock `polling="timestamp"` consumer turns our replay into a **time-scrubber** — rewind the durable log to the instant before the ring's 4th edit and the same agent flips DISMISS→ESCALATE as you step one record forward, proving the log (not the graph) carries the motion.
- **Guild.ai:** Guild is our intended agent control plane (free-tier live coordination via `guild session events`); when the room has no network we substitute RocketRide sub-wave delegation and say so plainly — no overclaim.
