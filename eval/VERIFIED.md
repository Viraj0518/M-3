# eval/ harness — verification receipt

The LongMemEval harness was authored by a build agent that **stalled before it could
commit or self-report** (API instability, 2026-08-03). Its tree was checkpoint-committed
to protect the work (commit message said UNVERIFIED), then verified independently. This
file is that verification.

## No-key gates — all GREEN (2026-08-03, live FalkorDB 127.0.0.1:6401)

| Gate | Command | Result |
|---|---|---|
| Dataset pin | `load_dataset(DATA_DIR/'longmemeval_oracle.json')` | 500 questions, sha256 hash-gate PASSED (T9) |
| Env verify | `uv run eval verify` | FalkorDB GRAPH.QUERY answered; keys-unset detected & named |
| Splits | `uv run eval splits --seed 20260803` | smoke-30 / dev-150 / full-500 generated, ids_sha256 locked; **regeneration byte-identical** (determinism proven) |
| Anti-phantom gate | `uv run eval gate --negative fixtures/negative` | **saboteurs caught 15/15 · controls passed 6/6** (T1–T15 guards load-bearing) |
| Dry smoke | `make eval-smoke-dry` (test-embedder + stub reader, no keys) | A1 naive-RAG + A3 PALIMPSEST both ran over smoke-30 on live FalkorDB; valid=True; recall metrics + run artifacts produced |

Dry-run recall (stub embedder — plumbing proof, NOT the real score):
`a1_naive_rag` recall@10 = 1.0000 · `a3_palimpsest` recall@10 = 0.9500 — the sub-1.0 on the
graph arm is the predicted extraction-lossiness signal (design §4.7), i.e. an honest number
from working machinery, not a bug.

## Ready for the first PAID run (needs BYO keys per .env.example)

```bash
export ANTHROPIC_API_KEY=…   # generation
export OPENAI_API_KEY=…      # embeddings (dim 256)
make eval-smoke              # A1 vs A3, oracle smoke-30, real models  (~$1)
make eval                    # full-500 _s_cleaned, all arms + ablations (~$130, ~1h)
make report                  # -> eval/runs/BOARD.md
```

Target: beat Zep's 0.712 on `_s_cleaned` under the official gpt-4o judge (micro, full 500),
with a per-sponsor removal-test matrix behind it. Any cell ≥0.90 triggers the T4 phantom
guard and is not reported until a second seed + second judge family confirm.
