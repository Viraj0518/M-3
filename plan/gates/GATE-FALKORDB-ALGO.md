# GATE — FalkorDB native graph algorithms (`ringleader` verb)

**Claim:** FalkorDB does not merely prove a co-edit ring *exists* — its own compiled
`algo.*` procedures **name the ringleader** (`algo.pageRank`) and **auto-discover the
collusion cell** (`algo.WCC`), unsupervised, recovering *exactly* the ring and leaving
the unrelated bystander pair in its own component. This is pure in-engine graph
topology. **No LLM, no key, no vector store** — a vector index computes cosine
similarity and has *no* notion of structural influence or connected components, so it
cannot express this verdict at any price.

**Ships:** `POST /v1/ringleader` (verb `ringleader`, MCP tool `palimpsest_ringleader`),
`app/bridge/server.py`. Zero new dependencies, zero new install — the 10 `algo.*`
procedures are already compiled into the live FalkorDB build on `127.0.0.1:6401`.

UI wiring (surfacing the ringleader + cell on the projector) is a **follow-up owned by
the codex UI lane** — this gate ships the verb + the proof, not `app/web/index.html`.

---

## What the verb does

1. **Projects** the bipartite `(:Actor)-[:EDITED]->(:Page)<-[:EDITED]-(:Actor)` co-edit
   structure into a weighted unipartite `(:Actor)-[:CO_EDITED_WITH]->(:Actor)` graph
   (`a.name < b.name`, `weight` = shared page count). Idempotent `MERGE`; additive; it
   never touches the `:EDITED` edges the `/ring` verb reads, so the ring beat is
   unchanged.
2. **`algo.pageRank('Actor','CO_EDITED_WITH')`** → the **ringleader** (highest
   structural influence).
3. **`algo.WCC({nodeLabels:['Actor'], relationshipTypes:['CO_EDITED_WITH']})`** → the
   **cell** (the ringleader's connected component), recovered unsupervised.
4. **`algo.labelPropagation({...})`** → the fuzzy-community view, included for
   transparency (see the design note — it is *not* load-bearing here).

---

## REAL captured results — live run against `palimpsest` (798 nodes), this session

Graph facts verified live: **798 nodes, 157 Actors, 251 Pages, 293 `:EDITED` edges**;
all **10** `algo.*` procedures compiled in.

### `algo.pageRank` — NAMES the ringleader

| rank | actor | pageRank score | vs. baseline (0.0012679) |
|-----:|-------|---------------:|-------------------------:|
| 1 | **VectorMint** | **0.003368** | **2.66×** |
| 2 | ~2026-39913-56 | 0.002308 | 1.82× |
| 3 | Niishikarazu | 0.001248 | 0.98× |
| 4 | Northstar-7 | 0.001248 | 0.98× |
| 5 | SableWeather | 0.001248 | 0.98× |

**Ringleader = `VectorMint`**, 2.66× the mean score — it is the hub both co-edit edges
point at (it co-edited *Metropolitan Transit Authority* with Northstar-7 **and**
*Regional power grid* with SableWeather).

### `algo.WCC` (map-config) — AUTO-DISCOVERS the collusion cell

| componentId | size | members |
|------------:|-----:|---------|
| 0 | **3** | **Northstar-7, SableWeather, VectorMint** ← the ring |
| 555 | 2 | Salebot, ~2026-39913-56 ← unrelated bystander pair, **excluded** |
| (152 singletons) | 1 | isolated actors |

WCC recovers **exactly** the ring `{VectorMint, Northstar-7, SableWeather}` as one
component and correctly isolates the unrelated `{Salebot, ~2026-39913-56}` co-edit pair
(the *Lucky Luke* page) into a **separate** component. Nobody told it how many cells
exist or who is in them — this is the unsupervised auto-discovery a pattern query and a
vector store both lack.

`largest_cell` = `{Northstar-7, SableWeather, VectorMint}` (size 3).

### `algo.labelPropagation` (map-config) — transparency only

| communityId | size | members |
|------------:|-----:|---------|
| 0 | 2 | Northstar-7, SableWeather |

Everything else is a singleton — **including VectorMint, the actual ringleader.** See
the design note below.

### `run_time_ms` — FalkorDB's OWN measured execution time (not a wall clock)

Captured breakdown from one live verb call (graph `palimpsest`):

| phase | ms |
|-------|---:|
| projection (`MERGE`) | 6.53 |
| **`algo.pageRank`** (names leader) | **1.35** |
| **`algo.WCC`** (discovers cell) | **0.74** |
| `algo.labelPropagation` (refinement) | 23.15 |
| **total** `run_time_ms` | **~31.8** |

The two **load-bearing** algorithms — pageRank + WCC — together run in **~2.1 ms**, the
same order as the `/ring` verb's own run_time_ms. (FalkorDB's measured time varies run
to run with cache warmth; these are real captured numbers, not fabricated constants.)

---

## THE TRAP (verified live) — map-config vs. positional

`algo.WCC`, `algo.labelPropagation`, and `algo.betweenness` **silently return nothing**
in the positional form; you *must* use the map-config form.

