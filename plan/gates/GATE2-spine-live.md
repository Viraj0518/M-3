# GATE 2 receipt — the spine is LIVE end-to-end on the Mac

**Status: GREEN** · 2026-08-03 ~12:50Z · bridge server up, both data planes reachable, UI on real data.

## What runs

- **Bridge**: `app/bridge/rest.py` (uvicorn, py3.12 .venv), `127.0.0.1:8931`, pid in `app/bridge/bridge.pid`, log `app/bridge/bridge.log`. Commit `a332c5c`.
- **FalkorDB**: container `palimpsest-falkordb`, host `127.0.0.1:6401`.
- **LaserData**: laser-stack iggy+plane healthy, host `127.0.0.1:8090` / `3000`.
- All on ONE kernel-6.14 lima VM (`laser25`).

## Verified (protocol-level, per the fleet rule — no /dev/tcp)

```
GET /health → 200  {ok:true, git_sha:a332c5c…, falkordb:{reachable:true, latency_ms:~3},
                    laser:{reachable:true, endpoint:127.0.0.1:8090},
                    mcp:{mounted:true, path:/mcp, transport:streamable-http}}
GET /graph  → 200  12 nodes / 9 edges, real FalkorDB, contributors per author_agent
GET /ring   → 200  fired=true, ring_score 0.6667, should_open_case=true,
                   actors [Northstar-7, VectorMint, SableWeather], run_time_ms 2.358
POST /mcp initialize → 200, tools/list = 12, tools/call palimpsest_ring → live FalkorDB hit
```

- Memory plane: **106 tests green** against the live container (79 memory + 27 REST); **8 mutation probes each turn the suite red** — guards proven load-bearing.
- UI reconciliation: bridge serves the UI's bare paths (`/graph`, `/ring`, `/stream_tail`) AND `/v1/<path>`; alias set asserted against `app/web/index.html` source so a UI change breaks a test, not the stage. UI mode = **LIVE** (12 nodes parse, all 9 edges survive endpoint filter, all 5 ring ids present).
- `stream_tail` = honest `stub:true` 200 (not_implemented but well-formed) until the stream verbs land; UI stream strip shows its built-in placeholder, graph is live.

## Recurring infra gotcha BANKED (cost two debugging cycles)

lima's hostagent forwards a container port to the SAME host port; if that host port
was momentarily held during a VM/container migration, the forward logs
`bind: address already in use` and **does not auto-retry**. Symptom: container healthy
inside the VM, `connection refused` from the host. **Fix: `docker restart <container>`**
inside the VM re-fires the forward. Seen on both 6401 (FalkorDB VM migration) and 8090
(iggy). Add a forward-health check to the fresh-stack walkthrough.

## Still open (not blocking Gate 2)

- Real bridge Dockerfile + directive-7 fresh-container walkthrough (next).
- stream_publish/stream_tail/stream_replay/act verbs (LaserData wiring lane).
- RocketRide `.pipe` + Guild agents (motion/coordination lanes).
- Windows compose re-port to 8931 + /health (in flight).
