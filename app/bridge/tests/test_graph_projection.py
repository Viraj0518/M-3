"""graph — the UI projection, checked against the UI'S OWN PARSER.

THE UI WAS BUILT FIRST. The bridge conforms to it, not the other way around.

So this file does not assert "the payload has a `nodes` key" and call it a
contract. It PORTS `normalizeGraph` and `normalizeRing` out of
`app/web/index.html` line-for-line into Python and runs the real handler output
through them. If the projection drifts, the port fails exactly where the
browser would have failed — silently falling back to the mock graph, which on
stage is indistinguishable from "the memory doesn't work".

app/web/ is READ-ONLY to this lane. The port below is a transcription; the
source of truth is the HTML.
"""

from __future__ import annotations

from app.bridge.tests.conftest import requires_live_db

pytestmark = requires_live_db

MAX_VISIBLE_NODES = 260          # app/web/index.html


# ═══════════════════════════════════════════════════════════════════════════
# The UI's parsers, transcribed
# ═══════════════════════════════════════════════════════════════════════════

def endpoint_id(value):
    """`endpointId` — app/web/index.html."""
    if value is None:
        return ""
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, dict):
        for key in ("id", "block_id", "node_id"):
            if value.get(key) is not None:
                return str(value[key])
        props = value.get("properties") or {}
        for key in ("id", "block_id"):
            if props.get(key) is not None:
                return str(props[key])
    return ""


def normalize_graph(payload):
    """`normalizeGraph` — app/web/index.html.

    NOTE THE FIRST LINE. The UI unwraps `payload.result ?? payload.data ??
    payload.graph ?? payload`. A top-level key named `graph` holding a STRING
    (the graph key!) makes `root` that string and the whole parse collapses to
    zero nodes. That is why every handler returns `graph_key`, never `graph`.
    """
    root = payload
    for key in ("result", "data", "graph"):
        if payload.get(key):
            root = payload[key]
            break

    raw_nodes = root.get("nodes") if isinstance(root, dict) else None
    raw_edges = root.get("edges") if isinstance(root, dict) else None
    raw_nodes = raw_nodes if isinstance(raw_nodes, list) else []
    raw_edges = raw_edges if isinstance(raw_edges, list) else []

    nodes = []
    for index, raw in enumerate(raw_nodes):
        props = raw.get("properties") if isinstance(raw.get("properties"), dict) else raw
        labels = raw["labels"] if isinstance(raw.get("labels"), list) else [
            raw.get("label") or props.get("type") or props.get("scope") or "Entity"
        ]
        nid = endpoint_id(raw) or str(index)
        ntype = str(labels[0] if labels else (props.get("type") or "Entity")).lstrip(":")
        label = None
        for key in ("name", "title", "display_name", "snippet", "content", "summary", "id"):
            if props.get(key) is not None:
                label = props[key]
                break
        if label is None:
            label = nid
        if nid:
            nodes.append(
                {
                    "id": nid,
                    "type": ntype,
                    "labels": labels,
                    "label": str(label)[:74],
                    "properties": props,
                }
            )

    node_ids = {n["id"] for n in nodes}
    edges = []
    for raw in raw_edges:
        relation = (
            raw.get("relation")
            or raw.get("type")
            or raw.get("label")
            or (raw.get("properties") or {}).get("relation")
            or "RELATES"
        )
        source = endpoint_id(
            raw.get("src") or raw.get("source") or raw.get("src_node")
            or raw.get("start") or raw.get("from")
        )
        target = endpoint_id(
            raw.get("dest") or raw.get("target") or raw.get("dest_node")
            or raw.get("end") or raw.get("to")
        )
        if source and target and source in node_ids and target in node_ids:
            edges.append({"source": source, "target": target, "relation": str(relation)})

    limited_nodes = nodes[:MAX_VISIBLE_NODES]
    limited_ids = {n["id"] for n in limited_nodes}
    limited_edges = [e for e in edges if e["source"] in limited_ids and e["target"] in limited_ids]
    meta = root.get("meta") or {"node_count": len(nodes), "edge_count": len(edges)}
    return {"nodes": limited_nodes, "edges": limited_edges, "meta": meta}


