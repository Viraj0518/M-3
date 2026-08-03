# Adding PALIMPSEST to your agent (MCP over HTTP/HTTPS)

PALIMPSEST's bridge serves **MCP streamable-http at `/mcp`, on the same port as
everything else**. There is no separate MCP process, no stdio wrapper to install,
and no npm package — if you can reach the bridge over HTTP you can add it to any
MCP client in one command.

This document is for **someone who did not build it**: how to connect, what the
auth story actually is, and what a hosted deployment has to expose for it to
work.

---

> ## ⚠️ Read this before you expose a bridge publicly
>
> **`main` has no authentication.** `app/bridge/identity.py` is an *attribution*
> layer and says so in its own docstring: it maps a session id to a name. Its
> precedence ladder ends at the `x-palimpsest-agent` header, which any caller can
> set to any string.
>
> So a publicly reachable bridge is an **unauthenticated write surface** where
> the caller also picks whose name goes on the write:
>
> ```bash
> curl -H 'x-palimpsest-agent: commander' -X POST https://<host>/v1/remember \
>      -d '{"content":"anything at all"}'      # recorded as the commander
> ```
>
> **Do not expose `/mcp` or `/v1/*` publicly in a writable configuration until
> PR #2 (the `auth` branch) merges *and* is switched on.**
>
> Two things people assume that are not true:
>
> 1. **Merging PR #2 does not by itself close the hole.** Read
>    `app/bridge/auth.py`: a request with no token, or with a bad token, falls
>    through to the existing ladder and is served as `unbound`. The module adds
>    *verified* identity; it does not *require* it. Something still has to refuse
>    unverified writes.
> 2. **There is no OAuth challenge to discover.** The bridge emits no `401`, no
>    `WWW-Authenticate`, and no `/.well-known/oauth-protected-resource`. Verified
>    live against the hosted deploy on 2026-08-03: both
>    `/.well-known/oauth-protected-resource` and
>    `/.well-known/oauth-authorization-server` return **404**. An MCP client has
>    nothing to auto-discover, so it simply connects unauthenticated and succeeds.
>
> **The stopgap, available now:** start the bridge with
> `PALIMPSEST_PUBLIC_MODE=readonly` (see [Public-exposure
> modes](#public-exposure-modes)). It is **off by default** and changes nothing
> unless set. It refuses every mutating verb at the dispatch chokepoint — which
> covers REST, MCP, the OpenAPI/Guild integration and the CLI in one place — and
> lets reads through so a public demo still works.

---

## 1. Pick a URL

| | URL | Notes |
|---|---|---|
| **Local stack** | `http://127.0.0.1:8931/mcp` | `cd deploy && ./up.sh`. Full stack, your data, nothing leaves the machine. |
| **Hosted demo** | `https://palimpsest-bridge.fly.dev/mcp` | Single self-contained Fly machine (`fly/`). **Public and unauthenticated — see the warning above.** |

**The hosted demo is not durable memory.** `fly/fly.toml` declares no volume and
`fly/entrypoint.sh` starts FalkorDB with `--save "" --appendonly no`, then
re-seeds the demo graph on every boot. Combined with `auto_stop_machines =
"suspend"` and `min_machines_running = 0`, anything you `remember` there is gone
the next time the machine idles out. Use it to *try* the tool surface; use a
local stack for anything you want to keep.

Verify any URL before you wire it up:

```bash
curl -s https://palimpsest-bridge.fly.dev/health | jq '{ok, mcp, public_mode, falkordb}'
```

`mcp.mounted` must be `true`. If it is `false`, the body carries a `reason` and
it is always the same one — the `mcp` SDK is not importable in that interpreter.
Install it **pinned**: `mcp>=1.28,<2`. The upper bound is load-bearing (SDK 2.x
removes the `Mcp-Session-Id` header `identity.py` keys on); relaxing it to make
an install succeed breaks per-session identity.

---

## 2. Add it to your client

### Claude Code

```bash
# hosted demo
claude mcp add --transport http palimpsest https://palimpsest-bridge.fly.dev/mcp

# local stack
claude mcp add --transport http palimpsest http://127.0.0.1:8931/mcp

# with a bearer token, once the auth lane is live
claude mcp add --transport http palimpsest https://<host>/mcp \
  --header "Authorization: Bearer $PALIMPSEST_TOKEN"
```

Then `/mcp` in the Claude Code REPL to confirm it connected, and ask for the tool
list. You should see 13 `palimpsest_*` tools (12 on a deployment one build
behind — `ablation` is the newest).

Or commit it to the project. Copy [`.mcp.json.example`](../.mcp.json.example) to
`.mcp.json` at the repo root — verbatim, it is already a valid config — and
everyone who opens the repo in Claude Code is prompted to connect:

```json
{
  "mcpServers": {
    "palimpsest": {
      "type": "http",
      "url": "http://127.0.0.1:8931/mcp"
    }
  }
}
```

Start the stack first (`cd deploy && ./up.sh`) and confirm with
`curl -s localhost:8931/health | jq .mcp`. Point `url` at
`https://palimpsest-bridge.fly.dev/mcp` for the hosted demo instead — reading
the warning at the top of this document first. Once a token is required, add
`"headers": {"Authorization": "Bearer ${PALIMPSEST_TOKEN}"}`; never put a
literal token in a committed file.

It ships as `.mcp.json.example` rather than `.mcp.json` on purpose: a live
`.mcp.json` makes every agent working in this repo attempt a connection to a
bridge that may not be running.

### Claude Desktop

Edit `claude_desktop_config.json`:

- macOS — `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows — `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "palimpsest": {
      "type": "http",
      "url": "https://palimpsest-bridge.fly.dev/mcp"
    }
  }
}
```

Restart Claude Desktop. *Unverified in this pass: which Claude Desktop builds
accept `"type": "http"` natively. If yours does not, the standard workaround is
an stdio↔http proxy such as `mcp-remote`; we did not test that path.*

### Codex

Codex reads `~/.codex/config.toml`:

```toml
[mcp_servers.palimpsest]
url = "https://palimpsest-bridge.fly.dev/mcp"

