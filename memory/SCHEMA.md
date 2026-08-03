# memory/SCHEMA.md — the graph, and the traps, written BEFORE anyone types code

Written at H0 on purpose. Every trap below was found by someone else, on this
box, before the sprint. Reading this file is cheaper than rediscovering any one
of them at hour 6.

Companion file: [`motion/PIPE_NOTES.md`](../motion/PIPE_NOTES.md) — same trap
register, RocketRide/LaserData detail.

Single source of constants: **`memory/config.py`**. Nothing below is duplicated
in code; if a number here disagrees with `config.py`, `config.py` wins and this
file is stale — fix it.

---

## 0. THE TRAP REGISTER (all ten, both lanes)

The complete list lives in BOTH `memory/SCHEMA.md` and `motion/PIPE_NOTES.md`
so neither reader can miss one. Deep detail for traps 1-4 and 10 is in this
file; 5-9 are detailed in `PIPE_NOTES.md`.

| # | Trap | One-line rule | Detail |
|---|---|---|---|
| 1 | Vector score is a **DISTANCE** | **Sort ASC.** 0.0 = identical | §4 below |
| 2 | Port **6401**, not 6399, not 6379 | 6379/6399 are already taken and fail as a false-green | §5 below |
| 3 | falkordblite persistence is **per-FILE-PATH** | One shared `DB_PATH` constant, committed | §6 below |
| 4 | Handover read: **envelope-unwrap + `status=='open'` guard** | Two separate bugs, both silent | §7 below |
| 5 | RocketRide MCP provider is `mcp_client`, **NOT** `tool_mcp_client` | The directory name is not the service name | PIPE_NOTES §2 |
| 6 | `input:` is DATA FLOW, `control:` is TOOL ATTACHMENT | Different keys, easy to conflate, declared on the TOOL | PIPE_NOTES §3 |
| 7 | `ttl=0` on `use()` | Long agent fan-outs die mid-demo: "Your pipeline is not currently running." | PIPE_NOTES §4 |
| 8 | `iggy:laser@...`, **NOT** `iggy:iggy@...` | Use the exact string `./scripts/up` prints; the mismatch looks like a network error | PIPE_NOTES §5 |
| 9 | `producer.init()` **BEFORE** the first `send()` | Required, not optional | PIPE_NOTES §6 |
| 10 | `'fix'` and `'learning'` are **NOT block_types** | Carry them in `metadata.kind` | §8 below |

---

## 1. Nodes

Ten labels. Do not invent an eleventh during the sprint without saying so out
loud — an unplanned label is invisible to every query already written.

| Label | Is | Key properties |
|---|---|---|
| `:Actor` | A wiki editor (human or bot) | `name` (unique), `is_bot`, `first_seen`, `edit_count` |
| `:Page` | An article | `title` (unique), `wiki`, `url` |
| `:Wiki` | A project (enwiki, dewiki, …) | `code` (unique) |
| `:Event` | One observed edit, verbatim off the log | `id`, `ts`, `bytes_delta`, `summary`, `emb`, `offset`, `author_agent` |
| `:Entity` | A thing extracted from text | `name`, `kind` |
| `:Claim` | An assertion the system holds to be true | `id`, `text`, `emb`, `confidence`, `block_type`, `metadata`, `author_agent` |
| `:Case` | An open investigation | `id` (unique), `status`, `opened_ts`, `ring_score` |
| `:Agent` | One of us — watcher / analyst / responder / commander | `agent_id` (unique), `role` |
| `:Assertion` | An agent's stated position on a claim | `id`, `stance`, `ts`, `author_agent` |
| `:Action` | A real side effect that was fired | `id`, `kind`, `url`, `ts`, `idempotency_key`, `author_agent` |

**Every node written by an agent carries `author_agent`.** Not decoration: it
is what makes "the analyst asserted X and the watcher contradicted it" a graph
fact rendered as node colour, rather than a caption on a slide. Stamp it via
`app/bridge/identity.stamp_params()` / `identity.author_clause()` — the property
name is spelled in exactly one place in the whole repo.

## 2. Edges

| Type | Direction | Properties |
|---|---|---|
| `[:EDITED]` | `(:Actor)->(:Page)` | `ts`, `bytes`, `summary` |
| `[:ON]` | `(:Page)->(:Wiki)` | — |
| `[:CO_EDITED_WITH]` | `(:Actor)->(:Actor)` | `window_s`, `count` — derived, not observed |
| `[:ABOUT]` | `(:Claim)->(:Entity)` | — |
| `[:MENTIONS]` | `(:Event)->(:Entity)` | — |
| `[:IMPLICATES]` | `(:Case)->(:Actor\|:Page)` | `why` |
| `[:AUTHORED]` | `(:Agent)->(:Assertion\|:Claim)` | `ts` |
| `[:RELATES]` | `(:Claim\|:Event\|:Action)->(:Claim\|:Event)` | `relation` (6-value), `note` |
| `[:HANDED_OFF_TO]` | `(:Agent)->(:Agent)` | `handover_id`, `status`, `summary`, `in_flight`, `next_steps`, `blockers`, `artifacts`, `checkpoint` |

