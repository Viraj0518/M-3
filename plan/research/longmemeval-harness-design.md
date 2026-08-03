I have everything. Here is the design doc.

---

# PALIMPSEST × LongMemEval — Benchmark Design Doc
**Status:** design, report-only. Nothing under `~/memory-meets-motion` was modified.
**Date:** 2026-08-03 · **Target:** GOAL.md victory condition #5

---

## 0. Executive summary — the five decisions

1. **Dataset is already half-pinned on this box, and half-poisoned.** `~/unblock-eval/eval-data/longmemeval_oracle.json` is **byte-identical to the official cleaned release** (sha256 `821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c` = the HF LFS hash of `xiaowu0162/longmemeval-cleaned@98d7416`). Use it as-is. But `~/unblock-eval/eval-data/longmemeval_s.json` (278,025,796 B, sha `08d8dad4be43…`) is the **deprecated pre-September-2025 `_s`** — the cleaned file is 277,383,467 B and named `longmemeval_s_cleaned.json`. **Any number from the local `_s` is non-comparable to every published score.** Re-download pinned.
2. **Reuse the *rubric and the traps*, not the code.** The judge protocol, the 42 documented failure modes, and the per-category loss profile from `~/unblock-eval/` are worth more than any script there. Two of the three prior harnesses are Windows-pathed, one has a never-executed live path (a confirmed `KeyError` in `judge_canonical_lane.py`), and the com_dom repo is a *different benchmark* (LongMemEval-**V2**, web trajectories) plus **it contains a plaintext API key** — do not copy files from it into an open-source repo.
3. **The official judge, verbatim, is the only citable lane.** `gpt-4o-2024-08-06`, `temperature=0`, `max_tokens=10`, the unmodified `get_anscheck_prompt` with its four per-type variants. Run the strict cross-family rubric panel *alongside* as an anti-phantom band, never as the headline.
4. **Four arms, three of them removal tests.** A0 full-context · A1 naive-RAG · A2 unblock substrate · A3 PALIMPSEST graph, plus A3 ablations (−supersede, −temporal, −expansion, −sufficiency). "Best score" only has receipts if the mechanism that produced it can be deleted and the score moves.
5. **The whole thing is affordable.** Full 500-question `_s` end-to-end: ~$130 and ~1h wall for the graph arm, ~$3 for naive-RAG. There is no reason to cite a sub-sample as the headline.

---

## 1. LOCAL RECON — what exists, what it's worth

### 1.1 Inventory with verdicts

| Asset | Path | Verdict |
|---|---|---|
| Oracle dataset, **pin-verified** | `/Users/tenzinyeshi/unblock-eval/eval-data/longmemeval_oracle.json` | **USE.** sha matches official cleaned release exactly |
| `_s` dataset, **stale bytes** | `/Users/tenzinyeshi/unblock-eval/eval-data/longmemeval_s.json` | **QUARANTINE.** pre-cleaning; rename to `longmemeval_s_DEPRECATED_20240ver.json` so nobody loads it |
| Strict judge rubric v1/v1.1 | `/Users/tenzinyeshi/unblock-eval/JUDGE-STRICT-RUBRIC-v1.md` | **LIFT VERBATIM.** Highest-value artifact in the tree. R1–R6, model-agnostic, human-validated |
| Canonical judge lane | `/Users/tenzinyeshi/unblock-eval/judge_canonical_lane.py` | **LIFT THE LOGIC, FIX THE BUG.** `ENTAIL_PROMPT.format()` raises `KeyError: 'question, gold, prediction'` — the rubric text contains a literal `{question, gold, prediction}` that `str.format` reads as a field. `--selftest` passes because it never calls `call_judge`; **the live path has never run.** Reusable: family-exclusion guard, majority-vote, sidecar schema, `sha256_file`, `compute_agreement` (≤0.02 ⇒ DUAL-CONFIRMED) |
| Rerank/recall methodology | `/Users/tenzinyeshi/unblock-eval/rerank-recall-check-methodology.md` | **LIFT THE RULES.** returned-set recall not NDCG; equal-k *and* production-k; per-category breakout |
| Retrieval-utility SQL | `/Users/tenzinyeshi/unblock-eval/retrieval-utility-diagnostic.sql` | Not applicable (unblock-Postgres shaped). One rule transfers: a wrong join key returns a **silent zero** — always validate the instrument returns a positive before trusting its zero |
| Soak overrun gate | `/Users/tenzinyeshi/unblock-eval/soak_overrun_gate.py` | **LIFT THE DISCIPLINE:** refuses to emit a number until ≥3 seeds; "single-run numbers are DIRECTIONAL, never headlined"; **LLM classifies, CODE computes** |
| V2 harness fork | `/Users/tenzinyeshi/unblock-eval/longmemeval-com-dom-memory/` | **DIFFERENT BENCHMARK** (LongMemEval-V2 web-agent trajectories, 451 q, `static/dynamic/procedure/gotchas` taxonomy). Not our target. ⚠️ `scratchpad/run1.sh` has a **plaintext `DEEPINFRA_API_KEY` + Supabase URL with password** — rotate, and never copy files from this tree into `memory-meets-motion` |
| Substrate LME arms | `/Users/tenzinyeshi/unblock-eval/unblock_substrate/{src/eval/longmemeval,scripts/eval}` | **BLACK-BOX IT.** See §3.5 |
| W5 CI gate | `/Users/tenzinyeshi/unblock-eval/unblock_ci/w5-eval-gate/` | Never executed a real run. **Lift one idea:** the negative fixture — a deliberately-broken candidate must FAIL, else the gate is broken |
| Specs dir | `~/.unblock/specs/` | No eval-relevant specs. UX.md/FLEET-PLAN.md/TIER-CAPS.md only |

