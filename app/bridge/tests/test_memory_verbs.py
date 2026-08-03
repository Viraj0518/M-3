"""remember / relate / recall — against the LIVE graph."""

from __future__ import annotations

import pytest

from app.bridge import graphstore
from app.bridge.tests.conftest import requires_live_db, vec, vec_near
from memory import config

pytestmark = requires_live_db


# ── remember ────────────────────────────────────────────────────────────────

def test_remember_recall_round_trip(call, run):
    """The headline: what goes in comes back out, attributed."""
    w = run(call("remember", {"content": "Northstar-7 edited the transit authority page"}))
    assert w["ok"] is True
    assert w["id"].startswith("blk_")
    assert w["created"] is True
    assert w["author_agent"] == "analyst"

    r = run(call("recall", {"text": "transit authority"}))
    assert r["ok"] is True
    assert r["count"] == 1
    node = r["results"][0]["node"]
    assert node["properties"]["id"] == w["id"]
    # author_agent is stamped SERVER-SIDE and survives the round trip — that is
    # what makes "who asserted this" a graph fact rather than a slide caption.
    assert node["properties"]["author_agent"] == "analyst"


def test_remember_is_idempotent_merge(call, run):
    """Twice with the same content is a MERGE, not a duplicate node."""
    a = run(call("remember", {"content": "same content"}))
    b = run(call("remember", {"content": "same content"}))
    assert a["id"] == b["id"]
    assert a["created"] is True and b["created"] is False


def test_remember_body_cannot_name_its_own_author(call, run):
    """The caller does not get to choose `author_agent`. Identity is resolved
    server-side at the router chokepoint, exactly like upstream refuses to let
    a request body pick its persona."""
    w = run(call("remember", {"content": "spoof attempt", "author_agent": "commander"}))
    assert w["author_agent"] == "analyst"
    assert w["node"]["properties"]["author_agent"] == "analyst"


def test_fix_and_learning_are_not_block_types(call, run):
    """SCHEMA.md trap 10. 'fix' must NOT silently degrade to 'note' — it becomes
    block_type='decision' + metadata.kind='fix', and the capture intent lives."""
    fix = run(call("remember", {"content": "patched the ordering bug", "block_type": "fix"}))
    assert fix["block_type"] == "decision"
    assert fix["metadata"]["kind"] == "fix"

    learning = run(call("remember", {"content": "sorting DESC is wrong", "block_type": "learning"}))
    assert learning["block_type"] == "note"
    assert learning["metadata"]["kind"] == "learning"

    # …and a genuinely unknown value RAISES rather than degrading. The router
    # turns it into an error envelope, which is still a refusal, not a write.
    bad = run(call("remember", {"content": "x", "block_type": "totally-made-up"}))
    assert bad.get("ok") is False
    assert "unknown block_type" in bad["error"]


def test_metadata_survives_the_no_map_property_type(call, run):
    """FalkorDB has no map property. metadata is JSON-encoded on write and
    decoded on read; if the two sides ever drift this test goes red."""
    meta = {"kind": "fix", "nested": {"a": [1, 2, 3]}, "n": 7}
    w = run(call("remember", {"content": "structured", "metadata": meta}))
    assert w["metadata"]["nested"] == {"a": [1, 2, 3]}
    raw = w["node"]["properties"]["metadata_json"]
    assert isinstance(raw, str)
    assert graphstore.decode_map(raw)["nested"]["a"] == [1, 2, 3]


def test_embedding_width_mismatch_is_refused(call, run):
    """A width mismatch corrupts the HNSW index SILENTLY — no error, just wrong
    neighbours. So it is refused at the door instead."""
    bad = run(call("remember", {"content": "x", "embedding": [0.1, 0.2, 0.3]}))
    assert bad.get("ok") is False
    assert "EMBED_DIM" in bad["error"] or "width" in bad["error"]


def test_event_label_carries_summary(call, run):
    """SCHEMA.md §1: :Event carries `summary`, :Claim carries `text`."""
    w = run(call("remember", {"content": "edit on the grid page", "labels": ["Event"]}))
    assert w["label"] == "Event"
    assert w["node"]["properties"]["summary"] == "edit on the grid page"


def test_unknown_label_is_refused(call, run):
    """A label cannot be a $param — it is interpolated, so it is allowlisted."""
    bad = run(call("remember", {"content": "x", "labels": ["Robert'); DROP GRAPH;--"]}))
    assert bad.get("ok") is False
    assert "unknown node label" in bad["error"]


