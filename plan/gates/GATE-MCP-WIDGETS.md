# GATE-MCP-WIDGETS receipt — the MCP surface is MCP-Apps (SEP-1865) compliant

**Status: GREEN** · 2026-08-03 · Tier-0 interactive widgets on the MCP surface ·
declaration probed with `@modelcontextprotocol/inspector@2 --app-info`, text fallback re-probed,
full bridge suite green. Ground truth: `plan/research/mcp-widgets-guide.md`.

## What this gate proves

The three highest-value verbs (`graph`, `ask`, `ablation`) each declare an interactive MCP-Apps
widget via `_meta.ui`, backed by a predeclared `ui://palimpsest/*-v1` resource whose bundle is
`text/html;profile=mcp-app`. The widget is **ADDITIVE**: every tool still returns a real
`content:[{type:text}]` block, so Claude Code (no widget render) and any non-widget host keep
working unchanged. Nothing else on the MCP surface moved — the dispatch table stays pinned at 13
verbs; REST / OpenAPI / CLI are byte-for-byte unaffected (they never read `.meta`).

| Verb | `ui://` resource | Widget | Widget→host interaction | Text fallback |
|---|---|---|---|---|
| `graph` | `ui://palimpsest/graph-v1` | live inline-SVG memory graph | polls `palimpsest_graph` via `tools/call` every 1.5 s | full nodes+edges JSON |
| `ask` | `ui://palimpsest/approval-v1` | approve/dismiss card, safe-default countdown | reports the human tap via `ui/message` (never fabricates an approval) | decision-card JSON |
| `ablation` | `ui://palimpsest/ablation-v1` | COLD \| WARM opposite-verdict, side by side | re-runs `palimpsest_ablation` via `tools/call` | opposite-verdict JSON |

## The render gate (guide §2.3, gotcha #1) — deterministic, self-computable

```
endpoint = http://127.0.0.1:8931/mcp        (the connector URL, incl. /mcp, no trailing slash)
domain   = sha256(endpoint)[:32] + ".claudemcpcontent.com"
         = 73f973214af3085d25493aaa62bfcab1.claudemcpcontent.com
```

Not an Anthropic credential and not on an allowlist — any auditor recomputes it:
`python -c "import hashlib;print(hashlib.sha256(b'http://127.0.0.1:8931/mcp').hexdigest()[:32]+'.claudemcpcontent.com')"`.
Every widget resource declares exactly this domain on both `resources/list` and `resources/read`.
For a deploy whose public connector URL differs from the bind port, `PALIMPSEST_MCP_ENDPOINT`
overrides the endpoint the hash is taken from (the domain must match the URL the host dials).

## Gate A — declaration probe (`--app-info`)

```
npx -y @modelcontextprotocol/inspector@2 --cli http://127.0.0.1:8931/mcp \
  --transport http --method tools/list --app-info
```

Captured output (the probe bound a scratch port to avoid disrupting the live :8931 bridge;
`PALIMPSEST_MCP_ENDPOINT` pinned the domain to the canonical `:8931/mcp` connector URL):

```
{"hasApp":false,"toolName":"palimpsest_remember"}
{"hasApp":false,"toolName":"palimpsest_relate"}
{"hasApp":false,"toolName":"palimpsest_recall"}
{"hasApp":false,"toolName":"palimpsest_ring"}
{"hasApp":true,"toolName":"palimpsest_ablation","resourceUri":"ui://palimpsest/ablation-v1","visibility":["model","app"],"domain":"73f973214af3085d25493aaa62bfcab1.claudemcpcontent.com","prefersBorder":true,"resourceMimeType":"text/html;profile=mcp-app"}
{"hasApp":true,"toolName":"palimpsest_graph","resourceUri":"ui://palimpsest/graph-v1","visibility":["model","app"],"domain":"73f973214af3085d25493aaa62bfcab1.claudemcpcontent.com","prefersBorder":true,"resourceMimeType":"text/html;profile=mcp-app"}
{"hasApp":false,"toolName":"palimpsest_stream_publish"}
{"hasApp":false,"toolName":"palimpsest_stream_tail"}
{"hasApp":false,"toolName":"palimpsest_stream_replay"}
{"hasApp":false,"toolName":"palimpsest_act"}
{"hasApp":false,"toolName":"palimpsest_handover_write"}
{"hasApp":false,"toolName":"palimpsest_handover_read"}
{"hasApp":true,"toolName":"palimpsest_ask","resourceUri":"ui://palimpsest/approval-v1","visibility":["model","app"],"domain":"73f973214af3085d25493aaa62bfcab1.claudemcpcontent.com","prefersBorder":true,"resourceMimeType":"text/html;profile=mcp-app"}
```