### 1.2 Prior scores with provenance — the numbers we inherit

All on LongMemEval **v1**, and all from a *different retrieval stack* than PALIMPSEST. They set expectations, not baselines.

| Value | Split | n | Setting | Status |
|---|---|---|---|---|
| **0.9010** | oracle | 101 | "wiki3way-l1b-harness" | **RETIRED PHANTOM — never cite** |
| 0.7525 – 0.8614 | oracle | 101 | *the exact 0.9010 config*, 3 seeds × 3 judge families × 5 votes | The honest replacement band. maj-of-3 mean **0.8152**, floor **0.7591** (rubric v1.1: maj-3 **0.8251**, floor **0.7624**) |
| ~0.706 / 0.71 strict | `_s` real haystack | 102 | single run | The only honest **non-oracle** figure prior work produced |
| 0.733 strict / 0.92 non-abstain | real | 30 | deployed MiniMax probe | gap was over-abstention, not retrieval |
| 0.65 | oracle | 20 | GPT-5.5 reader, no retrieval, votes=1 | `LME-GPT55-READER-SMOKE-n20.md` — explicitly not comparable |
| recall@5 = recall@10 = **1.000** | oracle | 30 | any stack | **Oracle recall is 1.0 by construction.** Every oracle number bounds the *reader*, never the system |
| token-F1 structural ceiling **0.890** | oracle | 500 | — | v1 token-F1 can never exceed this. Judge accuracy is the real metric |

**Per-category loss profile (n=30 oracle, recall=1.0 in every category — so this is pure reader failure):**

| category | token-F1 | abstention rate |
|---|---|---|
| single-session-user | 1.000 | 0.0 |
| single-session-assistant | 0.636 | 0.0 |
| knowledge-update | 0.600 | 0.0 |
| multi-session | 0.500 | 0.2 |
| **single-session-preference** | **0.040** | **0.6** |
| **temporal-reasoning** | **0.057** | **0.6** |

This is the single most useful inherited fact: **with gold context in hand, preference and temporal still collapse, and they collapse by *abstaining*.** That is a prompt/calibration problem, not a retrieval problem — and it is independently reproduced by GPT-5.5 (0/3 on preference, all three failures being abstention).

### 1.3 The trap table — encode these as executable guards, not prose

The 0.9010 phantom had **two** documented mechanisms and both matter:

> **Root cause (F-80):** "Harness silent-failed on a missing `SUPABASE_PG_URL` and **re-judged a stale report file** instead of re-running. It looked 'deterministic' only because two runs read the same stale file." Real score: 0.61–0.68.
> **Contributing:** lax judge family inflated it. "Judge miscalibration is **bidirectional** — truth is the cross-family-calibrated band, never one judge."

Prior work's own count: **"every prior ≥0.9 was a phantom — 4 for 4 (judge-switch, stale-report, lucky single-run, small-n)"**, plus a fifth (post-hoc metric selection).

| # | Trap | Guard to implement |
|---|---|---|
| T1 | **Stale-artifact re-read** | Every run mints a `run_id` (uuid) + `started_at`. Aggregator asserts `rows[*].run_id == manifest.run_id` and `mtime > started_at`. Fail **loud** |
| T2 | **Silent reader fallback** | Provider rewrites *both* requested-and-served fields on fallback, so `served != requested` passes cleanly. Correct check: `served == PINNED_MODEL_CONSTANT`, else INVALID-discard. Persist per-question |
| T3 | **Silent-kill / partial run** | `len(rows) == manifest.expected_n` asserted before any aggregate is computed or printed |
| T4 | **≥0.9 disbelief** | `phantom_guard()`: any arm/category ≥0.90 is stamped `SUSPECT — requires refutation round` in the manifest and blocked from the summary table until a second seed + second judge family confirm |
| T5 | **Oracle↔S mixing** | Split is part of the metric key: `acc.micro.s_cleaned@98d7416` vs `acc.micro.oracle@98d7416`. Structurally unmixable |
| T6 | **recall@k quoted as accuracy** | Separate namespaces `retrieval.*` vs `qa.*`. "That conflation is how phantom #1 was born" |
| T7 | **Reader ≡ judge family** | Hard-coded family exclusion list, substring-matched (a re-slug must still be caught) |
| T8 | **Micro vs macro** | Emit **both** + abstention separately, exactly as official `print_qa_metrics.py`. Mastra reports macro, the paper reports micro; they differ by many points because n ranges 30→133 |
| T9 | **Dataset drift** | sha256 asserted at load against a constant. Refuse to run on mismatch |
| T10 | **Prompt confound** | CoN+JSON formatting alone is worth **up to 10 absolute points**. The answering prompt is committed and hashed into `config_hash`; a prompt change is a new config |
| T11 | **Gold-label noise** | ~12 open annotation-error issues upstream. Anything ≥96% is at/past the noise floor — say so |
| T12 | **Ranking-not-serving law** | Triangulated ×3 in prior work: **rank position converts, volume never.** Do not "fix" a category by raising top-k. Report returned-set *size* per arm |
| T13 | **Per-lever deltas are never summable** | Only the measured ×3 of the *composed* stack is citable. Publish a lever × failure-bucket overlap matrix; each failure row reclaimable exactly once |
| T14 | **Router keyed on gold** | Any router must key on **runtime-computable question features**, never on gold `question_type` or qid lists. See §4.6 |
| T15 | **Broken gate** | Negative fixture: a deliberately-sabotaged arm must FAIL the gate; if it passes, the gate is broken |