# ── vector lane ─────────────────────────────────────────────────────────────

def test_vector_recall_sorts_ascending_because_score_is_a_distance(call, run):
    """SCHEMA.md TRAP 1, the highest embarrassment-per-character bug in the
    stack. The exact stored vector must come back FIRST with distance ~0.0.
    If anyone ever flips this sort to DESC, this test goes red instead of the
    demo showing judges the worst matches in the database."""
    run(call("remember", {"content": "alice", "embedding": vec(1.0)}))
    run(call("remember", {"content": "bob", "embedding": vec_near(1.0)}))

    r = run(call("recall", {"text": "alice", "embedding": vec(1.0), "top_k": 2}))
    assert r["ok"] is True
    assert r["mode"] == "vector"
    assert len(r["results"]) == 2

    first, second = r["results"][0], r["results"][1]
    assert first["node"]["properties"]["text"] == "alice"
    assert first["distance"] == pytest.approx(0.0, abs=1e-6)
    # STRICTLY ascending: nearer is SMALLER.
    assert first["distance"] < second["distance"]


def test_recall_falls_back_to_the_lexical_lane_without_an_embedding(call, run):
    run(call("remember", {"content": "the regional power grid capacity notice"}))
    r = run(call("recall", {"text": "power grid"}))
    assert r["mode"] == "lexical"
    assert r["count"] == 1


def test_sensing_gate_is_opt_in_and_rejects_non_novel(call, run):
    """Ported threshold (0.15) applied to the top-1 DISTANCE: BELOW means
    'too close to something we already know' => skip the write."""
    run(call("remember", {"content": "alice", "embedding": vec(1.0)}))

    gated = run(call("remember", {"content": "alice again", "embedding": vec(1.0), "gate": True}))
    assert gated["ok"] is True
    assert gated["skipped"] is True
    assert gated["distance"] < config.SALIENCE_THRESHOLD

    # Same write WITHOUT the flag goes in — the gate must never fire silently
    # on a deliberate capture.
    ungated = run(call("remember", {"content": "alice again", "embedding": vec(1.0)}))
    assert ungated.get("skipped") is None
    assert ungated["created"] is True


# ── relate: DIRECTION IS MEANING ────────────────────────────────────────────

def test_relate_is_genuinely_directed(call, run, cypher):
    """THE deliberate divergence from unblock. Their contradiction_edges table
    carries CHECK (block_id_a < block_id_b), which forces every edge into
    lexical id order and DESTROYS direction — after the write you cannot tell
    'A supersedes B' from 'B supersedes A'. We dropped that CHECK.

    Proven the only way that matters: write the edge in the direction that
    VIOLATES lexical id order, then assert the graph still reads it that way.
    """
    a = run(call("remember", {"content": "aaa first claim", "id": "zzz_high"}))
    b = run(call("remember", {"content": "bbb second claim", "id": "aaa_low"}))
    assert a["id"] > b["id"], "fixture must have from_id > to_id to be a real test"

    rel = run(call("relate", {"from_id": a["id"], "to_id": b["id"], "relation": "supersedes"}))
    assert rel["ok"] is True
    assert rel["directed"] is True

    # The edge exists in the written direction…
    fwd = cypher(
        "MATCH (x {id:$a})-[r:RELATES {relation:'supersedes'}]->(y {id:$b}) RETURN count(r)",
        {"a": a["id"], "b": b["id"]},
    )
    assert fwd[0][0] == 1
    # …and NOT in the reverse. A canonicalizing writer would have flipped it.
    rev = cypher(
        "MATCH (x {id:$b})-[r:RELATES {relation:'supersedes'}]->(y {id:$a}) RETURN count(r)",
        {"a": a["id"], "b": b["id"]},
    )
    assert rev[0][0] == 0


def test_relate_rejects_the_reader_only_fallback(call, run):
    """'relates' is the graph READER's fallback vocabulary for unknown statuses
    (roster-graph.ts mapContradictionStatus). It is NOT writable."""
    a = run(call("remember", {"content": "a"}))
    b = run(call("remember", {"content": "b"}))
    bad = run(call("relate", {"from_id": a["id"], "to_id": b["id"], "relation": "relates"}))
    assert bad["ok"] is False
    assert bad["code"] == "BAD_RELATION"
    assert "reader" in bad["error"].lower()


