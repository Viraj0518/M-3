# Memory Meets Motion — Hackathon Build

**Event:** Memory Meets Motion Hackathon — 2026-08-03, Frontier Tower SF (8-hour sprint)
**Team:** 3 humans + Claude agent workers

## Mandatory stack (all four must be load-bearing)

| Layer | Tech | Role |
|---|---|---|
| Memory | [FalkorDB](https://github.com/FalkorDB/falkorDB) | Graph memory / GraphRAG — what has ever happened |
| Real-time | [LaserData](https://docs.laserdata.com/laser-sdk/quickstart) | Live/streaming signals — what is happening now |
| Motion | RocketRide.ai | Orchestration / agent execution — decide + act |
| Coordination | Guild.ai | Multi-agent teams, handoffs, human-in-the-loop |

Reference flow: **LaserData → FalkorDB → RocketRide → Guild.ai → user.**

## Use it from your own agent

PALIMPSEST's bridge serves **MCP streamable-http at `/mcp`**, on the same port as
everything else — no separate process, no npm package, no stdio wrapper.

```bash
cd deploy && ./up.sh                                            # local stack
claude mcp add --transport http palimpsest http://127.0.0.1:8931/mcp
```

- **[`docs/MCP.md`](docs/MCP.md)** — Claude Code / Claude Desktop / Codex setup, the
  OAuth lane, and what a hosted deployment must expose. **Read the security
  warning at the top before exposing a bridge publicly: `main` has no auth.**
- **[`skills/palimpsest/SKILL.md`](skills/palimpsest/SKILL.md)** — the agent-facing
  skill: what every verb does, when to call it, and the real failure modes.

## Repo layout (to be filled as the plan lands)

```
plan/       # battle plan, architecture, demo script
memory/     # FalkorDB layer — graph schema, ingest, GraphRAG queries
realtime/   # LaserData layer — stream ingest
motion/     # RocketRide orchestration — workflows, tool calls
agents/     # Guild.ai agent team definitions + handoffs
app/        # demo surface (UI / CLI)
docs/       # MCP client onboarding + the hosted-exposure contract
skills/     # the PALIMPSEST agent skill (ships with the repo)
```

Concept, architecture, and hour-by-hour plan live in `plan/` (generated from the research workflow; edit freely).

## Use it as an MCP server

Connect any MCP host to the live PALIMPSEST memory tools — see [MCP.md](./MCP.md). Public endpoint: `https://palimpsest-bridge.fly.dev/mcp`.