---

## 2. DATASET — pin, split, taxonomy, metric

### 2.1 Obtain (pinned)

```bash
# eval/scripts/fetch_dataset.sh
REV=98d7416c24c778c2fee6e6f3006e7a073259d48f     # xiaowu0162/longmemeval-cleaned @ main, 2025-09-19
hf download xiaowu0162/longmemeval-cleaned --repo-type dataset --revision "$REV" \
  --include "longmemeval_oracle.json" --include "longmemeval_s_cleaned.json" \
  --local-dir eval/data/
shasum -a 256 -c eval/data/SHA256SUMS     # committed; refuses on mismatch
```

`eval/data/SHA256SUMS` (committed; oracle line already verified on this box):
```
821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c  longmemeval_oracle.json
<fill from download>                                              longmemeval_s_cleaned.json
```

Shortcut for the oracle: `cp ~/unblock-eval/eval-data/longmemeval_oracle.json eval/data/` — hash already matches. Do **not** copy the local `_s`.

Do **not** use `datasets.load_dataset()` — the HF viewer/PyArrow inference fails because `answer` mixes `str` and `int` across the 500 rows. Plain `json.load()`.

### 2.2 Which split

| Split | Sessions/q | Tokens/q | Role in our plan |
|---|---|---|---|
| `oracle` | mean **1.9** (min 1, max 6) | ~2–8k | **Dev + reader-ceiling arm.** Retrieval is 1.0 by construction. Cheap, fast, ideal for the T+0 smoke gate |
| `_s_cleaned` | ~40–50 | ~115k | **THE HEADLINE.** This is what every published number means |
| `_m_cleaned` | ~500 | ~1.5M (2.7 GB file) | **Out of scope.** Authors themselves call it "too long" |

**Feasibility of full `_s` (n=500) inside the sprint — costed:**

| Phase | Volume | Wall (conc 32–40) | $ |
|---|---|---|---|
| Embeddings, all arms (cached) | ~57.5M tok | ~15 min | ~$1.2 |
| A1 naive-RAG ingest | embeddings only | — | ~$0 |
| A3 claim extraction | ~25,000 sessions × ~2.5k tok | ~40–50 min | ~$120 |
| Reader (any arm) | 500 × ~4k in / 300 out | ~5 min | ~$3 |
| Judge (official, gpt-4o) | 500 × ~600 tok, `max_tokens=10` | ~2 min | ~$2 |
| Strict panel (3 families × 5 votes) | 500 × 15 calls | ~10 min | ~$8 |

**Conclusion: full-500 `_s` is affordable.** Do not headline a sub-sample. Use frozen sub-splits only for iteration:

