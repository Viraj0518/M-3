"""ONE table, FOUR surfaces — and the pre-registered payload-builder bug class.

These tests need NO database. They are the guard on the thing that makes the
sprint finishable: `_VERB_DISPATCH` generating REST, MCP, OpenAPI and the CLI
without a second implementation. If the table and a surface ever disagree, one
of them is silently wrong.

THE PRE-REGISTERED BUG CLASS (server.py header): in unblock, `unblock_ingest`
was not a passthrough — the TOOL spoke {utterances:[...]} while the BACKEND
spoke {items:[...]}. The mismatch silently 400'd every call and cost unblock
agent self-ingest ENTIRELY, because the failure was a well-formed error
envelope rather than a crash. A payload_builder is the ONLY place the
tool-facing shape becomes the handler-facing shape, so it is the only place
that class of bug can hide. Every builder therefore gets a shape test here.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.bridge import server as bridge

REPO_ROOT = Path(__file__).resolve().parents[3]

#: Verbs this lane implemented against FalkorDB.
LIVE_VERBS = {
    "remember", "relate", "recall", "ring", "graph",
    "handover_write", "handover_read", "ask",
}
#: Verbs deliberately left as honest not-implemented envelopes (separate lane).
STUB_VERBS = {"stream_publish", "stream_tail", "stream_replay", "act"}


# ── the table ───────────────────────────────────────────────────────────────

def test_table_covers_exactly_the_twelve_verbs():
    assert set(bridge.VERBS) == LIVE_VERBS | STUB_VERBS
    assert len(bridge.VERBS) == 12


def test_stream_and_act_are_still_honest_stubs():
    """This lane must NOT have touched the laser/rocketride wiring."""
    for verb in sorted(STUB_VERBS):
        _m, _p, _b, handler = bridge._VERB_DISPATCH[verb]
        assert handler.__name__ == "_h_{0}".format(verb)
        src = handler.__code__
        assert "todo" in handler.__globals__ and src.co_names.count("todo") == 1, verb


def test_no_verb_returns_a_ui_unwrap_hijacking_key():
    """`payload.result ?? payload.data ?? payload.graph ?? payload` — a handler
    returning any of those three as a top-level STRING collapses the UI parse
    to zero nodes with no error. Asserted structurally on the source so it
    cannot regress in a handler this suite does not exercise live."""
    import inspect
    import re

    # Word-boundary anchored: `metadata=` and `graph_key=` are FINE, a bare
    # `data=` / `result=` / `graph=` keyword argument to ok() is not.
    hijackers = [re.compile(r"(?<![A-Za-z0-9_])" + name + r"=") for name in ("result", "data", "graph")]
    for verb in sorted(LIVE_VERBS):
        _m, _p, _b, handler = bridge._VERB_DISPATCH[verb]
        src = inspect.getsource(handler)
        for pattern in hijackers:
            hit = pattern.search(src)
            assert hit is None, "{0} returns a UI-unwrap-hijacking key: {1}".format(
                verb, hit.group(0) if hit else ""
            )


# ── surface (b): MCP ────────────────────────────────────────────────────────

def test_mcp_tool_surface_matches_the_table_exactly():
    tools = {t.name for t in bridge.all_tools()}
    assert tools == {bridge.tool_name(v) for v in bridge.VERBS}


def test_tool_name_round_trips():
    for verb in bridge.VERBS:
        assert bridge.verb_of(bridge.tool_name(verb)) == verb
        assert bridge.verb_of(verb) == verb, "the CLI passes bare verbs"


def test_every_tool_schema_is_well_formed():
    for tool in bridge.all_tools():
        schema = tool.inputSchema
        assert schema["type"] == "object"
        assert tool.description and len(tool.description) > 40
        for name in schema.get("required", []):
            assert name in schema["properties"], "{0}: required {1} is not a property".format(
                tool.name, name
            )


def test_memory_verbs_accept_a_graph_selector():
    """Every memory verb must be able to target the ablation's cold graph, or
    the A/B beat cannot be driven from any surface."""
    by_name = {t.name: t for t in bridge.all_tools()}
    for verb in ("remember", "relate", "recall", "ring", "graph",
                 "handover_write", "handover_read"):
        props = by_name[bridge.tool_name(verb)].inputSchema["properties"]
        assert "graph" in props, verb
        assert props["graph"]["enum"] == ["palimpsest", "palimpsest_cold"]


def test_mcp_module_imports_without_the_sdk():
    """server.py must import on a bare interpreter — the guarded-import contract.
    Verified by importing it in a subprocess with `mcp` and `falkordb` blocked."""
    # `sys.modules[name] = None` makes `import name` raise ImportError — the
    # portable way to simulate an absent package (the meta_path find_module
    # hook this used to rely on was removed in Python 3.12).
    code = (
        "import sys\n"
        "for _m in ('mcp', 'mcp.server', 'mcp.server.stdio', 'mcp.types', 'falkordb'):\n"
        "    sys.modules[_m] = None\n"
        "sys.path.insert(0, {0!r})\n"
        "import app.bridge.server as s\n"
        "assert s.MCP_AVAILABLE is False\n"
        "import app.bridge.graphstore as g\n"
        "assert g.FALKORDB_AVAILABLE is False\n"
        "assert len(s.all_tools()) == 12\n"
        "import json; json.dumps(s.openapi_spec())\n"
        "print('OK')\n"
    ).format(str(REPO_ROOT))
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO_ROOT)
    )
    assert out.returncode == 0, out.stderr
    assert "OK" in out.stdout


def test_server_py_imports_on_python_39():
    """The 3.9-importability contract. The system interpreter on this box is
    3.9.6 and has neither `mcp` nor `falkordb`; the table, the schemas and the
    OpenAPI generator must still work there."""
    py39 = Path("/usr/bin/python3")
    if not py39.exists():
        pytest.skip("no system python3 at /usr/bin/python3")
    out = subprocess.run(
        [
            str(py39),
            "-c",
            "import sys; sys.path.insert(0, {0!r});"
            "import app.bridge.server as s; import json;"
            "json.dumps(s.openapi_spec());"
            "print(sys.version_info[:2], len(s.VERBS))".format(str(REPO_ROOT)),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert out.returncode == 0, out.stderr
    assert "12" in out.stdout


# ── surface (c): OpenAPI 3.0 — the ONE Guild Integration ────────────────────

def test_openapi_covers_every_verb_at_its_table_method_and_path():
    spec = bridge.openapi_spec()
    assert spec["openapi"] == "3.0.3"
    for verb, (method, path, _b, _h) in bridge._VERB_DISPATCH.items():
        op = spec["paths"][path][method.lower()]
        assert op["operationId"] == verb


def test_openapi_is_json_serializable_and_has_no_secrets():
    blob = json.dumps(bridge.openapi_spec())
    for leak in ("API_KEY", "sk-", "ghp_", "Bearer ", "password", "token="):
        assert leak not in blob


def test_openapi_moves_path_params_out_of_the_body():
    """stream_publish's `topic` is in the PATH; it must become a path parameter
    and must NOT also be a body property, or Guild sends it twice."""
    spec = bridge.openapi_spec()
    op = spec["paths"]["/v1/stream/{topic}"]["post"]
    params = {p["name"]: p for p in op["parameters"]}
    assert params["topic"]["in"] == "path"
    assert params["topic"]["required"] is True
    body = op["requestBody"]["content"]["application/json"]["schema"]
    assert "topic" not in body["properties"]
    assert "topic" not in body.get("required", [])


def test_openapi_get_verbs_use_query_parameters():
    spec = bridge.openapi_spec()
    op = spec["paths"]["/v1/graph"]["get"]
    assert "requestBody" not in op
    assert {p["name"] for p in op["parameters"]} >= {"graph", "limit"}


# ── the payload builders — THE PRE-REGISTERED BUG CLASS ─────────────────────

def test_passthrough_builder_is_a_copy_not_an_alias():
    src = {"content": "x"}
    out = bridge._passthrough(src)
    out["mutated"] = True
    assert "mutated" not in src, "a builder must never mutate the caller's dict"


def test_strip_keys_builder_removes_only_path_params():
    build = bridge._strip_keys("topic")
    out = build({"topic": "signal.raw", "payload": {"a": 1}, "key": "enwiki"})
    assert out == {"payload": {"a": 1}, "key": "enwiki"}


def test_handover_write_builder_forces_status_open():
    """A caller must not be able to write a non-open handover from ANY surface."""
    out = bridge._build_handover_write({"agent_id": "w", "status": "closed", "summary": "s"})
    assert out["status"] == "open"


def test_handover_write_builder_omits_undefined():
    out = bridge._build_handover_write({"agent_id": "w"})
    assert out == {"status": "open", "agent_id": "w"}
    assert "summary" not in out, "omit-undefined, ported from _handover_write_body"


def test_handover_write_builder_passes_through_routing_fields():
    """`to_agent` and `graph` reach the handler, or cross-agent delegation and
    the cold-graph ablation are unreachable from the tool surface."""
    out = bridge._build_handover_write(
        {"agent_id": "w", "to_agent": "commander", "graph": "palimpsest_cold"}
    )
    assert out["to_agent"] == "commander"
    assert out["graph"] == "palimpsest_cold"


def test_handover_builder_covers_every_declared_tool_property():
    """THE shape test that catches the unblock_ingest class of bug: every
    property the TOOL declares must survive the builder into the handler."""
    by_name = {t.name: t for t in bridge.all_tools()}
    props = by_name[bridge.tool_name("handover_write")].inputSchema["properties"]
    args = {name: {"x": 1} if name in ("artifacts", "checkpoint") else "v" for name in props}
    args["graph"] = "palimpsest"
    out = bridge._build_handover_write(args)
    missing = set(props) - set(out)
    assert not missing, "builder drops declared tool properties: {0}".format(sorted(missing))


def test_ask_builder_always_supplies_a_default():
    """`default` is the single most important borrowed field in the demo — it
    is what stops the pitch freezing at second 60 when nobody taps approve."""
    out = bridge._build_ask({"question": "Escalate?", "options": ["approve", "dismiss"]})
    assert out["default"] == "dismiss"
    assert out["default_source"] == "last_option_fallback"
    assert out["timeout_sec"] == 60.0
    assert out["intent"] == "ASK"


def test_ask_builder_respects_an_explicit_default():
    out = bridge._build_ask(
        {"question": "q", "options": ["a", "b"], "default": "a", "timeout_sec": 20}
    )
    assert out["default"] == "a"
    assert "default_source" not in out
    assert out["timeout_sec"] == 20


# ── surface (d): CLI ────────────────────────────────────────────────────────

def _cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "app.bridge.server"] + list(args),
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


def test_cli_lists_tools():
    out = _cli("--list-tools")
    assert out.returncode == 0
    assert len(out.stdout.strip().splitlines()) == 12
    assert "palimpsest_remember" in out.stdout


def test_cli_lists_the_dispatch_table():
    out = _cli("--verbs")
    assert out.returncode == 0
    assert "remember         POST   /v1/remember" in out.stdout
    assert "graph            GET    /v1/graph" in out.stdout


def test_cli_emits_the_openapi_spec():
    out = _cli("--openapi")
    assert out.returncode == 0
    spec = json.loads(out.stdout)
    assert len(spec["paths"]) >= 9


def test_cli_config_is_secret_free():
    out = _cli("--config")
    assert out.returncode == 0
    cfg = json.loads(out.stdout)
    assert cfg["falkordb"].endswith(":6401"), "port 6401, not 6379, not 6399"
    for key, value in cfg.items():
        if key.endswith("_set"):
            assert isinstance(value, bool), "{0} must report presence, never a value".format(key)


def test_cli_help_exits_clean():
    assert _cli("--help").returncode == 0


def test_cli_unknown_verb_is_an_honest_error():
    out = _cli("not_a_verb", "{}")
    assert out.returncode == 1
    payload = json.loads(out.stdout)
    assert payload["code"] == "UNKNOWN_TOOL"
    assert len(payload["known"]) == 12


# ── the router chokepoint ───────────────────────────────────────────────────

def test_router_turns_a_handler_crash_into_an_envelope(run):
    """One place turns a crash into an error envelope — never a stack trace out
    of a surface, and never a fabricated success."""
    r = run(bridge.dispatch("remember", {"content": "x", "graph": "../../etc/passwd"}))
    assert r["ok"] is False
    assert r["code"] == "HANDLER_ERROR"
    assert "refusing graph key" in r["error"]


def test_router_reports_a_missing_path_param(run):
    r = run(bridge.dispatch("stream_publish", {"payload": {}}))
    assert r["ok"] is False
    assert r["code"] == "MISSING_PATH_PARAM"


def test_router_resolves_identity_per_request(run):
    """Two sessions in ONE process act as two DIFFERENT agents, zero cross-bleed
    — that is what lets one bridge serve every Guild specialist."""
    from app.bridge import identity

    identity.clear()
    identity.bind("sess-a", "watcher")
    identity.bind("sess-b", "commander")

    a = run(bridge.dispatch("stream_tail", {"topic": "signal.raw"}, session_id="sess-a"))
    b = run(bridge.dispatch("stream_tail", {"topic": "signal.raw"}, session_id="sess-b"))
    assert a["author_agent"] == "watcher"
    assert b["author_agent"] == "commander"

    # A sessionless HTTP request must NOT fall through to the shared stdio
    # bucket — that is the isolation hole the upstream review caught.
    identity.bind(None, "stdio-agent")
    anon = run(bridge.dispatch("stream_tail", {"topic": "signal.raw"}, session_id=None))
    assert anon["author_agent"] == "unbound"
    identity.clear()


def test_ask_returns_the_decision_card_shape(run):
    """Stub, and says so — never a fabricated approval."""
    r = run(bridge.dispatch("ask", {"question": "Escalate campaign?", "options": ["approve", "dismiss"]}))
    assert r["ok"] is True
    assert r["status"] == "stub"
    assert r["pending"] is True
    assert r["answer"] is None
    card = r["card"]
    assert set(card) >= {"question", "options", "recommendation", "timeoutSec", "default"}
    assert card["timeoutSec"] == 60.0
    assert card["default"] == "dismiss"


def test_ask_documents_the_elicitation_branch():
    """plan/research/mcp-widgets-guide.md §2.6 tier 2 — the branch a terminal
    host needs. Recorded on the handler so the next lane finds it."""
    doc = bridge._h_ask.__doc__
    assert "elicit" in doc
    assert "2.6" in doc
    assert "SUBSCRIBE THE REPLY SUBJECT FIRST" in doc