# once a token is required:
# http_headers = { Authorization = "Bearer ${PALIMPSEST_TOKEN}" }
```

*Unverified in this pass: exercised against a live Codex install. The shape
above is the documented streamable-http server form; confirm against your Codex
version's own docs before relying on it.*

### Anything else

Any MCP client that speaks streamable-http works — the endpoint is ordinary. Two
requirements, both real:

- **Use the exact URL, no trailing slash, and do not follow redirects.** Bare
  `/mcp` is served by an explicit route inserted ahead of the mount so it does
  **not** `307` to `/mcp/` (both work, but the MCP Apps contract derives a widget
  sandbox domain from a sha256 *of the endpoint string*, so a client that follows
  a redirect computes a different domain than the one it registered).
- **`POST /mcp` requires `Accept: application/json, text/event-stream`.** Sending
  only `application/json` returns `406` with `"Client must accept both
  application/json and text/event-stream"`. Real clients get this right; curl
  probes usually do not.

---

## 3. What you get

13 tools, all prefixed `palimpsest_`:

```
memory       remember  relate  recall  ring  graph  ablation
stream       stream_publish  stream_tail  stream_replay
continuity   handover_write  handover_read
motion       act
human        ask
```

The server ships its own agent instructions in the `initialize` response, so a
connected agent already knows the capture/query/stream/handoff reflexes without
a system prompt.

**Read [`skills/palimpsest/SKILL.md`](../skills/palimpsest/SKILL.md)** for what
each verb is for, when to call it, and the failure modes. The one thing to
internalise before your first call:

> **HTTP 200 and `isError: false` do not mean success.** Over MCP a domain error
> arrives as HTTP 200 with the JSON-RPC `isError` set to **false**, and the real
> error inside the text content — which carries its own `isError: true`. Parse
> the envelope; `ok === true` is the only success signal.

Other surfaces on the same port, if you want them instead of MCP:

```
GET  /health                 liveness + real Cypher RETURN 1 + posture
GET  /v1/openapi.json        OpenAPI 3.0, generated from the same dispatch table
GET  /ui/                    the projector, same-origin
     /v1/*                   REST, one route per verb
```

---

## 4. Auth — the OAuth 2.1 + PKCE lane