- `smoke-30` — stratified 5/type, seed 20260803, **oracle**. Runs in ~90s. This is the `make eval-smoke` CI gate.
- `dev-150` — stratified proportional, seed 20260803, **`_s`**. Iteration set. Held-out from the headline analysis to avoid the peeking trap (prior art #39: thresholds were once set while the answer file was already on disk).
- `full-500` — the citable number.

Split membership is written to `eval/splits/*.json` and **sha256-locked** — the harness refuses to run a named split whose id-list hash doesn't match `eval/splits/registry.json` (this is the one discipline from the com_dom `iterate.sh` worth keeping).

### 2.3 Taxonomy (machine-verified against `longmemeval_oracle.json`, n=500)

| `question_type` | total | of which `_abs` | non-abstention |
|---|---|---|---|
| temporal-reasoning | 133 | 6 | 127 |
| multi-session | 133 | 12 | 121 |
| knowledge-update | 78 | 6 | 72 |
| single-session-user | 70 | 6 | 64 |
| single-session-assistant | 56 | 0 | 56 |
| single-session-preference | 30 | 0 | 30 |
| **total** | **500** | **30** | **470** |

Fields: `question_id, question_type, question, answer, question_date, haystack_dates, haystack_session_ids, haystack_sessions, answer_session_ids`. Turns carry `has_answer: true` on evidence turns — **never read this at inference; it is gold.** Haystack is timestamp-sorted for `_s`, **unsorted for oracle** (a real trap if you assume order).

Two non-obvious official behaviours to replicate exactly:
- **Abstention is a substring check on the id**: `'_abs' in question_id`. Those rows' `answer` field is an *explanation of why it's unanswerable*, not a gold answer.
- **Abstention rows are ALSO bucketed into their base `question_type`.** So the reported `temporal-reasoning` accuracy contains 6 abstention items, and `Task-averaged Accuracy` is a macro over 6 contaminated buckets. There is no clean 7-way breakdown in official code. **Replicate the contamination for comparability, and emit a clean 7-way table alongside labelled `non-official`.**

### 2.4 Official metric + judge protocol

`src/evaluation/evaluate_qa.py`, judge **`gpt-4o-2024-08-06`**, `temperature=0`, `max_tokens=10`, `n=1`, label = `'yes' in response.lower()`.

`get_anscheck_prompt` has **four** variants:
- default (single-session-user / -assistant / multi-session)
- **temporal-reasoning**: + "do not penalize off-by-one errors for the number of days"
- **knowledge-update**: + stale info alongside the updated answer still counts **correct** ← directly shapes our supersede serving strategy (§4.2)
- **single-session-preference**: `answer` is a **rubric**, partial credit explicit — "does not need to reflect all the points"
- **abstention** overrides all of the above: "Does the model correctly identify the question as unanswerable?"

Emit all four official aggregates: `Overall Accuracy` (micro/500), `Task-averaged Accuracy` (macro/6), per-type, `Abstention Accuracy` (the 30 `_abs`).

Judge's own agreement floor (paper Table 6, n=30/cell): 0.98 average but **0.90 on single-session-preference and 0.90 on abstention** — i.e. exactly the two cells we intend to move. Report those two with an explicit ±judge-noise band.

### 2.5 What "good" looks like

| System | `_s` | Judge | Note |
|---|---|---|---|
| GPT-4o full-context | 0.606 | gpt-4o | paper; **pre-cleaning bytes** |
| GPT-4o + CoN | 0.640 | gpt-4o | paper |
| Zep / Graphiti | **0.712** | gpt-4o, official prompts | cleanest vendor entry; 1.6k avg context tokens |
| Supermemory | 0.95 (gpt-4o) | gpt-4o | headline labelled "Recall@k=15 with aggregation"; baselines copied from Zep |
| Hindsight | 0.914 / 0.890 / 0.836 | **GPT-OSS-120B** | different judge — one table, three judges |
| Mastra | 0.9487 | gpt-4o | explicitly **macro**, not the paper's micro |
| Mem0 | "94.4" | unstated | **arithmetically unreconstructable**: their own six category scores macro to 85.1, micro to 88.5. Treat as unusable |

**Our defensible target: beat 0.712 (Zep, the only cleanly-methodologied graph-memory entry) on `_s_cleaned` under the official gpt-4o judge, micro, full 500, with published prompts and a removal test per mechanism.** Anything ≥0.90 triggers T4 and does not get spoken aloud until it survives refutation. Preference (Zep 0.567) and temporal are where the headroom is.

---

## 3. HARNESS DESIGN — `eval/`

### 3.1 Layout

```
eval/
├─ README.md                      # how to rerun, verbatim commands, what each number means
├─ pyproject.toml                 # uv-managed; requires-python = ">=3.11"   (box default is 3.9.6)
├─ uv.lock                        # committed
├─ Makefile
├─ data/            SHA256SUMS    # datasets gitignored, hashes committed
├─ splits/          smoke-30.json dev-150.json full-500.json registry.json
├─ configs/
│    a0_fullcontext.yaml  a1_naive_rag.yaml  a2_unblock.yaml
│    a3_palimpsest.yaml
│    a3_ablate_supersede.yaml  a3_ablate_temporal.yaml
│    a3_ablate_expansion.yaml  a3_ablate_sufficiency.yaml
├─ prompts/
│    reader_v1.md        # THE answering prompt — hashed into config_hash
│    extract_claims_v1.md
│    official_judge.py   # get_anscheck_prompt, transcribed verbatim from upstream
│    strict_rubric_v1_1.md   # lifted from ~/unblock-eval/JUDGE-STRICT-RUBRIC-v1.md
├─ src/lme/
│    dataset.py     # load + sha assert + split resolve + role normalisation
│    embed.py       # cached embedder (sha256(model|text) -> .npy)
│    arms/  base.py fullcontext.py naive_rag.py unblock.py palimpsest.py
│    graph/ schema.py ingest.py retrieve.py cypher.py
│    reader.py      # single-turn, model-pin assert (T2)
│    judge_official.py    # gpt-4o-2024-08-06, verbatim prompts
│    judge_strict.py      # cross-family panel, rubric v1.1, votes=5  (T7 family guard)
│    metrics.py     # micro/macro/abstention/per-type + retrieval.recall@k
│    manifest.py    # run_id, config_hash, pins, git sha, guards T1/T3/T4/T9
│    guards.py      # every trap in §1.3 as an assertion
│    cli.py         # `uv run eval ...`
├─ fixtures/negative/   # sabotaged arm that MUST fail the gate (T15)
└─ runs/                # gitignored; each run dir committed as a tarball artifact for gates
     <run_id>/ manifest.json  rows.jsonl  retrieval.jsonl  aggregate.json  judged.<lane>.json
```

### 3.2 One command

```makefile
# eval/Makefile
DATA ?= eval/data/longmemeval_s_cleaned.json
SPLIT ?= full-500
SEED  ?= 20260803

eval-smoke: ; uv run eval run --arm a1_naive_rag --arm a3_palimpsest \
                 --data eval/data/longmemeval_oracle.json --split smoke-30 --seed $(SEED)
eval:       ; uv run eval run --all-arms --data $(DATA) --split $(SPLIT) --seed $(SEED)
eval-ablate:; uv run eval run --arm a3_palimpsest --ablations --data $(DATA) --split dev-150 --seed $(SEED)
report:     ; uv run eval report --runs eval/runs --out eval/runs/BOARD.md
gate:       ; uv run eval gate --negative eval/fixtures/negative   # T15
```

`make eval` is the whole benchmark, from a clean checkout, given a `.env` with keys. Nothing else.

### 3.3 `manifest.json` — the reproducibility contract

```jsonc
{
  "run_id": "b2f1…",                      // T1
  "started_at": "2026-08-03T18:04:11Z",
  "git_sha": "…", "git_dirty": false,
  "arm": "a3_palimpsest",
  "config_hash": "sha256 over {config.yaml, reader_v1.md, extract_claims_v1.md, schema.py, retrieve.py, code_version}",
  "dataset": { "file": "longmemeval_s_cleaned.json",
               "sha256": "…", "hf_repo": "xiaowu0162/longmemeval-cleaned",
               "hf_revision": "98d7416c24c778c2fee6e6f3006e7a073259d48f" },   // T9
  "split": { "name": "full-500", "n": 500, "ids_sha256": "…", "seed": 20260803 },
  "expected_n": 500,                        // T3
  "pins": { "embedder": "text-embedding-3-small@1536",
            "extractor": "claude-haiku-4-5-20251001",
            "reader": "claude-sonnet-4-6-20260514",
            "judge_official": "gpt-4o-2024-08-06",
            "judge_strict_panel": ["…", "…", "…"] },   // T7: none may share reader family
  "sampling": { "temperature": 0.0, "top_p": 1.0, "reader_max_tokens": 512 },
  "served_model_violations": [],            // T2 — non-empty ⇒ run INVALID
  "cost": { "usd_est": 0.0, "tokens_in": 0, "tokens_out": 0 },
  "guards": { "T1":"pass","T2":"pass","T3":"pass","T4":"clean","T9":"pass" },
  "phantom_flags": []                       // T4
}
```

`rows.jsonl`, one per question: `question_id, question_type, is_abstention, question, gold, prediction, prediction_raw, retrieved_session_ids, retrieved_block_ids, n_context_tokens, latency_ms, served_model, run_id`.

`retrieval.jsonl`: `question_id, gold_session_ids, ranked_session_ids, recall@{1,5,10,k}, returned_set_size`. **Judge-free, deterministic — this is the GO/NO-GO gate** (prior art: "the gate that decides GO must have no judge in it at all, because all four historical phantoms were scoring artifacts, not capability gains").

Determinism: `temperature=0` everywhere; embeddings content-addressed and cached (`sha256(model|text) → .npy`), so a rerun is embedding-free and free-of-charge; extraction outputs also cached by `sha256(prompt|session_text|model)` so A3 ingest is paid once and every ablation reuses it.

### 3.4 The PALIMPSEST arm (A3)

**Isolation decision: one FalkorDB graph per question**, key `lme_{split}_{question_id}`. Reasons: (a) cross-question leakage is the single most likely false-green and per-graph isolation makes it structurally impossible; (b) traversals stay ~50 sessions wide so ring/expansion queries stay in the millisecond band the demo advertises; (c) trivially parallel and resumable; (d) `GRAPH.DELETE` is a clean teardown. Cost: 500 small HNSW indexes — fine at this scale.

**Schema (`eval/src/lme/graph/schema.py`) — LongMemEval mapped onto the PALIMPSEST taxonomy already specified in `plan/synthesis.json`:**

```
(:Session {id, date_iso, date_raw, ordinal})
(:Turn    {id, role, text, ordinal, ts, emb})
(:Claim   {id, text, emb, predicate, valid_from, polarity, kind})   // kind ∈ fact|preference|event
(:Entity  {name, norm})

(:Turn)-[:IN_SESSION]->(:Session)
(:Session)-[:NEXT]->(:Session)                       // temporal spine, ordered by date_iso
(:Claim)-[:FROM_TURN]->(:Turn)                       // provenance -> recall@k on answer_session_ids
(:Claim)-[:MENTIONS]->(:Entity)
(:Claim)-[:RELATES {relation}]->(:Claim)             // GENUINELY DIRECTED
        // relation ∈ supports|contradicts|derived_from|supersedes|duplicates|references

INDEX: range on Session.date_iso, Session.ordinal, Entity.norm, Claim.predicate
VECTOR: HNSW on Claim.emb and Turn.emb (cosine)
```

This is the same vocabulary the demo graph uses (unblock's 6-value `RELATION_KINDS`, `:RELATES` stored directed — including the CHECK-constraint defect the plan says we fix). **The benchmark and the demo share one schema module.** That is the point: the eval is not a side project, it is the removal test for the demo's central claim.

**Ingestion (`graph/ingest.py`), per question:**

1. `haystack_dates[i]` → `Session{date_iso, ordinal}`; wire `[:NEXT]` in date order. *(Both prior arms parsed the dates and then never stamped them — that alone structurally capped temporal-reasoning. Fixing it is our largest free win.)*
2. Each turn → `:Turn {role, text, ordinal, ts=session.date_iso}`, `[:IN_SESSION]`. **Index assistant turns too** — the official retrieval baseline drops 51 `single-session-assistant` questions precisely because it indexes user turns only.
3. **One extractor call per session** (not per turn — 25k calls, not 500k). Returns typed claims:
   ```json
   {"claims":[{"text":"...","predicate":"degree_earned","kind":"fact",
               "entities":["Business Administration","university"],
               "from_turn":3,"valid_from":"2023-05-20"}]}
   ```
   `predicate` is a free-form slot string, normalised lowercase-snake. `kind=preference` for stated likes/dislikes/constraints.
4. **Supersede pass, deterministic, no LLM:** group claims by `(subject_entity_norm, predicate)`. Within a group with ≥2 members and differing `text`, sort by `valid_from` and write `(newer)-[:RELATES{relation:'supersedes'}]->(older)`. `contradicts` for same-timestamp conflicts. This is the knowledge-update mechanism and it is **auditable** — a judge can read the chain.
5. Embed `Claim.text` and `Turn.text` (cached).
6. **Optional replay-through-LaserData mode** (`--via-laser`): ingest emits each turn as an ordered record on topic `signal.raw` with `key=question_id`, and `graph_writer.py` consumes it — the *same* consumer the demo uses. `--from-offset 0` replays the exact benchmark ingest. This makes reproducibility a property of the log spine rather than of the script, and it is a live removal test for the LaserData sponsor gate. Default **off** for cost/time; **on** for one recorded `smoke-30` run whose artifact goes in `plan/gates/`.

**Retrieval (`graph/retrieve.py`) — five stages, each individually ablatable:**

```cypher
-- S1a  semantic entry
CALL db.idx.vector.queryNodes('Claim','emb', $k1, vecf32($qemb)) YIELD node AS c, score
RETURN c, score

-- S1b  lexical entry over turns (BM25-ish; FalkorDB full-text index)
CALL db.idx.fulltext.queryNodes('Turn', $qtext) YIELD node AS t, score RETURN t, score
-- fuse S1a/S1b with RRF, rrf_k = 60      (prior-art constant)

-- S2  graph expansion: symbolic 1-hop over shared entities
UNWIND $seed_ids AS sid
MATCH (seed:Claim {id: sid})-[:MENTIONS]->(e:Entity)<-[:MENTIONS]-(nb:Claim)
WHERE nb.id <> sid
RETURN nb, e.norm, count(*) AS shared ORDER BY shared DESC LIMIT $fanout

-- S3  supersede resolution: replace each hit by the HEAD of its chain, keep the chain
MATCH (c:Claim) WHERE c.id IN $hit_ids
OPTIONAL MATCH p = (head:Claim)-[:RELATES*1..6 {relation:'supersedes'}]->(c)
WHERE NOT ()-[:RELATES {relation:'supersedes'}]->(head)
RETURN c, head, [n IN nodes(p) | {t:n.text, from:n.valid_from}] AS lineage

-- S4  temporal window (only when the temporal feature-router fires)
MATCH (s:Session) WHERE s.date_iso >= $lo AND s.date_iso <= $hi
MATCH (t:Turn)-[:IN_SESSION]->(s) RETURN s, t ORDER BY s.date_iso

-- S5  provenance hydration: every served claim carries its source turn + session date
MATCH (c:Claim)-[:FROM_TURN]->(t:Turn)-[:IN_SESSION]->(s:Session)
WHERE c.id IN $final_ids RETURN c, t, s
```

`final_top_k = 12` (prior-art constant from the 0.82 config). **S5 is non-negotiable:** serve the claim *and* its source turn verbatim. Extraction lossiness — the extractor paraphrases away the exact string the judge wants — is the #1 way a graph arm loses to naive RAG, and co-serving provenance removes it at the cost of context tokens.

**Answer phase** (`reader.py`): single turn, one call, `temperature=0`. Context block:

```
Today is {question_date}.

CONVERSATION HISTORY (retrieved):
[1] 2023-05-20 (Sat)  — session s_28035
    user: "...verbatim turn..."
    → claim: degree_earned = Business Administration
[2] 2023-11-02 (Thu)  — session s_9f21   ⚠ SUPERSEDES [5]
    ...
[5] 2023-06-14 (Wed)  — session s_44a1   (superseded on 2023-11-02 by [2])
    ...

SESSION DATE INDEX: 2023-05-20, 2023-06-14, ... (54 sessions, 2023-05-20 → 2024-01-08)

QUESTION: {question}
```

The session date index and explicit date-stamping are lifted from the prior 0.82 config's `date_post_step`/`event_calendar` flags — recorded as moving temporal from 0.2 → 0.5. Labelling superseded entries rather than deleting them is deliberate: **the official knowledge-update judge prompt explicitly counts stale-alongside-updated as correct**, so labelling is strictly dominant over deletion.

**Judge phase**: two lanes.
- **Lane 1 (citable)** — `judge_official.py`, gpt-4o-2024-08-06, verbatim prompts, `temperature=0`, `max_tokens=10`.
- **Lane 2 (anti-phantom)** — `judge_strict.py`, rubric v1.1 (R1 bare-number/unit · R2 entity+disambiguating-qualifier · R3 preference grounded-AND-non-violating · R4 abstention · R5 exact count · R6 surface-tolerance), 3 families × 5 votes, majority. Emits `floor = min(family)` and `maj3`. Divergence from Lane 1 > 0.02 ⇒ report a **JUDGE-BAND**, never a point. Fix the `.format()` KeyError on lift (escape as `{{…}}` or use `%`-templating).

### 3.5 The unblock comparison arm (A2)

Treat it as an HTTP black box — ~30 lines of `httpx` replicating `longmemeval-live-lib.mjs`:

- **Ingest:** `POST /v1/remember` per turn, body `{content: "<role>: <text>", scope: "member"}`.
- **Retrieve:** `POST /v1/query` `{text, final_top_k: 10, skip_synth: false}` → `{answer, abstained, hits[]}`.
- **Prediction:** `res.abstained ? "" : res.answer`. Reader prompt lives server-side; note that in the manifest as an uncontrolled variable.
- **Isolation:** one eval api_key per question, `purpose='eval'`, `eval_tenant_id`, `eval_run_id='q-<qid>'`; the EF's scope predicate gives zero cross-question and zero prod leakage.
- **Env names only** (never values): `SUPABASE_PG_URL` (key minting), `UNBLOCK_EF_BASE`, or a pre-provisioned `UNBLOCK_EVAL_API_KEY`.

**Two honesty notes that must appear in the report:** (1) neither substrate arm attaches `haystack_dates` or `session_id` to blocks — its temporal-reasoning ceiling is structural, not a tuning failure; (2) it needs the live Kaeva backend, which violates GOAL.md's "zero runtime dependency on the live Kaeva backend." **So A2 runs from `eval/` as an optional arm behind `--arm a2_unblock`, is never on the `make eval-smoke` path, and its absence never fails a gate.** The default `make eval` set is A0/A1/A3.

### 3.6 Environment

`python3` on this box is **3.9.6** and there is **no Docker and no `falkordb` module**. So:

```bash
uv python pin 3.12
uv sync                    # falkordb, httpx, numpy, tqdm, openai, anthropic, pyyaml
```

The harness talks to FalkorDB purely via `FALKORDB_HOST`/`FALKORDB_PORT` (default `127.0.0.1:6401`), so it is indifferent to Docker-FalkorDB vs embedded `falkordblite`. **Do not use 6379 or 6399** — a stray `redis-server` is confirmed listening on 6399 right now (pid 6733), and a 6379 collision produces a false-green (client connects, then `GRAPH.QUERY` fails). RocketRide is already up on 5565 (pid 9858).

---

## 4. SCORE STRATEGY — which mechanism earns which points

### 4.1 The arms

| Arm | Memory | Retrieval | Purpose |
|---|---|---|---|
| **A0** full-context | none | whole 115k haystack in the prompt | The bar everyone must clear. Paper: 0.606 |
| **A1** naive-RAG | flat vectors over session chunks | vector top-k=12, no graph, no dates | The honest baseline. **Same reader, same prompt, same judge** as A3 |
| **A2** unblock substrate | `remember`/`query` as-is | hybrid RRF + rerank, server synth | The "did we improve on our own production system" arm |
| **A3** PALIMPSEST | typed graph, dated sessions, supersede lineage | S1–S5 | The claim |
| A3−supersede | graph, no `[:RELATES{supersedes}]` | S3 disabled | Removal test → knowledge-update |
| A3−temporal | no `Session.date_iso`, no `[:NEXT]`, no date index | S4 disabled | Removal test → temporal-reasoning |
| A3−expansion | vector entry only | S2 disabled | Removal test → multi-session |
| A3−sufficiency | no entity-coverage gate | §4.5 disabled | Removal test → abstention |

Every ablation reuses cached embeddings and cached extractions, so each costs only reader+judge (~$5, ~7 min). **Eight arms is cheap. The receipts are the product.**

### 4.2 knowledge-update (n=78, 72 non-abs) — supersede lineage

Naive RAG returns the old and new fact with no ordering signal; the reader picks by position or recency-of-phrasing. A3 returns the **chain head** plus the labelled lineage. The official judge counts stale-alongside-updated as correct, so labelling is free upside. Reference: EverMemOS reports KU **+15.5pp** from exactly this class of mechanism. Prior local profile: KU token-F1 0.600 at recall 1.0 → it is a *reader-disambiguation* failure, which is precisely what lineage fixes. **Expect the largest single-category win. Removal test: A3−supersede.**

### 4.3 temporal-reasoning (n=133 — the largest bucket) — time as an index

`Session.date_iso` as a range-indexed property + `[:NEXT]` spine + the session date index in the prompt. Zep's finding: **+15pp over Mem0 on temporal, "because time is an INDEX."** Local profile: temporal token-F1 **0.057** with 0.6 abstention at recall 1.0 — the reader had the answer and refused. Both a retrieval fix (date-windowed Cypher) and a prompt fix (explicit dates on every served item) apply, and prior work measured the prompt half alone at 0.2→0.5. The judge does not penalise off-by-one days, so date arithmetic need only be approximately right. **133 questions × a 0.3 swing = 8pp of the overall micro. This is the biggest absolute prize on the board. Removal test: A3−temporal.**

### 4.4 multi-session (n=133) — graph expansion

S2's shared-`Entity` 1-hop *is* multi-hop retrieval. Local profile: 0.500 token-F1, 0.2 abstention. Watch T12 hard here — the temptation is to raise fanout, and prior work triangulated three separate times that **volume never converts, only rank does**. Keep `final_top_k=12`; win by ordering. **Removal test: A3−expansion.**

### 4.5 abstention (30 `_abs` rows) — entity-coverage sufficiency, the graph-native calibration signal

Extract the entity set from the question; check coverage against `:Entity` nodes reachable from the retrieved claims. If a load-bearing entity has **zero** node in the graph, the premise is false → abstain with the specific missing entity named. This is deterministic and graph-native, and it maps exactly onto the official abstention prompt ("correctly identify the question as unanswerable") plus the strict rubric's R4.

Two-sided risk, and it must be reported two-sided: over-abstention destroys preference and temporal (local: 0.6 abstention rate in both); under-abstention loses the 30 `_abs` rows. **So always report `abstention_accuracy` next to `false_abstention_rate` on answerable rows.** A one-sided abstention number is gameable and prior work flagged exactly that. **Removal test: A3−sufficiency.**

### 4.6 single-session-preference (n=30) — the universal collapse point

Everyone dies here: full-context gpt-4o **0.20**, Mem0 0.464, Zep 0.567. Local: token-F1 **0.040**, 0.6 abstention. GPT-5.5 independently scored **0/3, all failures being abstention** — it treats "what would the user prefer" as unanswerable.

Mechanism: `Claim{kind:'preference'}` + a **preference pack** — a standing block of the user's stated preferences whose entities intersect the query, always injected, never ranked away (the 0.82 config had `preference_pack: true`). The rubric rewards **grounding in the specific signals the gold names** and penalises generic advice and fabricated specifics; a preference pack is literally a list of the user's specific stated signals. Combined with an explicit "you may always answer a preference question; abstention is wrong here" instruction, this should be the most visible relative jump.

Caveats to state in the report: n=30, so **one item = 3.3 points of the type score**; judge-human agreement is only 0.90 here; under macro averaging preference carries 16.7% weight vs 6% under micro. A 0.20→0.90 jump is 21 items and must be manually inspected before being believed.

### 4.7 single-session-user (n=70) / single-session-assistant (n=56) — do not expect a win

Local profile: SSU token-F1 **1.000**, SSA 0.636 at recall 1.0. Retrieval is already trivial. **The only thing A3 can do here is lose**, via extraction lossiness or context dilution. S5 provenance hydration is the guard. **Treat any A3 < A1 on single-session-user as a regression to be fixed, not a trade to be accepted** — that is the T12 dilution signature.

SSA is a genuine asymmetric opportunity: the official retrieval baseline **drops all 56** because it indexes only user turns. We index assistant turns. Flag it explicitly — it is an advantage over published *retrieval* baselines but not over full-context ones, and hiding that would be exactly the kind of non-comparability §2.5 criticises in others.

### 4.8 The router (T14)

Stage S4 (temporal) and the preference pack fire off a **feature router keyed on question text only** — date/duration regexes, "how long/when/before/after/since", preference verbs, comparative superlatives. It must **never** read gold `question_type`. Run a second, clearly-labelled `a3_oracle_routed` arm using gold types as an **upper bound**, reported in a separate table with the header `ORACLE-ROUTED — BOUND, NOT A RESULT`. The gap between them is the router's cost, and publishing it is what makes the primary number honest.

### 4.9 Expected shape of the result

Honest projection, `_s_cleaned`, full 500, official gpt-4o judge, micro:

| | A0 full-ctx | A1 naive-RAG | A3 PALIMPSEST | mechanism |
|---|---|---|---|---|
| single-session-user | ~0.75 | ~0.80 | ~0.80 | neutral (watch for regression) |
| single-session-assistant | ~0.65 | ~0.55 | ~0.70 | assistant-turn indexing |
| single-session-preference | ~0.20 | ~0.30 | **~0.60** | preference pack |
| knowledge-update | ~0.60 | ~0.55 | **~0.80** | supersede lineage |
| temporal-reasoning | ~0.50 | ~0.45 | **~0.65** | dated sessions + date index |
| multi-session | ~0.50 | ~0.50 | **~0.65** | entity expansion |
| abstention (30) | ~0.40 | ~0.35 | **~0.65** | coverage sufficiency |
| **micro overall** | **~0.60** | **~0.55** | **~0.70–0.73** | |

That lands at/around Zep's 0.712 with a full removal-test matrix behind it — which is a far better stage story than an unaudited 0.9. **If any cell comes in ≥0.90, T4 fires and it does not get spoken until a second seed and a second judge family confirm it.** Prior work is 4-for-4 on that being a phantom, and one of those phantoms cost this team a published retraction.

---

## 5. EXECUTION PLAN, mapped to the GOAL.md clock

| When | Action | Owner |
|---|---|---|
| **now** | `cp ~/unblock-eval/eval-data/longmemeval_oracle.json eval/data/` (hash pre-verified). Start pinned `_s_cleaned` download in background (277 MB) | Mac |
| **now** | Rename `~/unblock-eval/eval-data/longmemeval_s.json` → `*_DEPRECATED_precleaning.json`. Rotate the key in `longmemeval-com-dom-memory/scratchpad/run1.sh` | Mac |
| **T+0:30** | `eval/` skeleton + `dataset.py` + `guards.py` + `metrics.py` + official judge transcription. `make eval-smoke` green on A1 vs A3 over oracle smoke-30 | Opus worker |
| **T+1:30** | A1 full-500 on `_s_cleaned` — embeddings only, ~$3, ~20 min. **First citable receipt on the board** | Windows box |
| **T+2:00** | A3 claim extraction full-500 kicked off in background (~50 min, ~$120) | Windows box |
| **T+3:00** | A0 full-context full-500 (the bar) | Mac |
| **T+3:30** | A3 full-500 + `retrieval.jsonl` recall gate. **Judge-free GO/NO-GO first** | Windows box |
| **T+4:00** | Four ablation arms on dev-150 (cached; ~$5 and 7 min each) | Mac |
| **T+4:30** | Official judge lane + strict panel lane. `make report` → `eval/runs/BOARD.md` | Mac |
| **T+5:00 (Gate 5)** | Board + manifests + tarballs into `plan/gates/`. **Nothing new starts after this** | — |
| overnight, if it exists | 3-seed ×3 for the headline arm (soak discipline: single-run numbers are DIRECTIONAL, never headlined) | Windows box |

**Cut order for this lane, pre-agreed:** strict-panel second lane → A2 unblock arm → A0 full-context → full-500 downgraded to dev-150. **Never cut the ablation matrix** — a number without removal tests is exactly the artifact this team has retracted before.

---

## 6. Things I would not have believed without checking

- The local oracle file is a **verified byte-exact pin** of the current official release. That is a genuine head start — zero download risk on the dev arm.
- The local `_s` is **not** the current `_s`. Two files, one name, ~642 KB apart, and every published score sits on the other one. This is the §1.3 T5/T9 trap in its purest form and it was sitting in the "head start" directory.
- The prior LME work's biggest asset is not code — it is **42 documented ways to fool yourself**, four of which produced a retracted headline score. Encoding them as assertions in `guards.py` is probably worth more points than any retrieval tuning.
- **Both** prior arms parsed `haystack_dates` and then never stamped them onto anything. The largest question bucket in the benchmark is temporal-reasoning. That is free money.
- `judge_canonical_lane.py`'s live path has never executed — `--selftest` passes because it doesn't touch the network path, which is itself a nice specimen of the "validate that the instrument returns a positive before trusting its zero" rule.