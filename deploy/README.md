# PALIMPSEST — deploy

Everything local, everything in Docker, no credentials anywhere.

This directory is the whole runtime. `up.sh` performs the directive-7 cold start:
destroy the previous stack **including its volumes**, build, start, then **block
until every container reports healthy** — no sleeps, no "give it a few seconds".

The `bridge` service here is **the real `app/bridge/`**, not a stand-in: the same
dispatch table that serves REST, MCP streamable-http, the OpenAPI 3.0 document
and the CLI, talking real Cypher to FalkorDB.

---

## Prerequisites

- Docker Engine 24+ with Compose v2 (verified on **Docker 28.5.1**, Compose v2)
- `bash` — git bash on Windows is fine and is what this was verified on
- **Network at build time, once.** The bridge image runs
  `pip install -r app/bridge/requirements.txt`. The `ui` image needs no network
  at all (no `npm install`, no `apk add`). Build before you need it; the
  dependency layer is cached because `requirements.txt` is copied on its own
  line, so editing bridge source does not re-run the install.

## Quick start

```bash
cd deploy

# core stack: FalkorDB (+ bundled falkordb-browser), the real bridge, the projector
./up.sh

# core stack + LaserData log spine (Apache Iggy + LaserData plane)
./up.sh --profile logspine      # or the shorthand: ./up.sh --logspine

# tear everything down, volumes included
./down.sh
```

