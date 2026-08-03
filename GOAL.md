# GOAL — Memory Meets Motion, 2026-08-03

**Win the hackathon by making one sentence undeniable on stage:**

> Every AI agent wakes up stateless and amnesiac — not with our product. The agent already
> knows **who you are**, **who it is**, and has a **constant datastream** of what is happening
> right now. It pulls required context automatically instead of asking for it, so conversations
> that used to be multi-turn interrogations resolve in a **single turn**.

PALIMPSEST is that sentence built on the four mandatory sponsor techs:

| UX promise | Mechanism |
|---|---|
| Knows who you are | FalkorDB attributed graph — every actor's full history one traversal away |
| Knows who it is | Per-agent identity + its own handover node in the same graph (cold-resume) |
| Constant datastream | LaserData log spine — durable, ordered, replayable from offset 0 |
| Pulls instead of asks | RocketRide Wave (NOW + EVER in one parallel wave) + Guild task.gather; the only human question ever asked is *approval to act* |

## Victory conditions (falsifiable, in priority order)

1. **The ablation lands live**: identical event, cold vs warm graph → opposite verdicts,
   `turns-to-answer` counter showing cold = interrogation, warm = 1. Never cut this beat.
2. **All four sponsor gates GREEN with saved artifacts** in `plan/gates/` by T+5:00
   (removal test per sponsor — judges get receipts, not claims).
3. **Two clean un-narrated 90-second runs** on the demo laptop by T+7:30, different driver each.
4. Real action fired (Discord + GitHub issue) with clickable provenance; kill-and-resume rehearsed until boring.
5. **LongMemEval**: this stack + the unblock stack benchmarked on LongMemEval with the best score
   we can produce across ALL question types — fully REPRODUCIBLE (pinned dataset, pinned seeds,
   harness + configs committed under `eval/`, one-command rerun). Prior LME work in
   `~/unblock-eval/` (judge scripts, strict rubric, v2 plans) is the head start — reuse it.

## Operating directives (operator, 2026-08-03)

1. **Collaborate across the whole fleet** — use ALL resources on both the Mac and the Windows box.
   Coordination plane: unblock DMs to `viraj-hackathon` (ndjson bridge) + this repo. Split work so
   both machines are always busy; the Windows box takes the final-deploy lane.
2. **Make the bridge MCP genuinely interactive** — study existing MCP server repos (unblock_mcp
   first, plus official MCP SDK examples) and wire interactive capability (elicitation/prompts,
   streaming progress) into our bridge's MCP surface, not just flat request/response tools.
3. **Open-sourceable from commit zero** — NO credentials anywhere in the repo, ever: no keys, no
   `.env` committed, no tokens in code or history. Everything runs **locally** or **bring-your-own-key**
   (`.env.example` documents every variable; secret-scan before every commit — a leaked key in git
   history cannot be un-leaked).
4. **Everything local, on Docker** — all services containerized and run locally (FalkorDB, laser-stack,
   the bridge, the UI). Docker Desktop install is a pre-event blocker for any box missing it; the
   embedded-falkordblite path stays as the no-Docker fallback ladder only.
5. **Codex owns UI/UX design**; **Fable 5 plans/coordinates**; **Opus 5 subagents build** — make the
   product the best it can be with the right model on each lane.
6. **Reuse unblock aggressively** and make the four sponsor techs (FalkorDB · LaserData · RocketRide ·
   Guild) STRONGLY integrated — load-bearing with removal tests, never decorative.
7. **Definition of done for every milestone**: commit to the repo → build & start a FRESH Docker
   container of the stack → remove the old containers → run a full end-to-end walkthrough on the
   fresh stack. Nothing counts as done on a warm, hand-tweaked environment.
8. **Interactive MCP widgets** — research the MCP interactive-widget ecosystem (MCP Apps / mcp-ui /
   ext-apps class of repos) and make our bridge's MCP tools return INTERACTIVE widgets where a host
   supports them (graph view, approval card), with plain-text fallback. Verify MCP + CLI surfaces
   end-to-end, everything.
9. **Standing research agent + use every resource** — keep a research agent running throughout;
   exhaust every relevant available resource: local repo clones, prior LongMemEval work, sponsor
   Discords, both fleet boxes. Comms partner on Windows: DM `viraj-hackathon-windows` (ack-verified),
   coordinate continuously.

## Sponsor-stack mandate (operator, 2026-08-03) + integrity rules

**Use every sponsor technology and everything they provide.** All four are mandatory and
load-bearing; judges verify actual usage. Use the FULL relevant offering of each:
- **FalkorDB** — Cypher ring/supersede/hybrid-vector queries + GraphRAG-SDK for pre-bake. ✅ live.
- **LaserData** — Log spine + durable replay-from-offset. ✅ live (Log primitive; graph()/memory()
  deliberately unused so FalkorDB stays the memory layer — an intentional, defensible boundary).
- **RocketRide** — the .pipe IS the orchestration (Wave planner, first-party tool_falkordb). 🔧 being
  wired (motion/ draft PR #3); MUST be real, not a UI simulation.
- **Guild.ai** — multi-agent coordination + human approval. ⛔ THE GAP — blocked on sponsor SEATS
  (human: email + booth). This is the one sponsor we are not yet using.

**Where a sponsor does NOT provide what we need, add other technology** — but only as a labelled
substitute, never a fake. If Guild seats never materialize, the multi-agent coordination + human gate
is provided by the plan's fallback (RocketRide sub-agents + the bridge `ask` verb), stated honestly
on stage. Do not present a substitute as the sponsor's tech.

**INTEGRITY (from the 2026-08-03 adversarial audit — non-negotiable):** NOTHING may be simulated and
presented as real. The UI renders a ring ONLY when the server returns `fired:true` with real paths —
never fabricate one. In LIVE mode the UI never claims sponsor actions (Guild/RocketRide) that did not
actually fire. The cold-vs-warm ablation must be a REAL opposite-verdict (warm fires / cold doesn't on
the same event), not a truncated graph. The eval harness must never emit a citable number that bypasses
its own anti-phantom guards. A demo that fakes load-bearing use loses on the judging criterion.

## Guardrails

- Nothing new starts after Gate 5 (T+5:00). Feature freeze T+6:30. Code freeze + tag T+7:00.
- Cut order (pre-agreed, never re-debated): kill-and-resume → judge-taps-approve → hybrid vector query. **Never the rewind A/B.**
- Zero runtime dependency on the live Kaeva backend; no keys/`.env` in this repo, ever.
- Full plan: `plan/palimpsest-plan.html` (artifact) + `plan/synthesis.json` (source data).