def extract_ring_ids(candidate):
    """`extractRingIds` — app/web/index.html."""
    if not candidate:
        return []
    if isinstance(candidate, list):
        return [x for x in (endpoint_id(c) for c in candidate) if x]
    if isinstance(candidate.get("nodes"), list):
        return [x for x in (endpoint_id(c) for c in candidate["nodes"]) if x]
    if isinstance(candidate.get("path"), list):
        return extract_ring_ids(candidate["path"])
    return []


def normalize_ring(payload, graph):
    """`normalizeRing` — app/web/index.html."""
    root = payload
    for key in ("result", "data"):
        if payload.get(key):
            root = payload[key]
            break
    candidates = root.get("paths") or root.get("rings") or root.get("path") or []
    ids = []
    if isinstance(candidates, list) and candidates:
        head = candidates[0]
        if isinstance(head, list) or (isinstance(head, dict) and (head.get("nodes") or head.get("path"))):
            ids = extract_ring_ids(head)
        else:
            ids = extract_ring_ids(candidates)
    else:
        ids = extract_ring_ids(candidates)
    ids = [i for i in ids if any(n["id"] == i for n in graph["nodes"])]
    run = root.get("run_time_ms")
    if run is None:
        run = root.get("runtime_ms")
    if run is None:
        run = (root.get("meta") or {}).get("run_time_ms")
    return {"ids": ids, "run_time_ms": float(run) if isinstance(run, (int, float)) else 2.45}


# ═══════════════════════════════════════════════════════════════════════════
# the contract
# ═══════════════════════════════════════════════════════════════════════════

def _seed(call, run, cypher):
    a = run(call("remember", {"content": "Coordinated campaign, 4th move"}))
    b = run(call("remember", {"content": "Same source cluster"}))
    run(call("relate", {"from_id": a["id"], "to_id": b["id"], "relation": "supports"}))
    cypher(
        "CREATE (x:Actor {name:'Northstar-7'})-[:EDITED {ts:1}]->(p:Page {title:'Transit'})"
    )
    return a, b


def test_graph_payload_parses_through_the_ui_parser(call, run, cypher):
    """The whole point: our real payload, the UI's real parser, non-zero nodes.

    `if (normalized.nodes.length) liveGraph = normalized;` — a zero-length parse
    is EXACTLY the condition under which the projector decides the bridge is
    offline and shows mock data instead.
    """
    _seed(call, run, cypher)
    payload = run(call("graph", {}))
    assert payload["ok"] is True

    parsed = normalize_graph(payload)
    assert len(parsed["nodes"]) > 0, "a zero-node parse makes the UI fall back to mock"
    assert len(parsed["edges"]) > 0


def test_no_top_level_key_hijacks_the_ui_unwrap(call, run, cypher):
    """`payload.result ?? payload.data ?? payload.graph ?? payload`.

    If a handler ever returns a top-level `graph` (or `result`/`data`) holding
    the graph-key STRING, `root` becomes that string, `root.nodes` is undefined,
    and the UI silently renders mock data with no error anywhere. This is the
    single highest-cost shape bug available to this payload."""
    _seed(call, run, cypher)
    payload = run(call("graph", {}))

    for hijacker in ("result", "data", "graph"):
        assert not isinstance(payload.get(hijacker), str), (
            "top-level {0!r} must not be a string — it hijacks the UI unwrap "
            "and collapses the parse to zero nodes".format(hijacker)
        )
    assert payload["graph_key"] == "palimpsest_test"


def test_node_shape_is_id_labels_properties(call, run, cypher):
    _seed(call, run, cypher)
    payload = run(call("graph", {}))

    for node in payload["nodes"]:
        assert set(node) == {"id", "labels", "properties"}
        # The id must be a STRING and must be what endpointId resolves, because
        # edges reference nodes by exactly this value.
        assert isinstance(node["id"], str) and node["id"]
        assert endpoint_id(node) == node["id"]
        assert isinstance(node["labels"], list) and node["labels"]
        assert isinstance(node["properties"], dict)


def test_every_node_renders_a_non_empty_label(call, run, cypher):
    """The UI's label chain is name/title/display_name/snippet/content/summary/id.
    Our :Claim carries `text`, which is NOT in that chain — so the projection
    synthesises `snippet`. Without it every Claim renders as an opaque id."""
    _seed(call, run, cypher)
    parsed = normalize_graph(run(call("graph", {})))

    for node in parsed["nodes"]:
        assert node["label"], "node {0} renders blank".format(node["id"])
        assert node["label"] != node["id"] or node["type"] in ("Entity",), (
            "node {0} fell through to its raw id".format(node["id"])
        )

    claims = [n for n in parsed["nodes"] if n["type"] == "Claim"]
    assert claims
    assert any("Coordinated campaign" in n["label"] for n in claims)