`up.sh` copies `.env.example` to `.env` on first run. `.env` is gitignored.
**There is not a single real credential in this repository** — see
[No credentials](#no-credentials).

### What comes up

| service | host | container | healthcheck |
| --- | --- | --- | --- |
| `falkordb` (graph / RESP) | `127.0.0.1:6401` | 6379 | `redis-cli PING` **and** an HTTP probe of the bundled browser |
| `falkordb-browser` (UI) | `127.0.0.1:3100` | 3000 | same container, same healthcheck — see note below |
| `bridge` | `127.0.0.1:8931` | 8931 | `GET /health` performs a **real Cypher `RETURN 1`** |
| `ui` (projector, static host) | `127.0.0.1:5173` | 5173 | `GET /index.html` |
| `iggy` *(profile `logspine`)* | `127.0.0.1:8090`, `:3000` | 8090, 3000 | `/usr/local/bin/iggy-healthcheck` |
| `plane` *(profile `logspine`)* | — | 8089 | `/usr/local/bin/plane-healthcheck` |

Surfaces on the bridge's one port:

```
GET  /health                 the compose healthcheck contract (below)
GET  /ui/                    the projector, same-origin with the bridge
GET  /graph  /ring  /stream_tail      the three bare aliases the projector polls
POST /v1/remember  /v1/relate  /v1/recall  /v1/ring  /v1/handover  /v1/ask
GET  /v1/graph  /v1/handover  /v1/stream/{topic}/tail
GET  /v1/openapi.json        the Guild Integration document, generated from the table
     /mcp                    MCP streamable-http, SAME port, no redirect on the bare path
```

### The bridge contract — read out of the code, not invented

Every value below comes from `app/bridge/` and `memory/config.py`. If you change
one, change it there and let the compose file follow.

| what | where | value |
| --- | --- | --- |
| port | `memory/config.py:BRIDGE_PORT` (`_env_int("BRIDGE_PORT", 8931)`) | **8931** |
| health route | `app/bridge/rest.py:403` | **`GET /health`** |
| graph endpoint | `memory/config.py:FALKORDB_HOST` / `FALKORDB_PORT` | env-driven |
| projector mount | `app/bridge/rest.py:477` (`config.REPO_ROOT/app/web`) | **`/ui/`** |

`GET /health` on a healthy stack — **real output from this stack**:

```json
{"ok":true,"service":"palimpsest-bridge","version":"0.1.0","git_sha":"unknown",
 "bridge":"0.0.0.0:8931",
 "falkordb":{"reachable":true,"latency_ms":1.789,"endpoint":"falkordb:6379"},
 "laser":{"reachable":false,"endpoint":"127.0.0.1:8090","latency_ms":0.286,"probe":"tcp","cached":false},
 "mcp":{"mounted":true,"path":"/mcp","transport":"streamable-http"},
 "graphs":{"warm":"palimpsest","cold":"palimpsest_cold"}}
```

`/health` is the **only** route that returns a non-2xx. Every verb envelope rides
on a 200 unless it carries an `err()` `code`, because the projector's `fetchJson`
throws on `!response.ok` and `pollBridge` reads a throw on `/graph` as BRIDGE
OFFLINE → mock graph. A not-yet-implemented verb is not an offline bridge. A
container orchestrator, on the other hand, needs the failure in the status line,
which is why `/health` is carved out.

**Why `8931` is load-bearing in three places at once.** `memory/config.py`
defaults to it; `app/web/index.html:581` hard-codes `http://127.0.0.1:8931`; and
the MCP Apps contract derives the widget sandbox domain from a **sha256 of the
endpoint string** (`plan/research/mcp-widgets-guide.md` §2.3 — "must match the
connector URL exactly"), so the gate scripts' domain hash changes if the port
changes. It is also why the bare `/mcp` route is an exact `Route` inserted ahead
of the `Mount`: a `Mount` alone 307s `/mcp` → `/mcp/`, and a client that follows
the redirect ends up on a different endpoint string than the one the domain hash
was computed from.

**Why `6401` on the host but `6379` in the container.** `memory/SCHEMA.md` §5 and
`memory/config.py:52-59` pin 6401 because 6379 (redis default) and 6399 (a stray
`redis-server` verified live on a build box) produce a *false green*: the client
connects fine over RESP and then fails with `unknown command GRAPH.QUERY`. That
is a **host** trap — it is about what else is listening on your laptop. Inside
this compose network the name `falkordb` resolves to exactly one container, the
stock image on its own 6379, and there is no stray anything to collide with. So
the bridge dials `falkordb:6379` while every human-facing surface — `redis-cli`,
`eval/README.md`, GATE2 — keeps using `127.0.0.1:6401`. Both ports are
`.env`-overridable (`PALIMPSEST_FALKORDB_PORT` /
`PALIMPSEST_FALKORDB_CONTAINER_PORT`).

**Why the bridge binds `0.0.0.0` inside the container.** `memory/config.py`
defaults `BRIDGE_HOST` to `127.0.0.1`, which is right for a laptop process and
fatal in a container — uvicorn would bind the container's own loopback and the
published port would connect-refuse. The safety lives on the *host* side of the
mapping: every `ports:` entry binds `${PALIMPSEST_BIND_ADDRESS}`, `127.0.0.1` by
default, so nothing in this stack is reachable off the machine.

### Why `falkordb-browser` is not its own service

The `falkordb/falkordb` image already ships and supervises the browser on
container port 3000 inside the same container. Splitting it out would mean
running the same image twice and suppressing the second `redis-server`, or
chasing a browser-only image FalkorDB does not publish. Both are more moving
parts for zero benefit, and both would let the healthcheck lie: the UI is only
meaningful when its own graph process is up, which one container gives us for
free. The single healthcheck gates **both** surfaces.

### Cold-start gating (directive 7)

`depends_on` uses `condition: service_healthy` everywhere, so the start order is
enforced by the runtime. **Real output** from `./up.sh` on a fresh tree
(containers and volumes destroyed first):

```
 Container palimpsest-falkordb  Started
 Container palimpsest-falkordb  Waiting
 Container palimpsest-falkordb  Healthy
 Container palimpsest-bridge    Starting
 Container palimpsest-bridge    Started
 Container palimpsest-bridge    Waiting
 Container palimpsest-bridge    Healthy
 Container palimpsest-ui        Starting
 Container palimpsest-ui        Started

==> Waiting for every service to report healthy (timeout 300s, no sleeps in the gate)
  OK   all 3/3 services healthy

==> Readiness summary
  OK   falkordb   RESP  127.0.0.1:6401   PING -> PONG
  OK   falkordb   GRAPH GRAPH.QUERY create+match round-trip -> 1 node
  OK   browser    UI     -> HTTP 200   http://127.0.0.1:3100/
  OK   bridge     HEALTH -> HTTP 200   http://127.0.0.1:8931/health
  OK   bridge     UI     -> HTTP 200   http://127.0.0.1:8931/ui/
  OK   bridge     GRAPH  -> HTTP 200   http://127.0.0.1:8931/graph
  OK   ui         PAGE   -> HTTP 200   http://127.0.0.1:5173/index.html
  OK   bridge     GRAPH falkordb reachable from inside the bridge
```

There is not a single `sleep` in the readiness gate. The one `sleep 1` in `up.sh`
is the poll interval of a loop that reads the **runtime's own health state** —
a poll of truth, not a blind wait standing in for readiness.

---

## REMOVAL TESTS

Each sponsor technology, and a way to prove it is **load-bearing** rather than
decorative: a command you can run against a live stack and an observable failure.

Bring the stack up first (`./up.sh`), then seed something real:

```bash
docker exec palimpsest-falkordb redis-cli GRAPH.QUERY palimpsest \
  "CREATE (a:Actor {name:'deploy-probe'})-[:EDITED {ts:1}]->(p:Page {title:'cold-start-receipt'})
   RETURN a.name, p.title"
```

### 1. FalkorDB attributed graph — the memory of record

**Disable it**

```bash
docker compose -f docker-compose.yml stop falkordb
```

**Observable failure** — VERIFIED on this stack, immediately:

```console
$ curl -sS -w '\n__CODE__%{http_code}' http://127.0.0.1:8931/health
{"ok":false, ... ,"falkordb":{"reachable":false,"latency_ms":3981.226,
  "endpoint":"falkordb:6379",
  "error":"GraphUnavailable: FalkorDB at falkordb:6379 rejected the query:
           ConnectionError: Error -2 connecting to falkordb:6379."},
 "code":"FALKORDB_UNAVAILABLE",
 "error":"the memory plane at falkordb:6379 is unreachable — the bridge is up
          but has nothing to remember with."}
__CODE__503
```

and **14 seconds later** the runtime itself agrees — this is the compose
healthcheck, not our own assertion:

```console
$ docker compose ps
SERVICE   STATUS
bridge    Up About a minute (unhealthy)
ui        Up About a minute (healthy)
```

`docker inspect` keeps the 503 body in the health log, so the *reason* is in the
runtime too, not only in the terminal you happened to be watching:

```
"ExitCode":1,"Output":"503 {\"ok\": false, \"code\": \"FALKORDB_UNAVAILABLE\",
  \"falkordb\": {\"reachable\": false, \"endpoint\": \"falkordb:6379\", ...}}"
```

**Recovery** — VERIFIED: `docker compose start falkordb` and the bridge was
`healthy` again **3 seconds** later, with the seeded graph intact (named volume +
RDB snapshot).

**Cold-start variant** — the strongest one. With FalkorDB unable to go healthy,
`./up.sh` never reaches the bridge at all: compose refuses to start a dependant
of an unhealthy dependency (`dependency failed to start: container
palimpsest-falkordb is unhealthy`). There is no code path in which the bridge
runs without its graph.

### 2. Per-agent identity + handover — "who it is" / cold resume

Attribution and continuity live in the **same** graph as everything else, which
is the architectural claim: the agent's self-knowledge and its knowledge of the
world are one query away from each other, not two systems to reconcile. Every
write is stamped `author_agent` server-side (`app/bridge/identity.py`) — the
request body never gets to name its own author.

**Disable it**

```bash
docker exec palimpsest-falkordb redis-cli GRAPH.QUERY palimpsest \
  "MATCH (a:Agent)-[h:HANDED_OFF_TO]->(b:Agent) DELETE h"
```

`GET /v1/handover?agent_id=<id>` then returns `{"ok":true,"handover":null,
"reason":"no handover row for agent ..."}` — an honest miss, never a fabricated
resume. A cold restart has nothing to resume from: the agent cannot say where it
left off, and falls back to a from-scratch greeting. That is the difference
between an agent that reboots and an agent that *continues*.

### 3. LaserData log spine (Apache Iggy + LaserData plane) — constant datastream

**Disable it** — it is disabled by default. Enable with `--profile logspine`.

With the spine gone there is no append-only record to replay from offset 0: the
graph still holds the *conclusions* but nothing holds the *stream* they were
derived from, so a wiped graph cannot be rebuilt and nothing is auditable.
`/health` reports `laser.reachable:false` rather than pretending, and the
`stream_*` verbs return honest `status:"not_implemented"` envelopes that never
invent a record.

Also verified: `plane` refuses to start when `iggy` is unhealthy
(`dependency failed to start: container palimpsest-iggy is unhealthy`) — the
spine is gated, not best-effort.

> ### Why the spine is behind a compose profile — READ THIS
>
> It is **not** optional to the architecture. It is profiled out because the
> default dev/judging **host kernel is too old to run the fork**.
>
> **Confirmed root cause.** The `-ld` iggy fork (`laserdatainc/iggy-server`,
> which `laser-plane` REQUIRES) issues io_uring operations whose opcodes landed
> in **~Linux 6.11** — suspect class `IORING_OP_BIND` / `IORING_OP_LISTEN`. On
> older kernels the ring *sets up fine* and then panics at runtime when an
> unsupported op is issued and is not offloaded to a worker thread:
>
> ```
> thread 'shard-0' panicked at compio-driver-0.12.4/src/asyncify.rs:118:25:
> the thread pool is needed but no worker thread is running
> Error: ShardJoinFailures { failures: [ShardJoinFailure { shard_id: 0,
>   kind: Panic { message: "the thread pool is needed but no worker thread
>   is running" } }] }
> ```
>
> **Verified kernel matrix** — seccomp **and** apparmor `unconfined` in every
> row, both confirmed applied via `docker inspect`:
>
> | Host | Kernel | Arch | Result |
> |---|---|---|---|
> | Docker Desktop / WSL2, Windows 11 | 6.6.87 | amd64 | **FAIL** |
> | stock Ubuntu 24.04, colima VZ | 6.8.0-117 | arm64 | **FAIL** |
> | stock Ubuntu 24.04 — upstream `apache/iggy` control | 6.8.0-117 | arm64 | **PASS** (proves it is not the kernel generically) |
> | Ubuntu 25.04, lima VZ | **6.14.0-37** | arm64 | **PASS** — shards start, full smoke pass |
>
> Also controlled for: root vs uid-10001, `kernel.io_uring_disabled = 0`,
> `:latest` vs `:edge` tags, plane on and off.
>
> **It is NOT a WSL2-specific defect.** It reproduces identically on stock
> Ubuntu 24.04 under colima with no WSL anywhere in the picture. The
> `Environment: WSL2 (Microsoft kernel fork detected)` banner iggy prints on the
> Windows host is incidental and misled an earlier revision of this file; the
> discriminator is the **kernel version**.
>
> **And upstream is not a substitute.** `laser-plane` is VSR-locked to the fork:
> pointed at `apache/iggy` it fails with `Timed out waiting for VSR response
> header, code 12`. "Just use upstream" is not a fallback — the whole spine needs
> the fork, which needs >= ~6.11.
>
> So on a >= 6.11 host, `./up.sh --profile logspine` brings the spine up for
> real. On an older kernel it stays inert by default so a cold start on a judging
> laptop is green instead of stuck in a crash loop that has nothing to do with
> our code. `up.sh` detects this exact failure and prints the matrix instead of a
> bare compose error.
>
> Receipts: [`plan/gates/GATE0-laserdata-local.md`](../plan/gates/GATE0-laserdata-local.md)
> (kernel matrix + a no-admin lima Ubuntu 25.04 recipe) and
> [`SPONSOR-BUG-REPORT-DRAFT.md`](SPONSOR-BUG-REPORT-DRAFT.md).

---

## Honest status

Read this before you read anything above as a claim.

- **`laser.reachable` in `/health` is always `false` inside a container, even
  with `--profile logspine` up.** `app/bridge/rest.py:114-115` hard-codes
  `LASER_PROBE_HOST = "127.0.0.1"` / `LASER_PROBE_PORT = 8090` as module
  constants, so from inside the bridge container the probe dials *its own*
  loopback and never reaches the `iggy` service. It does **not** affect the
  health *verdict* — only `falkordb.reachable` gates the status code — but the
  `laser` block of the body is not trustworthy in this deployment. The fix is a
  one-line `os.environ.get` on those two constants, which belongs to the bridge
  lane, not to `deploy/`. Until then: read the spine's health from
  `docker compose ps iggy plane`, not from `/health`.
- **`git_sha` reports `"unknown"` in the container**, by design of
  `rest.py:git_sha()` — the image is built from a `COPY`, not a checkout, and
  there is no `git` binary in it. A missing sha is a reporting gap, not a reason
  to fail a healthcheck. If you need provenance, tag the image.
- **The bridge image needs the network at build time** (`pip install`). The `ui`
  image does not. This is a real regression versus a dependency-free placeholder,
  and it is the price of shipping the *real* bridge instead of a stand-in.
- **The `stream_*` and `act` verbs are honest `not_implemented` stubs.** They
  return `ok:false, status:"not_implemented"` on a 200 and never invent a record;
  `stream_tail` additionally declares `events: []` / `offset: null` / `stub:true`
  so the projector's stream strip parses an empty tail instead of erroring into
  MOCK. That is a lane that has not landed, not a lane that is pretending.
- **`ui` stays `healthy` when FalkorDB dies.** Its healthcheck only asserts that
  it serves its own page — deliberately, so a removal test is unambiguous about
  *which* service broke. The hard gate is at cold start (`ui` will not start
  without a healthy `bridge`, which will not start without a healthy
  `falkordb`). Runtime degradation is surfaced in the page, which flips its own
  badge to `MOCK · BRIDGE OFFLINE`.
- **Removal test 3 is verified in the negative direction only on this box.** The
  "spine is off, and the stack says so" half is verified here. The "spine is on,
  and replay from offset 0 works" half needs a >= 6.11 kernel (see the matrix).
  What *is* verified on an older kernel: the `logspine` profile parses, schedules
  `iggy` and `plane`, gates `plane` behind `iggy`'s health, and fails loudly with
  the kernel explanation.

## Troubleshooting

**Port already in use.** `up.sh` preflights every published port and names the
squatter:

```
WARN host port 6401 is already published by: some-old-container
  -> 'docker rm -f some-old-container'  (or change the port in .env), then re-run
```

**Two probing traps that report FAILURE for a WORKING port.** Both bit this
project; both are avoided in `up.sh` and in every command in this README:

1. `timeout N cat < /dev/tcp/host/port` exits **non-zero when the connection
   SUCCEEDS** — it blocks, then gets killed. Never use it. Use `nc -z`, or
   PowerShell `Test-NetConnection -Port N -InformationLevel Quiet`.
2. `curl -o /dev/null` in git bash with `MSYS_NO_PATHCONV=1`: `/dev/null` stops
   being translated, curl cannot write to it, and curl exits **23** *after* a
   perfectly good HTTP 200. Use a `%{http_code}` sentinel and no `-o` at all.

**Build context looks enormous.** Both images build from the **repo root** (the
bridge imports `memory.config` and serves `app/web`; anything narrower cannot
build it). `deploy/bridge/Dockerfile.dockerignore` and
`deploy/ui/Dockerfile.dockerignore` keep a local `.venv` / `node_modules` out of
the upload. They are per-Dockerfile ignore files (BuildKit reads
`<dockerfile>.dockerignore` first) precisely so `deploy/` does not have to drop a
`.dockerignore` at the repo root where it would affect everyone else.

**Line endings.** `.gitattributes` forces LF on `*.sh`, `*.py`, Dockerfiles and
YAML. CRLF in a script that runs in a container produces the classic
`exec format error` / `no such file or directory`, which names the wrong thing
entirely.

## No credentials

Directive 3, enforced:

- `.env.example` documents variable **names** with placeholder values and a
  comment per variable. It contains no real secret, key, token or password.
- `.env` is gitignored. The repo's root `.gitignore` ignores `.env` and `.env.*`
  and **allowlists `.env.example`** — that negation was added with this stack,
  because `.env.*` had been silently swallowing `.env.example`
  (`git check-ignore -v .env.example` → `.gitignore:3:.env.*`), which quietly
  defeated the directive-3 requirement that the variable documentation be
  committed. `.env`, `.env.local` and `.env.production` all remain ignored.
- `docker-compose.yml` contains no secret. The only password-shaped string in the
  tree is `LASER_IGGY_PASSWORD=palimpsest-local-dev`, a documented non-secret
  placeholder for a broker bound to `127.0.0.1` for the length of a demo. It is a
  default rather than a required `:?` variable so `docker compose config` parses
  on a fresh clone with no `.env` — verified, both with and without the
  `logspine` profile.
- FalkorDB runs unauthenticated **on the private compose network only**, and every
  published port binds to `PALIMPSEST_BIND_ADDRESS` (`127.0.0.1` by default), so
  nothing in this stack is reachable off the machine. Set
  `PALIMPSEST_REDIS_ARGS='--save 60 1 --appendonly no --requirepass <your-own>'`
  in your own `.env` if you want auth locally — never in a committed file.
- The bridge holds no credential of its own: `app/bridge/` takes **zero** runtime
  dependency on any hosted backend, and `memory/config.py` reads key-shaped
  settings from the process environment on demand, never defaulting them and
  never echoing them into an error message.