**Status: on the `auth` branch (PR #2). Not merged. Not deployed.** Everything in
this section describes that branch; none of it is live today.

### Shape

```
humans  →  email + password  →  session         →  /login, /account
agents  →  OAuth 2.1 + PKCE  →  JWT (EdDSA)     →  Authorization: Bearer …
```

The auth service (`auth/`) is a separate Node process on its own port (8932 by
default), speaking to the same FalkorDB but on its own graph key
(`palimpsest_auth`) with every label prefixed (`AuthUser`, `AuthAgent`, …), so
its nodes never appear in the projector or inflate the demo's node count.

An agent principal is the pair **(user × OAuth client)** — one human arriving
through two clients is two principals, which is exactly the granularity
`author_agent` wants.

### The flow, from the client's side

You do not hand-build this. A conformant MCP client runs it for you: discover →
dynamically register → PKCE authorize (browser: login, then consent) → token →
send the JWT as `Authorization: Bearer …`. Dynamic client registration is
enabled *and* unauthenticated registration is allowed, precisely so
`claude mcp add` can self-register with no pre-shared credentials.

The routes below are the ones mounted by `auth/src/auth-module.ts`:

| Route | What it is |
|---|---|
| `GET /.well-known/oauth-authorization-server/api/auth` | RFC 8414 metadata. **This document is authoritative** for the exact authorize / token / registration endpoints — read it, do not hardcode them. |
| `GET /.well-known/openid-configuration/api/auth` | OpenID configuration. |
| `GET\|POST /api/auth/*` | Better Auth: sign-in, dynamic client registration, authorize, token, JWKS. |
| `GET /api/auth/jwks` | The public keys the bridge verifies against. |
| `GET /login`, `/consent`, `/account` | The human pages in an agent's OAuth flow. |
| `GET /api/auth-health`, `/api/me`, `/api/provenance`, `/api/graph` | Session-authenticated identity + provenance reads. |

Note the RFC 8414 placement: the well-known segment goes **between the host and
the issuer path** (`/.well-known/oauth-authorization-server/api/auth`), not on
the end. Getting this wrong is why client discovery 404s and nothing can
register.

The issuer is `<auth base URL>/api/auth`; the JWKS is `<issuer>/jwks`.

### The trap that will cost you an hour

**RFC 8707 resource indicators are mandatory.** Without `resource=<your MCP URL>`
on *both* the authorize and the token call, Better Auth issues an **opaque**
token instead of a JWT, and every JWT verifier rejects it as unparseable. Your
MCP URL must also be in the service's `audiences` list — `auth/src/server.ts`
defaults it to `[AUTH_BASE_URL, BRIDGE_URL, BRIDGE_URL + "/mcp"]`.

### Turning verification on at the bridge

Set these on the **bridge** process:

```bash
AUTH_JWKS_URL=https://<auth-host>/api/auth/jwks   # UNSET == verifier is inert
AUTH_ISSUER=https://<auth-host>/api/auth
AUTH_AUDIENCE=https://<bridge-host>/mcp
# optional: AUTH_JWKS_TTL_S (300), AUTH_LEEWAY_S (60), AUTH_SELECTOR_CLAIM (azp)
```

With `AUTH_JWKS_URL` unset the verifier returns `None` in ~1µs and the bridge is
byte-for-byte what it is today.

### What it does and does not buy you

- **Valid token** → a cryptographically earned selector that outranks any header
  claim. `author_agent` becomes a fact instead of an assertion.
- **No token** → falls through to today's ladder and is served as `unbound`.
- **Bad token** → also falls through. A forged token cannot *remove* an identity
  the caller would otherwise have had; it just fails to grant a better one.

That third bullet is the important one: **merging PR #2 does not close the
public write surface.** It makes good identity possible; it does not make bad
identity fatal. To actually close it you need one of:

1. `PALIMPSEST_PUBLIC_MODE=readonly` (available now, no merge required), or
2. a follow-up that refuses mutating verbs when `auth.enabled()` and
   `resolve_verified()` returned `None`, and emits a `401` +
   `WWW-Authenticate` + `/.well-known/oauth-protected-resource` (RFC 9728) so
   clients can discover the auth server and run the flow instead of silently
   connecting anonymously.

---

## 5. Public-exposure modes

`PALIMPSEST_PUBLIC_MODE` (`app/bridge/guard.py`) — **unset by default, and unset
means no guard at all.**

| value | effect |
|---|---|
| unset / `off` | default. No guard. Identical to the bridge without this feature. |
| `readonly` | reads answer normally; every mutating verb is refused with `PUBLIC_READONLY` (HTTP 403 on REST). |
| `closed` | every verb is refused with `PUBLIC_CLOSED`. `/health` still answers, so the host stays monitorable. |
| anything else | treated as `closed`. A config typo must fail safe — `read-only` with a hyphen silently meaning "wide open" is exactly the bug this guards. |

**Local surfaces are exempt.** `surface="cli"` is never guarded, because reaching
the CLI already requires shell access on the box. That exemption is load-bearing,
not a convenience: `app/bridge/seed_demo.py` and `realtime/ablation.py` drive the
router with `surface="cli"`, and `fly/entrypoint.sh` runs
`python -m app.bridge.seed_demo` **at boot**. Guarding it would boot a guarded
deployment with an empty warm graph — a dead demo. `surface` is hardcoded by each
entry point (`rest.py` → `"rest"`, the MCP handler → `"mcp"`) and is never
caller-supplied, and an unrecognised or missing surface is treated as a network
surface, so the exemption cannot be reached from outside.

Reads still allowed under `readonly`: `recall`, `ring`, `graph`, `stream_tail`,
`handover_read`. Everything else is a write, **including any verb added later** —
the read set is an allowlist, so a new verb is refused until someone classifies
it deliberately.

`readonly` leaves the projector's polling loop fully live: `/graph`, `/ring` and
`/stream_tail` are all reads.

**`ablation` counts as a write.** It looks like a read — it answers a question
and touches neither demo graph — but `realtime.ablation.run_ablation` runs with
`reset=True`, which `drop_graph()`s and re-seeds `palimpsest_ablation_warm` and
`palimpsest_ablation_cold` on *every call*. A destructive reset plus a full
corpus ingest per anonymous request is a DoS lever, not a read. Consequence:
**under `readonly` the ablation demo is unavailable.** A box that has to drive
that beat live should run with the guard `off` behind a trusted network.

### What `readonly` does not close

It refuses mutating verbs. It is not a sandbox.

- **Reads stay anonymous.** Everything in the graph is world-readable to anyone
  who can reach the URL. Use `closed` if that is not acceptable.
- **Read verbs still bootstrap indexes.** `recall` / `graph` / `ring` call
  `_ensure(graph_key)` → `ensure_indexes()`, which issues `CREATE INDEX`. And
  `check_graph_key` is a *regex* allowlist (`^palimpsest(_[a-z0-9]+)*$`), not an
  enumeration — so `?graph=palimpsest_whatever` on a read creates an empty graph
  and its indexes. No data is written, but it is unbounded graph creation.
  Closing that means enumerating permitted keys or skipping `_ensure` under the
  guard; neither is done here, because both would change behaviour on the default
  path and this feature's contract is that it does not.

The check runs in `server.dispatch()`, the single function every surface enters
through, which is why one env var covers REST, MCP, the OpenAPI/Guild
integration and the CLI. `GET /health` and `GET /` report the posture under
`public_mode` so a third party can see it without guessing.

```bash
# Fly
flyctl secrets set PALIMPSEST_PUBLIC_MODE=readonly -a palimpsest-bridge
# compose / plain env
PALIMPSEST_PUBLIC_MODE=readonly python -m app.bridge.rest
```

This is a stopgap, not a security model. A read-only public endpoint is still an
unauthenticated *read* of everything in the graph. Merge the auth lane.

---

## 6. Contract for a hosted deployment

For the owner of `fly/` and `.github/`. These are requirements read out of the
bridge's code and out of the MCP streamable-http transport — not preferences.

**Already satisfied by `fly/fly.toml` as of 2026-08-03:** TLS termination with
`force_https = true`, `internal_port = 8931`, and a `GET /health` check.
Verified live: `https://palimpsest-bridge.fly.dev/health` → 200 with
`mcp.mounted: true`, and a full MCP `initialize` → `tools/list` handshake over
public HTTPS returned 12 tools.

The rest is what still needs a decision.

### 6.1 One process, or sticky sessions — this is not optional

`StreamableHTTPSessionManager` holds sessions **in memory in the process that
minted them**. The `Mcp-Session-Id` a client receives on `initialize` is only
valid against that one process. Two machines behind one hostname with round-robin
routing means roughly half of every client's follow-up requests hit a process
that has never heard of its session.

So: **exactly one machine**, or session affinity keyed on `Mcp-Session-Id`.
`fly/fly.toml` currently sets `min_machines_running = 0` and does not pin a
count — that works while one machine exists and breaks silently the moment it
scales. Pin it, or add affinity.

Related: `auto_stop_machines = "suspend"` means a suspend/resume **drops every
MCP session**. Clients generally recover by re-initializing, but any in-flight
notification stream dies. And because the graph is in-memory and re-seeded on
boot, a suspend also **discards everything anyone remembered**. If the hosted URL
is meant to be usable memory rather than a demo, it needs a volume and
`min_machines_running = 1`.

### 6.2 The `/mcp` path must stream

- **No response buffering.** The bridge already sets `x-accel-buffering: no` on
  its SSE responses, which covers nginx-class proxies. Any proxy that buffers
  anyway will make every tool call appear to hang until the stream closes.
- **No idle timeout on a long-lived `GET /mcp` stream**, or set it generously.
  Streamable-http opens an SSE channel for server→client notifications that is
  idle by design. *Unverified: Fly's exact proxy idle timeout — measure it before
  claiming the notification channel is durable.*
- **Bare `/mcp` must not be redirected or rewritten** to `/mcp/`. The bridge
  deliberately serves both without a redirect; a proxy that adds one reintroduces
  the endpoint-string mismatch described above.
- **Pass through** `Mcp-Session-Id`, `Mcp-Protocol-Version`, `Accept`,
  `Last-Event-ID` and `Authorization` unmodified in both directions. The bridge
  already lists `Mcp-Session-Id` in `expose_headers`.

Self-hosting behind nginx, the minimum that works:

```nginx
location /mcp {
    proxy_pass         http://127.0.0.1:8931/mcp;
    proxy_http_version 1.1;
    proxy_buffering    off;          # SSE dies without this
    proxy_cache        off;
    proxy_read_timeout 1h;           # the notification stream is idle by design
    proxy_set_header   Connection    "";
    proxy_set_header   Host          $host;
    proxy_set_header   Authorization $http_authorization;
}
```

Caddy needs no special configuration — `reverse_proxy 127.0.0.1:8931` streams
correctly by default.

### 6.3 Environment the bridge actually reads

| variable | why |
|---|---|
| `BRIDGE_PORT` | 8931. Load-bearing in three places: `memory/config.py`, the projector's hardcoded origin, and the MCP Apps sandbox-domain hash. |
| `BRIDGE_BIND_HOST` | must be `0.0.0.0` in a container. Overrides only the uvicorn bind address; `BRIDGE_HOST` still drives what `/health` reports. |
| `FALKORDB_HOST` / `FALKORDB_PORT` | the memory plane. `/health` returns **503 `FALKORDB_UNAVAILABLE`** when unreachable — the correct healthcheck target, and the only route whose status is derived from something other than an `err()` code. |
| `PALIMPSEST_PUBLIC_MODE` | **set this to `readonly` on any public deployment until auth lands.** |
| `AUTH_JWKS_URL` / `AUTH_ISSUER` / `AUTH_AUDIENCE` | after PR #2 merges. Unset ⇒ the verifier is inert. |

The auth service, when deployed, needs a **publicly reachable HTTPS origin** —
not just bridge-reachable. The bridge fetches JWKS server-to-server, but the
`/login` and `/consent` pages are opened **in the user's browser** during the
PKCE flow, and `AUTH_BASE_URL` must be that public origin or every redirect URI
is wrong.

### 6.4 CORS

`allow_origins=["*"]` is set deliberately: the projector is regularly opened as a
`file://` URL, whose `Origin` is the literal string `null`. That is safe while
the process holds no credentials. Once bearer tokens are in play, revisit it —
a wildcard origin plus a token-bearing API is a broader surface than a demo needs.

---

## 7. Verification receipts

Everything below was run on **2026-08-03** against real endpoints. Nothing here
is inferred.

**Hosted, `https://palimpsest-bridge.fly.dev` (public internet, no credentials):**

```
GET  /health                                   → 200  mcp.mounted=true, falkordb.reachable=true
POST /mcp   initialize                         → 200  text/event-stream, mcp-session-id issued
POST /mcp   tools/list                         → 12 palimpsest_* tools
POST /v1/remember  {}                          → 400  MISSING_CONTENT   ← reached the handler with NO auth
GET  /.well-known/oauth-protected-resource     → 404
GET  /.well-known/oauth-authorization-server   → 404
```

The `MISSING_CONTENT` line is the finding: argument validation is the *only*
thing between an anonymous caller and a graph write.

**Local containerised stack, `http://127.0.0.1:8931`:**

```
GET  /health                                   → 200  mcp.mounted=true, laser.reachable=false
POST /mcp   initialize → notifications/initialized → tools/list  → 12 tools
GET  /mcp   (no session)                       → 400   (not a redirect; bare path is served directly)
POST /mcp/  initialize                         → 200   (mount also answers)
POST /mcp   Accept: application/json only      → 406   "must accept both application/json and text/event-stream"
POST /v1/handover  (no agent_id)               → 400   MISSING_AGENT_ID
MCP  palimpsest_handover_read {}               → 200, isError:false, body {"isError": true, "code": "MISSING_AGENT_ID"}
```

**Identity divergence, same header on both surfaces:**

```
REST GET /v1/stream/signal.raw/tail   x-palimpsest-agent: probe-rest  → author_agent "probe-rest"
MCP  tools/call palimpsest_stream_tail x-palimpsest-agent: probe-mcp  → author_agent "unbound"
MCP  tools/call palimpsest_graph                                      → agents {} , contributors {"unbound": 1}
```

`server.py`'s MCP handler dispatches with `is_stdio=True` and passes no headers,
so the HTTP MCP surface resolves through the shared stdio bucket, finds nothing
bound, and stamps `unbound`. There is no verb that calls `identity.bind()`.

**The guard, on a bridge started from this branch:**

```
default (no env var)
  /health public_mode        → {"mode":"off","enforcing":false,...}
  POST /v1/remember {}       → 400 MISSING_CONTENT              (unchanged)
  MCP tools/list             → 13 tools                         (unchanged)

PALIMPSEST_PUBLIC_MODE=readonly
  /health public_mode        → {"mode":"readonly","enforcing":true,
                                "writes_refused":true,"exempt_surfaces":["cli"]}
  POST /v1/remember          → 403 PUBLIC_READONLY
  GET  /v1/ablation          → 403 PUBLIC_READONLY   (it drops + re-seeds graphs)
  GET  /graph                → 200
  GET  /ring                 → 200
  MCP  palimpsest_remember   → PUBLIC_READONLY
  MCP  palimpsest_ablation   → PUBLIC_READONLY
  MCP  palimpsest_graph      → ok:true
  python -m app.bridge.seed_demo --graph palimpsest_guardproof --reset
                             → seeded 12 nodes, ring_fired:true  ← boot seed works
```

That last line is the one that matters operationally: the boot-time seeder runs
on a guarded deployment, so turning the guard on does not leave you with an
empty graph.

Test gate: `app/bridge/tests` — **125 passed, 1 skipped** on this branch,
including 19 new tests in `test_guard.py` (which need neither the MCP SDK nor a
database and run on a bare interpreter).

**Not verified in this pass:** Claude Desktop's acceptance of `"type": "http"`;
the Codex `config.toml` form against a live Codex install; Fly's proxy idle
timeout on a long-lived SSE stream; and the whole OAuth flow end to end — the
`auth` branch was read, not run. `auth/scripts/verify-e2e.mjs` on that branch
runs the real flow (sign-up → DCR → PKCE authorize → consent → token) and hands
the resulting JWT to the bridge's own Python verifier; run it before trusting
section 4.