def test_edge_shape_is_relation_src_dest_and_endpoints_resolve(call, run, cypher):
    """The UI drops any edge whose endpoints are not in the node id set. A
    projection that emits domain ids on nodes and internal ids on edges would
    render a graph with ZERO edges and no error."""
    _seed(call, run, cypher)
    payload = run(call("graph", {}))
    node_ids = {n["id"] for n in payload["nodes"]}

    assert payload["edges"]
    for edge in payload["edges"]:
        assert {"relation", "src", "dest"} <= set(edge)
        assert isinstance(edge["src"], str) and isinstance(edge["dest"], str)
        assert edge["src"] in node_ids, "edge src {0} is not a projected node".format(edge["src"])
        assert edge["dest"] in node_ids

    parsed = normalize_graph(payload)
    assert len(parsed["edges"]) == len(payload["edges"]), "the UI dropped edges"


def test_relation_prefers_the_directed_vocabulary_over_the_edge_type(call, run, cypher):
    """`[:RELATES]` edges must surface their 6-value `relation` property, not the
    literal type 'RELATES' — the UI colours contradicts/supports differently."""
    _seed(call, run, cypher)
    parsed = normalize_graph(run(call("graph", {})))
    relations = {e["relation"] for e in parsed["edges"]}
    assert "supports" in relations
    assert "EDITED" in relations, "structural edges fall back to the edge TYPE"


def test_meta_matches_the_roster_graph_contract(call, run, cypher):
    """BrainGraph.meta from roster-graph.ts: node_count, edge_count, capped,
    real_edges, derived_edges. The UI reads node_count/edge_count for the
    'N nodes · M edges' counters."""
    _seed(call, run, cypher)
    payload = run(call("graph", {}))
    meta = payload["meta"]

    assert {"node_count", "edge_count", "capped", "real_edges", "derived_edges"} <= set(meta)
    assert meta["node_count"] == len(payload["nodes"])
    assert meta["edge_count"] == len(payload["edges"])
    assert meta["capped"] is False
    assert meta["real_edges"] + meta["derived_edges"] == meta["edge_count"]
    assert meta["real_edges"] == 1          # the [:RELATES] supports edge
    assert meta["derived_edges"] == 1       # the [:EDITED] structural edge


def test_embeddings_are_stripped_from_the_projection(call, run):
    """256 floats per node would be ~90% of the payload and render as nothing."""
    from app.bridge.tests.conftest import vec

    run(call("remember", {"content": "vectored claim", "embedding": vec(1.0)}))
    payload = run(call("graph", {}))
    for node in payload["nodes"]:
        assert "emb" not in node["properties"]


def test_capped_is_reported_when_more_nodes_exist(call, run):
    for i in range(5):
        run(call("remember", {"content": "claim number {0}".format(i)}))
    payload = run(call("graph", {"limit": 2}))
    assert len(payload["nodes"]) == 2
    assert payload["meta"]["capped"] is True
    assert payload["meta"]["total_nodes"] == 5


def test_contributors_colour_the_graph_by_author_agent(call, run):
    run(call("remember", {"content": "an analyst claim"}))
    payload = run(call("graph", {}))
    assert payload["contributors"] == {"analyst": 1}


def test_ring_payload_parses_through_the_ui_ring_parser(call, run, cypher):
    """normalizeRing takes paths[0].nodes -> endpointId -> and then FILTERS to
    ids present in the graph payload. If the two projections disagree on node
    identity, the ring animation highlights nothing at all."""
    from app.bridge.tests.test_ring import seed_ring

    seed_ring(cypher, spread_s=120)
    graph_payload = run(call("graph", {}))
    ring_payload = run(call("ring", {"window_s": 720}))

    parsed_graph = normalize_graph(graph_payload)
    parsed_ring = normalize_ring(ring_payload, parsed_graph)

    assert len(parsed_ring["ids"]) == 5, (
        "every ring node must also be a graph node — the UI filters ring ids "
        "against the rendered graph before animating"
    )
    assert parsed_ring["run_time_ms"] > 0.0
    assert parsed_ring["run_time_ms"] != 2.45, "must be FalkorDB's time, not the UI default"
