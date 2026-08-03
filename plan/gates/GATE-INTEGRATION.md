# GATE-INTEGRATION — three-PR coherent integration on `integ/ready-set`

**Verdict: GO** (demo-critical features validated; one PRE-EXISTING deploy blocker flagged, see §6)

Integration branch `integ/ready-set`, built on `origin/main @ 1b6d3ad`.
Integration HEAD: **`03d226f8680810da4c4eb98c32481d3a91485d2a`** (`03d226f`).

Integrator: Claude Opus (integration agent). Does NOT merge to main and did NOT
touch the running demo bridge (`:8931`, PID 66873) — those are the coordinator's calls.

---

## 1. PRs integrated (all three landed)

| PR | branch | commit | files | landed via |
|----|--------|--------|-------|-----------|
| #15 | `feat/laserdata-tail-scrub` | `799de3d` | `app/bridge/stream.py`, `realtime/laser_io.py`, `realtime/graph_writer.py`, `plan/gates/GATE-LASERDATA-TAIL.md` | `git merge --no-ff` (clean, disjoint) |
| #14 | `feat/falkordb-ringleader` | `c9bfbff` | `app/bridge/server.py`, `app/bridge/guard.py`, `app/bridge/tests/test_surfaces.py`, `plan/gates/GATE-FALKORDB-ALGO.md` | `git merge --no-ff` (clean) |
| #13 | `feat/mcp-widgets` | `ecf621f` | `app/bridge/server.py`, `app/bridge/widget_apps.py`, `app/bridge/widgets/*.html`, `plan/gates/GATE-MCP-WIDGETS.md` | `git cherry-pick ecf621f` (clean — see §2) |

Full integration delta vs `origin/main`: **13 files, +1973 / −53**, exactly the union
of the three PRs. No collateral: `app/web/index.html` is NOT modified and
`plan/research/SPONSOR-GITHUB-PLAYBOOK.md` is NOT deleted (see §2).

Commit graph:
```
* 03d226f feat(mcp): Tier-0 interactive widgets — graph/ablation/approval   (#13, cherry-pick)
* a33664d integ: merge #14 feat/falkordb-ringleader
|\
| * c9bfbff feat(falkordb): ringleader + auto-cell graph-algorithm verb
* bf58887 integ: merge #15 feat/laserdata-tail-scrub
* 799de3d fix(laserdata): real live tail + wall-clock time-scrub
* 1b6d3ad (origin/main)
```

---

## 2. Conflicts + resolutions