| call | rows |
|------|-----:|
| `algo.labelPropagation('Actor','CO_EDITED_WITH')` (positional) | **0** (silent) |
| `algo.WCC('Actor','CO_EDITED_WITH')` (positional) | **count 0** (silent) |
| `algo.labelPropagation({nodeLabels:['Actor'],relationshipTypes:['CO_EDITED_WITH']})` | 157 |
| `algo.WCC({nodeLabels:['Actor'],relationshipTypes:['CO_EDITED_WITH']})` | 157 |
| `algo.pageRank('Actor','CO_EDITED_WITH')` (positional) | 157 ✓ (pageRank is fine positional) |

A naive positional call is the exact false-green this gate exists to prevent: an empty
result that looks like "no collusion found" when the real answer is "you called the
procedure wrong."

---

## REMOVAL TEST — delete the algos and the demo goes dark

Run the same graph through `/ring` alone (algos removed) vs. `/ringleader`:

| capability | `/ring` (pattern query only) | `/ringleader` (native algos) |
|------------|------------------------------|------------------------------|
| ring **exists**? | ✅ `fired=true`, `ring_score=0.6667`, actors `[Northstar-7, VectorMint, SableWeather]` | ✅ (unchanged) |
| **names the leader**? | ❌ no `ringleader` field — a, b, c are symmetric | ✅ `VectorMint` @ 2.66× (pageRank) |
| **auto-clusters the cell**? | ❌ no `communities`/`largest_cell` field | ✅ WCC: `{VectorMint, Northstar-7, SableWeather}` |
| **excludes the bystander**? | ❌ never considers `{Salebot, ~2026-39913-56}` | ✅ separate component, unsupervised |

Captured `/ring` response keys: `fired, ring_count, ring_score, rings, paths, ids,
run_time_ms, …` — **no `ringleader`, no `communities`, no `largest_cell`.**

**Without the algorithms the demo can point at a ring and say "something happened here,"
but it cannot say WHO drove it or WHICH actors form the cell.** And a vector store — the
thing this whole project is contrasted against — *fundamentally* cannot supply either:
PageRank and Weakly-Connected-Components are graph-topology computations with no
cosine-similarity analog. That is the thesis, sharpened to its point.

---

## Design note (honest deviation, verified live)

The playbook's plan named `algo.labelPropagation` as the cell detector. **Live testing
proved that wrong on this graph's topology and it was corrected — not papered over.**

The ring is a **star / hub**: both co-edit edges point at VectorMint. Label propagation
is unstable on a hub — it oscillates and **fragments the hub actor out of its own
community**, so it puts `{Northstar-7, SableWeather}` together and leaves **VectorMint —
the actual ringleader — as a singleton**, the opposite of the intended result. This was
reproduced on the real `palimpsest` graph and on an isolated scratch graph; label
propagation has **no** `direction` config in this FalkorDB build (`unknown key` error),
so it cannot be steered into the right answer.

**`algo.WCC` (Weakly Connected Components) is the correct connected-cell detector** and
recovers the exact cell (and excludes the bystander) — verified above. It is one of the
same 10 compiled procedures and is explicitly named in the map-config trap. It is
therefore the **load-bearing** cell detector in the verb. `algo.labelPropagation` is
still called (map-config form) and its output returned under `label_propagation`, purely
for transparency so the fragmentation is visible rather than hidden.

---

## Verification commands (reproduce)

```bash
# byte-check parse on the bare system interpreter (guarded-import contract)
python3 -c "import ast; ast.parse(open('app/bridge/server.py').read())"   # -> OK on 3.9.6

# prove the algos return real rows against the live palimpsest graph
.venv/bin/python -c "from falkordb import FalkorDB; g=FalkorDB(host='127.0.0.1',port=6401).select_graph('palimpsest'); print(g.ro_query(\"CALL algo.pageRank('Actor','CO_EDITED_WITH') YIELD node,score RETURN node.name ORDER BY score DESC LIMIT 3\").result_set)"

# end-to-end HTTP smoke (own bridge on a NON-default port, never :8931)
BRIDGE_PORT=8945 .venv/bin/python -m uvicorn app.bridge.rest:app --port 8945 &
curl -s -X POST http://127.0.0.1:8945/v1/ringleader -d '{"graph":"palimpsest","k":5}'

# regression
.venv/bin/python -m pytest app/bridge/tests/test_ring.py app/bridge/tests/test_graph_projection.py \
  app/bridge/tests/test_surfaces.py app/bridge/tests/test_guard.py -q
```

## Test results (this session)

- `test_surfaces.py` + `test_guard.py`: **52 passed** (verb/tool count 13 → 14; `ringleader`
  classified WRITE in `guard.py` because its projection `MERGE`s).
- `test_ring.py` + `test_graph_projection.py`: **17 passed** — no regression.
- Full `app/bridge/tests/`: only the **4 pre-existing** LaserData `stream_tail` failures
  (missing `LASER_CONNECTION_STRING` env; fail identically on clean `origin/main`, and my
  diff touches no stream-lane file).
- AST parse on system Python 3.9.6: **OK** (guarded-import contract holds — module parses
  and imports with neither `mcp` nor `falkordb` present).

## Integrity

Every number above is from a live run against the real `127.0.0.1:6401` `palimpsest`
graph this session. Nothing is simulated. The label-propagation limitation is reported,
not hidden; WCC is the load-bearing detector precisely *because* it is the one that
actually works on the real data.
