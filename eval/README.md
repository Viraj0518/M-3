# PALIMPSEST × LongMemEval

The reproducible-benchmark lane for **GOAL.md victory condition 5**. Implements
`plan/research/longmemeval-harness-design.md`.

**The target:** beat **0.712** (Zep — the only cleanly-methodologied graph-memory
entry) on `longmemeval_s_cleaned`, under the **official gpt-4o judge**, **micro**,
**full 500**, with published prompts and **a removal test per mechanism**.

Anything ≥ 0.90 trips the T4 guard and does not get spoken aloud until a second
seed and a second judge family confirm it. Prior work on this benchmark is
**4-for-4 on ≥ 0.9 being a phantom**, and one of those cost a retraction.

---

## Quick start

```bash
cd eval
uv python pin 3.12 && uv sync     # box default is 3.9.6; this project needs >=3.11

make verify                       # free — dataset pins, split locks, prompts, FalkorDB
make gate                         # free — T15: sabotage must FAIL, controls must PASS
make test                         # free — 103 unit tests, judges on canned responses
make eval-smoke-dry               # free — A1 vs A3 over smoke-30, zero API calls
```

Everything above runs **without any API key**. See *What is proven* below for
exactly what that does and does not establish.

---

## What each number means

Metrics live in **two namespaces that never share a dict** (guard T6 — "that
conflation is how phantom #1 was born"):

| Key | Meaning |
|---|---|
| `qa.micro.<split>` | **Overall Accuracy** — the paper's headline. Mean over every row. |
| `qa.macro.<split>` | **Task-averaged Accuracy** — mean over the **6 contaminated buckets**. |
| `qa.by_type.<split>` | Per-type, **official** — each bucket *contains* its `_abs` rows. |
| `qa.by_type_clean_non_official.<split>` | Clean 7-way, abstention split out. **Labelled non-official**: there is no clean 7-way breakdown in upstream code. |
| `qa.abstention.<split>.accuracy` | The 30 `_abs` rows. |
| `qa.abstention.<split>.false_abstention_rate_on_answerable` | **Always reported next to it.** A one-sided abstention number is gameable. |
| `retrieval.recall@k.<split>` | **Returned-set** recall (not NDCG). **Judge-free — this is the GO/NO-GO gate.** |
| `retrieval.returned_set_size.<split>` | Per-arm serving volume (T12). |

**Micro ≠ macro, and the gap is large.** Bucket n ranges 30 → 133. Mastra reports
macro (0.9487); the paper reports micro. Both are always emitted (T8) so a lone
number labelled "accuracy" can never be quoted.

**Split is part of every metric key** (`qa.micro.s_cleaned@98d7416`). Oracle and
`_s` numbers are structurally unmixable (T5). **Oracle recall is 1.0 by
construction** — the haystack *is* the gold sessions, so an oracle number bounds
the *reader*, never the system.

**≥ 0.96 is at the gold-label noise floor** (~12 open upstream annotation-error
issues). The harness attaches that note to the number automatically (T11).

---

## The arms

| Arm | Memory | Retrieval | Purpose |
|---|---|---|---|
| **A0** `a0_fullcontext` | none | whole haystack in the prompt | The bar. Paper: 0.606 |
| **A1** `a1_naive_rag` | flat vectors over turn windows | vector top-k=12 | The honest baseline |
| **A2** `a2_unblock` | substrate `remember`/`query` | server-side | **Optional, off the gate path** |
| **A3** `a3_palimpsest` | typed graph, dated sessions, supersede lineage | S1a–S5 | **The claim** |
| `a3_ablate_supersede` | — no `[:RELATES{supersedes}]` | S3 off | removal test → knowledge-update |
| `a3_ablate_temporal` | — no `date_iso`, no `[:NEXT]`, no date index | S4 off | removal test → temporal-reasoning |
| `a3_ablate_expansion` | vector entry only | S2 off | removal test → multi-session |
| `a3_ablate_sufficiency` | no entity-coverage gate | §4.5 off | removal test → abstention |

**A1 and A3 share the same reader, the same prompt file, the same judge, and the
same k.** That equality is what makes an A3−A1 delta a *retrieval* delta.

**Never cut the ablation matrix.** A number without removal tests is exactly the
artifact this team has retracted before.

### A2 is deliberately not on the gate path

It needs the **live Kaeva backend**, which violates GOAL.md's "zero runtime
dependency on the live Kaeva backend", so a third party cannot rerun it. Its
**reader prompt lives server-side**, making the reader an uncontrolled variable.
And neither substrate arm attaches `haystack_dates`/`session_id` to blocks, so
its temporal ceiling is **structural, not a tuning failure**. It is a stub
(`src/lme/arms/unblock.py`) implementing the §3.5 HTTP shape; **it has never been
executed against a live backend from this repo**. Its absence never fails a gate.

---

## What is proven right now, and what needs keys

### Proven on this box, no API keys, no money spent

| Check | Result |
|---|---|
| `uv sync` on py3.12 | 24 packages, clean |
| Dataset pins (T9) | **both files verified byte-exact** vs committed `SHA256SUMS` |
| Taxonomy replication | `_s_cleaned` **and** oracle: 500 rows, 6 types at 133/133/78/70/56/30, **30 `_abs`** — matches the design doc's machine-verified table |
| Split generation + registry lock | smoke-30 (`daf271ea…`), dev-150 (`1a2a05f4…`), full-500 (`702287fe…`) |
| Split tamper detection | a hand-edited split file is refused |
| **T15 gate** | **15/15 saboteurs correctly FAILED, 6/6 positive controls PASSED** |
| Unit tests | **103 passed** (judges driven by canned responses — no API calls) |
| Official judge transcription | pinned by rendered-output sha256 `2c551dc7…`, incl. upstream whitespace quirks |
| Strict-lane `.format()` KeyError | **fixed structurally** and regression-tested with brace-laden inputs |
| `make eval-smoke-dry` | A1 **and** A3, 30/30 rows each, `valid=True`, against **live FalkorDB @ 127.0.0.1:6401** |
| A3 graph mechanisms | real claims/entities; **supersede + contradicts edges written**; S1a/S1b/S2/S3/S4/S5 all return non-zero |
| **Ablation matrix** | clean diagonal — each ablation zeroes **only** its own mechanism (see below) |
| Report discipline | dry runs are **excluded from the board**, not rendered as results |

Measured ablation effect matrix (dry, n=4, oracle):

```
arm                      supersede_edges dated_sess    s2    s4 miss_ent
a3_palimpsest                         47          6    48    40        4
a3_ablate_supersede                    0          6    48    40        4
a3_ablate_temporal                    47          0    48     0        4
a3_ablate_expansion                   47          6     0    40        4
a3_ablate_sufficiency                 47          6    48    40        0
```

### NOT proven — needs keys

| Path | Needs |
|---|---|
| Real embeddings (`text-embedding-3-small`, dim 256) | `OPENAI_API_KEY` |
| Real claim extraction (`claude-haiku-4-5`) | `ANTHROPIC_API_KEY` |
| Real reader (`claude-sonnet-4-6`) | `ANTHROPIC_API_KEY` |
| **Official judge lane** (`gpt-4o-2024-08-06`) — live HTTP | `OPENAI_API_KEY` |
| Strict panel — live HTTP (logic is unit-tested with canned responses; **no live client is wired for the Gemini/Llama families**) | keys + clients |
| **Every `qa.*` number** | all of the above |
| A2 unblock arm | live Kaeva backend |

**The dry path proves plumbing, not quality.** `test_embedder` is a hash
projection, not a semantic model, so dry `retrieval.*` numbers reflect real graph
*behaviour* over meaningless *rankings*, and dry `qa.*` numbers are meaningless
outright. The harness enforces this itself: dry runs are stamped non-citable in
the manifest, `eval judge` **refuses** them without `--force-dry`, and `eval
report` excludes them from the board.

---

## No silent fallbacks — the rule this harness is built around

There are three stubs — `test_embedder`, `test_reader`, `test_extractor` — and
**each is reachable only by an explicit config flag.** A missing key **raises**.

There is deliberately no `try live / except → stub` branch anywhere. If you find
one, delete it. The 0.9010 phantom was born exactly this way:

> "Harness silent-failed on a missing `SUPABASE_PG_URL` and **re-judged a stale
> report file** instead of re-running. It looked 'deterministic' only because two
> runs read the same stale file." Real score: 0.61–0.68.

Every stub also **self-labels in the manifest pin** (`hash-test-embedder@256`),
so a dry run cannot be mistaken for a real one downstream.

---

## The guards (T1–T15)

All fifteen live in `src/lme/guards.py` as assertions that **fail loud**, and all
fifteen are exercised in **both directions** by `fixtures/negative/sabotaged.py`
+ `make gate`.

The positive-control half is the part people skip, and it is why it is here:
`~/unblock-eval/judge_canonical_lane.py --selftest` passed for weeks while its
live path had **never executed once**. *Validate that the instrument returns a
positive before trusting its zero.*

| # | Trap | Guard |
|---|---|---|
| T1 | stale-artifact re-read | `run_id` on every row + artifact mtime > `started_at` |
| T2 | silent reader fallback | `served == PINNED_CONSTANT` (**not** `served == requested`) |
| T3 | silent-kill / partial run | `len(rows) == expected_n` before any aggregate |
| T4 | ≥ 0.90 disbelief | flags `SUSPECT`, blocks it from the board |
| T5 | oracle↔`_s` mixing | split id is part of every metric key |
| T6 | recall@k as accuracy | `qa.*` / `retrieval.*` namespaces |
| T7 | reader ≡ judge family | substring-matched family exclusion |
| T8 | micro vs macro | both mandatory |
| T9 | dataset drift | sha256 asserted at load |
| T10 | prompt confound | prompts hashed into `config_hash` |
| T11 | gold-label noise | auto-note ≥ 0.96 |
| T12 | ranking-not-serving | returned-set budget + equal-k check |
| T13 | non-summable levers | advisory on implied additivity |
| T14 | router keyed on gold | router takes a `str`; + source scan |
| T15 | broken gate | sabotage must fail, controls must pass |

---

## Reproducing a run

```bash
# 1. Data (gitignored; only hashes are committed)
REV=98d7416c24c778c2fee6e6f3006e7a073259d48f
hf download xiaowu0162/longmemeval-cleaned --repo-type dataset --revision "$REV" \
  --include "longmemeval_oracle.json" --include "longmemeval_s_cleaned.json" \
  --local-dir data/
shasum -a 256 -c data/SHA256SUMS      # refuses on mismatch

# 2. Splits (deterministic under seed 20260803, then sha256-locked)
uv run eval splits

# 3. FalkorDB on 6401 — NOT 6379, NOT 6399
docker run -d --name palimpsest-falkordb -p 127.0.0.1:6401:6379 falkordb/falkordb
uv run eval verify                     # asserts a real GRAPH.QUERY result set
```

`memory/config.py` is the single source for host/port/`EMBED_DIM`. Port 6379 and
6399 are already taken on the build Mac: a stray `redis-server` lets the client
**connect fine** and only then fails on `GRAPH.QUERY` — a false-green that eats
45 minutes. `verify` therefore asserts a **positive result set**, not merely a
successful connect.

---

## The first paid run

```bash
cd eval
cp ../.env.example ../.env    # fill in OPENAI_API_KEY + ANTHROPIC_API_KEY
set -a && source ../.env && set +a

make verify                                            # free, do this first

# T+0  smoke on oracle, both arms, real models              (~$1, ~3 min)
make eval-smoke
uv run eval judge --run runs/a1_naive_rag__smoke-30__<id>
uv run eval judge --run runs/a3_palimpsest__smoke-30__<id>

# T+1  A1 full-500 on _s_cleaned — first citable receipt     (~$3, ~20 min)
uv run eval run --arm a1_naive_rag --data data/longmemeval_s_cleaned.json --split full-500

# T+2  A3 full-500 — claim extraction dominates the cost     (~$120, ~50 min)
uv run eval run --arm a3_palimpsest --data data/longmemeval_s_cleaned.json --split full-500

# T+3  A0 full-context — the bar                             (~$25, ~30 min)
uv run eval run --arm a0_fullcontext --data data/longmemeval_s_cleaned.json --split full-500

# T+4  the four removal tests — cached embeds + extractions  (~$20, ~30 min)
make eval-ablate

# T+5  judge every run, then render the board
for d in runs/*/; do uv run eval judge --run "$d"; done
make report && cat runs/BOARD.md
```

**Check `retrieval.jsonl` recall BEFORE paying for any judge.** It is
deterministic and judge-free, and *the gate that decides GO must have no judge in
it at all* — all four historical phantoms were scoring artifacts, not capability
gains.

**Cut order, pre-agreed:** strict-panel lane → A2 → A0 → full-500 down to
dev-150. **Never the ablation matrix.**

---

## Layout

```
eval/
├─ Makefile              one command per lane; every paid target states its cost
├─ configs/              a0/a1/a2/a3 + 4 ablations + dry_smoke; all hashed
├─ prompts/              reader_v1.md · extract_claims_v1.md · strict_rubric_v1_1.md
├─ splits/               smoke-30 · dev-150 · full-500 + registry.json (sha-locked)
├─ data/SHA256SUMS       committed; the .json files are not
├─ fixtures/negative/    the sabotaged arm that MUST fail the gate (T15)
├─ tests/                103 tests; judges driven by canned responses
└─ src/lme/
   ├─ dataset.py  guards.py  metrics.py  manifest.py  config.py
   ├─ embed.py    reader.py  runner.py   gate.py      cli.py
   ├─ judge_official.py      # gpt-4o-2024-08-06, prompts transcribed VERBATIM
   ├─ judge_strict.py        # 3-family panel, rubric v1.1, .format() bug fixed
   ├─ arms/       base · fullcontext · naive_rag · unblock · palimpsest
   └─ graph/      schema · ingest · retrieve
```

`graph/schema.py` imports `memory/taxonomy.py` and `memory/config.py` **read-only**
— the benchmark and the demo share one vocabulary on purpose. The eval is not a
side project; it is the removal test for the demo's central claim.
`assert_directed_vocabulary()` asserts that import is *live*, so the eval can
never quietly fall back to a private copy while appearing to share the demo's.

---

## Deviations from the design doc

Named, not silent — see the commit message and the final report for the full list.
The main ones:

1. **`prompts/official_judge.py` → `src/lme/judge_official.py`.** The verbatim
   transcription is importable code, so it belongs in the package. Pinned by a
   rendered-output hash instead.
2. **`test_extractor` flag added.** The doc specifies `test_embedder` and
   `test_reader`; A3 also needs claims, so the dry path needs a third opt-in
   stub. Same discipline: explicit flag only, never reached by a missing key.
3. **`--dry` is an overlay, not a config swap.** Otherwise `--dry --ablations`
   would run four identical non-ablated arms and the removal-test wiring would
   never be exercised.
4. **A1 chunks turn-windows, not whole sessions.** Whole-session chunks on `_s`
   (~115k tokens) at k=12 would make A1 a degraded A0 rather than a baseline.
5. **`--via-laser` replay mode (§3.4 step 6) is not implemented.** It is
   explicitly "default off" in the doc and off the gate path.
6. **`a3_oracle_routed` (§4.8) is not implemented as an arm.** The guard that
   makes it safe (`allow_oracle=True`) exists and is tested; the arm itself is
   an upper-bound report, not a gate input.
7. **`uv.lock` is committed** (the doc lists it); `runs/` is gitignored rather
   than committed as tarballs — gate artifacts get tarballed into `plan/gates/`
   deliberately instead.
