# GATE-LASER receipt — REPRODUCIBILITY: replay-from-0 re-derives the identical graph

**Status: GREEN** · 2026-08-03 · LaserData→FalkorDB spine live end-to-end on the Mac.

> ⚠️ **HONEST SCOPE CORRECTION (2026-08-03).** This gate proves **REPRODUCIBILITY** — that
> replaying the durable log from offset 0 re-derives a **byte-identical** graph
> (`digest_warm == digest_cold`, an **EQUALITY**). That is a real and valuable LaserData
> property, but it is **NOT** the cold-vs-warm ablation and does **NOT** by itself prove GOAL
> victory condition 1. The ablation is an **opposite verdict** on the **same** event fed to two
> **differently-seeded** graphs — a **DIFFERENCE**, not an equality. Asserting graph equality
> can never demonstrate opposite verdicts (two identical graphs answer identically). The real
> ablation now lives in `realtime/ablation.py` + the `/ablation` bridge verb, with its own
> receipt at **`plan/gates/GATE-ABLATION.md`**. Read the two gates together: this one =
> "the log faithfully rebuilds memory"; GATE-ABLATION = "memory changes the verdict".
>
> The digest below was also hardened on 2026-08-03 to include `author_agent` on nodes and
> edges (see `realtime/pipeline.digest_graph`), so a replay that re-derived the same topology
> under a **different** author no longer false-passes the match. The equality still holds for a
> TRUE same-attribution replay (author is deterministic on the write path); the recorded hash
> value in this document predates that change and is illustrative only.

## Transport used

**`laser-sdk` (v0.0.1), Log primitive ONLY**, over the running laser-stack iggy at
`127.0.0.1:8090`, stream `live`. No raw-TCP / bare-iggy fallback was needed — the SDK
drives Iggy's Log cleanly. Connection string read from `memory/config.laser_connection()`
(`iggy:laser@…`, never hardcoded; never the SDK examples' `iggy:iggy@…`).

Scope discipline held: only `Topic.producer` (publish), `Topic.consumer_group` /
per-partition `Topic.consumer` (consume), and `Topic.replay` (replay-from-offset) are
called. The substrate advertises premium capabilities (`graph/query/managed` = true) — we
call **none** of them. `laser.graph()` / `laser.memory()` are never touched; FalkorDB owns
EVER, LaserData owns NOW.

## The run (no API keys — file source + deterministic test-embedder)

```
cd ~/memory-meets-motion && . .venv/bin/activate
export LASER_CONNECTION_STRING="iggy:laser@127.0.0.1:8090"

# whole spine, one command: file -> signal.raw -> consumer+gate -> FalkorDB,
# then replay-from-0 -> FalkorDB, assert digests match.
python -m realtime.verify_e2e --limit 800
```

Pipeline exercised (each arrow a durable LaserData record):

```
file://demo/seed_replay.ndjson --(edge tap, key=wiki)--> signal.raw (4 partitions)
  --> consumer group (all partitions, commit-after-side-effects) --> sensing gate --> FalkorDB  [WARM]
  --> replay cursor from offset 0                                 --> sensing gate --> FalkorDB  [COLD]
```

## Result — DIGESTS MATCH

```
produced:        800 events -> signal.raw across 53 wikis (top: commonswiki 408, wikidatawiki 75, enwiki 61)

WARM build  (read via consumer group, all partitions):
  737 nodes / 822 edges
  gate: 800 ingested, 287 admitted, 513 gated_out  => 64.1% rejected as non-novel
  known edit present: Infierno -> "Tournoi de tennis du Canada" (frwiki)
  digest = 62c3041b4b6a4d664531e07c19d2795a39a430e5c248c0f7e6fa4a7581f9063c

COLD build  (read via replay cursor from offset 0):
  737 nodes / 822 edges
  digest = 62c3041b4b6a4d664531e07c19d2795a39a430e5c248c0f7e6fa4a7581f9063c

PARITY: digests_match = TRUE   same_event_set = TRUE   (800 group records == 800 replay records)
elapsed: 80.8s total (produce 50s + two full builds + derivations)
```

Two **independent** LaserData read mechanisms (consumer group vs replay-from-0), over the
same durable log, re-derive a **byte-identical** attributed graph. That is the falsifiable
**reproducibility** claim (the log rebuilds memory exactly), proven with a saved artifact
rather than asserted. It is the FOUNDATION the ablation stands on — but it is not the ablation
itself; the opposite-verdict proof is `plan/gates/GATE-ABLATION.md`.

Canonical demo graphs also populated by the same run (additive, non-destructive — the
hand-seeded ring corpus in warm `palimpsest` is preserved):
- `palimpsest` (warm): 748 nodes (12 seed + 736 firehose)
- `palimpsest_cold`: 737 nodes (replay-from-0)

Digest is over **domain keys** (Actor.name / Page.title / Wiki.code / Event.id), never
FalkorDB internal ids, so two independently-built graph keys hash identically iff their
content is identical (`realtime/pipeline.digest_graph`).

## Why the digests can match at all (the honest note)

The sensing gate's novelty is order-dependent, so both builds process the drained record
SET in canonical `(partition, offset)` log order (`pipeline.build_canonical`). The graph is
then a pure function of the admitted set — idempotent MERGE on domain ids means an
at-least-once redelivery is a no-op. The replay invariant ("the log re-derives the graph")
is thus a clean equality, not a coin flip.

## Tests (against LIVE iggy + FalkorDB)

```
python -m pytest realtime/tests -q       # 14 passed
python -m pytest app/bridge/tests -q     # 107 passed (stream verbs re-verified live)
```

- `test_consumer_invariants.py` — the four ported invariants (enqueue/surface-before-ack;
  dedup on STREAM SEQUENCE `(partition,offset)` not message_id, with message_id=None on the
  frames; backpressure/no-drop via pull-based demand; idempotent cancellation-safe teardown).
- `test_gate.py` — DISTANCE-not-similarity novelty; exact duplicate gated, novel admitted,
  cold-start admits; OpenAI embedder raises without a key (never silent).
- `test_graph_writer.py` — recentchange→graph mapping; idempotent MERGE (redelivery creates
  no duplicate node); CO_EDITED_WITH + edit_count derived.
- `test_replay_parity.py` — LIVE: produce 120 events → warm(consumer) vs cold(replay-from-0)
  digest MATCH; replay-into-cold populates from empty.

## What is live vs stubbed after this lane

- **LIVE**: `realtime/{producer,consumer,gate,graph_writer,replay,pipeline}.py` +
  `app/bridge/stream.py`; bridge verbs `stream_publish`, `stream_tail` (real records+offset,
  no longer a stub — honest degraded envelope preserved for the projector), `stream_replay`
  (the rewind, into a target graph).
- **Stub (separate lane, unchanged)**: `act` (RocketRide) — still an honest
  `not_implemented` envelope.

## Reproduce

```
python -m realtime.producer --source file://demo/seed_replay.ndjson --limit 800   # edge tap
python -m realtime.verify_e2e --limit 800                                          # full gate
```
Re-running appends to `signal.raw`; both builds read the whole topic from 0, so the digest
match holds within a run.
