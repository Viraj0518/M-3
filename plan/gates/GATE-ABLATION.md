# GATE-ABLATION receipt — the cold-vs-warm rewind is REAL (same event, opposite verdict)

**Status: GREEN** · 2026-08-03 · topology-only against live FalkorDB `127.0.0.1:6401` ·
proves **GOAL victory condition 1** ("the ablation lands live: identical event, cold vs
warm graph → opposite verdicts") — the beat that is NEVER cut.

## What this gate proves — and how it differs from GATE-LASER-replay

Two DISTINCT, both-valid claims, kept honestly separate:

| Claim | Property | Where |
|---|---|---|
| **Reproducibility** | replay-from-0 re-derives the **byte-identical** graph (`digest_warm == digest_cold`) — an **EQUALITY** | `GATE-LASER-replay.md` |
| **The ablation** (this gate) | the **same** new event, into two **differently-seeded** graphs, yields the **OPPOSITE** mechanical verdict — a **DIFFERENCE** | this file |

Equality proves the log faithfully rebuilds memory. It is **not** the ablation, and asserting
graph equality can never demonstrate an opposite verdict — two identical graphs answer
identically by construction. The ablation is the headline beat, and it lives in a NEW,
purpose-built artifact: `realtime/ablation.py` + the `/ablation` bridge verb.

## The mechanism (no LLM, no API key, no network)

One target **new event** — the 4th co-edit, the move that *completes* a ring — is fed to two
graphs that differ **only in their history**:

```
Ring shape:  Northstar-7 ─EDITED→ Transit ←EDITED─ VectorMint ─EDITED→ Grid ←EDITED─ SableWeather
             └─ history (3 prior edits) ─┘                              └─ THE NEW EVENT ─┘

WARM = full historical corpus (3 prior ring edits) + the new event   →  the 3-hop ring FIRES
COLD = ONLY the new event, no history                                →  the SAME query does NOT fire
```

The verdict on **both** graphs is computed by the **same** code the projector and the demo
call — `app.bridge.server.dispatch("ring", …)`, the exact `_RING_CYPHER` in `_h_ring`. There
is no second ring implementation to drift. The corpus is ingested through the **same**
`realtime.graph_writer.GraphWriter` the live firehose uses. Pure Cypher; milliseconds.

## The run (keyless — topology only)

```
cd ~/memory-meets-motion && . .venv/bin/activate

# module CLI
python -m realtime.ablation

# through the bridge router (CLI surface)
python -m app.bridge.server ablation '{}'

# through the REST surface the UI/demo call live
curl -s http://127.0.0.1:8931/v1/ablation | jq .
```

Non-destructive: seeds dedicated keys `palimpsest_ablation_warm` / `palimpsest_ablation_cold`,
never the live demo graphs `palimpsest` / `palimpsest_cold`.

## Result — OPPOSITE VERDICT ON THE SAME EVENT

```
NEW EVENT (fed to both graphs, identically):
  actor = SableWeather   page = "Regional power grid"   event_id = ablation-new
  role  = "the 4th co-edit — the move that completes the ring"

WARM  (history + new event; 4 edits seeded)
  fired            = TRUE
  ring_count       = 1
  ring_score       = 0.6667
  should_open_case = TRUE
  run_time_ms      = 2.16   (FalkorDB's own execution time — the number on screen)
  ring.actors      = ['Northstar-7', 'VectorMint', 'SableWeather']   (3 DISTINCT)
  ring.pages       = ['Metropolitan Transit Authority', 'Regional power grid']  (2 DISTINCT)
  ring.span_s      = 240.0  (≤ 720 s window)
  VERDICT_WARM     = escalate

COLD  (new event ONLY; 1 edit seeded)
  fired            = FALSE
  ring_count       = 0
  ring_score       = 0.0
  VERDICT_COLD     = dismiss

opposite_verdict = TRUE
```

Same event. Same query. Opposite verdict — for a **MECHANICAL** reason (graph topology has
no chain for the ring to close in COLD), not an LLM mood. That is the difference between a
demo and a coin flip.

## The digest is now attribution-sensitive (the honest-receipt fix)

The reproducibility claim is "byte-identical **attributed** graph". Before the fix,
`realtime/pipeline.digest_graph` hashed only topology (`label + domain_key`), so a replay
that re-derived the same shape under a **different** `author_agent` would have **false-passed**
the digest match. The digest now includes `author_agent` on both node and edge lines
(deterministic wall-clock fields like `created_ts` stay out, or equality would become a coin
flip). Proven load-bearing by a mutation test:

```
SAME topology + SAME author  (watcher vs watcher)   → digests MATCH        (a true replay still reproduces)
SAME topology + DIFF author   (watcher vs analyst)  → digests DIFFER       (attribution is in the hash)
in-place flip of one node's author_agent → 'tampered'→ digest CHANGES      (the mutation test)
```

## Tests (against live FalkorDB; no laser, no LLM)

```
python -m pytest realtime/tests -q       # 18 passed (16 + the 2 laser replay-parity when configured)
python -m pytest app/bridge/tests -q     # 107 passed
```

New tests added by this lane:

- `realtime/tests/test_ablation.py` — `test_same_event_opposite_verdict`
  (WARM escalate / COLD dismiss on the same event, real Path objects, 3 distinct actors /
  2 distinct pages), `test_cold_has_only_the_new_event` (COLD holds exactly 1 EDITED edge,
  WARM holds 4 — the discriminator is HISTORY, not the event).
- `realtime/tests/test_digest_attribution.py` —
  `test_same_attribution_matches_different_differs` (same author matches; different author
  differs) and `test_perturbing_author_agent_changes_digest` (the mutation test).

The existing `realtime/tests/test_replay_parity.py` (reproducibility EQUALITY) still passes
with the attribution-aware digest — a true same-attribution replay reproduces exactly.

## Surfaces

`ablation` is entry #13 in the ONE dispatch table, so it is live on all four surfaces with no
second implementation: REST `GET /v1/ablation` (curled live, returns the opposite-verdict
envelope), MCP `palimpsest_ablation`, the generated OpenAPI 3.0 operation `ablation`, and the
CLI. `rest.py` (bind/laser/memory verbs) was NOT touched — the route is generated from the
table.

## What still needs a reader/UI to complete the beat on stage

The mechanism is real and callable live. To land it as the 90-second **[0:40–0:52] REWIND**
beat, the projector's `COLD | WARM` split must call `GET /v1/ablation` and render
`verdict_warm='escalate'` / `verdict_cold='dismiss'` side by side (the payload already carries
`warm.paths` for the animated ring trace and `warm.run_time_ms` for the on-screen number).
That UI wiring is the app/web lane (out of scope for this gate); the endpoint and its
structured result are ready for it.