Assertions — **all hold**:
- `hasApp:true` count == **3** (exactly `graph`, `ask`, `ablation`); the other 10 verbs are `hasApp:false`.
- each `resourceUri` == `ui://palimpsest/{graph,approval,ablation}-v1`.
- each `domain` == `73f973214af3085d25493aaa62bfcab1.claudemcpcontent.com` == `sha256(endpoint)[:32]+".claudemcpcontent.com"`.
- each `resourceMimeType` == `text/html;profile=mcp-app`.
- gotcha #4 satisfied on the wire: each tool's `_meta` carries BOTH nested `ui.resourceUri` and flat `ui/resourceUri` (verified by dumping the serialized `ListToolsResult`).

## Gate B — the plain-text fallback never regresses

`tools/call` still returns a non-empty `content[0].text` for every widget verb:

```
palimpsest_ask       content[0].text length: 662     (decision-card JSON)
palimpsest_graph     content[0].text length: 273274  (live nodes+edges from FalkorDB)
palimpsest_ablation  content[0].text length: 3228    (opposite-verdict envelope)
```

Elicitation stays the interactive path for terminal hosts: `_h_ask` already documents the
`ctx.elicit()` branch (guide §2.6 tier 2) and Claude Code declares `elicitation:{}`. One verb
carries BOTH `_meta.ui` and the elicitation branch — no fork — because no host today declares both.

## Which hosts actually render these (honest matrix, guide §0)

| Host | Renders the widget | Interactive path |
|---|---|---|
| **Claude Desktop / claude.ai** | **YES** | the widget |
| **Claude Code (CLI)** | **NO** (measured: 2.1.220 declares no `io.modelcontextprotocol/ui`) | elicitation + the text fallback |
| Cursor / VS Code Copilot / Goose / ChatGPT / MCP Inspector-probe | yes / probe | varies |

If judges watch a terminal, the widgets are invisible and elicitation + the projector web UI are the
interactive surfaces. If judges watch Claude Desktop, the widgets render. The widgets are a
**receipt of MCP-Apps compliance**, additive to the demo — never on the critical path.

## Tests (against live FalkorDB `127.0.0.1:6401`; no laser configured in this shell)

```
.venv/bin/python -m pytest app/bridge/tests -q
```

**133 passed, 4 deselected.** The 4 deselected (`test_path_params_reach_the_verb` and the three
`test_stream_tail_*`) require `LASER_CONNECTION_STRING`, which is unset in this shell; they fail
**identically on origin/main** (confirmed by re-running them on the base with this diff removed), so
they are a pre-existing environmental gap, not a regression from this lane. Guarded-import contracts
also hold: `app.bridge.server` imports on a bare interpreter (mcp + falkordb blocked) and on
Python 3.9, still reporting exactly 13 tools and a JSON-serializable OpenAPI spec.

## Reproduce

```
# 1. serve the MCP surface (any bridge instance; the connector URL fixes the domain)
BRIDGE_PORT=8931 .venv/bin/python -m app.bridge.rest

# 2. Gate A — declaration probe
npx -y @modelcontextprotocol/inspector@2 --cli http://127.0.0.1:8931/mcp \
  --transport http --method tools/list --app-info | grep '"hasApp":true' | wc -l   # -> 3

# 3. Gate B — text fallback non-empty
npx -y @modelcontextprotocol/inspector@2 --cli http://127.0.0.1:8931/mcp --transport http \
  --method tools/call --tool-name palimpsest_ask \
  --tool-args-json '{"question":"Escalate?","options":["approve","dismiss"]}'   # content[0].text non-empty
```

## Files

- `app/bridge/widget_apps.py` — the `_meta.ui` declaration layer (domain hash, `UiSpec`, per-verb specs, both `resourceUri` forms). No `mcp` import, no network — importable on a bare interpreter.
- `app/bridge/widgets/{graph,approval,ablation}.html` — dependency-free vanilla-JS bundles (the guide's postMessage handshake: unconditional `ui/notifications/initialized`, real-number `size-changed`, `tool-result` render, theme-aware).
- `app/bridge/server.py` — `_attach_widget_meta` on the 3 tools + `list_resources`/`read_resource` handlers serving the `ui://` bundles. The plain-text `call_tool` return is unchanged.