### `[:RELATES]` is GENUINELY DIRECTED — and that is a deliberate improvement

The 6-value relation vocabulary — `supports`, `contradicts`, `derived_from`,
`supersedes`, `duplicates`, `references` — is lifted verbatim from unblock
(`relate-verb.ts:83-90`). What we did **not** lift is how it is stored.

unblock's edge table carries this constraint
(`unblock_storage/src/migrations/031_lockdown_contradiction_edges.sql`):

```sql
CONSTRAINT contradiction_edges_ordered_chk CHECK (block_id_a < block_id_b)
```

That is a Postgres dedup trick that forces every edge into lexical id order —
and in doing so it **destroys direction**. After the write you cannot tell
"A supersedes B" from "B supersedes A". unblock's own source flags it.

**PALIMPSEST drops that CHECK.** FalkorDB gives real edge direction for free, so
`(:Claim)-[:RELATES {relation:'supersedes'}]->(:Claim)` means what it reads as.

Do not "normalize" endpoints into id order in any writer. `taxonomy.is_directed()`
names which kinds are asymmetric so this is assertable, not just a comment.

Also: `'relates'` is a **reader-side fallback only** for unknown statuses. It is
not writable. `taxonomy.check_relation()` rejects it explicitly.

## 3. Indexes

```cypher
CREATE INDEX FOR (a:Actor) ON (a.name)
CREATE INDEX FOR (p:Page)  ON (p.title)
CREATE INDEX FOR (c:Case)  ON (c.id)

-- NOTE (verified live on FalkorDB graph v42001, 2026-08-03): the createNodeIndex
-- PROCEDURE is NOT registered on this build. Index CREATION is DDL; the QUERY
-- procedure is still positional 4-arg db.idx.vector.queryNodes. See app/bridge/graphstore.py.
CREATE VECTOR INDEX FOR (c:Claim) ON (c.emb) OPTIONS {dimension: 256, similarityFunction: 'cosine'}
CREATE VECTOR INDEX FOR (e:Event) ON (e.emb) OPTIONS {dimension: 256, similarityFunction: 'cosine'}
```

`256` is `config.EMBED_DIM` and it MUST equal the embedder's `dimensions=256`.
A mismatch **corrupts the vector index silently** — no error, just wrong
neighbours, which on stage is indistinguishable from "the memory doesn't work".

There is **no Anthropic embedder**, and `embedder` is a required constructor
argument for the GraphRAG path — embeddings come from `OPENAI_API_KEY`,
generation from `ANTHROPIC_API_KEY`. Two different keys, two different jobs.

---

## 4. TRAP 1 — the vector score is a DISTANCE, not a similarity

Verified on this box: querying with the exact vector of "Alice" returned
**0.0**; the near-neighbour "Bob" returned **0.0061**.

**Lower = closer. SORT ASCENDING.**

```cypher
CALL db.idx.vector.queryNodes('Claim', 'emb', $k, vecf32($q))
YIELD node, score
RETURN node, score ORDER BY score ASC      // ASC. Always ASC.
```

Sorting DESC "to get the most similar first" shows the judges **the worst
matches in the database**, with no error and no warning. This is the highest
embarrassment-per-character bug in the stack.

The sensing gate depends on the same fact: skip the write when the top-1
**distance** is *below* `config.SALIENCE_THRESHOLD` (0.15) — i.e. too close to
something we already know.

## 5. TRAP 2 — port 6401, not 6399, not 6379

```bash
lsof -i :6379 -i :6399 -i :6401     # 5 minutes that saves 45
```

* **6379** is the Redis default. FalkorDB speaks the Redis wire protocol, so a
  stray `redis-server` lets your client **connect successfully** and then fail
  with `unknown command GRAPH.QUERY`. A textbook false-green.
* **6399** is what the falkordblite examples use — and a stray `redis-server`
  was verified holding `127.0.0.1:6399` on the build Mac.

PALIMPSEST binds **6401** either way. `config.FALKORDB_PORT`. Also note
falkordblite defaults to a **UNIX socket in a temp dir**, so Node/TS clients and
the Browser UI cannot see it at all until you force TCP:

```python
serverconfig = {"port": "6401", "bind": "127.0.0.1"}   # port value is a STRING
```

And the import path is not the package name:

```python
from redislite.falkordb_client import FalkorDB   # correct
import falkordblite                              # gets you nothing useful
```

falkordblite needs **Python >= 3.12**. System python on this Mac is 3.9.6.

## 6. TRAP 3 — persistence is per-FILE-PATH