**No textual merge conflicts occurred.** The two PRs that both edit `server.py`
(#14 and #13) touch **disjoint regions**, so git's 3-way merge combined them
automatically. Verified post-merge that both feature sets coexist:

- #14 (ringleader): `_COEDIT_PROJECT` (L934), `_cells_from` (L981), `_h_ringleader`
  (L998), dispatch entry `"ringleader": ("POST","/v1/ringleader",…)` (L1586), MCP Tool.
- #13 (widgets): `_attach_widget_meta` (L1663, called from `_tools()` at L2148),
  `list_resources`/`read_resource` under `if MCP_AVAILABLE:` (L2416/L2431).

Final dispatch table = **14 verbs** (ringleader included); the widget layer adds
MCP *resources* + tool `_meta`, not a dispatch verb — exactly the intended
post-merge shape. `test_surfaces.py` (from #14) asserts `len(VERBS) == 14` and
`len(all_tools()) == 14`; both pass in the integrated tree.

**Diff-direction correction (why #13 was cherry-picked, not branch-merged):**
`feat/mcp-widgets` is NOT based on current main — its merge-base is `df5b86f`
(an older main). A two-dot `git diff main feat/mcp-widgets` therefore *falsely*
showed `app/web/index.html` rewritten (−379) and `SPONSOR-GITHUB-PLAYBOOK.md`
deleted (−153); those are commits main gained *after* `df5b86f`, not branch
changes. The **true** branch delta (`main...feat/mcp-widgets`, three-dot) is only
the 6 widget files. `server.py` is byte-identical between `df5b86f` and main, so
cherry-picking the single commit `ecf621f` applies just the widget diff against
#14's server.py with zero collateral — confirmed by the clean §1 diffstat
(no index.html change, no playbook deletion).

---

## 3. Static + guarded-import validation

- **AST parse (Python 3.9.6):** `ast.parse` OK on `server.py`, `stream.py`,
  `guard.py`, `laser_io.py`.
- **Guarded bare-import contract (Python 3.9.6, no `falkordb` / no `mcp` in the
  interpreter — both confirmed absent):** all import cleanly —
  `app.bridge.guard`, `app.bridge.widget_apps`, `realtime.laser_io`,
  `app.bridge.stream`, `app.bridge.server`. `server.py` reports
  `MCP_AVAILABLE=False` and `len(_VERB_DISPATCH)=14`. Note: #13 adds
  `widget_apps` to server.py's module-level import; `widget_apps.py` imports only
  stdlib + `memory.config`, so the bare-import contract still holds.

---

## 4. Full test suite (live stack, repo `.venv` py3.12)

`LASER_CONNECTION_STRING='iggy:laser@127.0.0.1:8090'`
`python -m pytest app/bridge/tests realtime/tests -q -rA`

- **155 passed**
- **2 failed** — both `app/bridge/test_replay_parity.py`
  (`test_replay_from_zero_matches_consumer_build`,
  `test_replay_into_cold_populates_from_empty`), each
  `FileNotFoundError: demo/seed_replay.ndjson`.
- 0 pytest errors, 0 skipped.

The two failures are the **known-environmental** missing 42 MB replay fixture
(`demo/seed_replay.ndjson` is absent on this box), NOT a code regression. Every
other test — including all `test_surfaces` verb/tool/count/OpenAPI tests and the
ringleader graph-writer tests — is green.

---

## 5. Fresh-stack walkthrough (real output)

Docker IS usable on this box, but the `fly/` image cannot build (§6). To validate
the integrated bridge end-to-end without touching the demo graph, an **isolated
fresh stack** was stood up: a fresh `falkordb/falkordb` container on host **:6402**
(seeded via `app.bridge.seed_demo` → 12 nodes / 9 edges / ring fired) + the
integrated bridge as a fresh process on **:8946**. The demo bridge (:8931) and
demo FalkorDB (:6401) were never touched (confirmed healthy before and after).

`GET /health` → `git_sha=03d226f8…` (= integration HEAD), `falkordb 127.0.0.1:6402
reachable`, `mcp mounted /mcp`.

`GET /graph` → `nodes: 12, edges: 9`.

`GET /ring` → `fired: true, ring_count: 1, ring_score: 0.6667` (actors
Northstar-7 / VectorMint / SableWeather over pages Metropolitan Transit
Authority / Regional power grid).

`GET /v1/ablation` (dispatch verb is **GET**, not POST) → `ok: true,
opposite_verdict: true`, warm graph fires the ring, `run_time_ms: 2.998`.

**`POST /v1/ringleader` (the new #14 surface)** →
```json
{
  "ok": true,
  "ringleader": {"name": "VectorMint", "score": 0.197079, "score_vs_baseline": 1.72,
                 "cell": ["Northstar-7","SableWeather","VectorMint"]},
  "influence_ranking": [{"name":"VectorMint","score":0.197079},
                        {"name":"Northstar-7","score":0.072993},
                        {"name":"SableWeather","score":0.072993}],
  "communities": [{"id":0,"members":["Northstar-7","SableWeather","VectorMint"],"size":3}],
  "cell_count": 1,
  "label_propagation": [{"id":0,"members":["Northstar-7","SableWeather"],"size":2}],
  "coedit_edges_created": 2,
  "method": {"ringleader":"algo.pageRank","cell":"algo.WCC (map-config)",
             "refinement":"algo.labelPropagation (map-config)"},
  "run_time_ms": 32.098419,
  "timings": {"projection":2.78,"pageRank":3.98,"wcc":1.67,"labelPropagation":23.66},
  "graph_key": "palimpsest"
}
```
`ringleader` + `communities` + `run_time_ms` all present, computed in-engine
(pageRank / WCC / labelPropagation) — the zero-install value prop holds.

---

## 6. PRE-EXISTING blocker the coordinator MUST know (fly deploy only)

`docker build -f fly/Dockerfile .` **FAILS** at
`Step 8: COPY realtime/requirements.txt` →
`file not found in build context or excluded by .dockerignore`.

Root cause: `.dockerignore` line 46 excludes `realtime/` (with a stale comment at
L39-40 claiming the Dockerfile "only copies app/, memory/ and fly/entrypoint.sh"),
but the current `fly/Dockerfile` does `COPY realtime/requirements.txt` and
`COPY realtime/ /app/realtime/`.

This is **PRE-EXISTING on `origin/main`, not an integration regression**:
`.dockerignore` and `fly/Dockerfile` are byte-identical between `integ/ready-set`
and `origin/main` (none of the three PRs touches `fly/` or `.dockerignore`), and a
control build reproduced the identical failure at Step 8.

**Impact:** the hosted fly image (backing palimpsest-740.pages.dev) cannot be
rebuilt/redeployed until fixed. It does NOT affect the local demo bridge, the
integration's correctness, or the test suite.

**Fix (one line, outside this integration's scope):** in `.dockerignore`, either
remove `realtime/` or add negations, e.g.
```
realtime/
!realtime/
!realtime/**
```
(and refresh the stale L39-40 comment). Recommend the coordinator apply this on
main/a separate infra fix so it is attributed correctly.

---

## 7. Secret scan

`git diff origin/main integ/ready-set`, added lines scanned for private keys,
AWS/GitHub/Slack/OpenAI/Google tokens, and `api_key/secret/password/token=…`
literals: **zero credential hits.**

---

## 8. Coverage note (non-blocking)

#13's MCP resource layer (`list_resources` / `read_resource`) has no dedicated
pytest in `app/bridge/tests`; its tool-`_meta` wiring is covered indirectly via
the `all_tools()` count/iteration tests. Widgets are off the demo critical path
(Claude Desktop host only), so this does not gate the demo.
