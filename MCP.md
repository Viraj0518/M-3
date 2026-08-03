# Use PALIMPSEST as an MCP server

PALIMPSEST is a live **attributed memory graph** (FalkorDB) on a durable ordered
event log. The hosted bridge exposes it as a public **MCP server** — connect any
MCP host (Claude Code, Claude Desktop, Cursor, Goose, the MCP Inspector, …) and
your agent gains memory tools it can call directly: remember facts, recall with
graph expansion, detect coordinated edit-rings, and run the cold-vs-warm ablation.

- **Endpoint:** `https://palimpsest-bridge.fly.dev/mcp` (streamable-HTTP, no auth)
- **The public host is READ-ONLY** (`PALIMPSEST_PUBLIC_MODE=readonly`): reads
  (`recall` / `ring` / `graph` / `handover_read`) answer normally; every mutating
  verb — including `remember`, `relate`, and `ablation` — is refused with
  `PUBLIC_READONLY`. This keeps an anonymous, unauthenticated public endpoint from
  being a write/DoS target. To run writes and the live `ablation`, use a **local**
  instance (below).
- Backed by a seeded demo corpus; nothing private is stored.
- The Fly machine auto-suspends when idle, so the **first** call may take a few
  seconds to cold-start. No API key required.

## Tools (13)

`palimpsest_remember` · `palimpsest_relate` · `palimpsest_recall` ·
`palimpsest_ring` · `palimpsest_ablation` · `palimpsest_graph` ·
`palimpsest_handover_write` · `palimpsest_handover_read` · `palimpsest_ask` ·
`palimpsest_stream_publish` · `palimpsest_stream_tail` · `palimpsest_stream_replay` ·
`palimpsest_act`

> The LaserData stream is a local-only, kernel-gated service, so on the hosted
> image `stream_*` return an honest declared-empty tail and `/health` reports
> `laser.reachable:false` / `falkordb.reachable:true`. The memory tools
> (remember / recall / ring / ablation / graph / handover) are fully live.

The single most demo-worthy call is **`palimpsest_ablation`** — it runs the same
ring query against a warm graph (with history) and a cold graph (event only) and
returns the **opposite verdict** (escalate vs dismiss). That is the proof that
memory is load-bearing, callable from inside your MCP host. Because it drops and
re-seeds two graphs per call it is a **write**, so it runs on a **local** instance
(below), not the read-only public host.

## Connect

### Claude Code (this repo)

A project-scoped `.mcp.json` is committed at the repo root, so opening M-3 in
Claude Code offers the `palimpsest` server automatically. Or add it yourself:

```bash
claude mcp add palimpsest https://palimpsest-bridge.fly.dev/mcp --transport http
```

### Claude Desktop / other hosts

Add to your host's MCP config (Claude Desktop: `claude_desktop_config.json`):

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

### Verify from a terminal (no host needed)

```bash
npx -y @modelcontextprotocol/inspector@2 --cli \
  https://palimpsest-bridge.fly.dev/mcp --transport http --method tools/list
```

## Run it locally instead

The same MCP surface is served by the local bridge on `127.0.0.1:8931/mcp`
(see [DEPLOY.md](./DEPLOY.md) for the local stack). Point any of the configs
above at `http://127.0.0.1:8931/mcp` for a wifi-free, full-featured instance
(local adds the live LaserData stream tools).
