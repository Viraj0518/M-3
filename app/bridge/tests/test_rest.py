"""SURFACE (a): the REST layer — health, the /v1 table, the UI's bare aliases.

The bug this file exists to catch is NOT "a route 500s". It is the false-green:
the bridge is up, FalkorDB is up, the graph is full, and the projector still
shows MOCK · BRIDGE OFFLINE because a path, a method or a status code did not
match what ``app/web/index.html`` actually asks for. That failure is invisible
from the server side — every log line is a 200 — so the alias contract is
asserted against THE UI FILE ITSELF (``test_the_alias_set_is_exactly_what_the_ui_fetches``),
not against a list someone typed here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.bridge import rest
from app.bridge import server as bridge
from app.bridge.tests.conftest import TEST_GRAPH, requires_live_db
from memory import config

REPO_ROOT = Path(__file__).resolve().parents[3]
UI_FILE = REPO_ROOT / "app" / "web" / "index.html"


@pytest.fixture(scope="module")
def client():
    """ONE client for the module.

    ``StreamableHTTPSessionManager.run()`` may be entered exactly once per
    instance, and ``rest.app`` holds one instance — so entering the lifespan
    per-test would raise on the second test rather than testing anything.
    """
    with TestClient(rest.app) as c:
        yield c


# ═══════════════════════════════════════════════════════════════════════════
# health — the Windows compose healthcheck contract
# ═══════════════════════════════════════════════════════════════════════════

@requires_live_db
def test_health_is_200_with_the_full_contract(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()

    assert body["ok"] is True
    # git_sha is resolved ONCE at startup from `git rev-parse HEAD`.
    assert re.fullmatch(r"[0-9a-f]{40}|unknown", body["git_sha"]), body["git_sha"]

    falkordb = body["falkordb"]
    assert falkordb["reachable"] is True
    assert isinstance(falkordb["latency_ms"], float)
    assert falkordb["endpoint"] == "{0}:{1}".format(
        config.FALKORDB_HOST, config.FALKORDB_PORT
    )

    # The laser probe is a BOOL either way — an absent log spine is a reported
    # fact, never an exception and never a missing key.
    assert isinstance(body["laser"]["reachable"], bool)
    assert body["laser"]["endpoint"] == "{0}:{1}".format(
        rest.LASER_PROBE_HOST, rest.LASER_PROBE_PORT
    )


def test_health_is_503_FALKORDB_UNAVAILABLE_when_the_graph_store_is_down(client, monkeypatch):
    """The compose healthcheck must FAIL THE CONTAINER when the memory plane is
    gone. A bridge that answers 200 with an empty graph is the single most
    expensive lie in this codebase — it looks alive and remembers nothing."""
    monkeypatch.setattr(
        rest,
        "_probe_falkordb",
        lambda: {
            "reachable": False,
            "latency_ms": 1.0,
            "endpoint": "127.0.0.1:6401",
            "error": "GraphUnavailable: simulated",
        },
    )
    r = client.get("/health")
    assert r.status_code == 503
    body = r.json()
    assert body["ok"] is False
    assert body["code"] == "FALKORDB_UNAVAILABLE"
    assert body["falkordb"]["reachable"] is False
    assert body["git_sha"]  # still reported — a 503 must stay diagnosable


def test_laser_probe_is_cached(monkeypatch, run):
    calls = {"n": 0}

    def counting():
        calls["n"] += 1
        return {"reachable": False, "endpoint": "x", "latency_ms": 0.1, "probe": "tcp"}

    monkeypatch.setattr(rest, "_probe_laser_blocking", counting)
    rest._laser_cache["value"] = None
    rest._laser_cache["at"] = 0.0

    first = run(rest.probe_laser())
    second = run(rest.probe_laser())
    assert calls["n"] == 1, "the second call inside the TTL must not re-probe"
    assert first["cached"] is False and second["cached"] is True

    third = run(rest.probe_laser(ttl_s=0.0))
    assert calls["n"] == 2 and third["cached"] is False


# ═══════════════════════════════════════════════════════════════════════════
# the routes ARE the table
# ═══════════════════════════════════════════════════════════════════════════

def _routes(app):
    out = set()
    for route in app.routes:
        for method in getattr(route, "methods", None) or ():
            out.add((method, route.path))
    return out


def test_every_table_entry_is_served_at_its_own_method_and_path(client):
    served = _routes(rest.app)
    for verb, (method, path, _b, _h) in bridge._VERB_DISPATCH.items():
        assert (method, path) in served, "{0} is in the table but not routed".format(verb)


def test_the_alias_set_is_exactly_what_the_ui_fetches():
    """THE reconciliation, asserted against the UI SOURCE.

    The UI polls bare, unprefixed paths; the table's paths carry /v1. If
    somebody 'cleans up' the aliases, or the UI starts polling a fourth path,
    this test names it — instead of the projector quietly falling back to its
    mock graph on stage.
    """
    html = UI_FILE.read_text(encoding="utf-8")
    fetched = {p.split("?")[0] for p in re.findall(r'fetchJson\("([^"]+)"\)', html)}
    assert fetched, "no fetchJson call sites found — did the UI change shape?"

    alias_paths = {alias for alias, _m, _d in rest._UI_ALIASES.values()}
    assert alias_paths == fetched, (
        "alias set {0} does not match what the UI actually fetches {1}".format(
            alias_paths, fetched
        )
    )

    # and every one of them is really mounted, at the method the UI uses (GET).
    served = _routes(rest.app)
    for path in fetched:
        assert ("GET", path) in served


def test_aliases_cover_only_verbs_that_exist_in_the_table():
    for verb in rest._UI_ALIASES:
        assert verb in bridge._VERB_DISPATCH


# ═══════════════════════════════════════════════════════════════════════════
# param mapping: query string -> verb args
# ═══════════════════════════════════════════════════════════════════════════

def test_coerce_uses_the_declared_json_type():
    assert rest.coerce("25", {"type": "integer"}) == 25
    assert rest.coerce("0.5", {"type": "number"}) == 0.5
    assert rest.coerce("a,b", {"type": "array"}) == ["a", "b"]
    assert rest.coerce('["a"]', {"type": "array"}) == ["a"]
    assert rest.coerce("plain", None) == "plain"


def test_coerce_turns_the_string_false_into_False():
    """A query string is all strings and a non-empty str is TRUTHY in Python.
    Un-coerced, `?gate=false` would turn the sensing gate ON and silently drop
    the write it was told to make."""
    assert rest.coerce("false", {"type": "boolean"}) is False
    assert rest.coerce("0", {"type": "boolean"}) is False
    assert rest.coerce("true", {"type": "boolean"}) is True


def test_args_from_query_is_driven_off_the_tool_schema():
    args = rest.args_from_query("graph", [("limit", "3"), ("since", "1700000000.5")])
    assert args == {"limit": 3, "since": 1700000000.5}


def test_unknown_query_params_survive_as_strings():
    assert rest.args_from_query("graph", [("graph", "palimpsest_test")]) == {
        "graph": "palimpsest_test"
    }


@requires_live_db
def test_get_graph_maps_query_params_onto_verb_args(client, graph_key):
    from app.bridge import graphstore

    graphstore.query(
        "CREATE (:Actor {name:'QueryParamActor'}), (:Actor {name:'Second'})",
        graph_key=graph_key,
    )
    both = client.get("/v1/graph", params={"graph": graph_key}).json()
    assert both["ok"] is True
    assert both["meta"]["node_count"] == 2

    capped = client.get("/v1/graph", params={"graph": graph_key, "limit": 1}).json()
    assert capped["meta"]["node_count"] == 1
    assert capped["meta"]["capped"] is True, "limit was not applied as an int"


@requires_live_db
def test_post_body_maps_onto_verb_args(client, graph_key):
    r = client.post(
        "/v1/remember",
        json={"content": "rest-layer body mapping", "graph": graph_key},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["id"].startswith("blk_")
    assert body["graph_key"] == graph_key
    # identity is resolved server-side: a sessionless HTTP call is `unbound`,
    # never the shared stdio bucket.
    assert body["author_agent"] == "unbound"


@requires_live_db
def test_the_agent_header_selects_the_author(client, graph_key):
    r = client.post(
        "/v1/remember",
        json={"content": "header-selected author", "graph": graph_key},
        headers={"X-Palimpsest-Agent": "analyst"},
    )
    assert r.json()["author_agent"] == "analyst"


@requires_live_db
def test_path_params_reach_the_verb(client):
    r = client.get("/v1/stream/{0}/tail".format(config.TOPIC_CASE_OPENED))
    assert r.status_code == 200
    body = r.json()
    assert body["topic"] == config.TOPIC_CASE_OPENED
    assert body["routing"]["path"] == "/v1/stream/{0}/tail".format(config.TOPIC_CASE_OPENED)


def test_a_non_object_json_body_is_a_400(client):
    r = client.post(
        "/v1/remember", content=b"[1,2,3]", headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 400
    assert r.json()["code"] == "BAD_REQUEST"


# ═══════════════════════════════════════════════════════════════════════════
# the UI's three aliases, on real data
# ═══════════════════════════════════════════════════════════════════════════

@requires_live_db
def test_bare_graph_alias_serves_the_same_payload_as_v1(client, graph_key):
    from app.bridge import graphstore

    graphstore.query("CREATE (:Actor {name:'AliasActor'})", graph_key=graph_key)
    bare = client.get("/graph", params={"graph": graph_key}).json()
    prefixed = client.get("/v1/graph", params={"graph": graph_key}).json()
    assert bare["nodes"] == prefixed["nodes"]
    assert bare["meta"]["node_count"] == 1


@requires_live_db
def test_bare_ring_alias_accepts_GET_although_the_table_says_POST(client, graph_key):
    """The table's method is POST. The UI GETs it. Both are served by the same
    verb, so a ring can never mean two different things on two surfaces."""
    assert bridge._VERB_DISPATCH["ring"][0] == "POST"
    from app.bridge.tests.test_ring import seed_ring
    from app.bridge import graphstore

    def cypher(statement, params=None):
        graphstore.query(statement, params or {}, graph_key=graph_key)

    seed_ring(cypher, spread_s=120)

    got = client.get("/ring", params={"graph": graph_key, "window_s": 720})
    assert got.status_code == 200
    body = got.json()
    assert body["fired"] is True and body["ring_count"] >= 1
    assert len(body["paths"][0]["nodes"]) == 5

    posted = client.post("/ring", json={"graph": graph_key, "window_s": 720}).json()
    assert posted["ring_count"] == body["ring_count"]


@requires_live_db
def test_ring_ids_are_node_ids_the_graph_projection_also_emits(client, graph_key):
    """``normalizeRing`` filters the ring ids against the ids in the rendered
    graph — a ring whose ids are not in /graph highlights NOTHING."""
    from app.bridge.tests.test_ring import seed_ring
    from app.bridge import graphstore

    seed_ring(lambda s, p=None: graphstore.query(s, p or {}, graph_key=graph_key), 120)

    graph = client.get("/graph", params={"graph": graph_key}).json()
    ring = client.get("/ring", params={"graph": graph_key}).json()
    ids = {n["id"] for n in graph["nodes"]}
    assert ring["ids"] and set(ring["ids"]) <= ids


# ═══════════════════════════════════════════════════════════════════════════
# the stream_tail stub — 200, honest, and UI-parseable
# ═══════════════════════════════════════════════════════════════════════════

def test_stream_tail_stub_shape(client):
    r = client.get("/stream_tail", params={"limit": 80})
    assert r.status_code == 200, "a stub is NOT an offline bridge — must be 2xx"
    body = r.json()
    assert body["stub"] is True
    assert body["events"] == []
    assert body["offset"] is None
    # and it is still HONEST about not being implemented
    assert body["ok"] is False
    assert body["status"] == "not_implemented"
    assert "STREAM LANE" in body["todo"]


def test_stream_tail_alias_defaults_the_topic_the_ui_cannot_supply(client):
    body = client.get("/stream_tail").json()
    assert body["topic"] == config.TOPIC_SIGNAL_RAW
    assert rest._UI_ALIASES["stream_tail"][2]["topic"] == config.TOPIC_SIGNAL_RAW


def test_the_stub_does_not_feed_the_ui_any_records(client):
    """``flattenRecords`` reads records/events/items/messages IN THAT ORDER.
    Every list the stub declares must be EMPTY or the strip renders invented
    traffic."""
    body = client.get("/stream_tail").json()
    for key in ("records", "events", "items", "messages"):
        assert body.get(key, []) == []


def test_status_is_derived_only_from_an_err_code():
    assert rest.status_for({"ok": True}) == 200
    assert rest.status_for({"ok": False, "status": "not_implemented"}) == 200
    assert rest.status_for({"ok": False, "code": "MISSING_CONTENT"}) == 400
    assert rest.status_for({"ok": False, "code": "HANDLER_ERROR"}) == 500
    assert rest.status_for({"ok": False, "code": "NEVER_SEEN_BEFORE"}) == 400


@requires_live_db
def test_a_missing_required_field_is_a_400_not_a_crash(client, graph_key):
    r = client.post("/v1/remember", json={"graph": graph_key})
    assert r.status_code == 400
    assert r.json()["code"] == "MISSING_CONTENT"


# ═══════════════════════════════════════════════════════════════════════════
# cross-surface
# ═══════════════════════════════════════════════════════════════════════════

def test_cors_is_open_for_the_file_origin_projector(client):
    """The projector is regularly opened as file://…/index.html, whose Origin
    header is the literal string "null". Without the wildcard the browser
    blocks every poll and the UI shows BRIDGE OFFLINE next to a healthy
    bridge."""
    r = client.get("/graph", headers={"Origin": "null"})
    assert r.headers.get("access-control-allow-origin") == "*"


def test_table_openapi_is_served_and_matches_the_generator(client):
    spec = client.get("/v1/openapi.json").json()
    assert spec == json.loads(
        json.dumps(
            bridge.openapi_spec(
                "http://{0}:{1}".format(config.BRIDGE_HOST, config.BRIDGE_PORT)
            )
        )
    )
    for _verb, (method, path, _b, _h) in bridge._VERB_DISPATCH.items():
        assert method.lower() in spec["paths"][path]


def test_root_advertises_every_surface(client):
    body = client.get("/").json()
    assert body["ok"] is True
    assert set(body["verbs"]) == set(bridge.VERBS)
    assert body["ui_aliases"] == {v: a for v, (a, _m, _d) in rest._UI_ALIASES.items()}


@pytest.mark.skipif(not rest._MCP_HTTP_IMPORTABLE, reason="mcp SDK not installed")
def test_mcp_is_mounted_on_the_same_port_without_a_redirect(client):
    """Bare /mcp must answer, not 307. The MCP Apps sandbox domain is a sha256
    OF THE ENDPOINT STRING, so a redirect silently changes the endpoint."""
    assert rest.app.state.mcp["mounted"] is True
    assert rest.app.state.mcp["path"] == "/mcp"

    r = client.post(
        "/mcp",
        headers={"Accept": "application/json, text/event-stream"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        },
        follow_redirects=False,
    )
    assert r.status_code == 200, r.status_code
    assert r.headers.get("mcp-session-id")
    assert '"serverInfo"' in r.text
