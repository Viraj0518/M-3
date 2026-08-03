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

## Guardrails

- Nothing new starts after Gate 5 (T+5:00). Feature freeze T+6:30. Code freeze + tag T+7:00.
- Cut order (pre-agreed, never re-debated): kill-and-resume → judge-taps-approve → hybrid vector query. **Never the rewind A/B.**
- Zero runtime dependency on the live Kaeva backend; no keys/`.env` in this repo, ever.
- Full plan: `plan/palimpsest-plan.html` (artifact) + `plan/synthesis.json` (source data).