falkordblite persists to a **file path**. Two teammates pointing at two
different paths get **silently different graphs, with no error** — the queries
just come back empty and everyone blames the Cypher.

One constant, committed: `config.DB_PATH`. Import it. Never write a literal path.

Same class of failure on the Docker path: `docker run --rm` without
`-v ./data:/var/lib/falkordb/data` loses the entire graph on container stop.
For a demo whose whole thesis is "it remembers", that is fatal — mount the
volume and rehearse a restart.

## 7. TRAP 4 — handover read: TWO silent bugs, both inherited as fixes

Both are documented in unblock `server.py:4608-4672` (reference copy at
`vendor/unblock-reuse/`). We inherit the **fixes**, not the bugs.

**(a) Unwrap the envelope explicitly.** The read returns
`{"handovers": [row?]}` — an envelope with zero or one rows. A naive
truthiness check treats a **genuine MISS as a HIT**, because `{"handovers": []}`
is itself a truthy dict. You end up with a non-null "handover" that is really an
empty list nested inside a wrapper.

```python
rows = parsed.get("handovers") if isinstance(parsed, dict) else None
if isinstance(rows, list) and rows:
    row = rows[0]
```

**(b) Status-guard on `status == 'open'`.** The by-agent read is
`ORDER BY updated_at DESC LIMIT 1` with **no status filter** (only the
all-agents path filters `status='open'`). Without the guard a cold-resume
**resurrects a superseded handover** and the agent confidently resumes work
that was already finished.

```python
if isinstance(row, dict) and row.get("status") == "open":
    return {"handover": row}
return {"handover": None, "reason": f"latest row is not open (status={row.get('status')!r})"}
```

Writes are always `status='open'` (`_build_handover_write` enforces it);
superseding is the reader's/writer's transaction job, never a caller's choice.

The handover node carries the **committed LaserData offset** in `checkpoint` —
that is what makes cold-resume exact rather than approximate, and it is the
headline demo beat: kill the agent, restart it cold, it reads its own handover
node out of the graph and resumes from its offset.

## 8. TRAP 10 — `'fix'` and `'learning'` are NOT block_types

The 13-value `BLOCK_TYPES` list is authoritative (`ingest-verb.ts:93-107`;
unblock_protocol carries a stale 11-value list — ignore it):

```
note  snippet  doc  code  trace  decision  anti-pattern
dataset  exploit  kg  conversation  utterance  other
```

`fix` and `learning` look like block types, read like block types, and are
**rejected by the storage CHECK**. In unblock they silently degraded to `note`
and the capture intent was lost forever. Carry them in `metadata.kind`:

| you mean | block_type | metadata.kind |
|---|---|---|
| a bug fix | `decision` | `fix` |
| a learning | `note` | `learning` |

`taxonomy.normalize_block_type()` does exactly that, and **raises** on a truly
unknown value instead of degrading — the silent degradation is the bug.

---

## 9. Other things that will bite (not in the ten, still real)

* **FalkorDB is an openCypher SUBSET, not Neo4j.** Do not paste Neo4j Cypher
  wholesale — check `cypher-support` and `known-limitations` before relying on
  APOC, subqueries, or exotic clauses. Verified working:
  `MATCH p=(a)-[:R*1..3]->(b)`, `$params`, `vecf32`.
* **GraphRAG-SDK is PRE-BAKE ONLY**, never on the live path. Batch every ingest
  and call `finalize()` **exactly once** — it is O(total graph size), not
  O(change). Save the `Ontology.from_sources(method='llm')` output: "the system
  read the corpus and invented its own schema" is a free demo beat.
* **No LLM on the live write path.** Gate → Cypher MERGE → delta. Milliseconds,
  and nothing that can hang on stage.
* **falkordblite gives you NO visualizer** — the Browser UI at :3000 only exists
  in the Docker image. Render from the exposed objects instead: Node
  `.id` / `.labels` / `.properties`, Edge `.relation` / `.src_node` /
  `.dest_node`, plus `Path` objects for the animated ring trace. All verified.
* **The Docker one-liner has NO AUTH** and binds to all interfaces. On
  conference wifi that is an open, writable database. Bind `127.0.0.1`.
* **Don't let LaserData be the knowledge graph.** Its SDK ships `laser.graph()`
  and `laser.memory()`, which overlap FalkorDB's mandated role. FalkorDB owns
  "what ever happened"; LaserData owns "what is happening now". Mixing them
  muddies both sponsor stories.

## 10. Repo hygiene (non-negotiable)

No credentials in this repo, ever — not in code, not in `.env`, not in git
history. A leaked key in history cannot be un-leaked. Everything is local or
bring-your-own-key; `.env.example` documents every variable; `config.secret()`
never defaults a key-shaped value and never echoes one into an error message.
Secret-scan before every commit.