def test_relate_never_invents_its_endpoints(call, run):
    """A typo must be a refusal, not a phantom node."""
    a = run(call("remember", {"content": "a"}))
    bad = run(call("relate", {"from_id": a["id"], "to_id": "does-not-exist", "relation": "supports"}))
    assert bad["ok"] is False
    assert bad["code"] == "NODE_NOT_FOUND"


# ── supersede chain resolution ──────────────────────────────────────────────

def test_recall_resolves_the_supersede_chain_to_its_head(call, run):
    """'You told me X, then Y, then Z — here is the CURRENT truth and the chain
    that got there.' Serving a stale claim as current is the failure mode."""
    v1 = run(call("remember", {"content": "the meeting is on Tuesday"}))
    v2 = run(call("remember", {"content": "the meeting moved to Wednesday"}))
    v3 = run(call("remember", {"content": "the meeting moved to Friday"}))

    run(call("relate", {"from_id": v2["id"], "to_id": v1["id"], "relation": "supersedes"}))
    run(call("relate", {"from_id": v3["id"], "to_id": v2["id"], "relation": "supersedes"}))

    r = run(call("recall", {"text": "the meeting is on Tuesday"}))
    hit = r["results"][0]
    assert hit["node"]["properties"]["id"] == v1["id"]
    assert hit["superseded"] is True
    assert hit["superseded_by"] == v3["id"], "must resolve to the HEAD, not one hop"
    assert hit["supersede_depth"] == 2
    lineage_ids = [n["properties"]["id"] for n in hit["lineage"]]
    assert lineage_ids == [v1["id"], v2["id"], v3["id"]]


def test_head_of_a_chain_is_not_marked_superseded(call, run):
    v1 = run(call("remember", {"content": "old fact about zebras"}))
    v2 = run(call("remember", {"content": "new fact about zebras"}))
    run(call("relate", {"from_id": v2["id"], "to_id": v1["id"], "relation": "supersedes"}))

    r = run(call("recall", {"text": "new fact about zebras"}))
    hit = [x for x in r["results"] if x["node"]["properties"]["id"] == v2["id"]][0]
    assert hit["superseded"] is False
    assert hit["head"] is None


# ── hybrid expansion ────────────────────────────────────────────────────────

def test_one_hop_entity_expansion(call, run, cypher):
    """Semantic entry + symbolic expansion in one round trip — the half a
    vector store cannot do."""
    seed = run(call("remember", {"content": "grid capacity anomaly", "embedding": vec(1.0)}))
    other = run(call("remember", {"content": "unrelated prose, no lexical overlap"}))
    cypher(
        "MATCH (s {id:$s}), (o {id:$o}) "
        "MERGE (e:Entity {name:'power-grid'}) "
        "MERGE (s)-[:ABOUT]->(e) MERGE (o)-[:ABOUT]->(e)",
        {"s": seed["id"], "o": other["id"]},
    )

    r = run(call("recall", {"text": "grid capacity anomaly", "embedding": vec(1.0), "top_k": 1}))
    assert len(r["results"]) == 1
    expanded_ids = [x["node"]["properties"]["id"] for x in r["expanded"]]
    assert other["id"] in expanded_ids
    assert r["expanded"][0]["via_entity"]["properties"]["name"] == "power-grid"
    assert r["expanded"][0]["hop"] == 1


# ── graph-key safety ────────────────────────────────────────────────────────

def test_graph_key_is_allowlisted_not_interpolated(call, run):
    """The graph key arrives off a REST query string. It must not be free text."""
    bad = run(call("recall", {"text": "x", "graph": "; FLUSHALL"}))
    assert bad["ok"] is False
    assert "refusing graph key" in bad["error"]


def test_indexes_are_created_idempotently(graph_key):
    """SCHEMA.md §3. FalkorDB has no IF NOT EXISTS for these and a re-create
    raises 'already indexed' — which is the SUCCESS case on a second boot."""
    graphstore.forget_ensured(graph_key)
    first = graphstore.ensure_indexes(graph_key)
    assert first["vector:Claim.emb"] == "created"
    assert first["range:Actor.name"] == "created"

    graphstore.forget_ensured(graph_key)
    second = graphstore.ensure_indexes(graph_key)
    assert set(second.values()) == {"exists"}, second

    rows, _ms, _h = graphstore.query("CALL db.indexes()", graph_key=graph_key)
    dims = [r for r in rows if "Claim" in str(r)]
    assert dims, "the Claim vector index must exist"
    # The HNSW dimension MUST equal config.EMBED_DIM or the index corrupts
    # silently — wrong neighbours, no error.
    assert str(config.EMBED_DIM) in str(dims)
