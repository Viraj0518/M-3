# PALIMPSEST × LongMemEval — one command per lane. Run from `eval/`.
#
# `make eval` is the whole benchmark, from a clean checkout, given a `.env` with
# keys. Nothing else.
#
# THE COST LINE IS PART OF THE INTERFACE. A target that spends money says so.

DATA  ?= data/longmemeval_s_cleaned.json
SPLIT ?= full-500
SEED  ?= 20260803
RUNS  ?= runs

.PHONY: help verify splits gate eval-smoke eval-smoke-dry eval eval-ablate report test clean-graphs

help:
	@echo "verify         — no-cost preflight: dataset pins, split locks, prompts, FalkorDB"
	@echo "gate           — T15: the sabotaged fixture must FAIL, controls must PASS   (free)"
	@echo "test           — unit tests, judge lanes with canned responses               (free)"
	@echo "splits         — build + sha256-lock smoke-30 / dev-150 / full-500           (free)"
	@echo "eval-smoke-dry — A1 vs A3 over smoke-30 with EXPLICIT stubs, no keys         (free)"
	@echo "eval-smoke     — A1 vs A3 over smoke-30, oracle, real models              (~\$$1)"
	@echo "eval           — A0+A1+A3 over \$$(SPLIT) on \$$(DATA)                     (~\$$130)"
	@echo "eval-ablate    — 4 removal tests on dev-150 (cached embeds+extractions)   (~\$$20)"
	@echo "report         — render \$$(RUNS)/BOARD.md                                   (free)"

# ── free lanes ──────────────────────────────────────────────────────────────
verify: ; uv run eval verify

gate: ; uv run eval gate --negative fixtures/negative

test: ; uv run pytest -q

splits: ; uv run eval splits --seed $(SEED)

# The DRY smoke path: proves plumbing end-to-end (retrieval, graph, guards,
# metrics) with zero API calls. Stubs are opted into in configs/dry_smoke.yaml,
# never reached by a missing key. Its qa.* numbers are non-citable by design.
eval-smoke-dry:
	uv run eval run --arm a1_naive_rag --arm a3_palimpsest --dry \
	  --data data/longmemeval_oracle.json --split smoke-30 --seed $(SEED)

# ── paid lanes ──────────────────────────────────────────────────────────────
eval-smoke:
	uv run eval run --arm a1_naive_rag --arm a3_palimpsest \
	  --data data/longmemeval_oracle.json --split smoke-30 --seed $(SEED)

eval:
	uv run eval run --all-arms --data $(DATA) --split $(SPLIT) --seed $(SEED)

eval-ablate:
	uv run eval run --arm a3_palimpsest --ablations \
	  --data $(DATA) --split dev-150 --seed $(SEED)

report: ; uv run eval report --runs $(RUNS) --out $(RUNS)/BOARD.md

# Per-question graphs are dropped in teardown; this sweeps any left by a crash
# or by `keep_graph: true`.
clean-graphs:
	@uv run python -c "import falkordb; from memory.config import FALKORDB_HOST, FALKORDB_PORT; \
	d=falkordb.FalkorDB(host=FALKORDB_HOST, port=FALKORDB_PORT); \
	ks=[k for k in d.list_graphs() if k.startswith('lme_')]; \
	[d.select_graph(k).delete() for k in ks]; print(f'dropped {len(ks)} lme_* graphs')"
