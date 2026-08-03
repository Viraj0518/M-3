# Deploy

[![ci](https://github.com/Viraj0518/M-3/actions/workflows/ci.yml/badge.svg)](https://github.com/Viraj0518/M-3/actions/workflows/ci.yml)
[![deploy](https://github.com/Viraj0518/M-3/actions/workflows/deploy.yml/badge.svg)](https://github.com/Viraj0518/M-3/actions/workflows/deploy.yml)

## Public demo (Cloudflare Pages)

The projector UI is deployed as a static site — it runs self-contained in **MOCK mode**
(deterministic canned data) with no backend, so the public page is a working preview of
the live demo without needing the local stack.

- **Live:** https://palimpsest-740.pages.dev
- Project: `palimpsest` (Cloudflare Pages), production branch `main`.

Redeploy after a UI change:

```bash
npx wrangler pages deploy app/web --project-name palimpsest --branch main
```

When the bridge is reachable at its origin the same page flips from MOCK to LIVE
(graph + ring off real FalkorDB); on `*.pages.dev` there is no bridge, so it stays MOCK.

**Optional LIVE backend:** the hosted bridge below (`https://palimpsest-bridge.fly.dev`)
is a real, self-contained FalkorDB-backed backend. Point the Pages UI at it (open
`https://palimpsest-740.pages.dev/?bridge=https://palimpsest-bridge.fly.dev`, or set
the bridge origin the UI reads) and the graph + ring go LIVE off real rows instead of
the canned mock. The stream strip stays declared-empty there (the LaserData spine is a
local-only, kernel-gated service — see below).

## Hosted bridge (Fly.io)

- **Live:** https://palimpsest-bridge.fly.dev — `GET /health`, `GET /graph`, `POST /ring`,
  MCP at `/mcp`, OpenAPI at `/v1/openapi.json`, projector mirror at `/ui/`.
- App: `palimpsest-bridge` (Fly.io, org `personal`, region `sjc`).

**Architecture — ONE self-contained machine.** A single Fly container runs *both*
FalkorDB (redis-server + the graph module, on `127.0.0.1:6401` inside the container)
*and* the bridge (`0.0.0.0:8931`), so the hosted demo needs no external database. On
boot the container seeds a real co-edit ring (`app/bridge/seed_demo.py`) so `/graph` and
`/ring` are non-empty for the Pages UI. The graph is **ephemeral** (no volume) and
re-seeded on every boot — exactly what a stateless demo backend wants. FalkorDB is bound
to loopback only; nothing outside the container can reach the database directly. The
LaserData stream lane is intentionally *not* in this image (it needs a kernel ≥ ~6.11
host); the stream verbs degrade to an honest declared-empty tail, and `/health` reports
`laser.reachable=false` while `falkordb.reachable=true`.

Build/deploy inputs live under `fly/`: `fly/Dockerfile` (single container, base
`falkordb/falkordb`), `fly/entrypoint.sh` (start FalkorDB → wait for PING → seed → serve
the bridge, forwarding SIGTERM to both), `fly/fly.toml`. The build context is the **repo
root** (the Dockerfile COPYs `app/` and `memory/`); `.dockerignore` at the repo root
keeps `.git`, any local `.env`, the venvs and local data out of the builder tarball.

Cost control (hackathon): `auto_stop_machines = "suspend"`, `auto_start_machines = true`,
`min_machines_running = 0`, a 512 MB shared-cpu-1x VM, and a `GET /health` check.

### How CD works

- **`.github/workflows/ci.yml`** runs on every push and PR: (1) the bridge test suite
  (`pytest app/bridge/tests`) against a FalkorDB service on 6401, (2) the anti-phantom
  saboteur gate (`eval gate --negative fixtures/negative`, 15/15, no keys, no DB) plus a
  guarded splits-determinism check, and (3) a guarded `docker compose config` validation
  of `deploy/docker-compose.yml` when that file exists.
- **`.github/workflows/deploy.yml`** runs on push to `main`. It re-runs the SAME bridge
  tests and anti-phantom gate **inline first**, so a red `main` never deploys, then
  `flyctl deploy --config fly/fly.toml --dockerfile fly/Dockerfile --remote-only`.
  Auth is the `FLY_API_TOKEN` GitHub Actions secret — no token is ever committed.

Manual deploy (from the repo root):

```bash
flyctl deploy --config fly/fly.toml --dockerfile fly/Dockerfile --remote-only
```

Local image smoke test (no Fly, no keys):

```bash
docker build -f fly/Dockerfile -t palimpsest-bridge-test .
docker run -d -p 18931:8931 --name pbtest palimpsest-bridge-test
curl -s http://127.0.0.1:18931/health   # ok:true, falkordb.reachable:true
curl -s http://127.0.0.1:18931/graph    # seeded nodes present
docker rm -f pbtest
```

## Local full stack (the real demo)

Everything runs locally — no cloud dependency, bring-your-own-key (see `.env.example`):

- **FalkorDB** (memory) — container, `127.0.0.1:6401`
- **LaserData** (log spine) — laser-stack iggy+plane; requires a **kernel ≥ ~6.11** host
  (the `-ld` iggy fork needs recent io_uring opcodes — see `plan/gates/GATE0-laserdata-local.md`
  for the kernel matrix and a no-admin macOS recipe via lima Ubuntu 25.04)
- **Bridge** (REST + MCP + OpenAPI + CLI) — `127.0.0.1:8931`, `GET /health`
- **UI** — `app/web/index.html`, served by the bridge at `/ui/` or any static host

The container compose for the full stack lands under `deploy/` (final-deploy lane).
Definition of done for any milestone: commit → fresh container build → old containers
removed → full end-to-end walkthrough on the fresh stack.
