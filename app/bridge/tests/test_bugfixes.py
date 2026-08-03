"""Two audit-confirmed correctness bugs, fixed and pinned against the LIVE db.

BUG 1 — MCP-surface writes were attributed to 'unbound'.
    The MCP ``call_tool`` path dispatched with no session and no headers, so
    ``identity.resolve`` fell straight to ``UNBOUND_AGENT`` and EVERY write
    driven through the surface we hand judges undercut GOAL's "the agent knows
    who it is" pillar. The fix gives the MCP surface a configured default
    author (``MCP_DEFAULT_AGENT``) at the BOTTOM of the identity ladder — an
    explicit binding, a forwarded ``x-palimpsest-agent`` header, and any future
    verified-token selector all still outrank it, so it never masks a real
    identity; it only replaces the honest-but-useless 'unbound'.

BUG 2 — relate(supersedes) accepted self-edges and cycles, and head selection
    was nondeterministic on a fork.
    ``a supersedes b`` + ``b supersedes a`` both returned ok:true, after which
    recall(a) reported superseded:false / head:null because a cycle leaves the
    chain with NO head. A fork (new_a→old←new_b) picked a head arbitrarily via
    ``ORDER BY length DESC LIMIT``. The fix rejects self-edges and any
    cycle-closing supersedes edge at write time, and breaks head-selection ties
    with a stable key so a fork resolves the SAME head every run.
"""

from __future__ import annotations

import json

import pytest

from app.bridge import graphstore, identity
from app.bridge import server as bridge
from app.bridge.tests.conftest import TEST_GRAPH, requires_live_db

pytestmark = requires_live_db


# ═══════════════════════════════════════════════════════════════════════════
# BUG 1 — MCP writes carry a real author_agent, not 'unbound'
# ═══════════════════════════════════════════════════════════════════════════

requires_mcp = pytest.mark.skipif(
    not bridge.MCP_AVAILABLE, reason="the mcp SDK is not installed"
)


@requires_mcp
def test_mcp_surface_write_is_attributed_not_unbound(graph_key, run):
    """Drive a write THROUGH the MCP call_tool handler (the surface judges
    touch) with nothing bound and no headers, exactly like the stdio mount.
    The created node must NOT be stamped 'unbound'."""
    identity.clear()  # no stdio binding: the fallback must be the configured default
    try:
        out = run(
            bridge.call_tool(
                bridge.tool_name("remember"),
                {"content": "written over the MCP surface", "graph": graph_key},
            )
        )
        env = json.loads(out[0].text)
    finally:
        identity.clear()

    assert env["ok"] is True
    assert env["author_agent"] != identity.UNBOUND_AGENT
    assert env["author_agent"] == bridge.MCP_DEFAULT_AGENT
    # …and it is PERSISTED on the node, not just echoed on the envelope.
    assert env["node"]["properties"]["author_agent"] == bridge.MCP_DEFAULT_AGENT


@requires_mcp
def test_mcp_persisted_node_is_readable_back_with_its_author(graph_key, run):
    """A round trip: the MCP-authored node is recall-able and still attributed."""
    identity.clear()
    try:
        run(
            bridge.call_tool(
                bridge.tool_name("remember"),
                {"content": "the transit authority anomaly", "graph": graph_key},
            )
        )
        r = run(bridge.dispatch("recall", {"text": "transit authority", "graph": graph_key}))
    finally:
        identity.clear()
    assert r["count"] == 1
    assert r["results"][0]["node"]["properties"]["author_agent"] == bridge.MCP_DEFAULT_AGENT


def test_forwarded_agent_header_outranks_the_mcp_default(run):
    """A forwarded x-palimpsest-agent still wins over the configured default —
    the default is the LOWEST rung, so it can never mask a real selector."""
    identity.clear()
    r = run(
        bridge.dispatch(
            "ask",
            {"question": "Escalate?", "options": ["approve", "dismiss"]},
            headers={"x-palimpsest-agent": "watcher", "mcp-session-id": "sess-hdr"},
            surface="mcp",
            is_stdio=False,
            default_agent=bridge.MCP_DEFAULT_AGENT,
        )
    )
    assert r["author_agent"] == "watcher"


def test_bound_session_outranks_the_mcp_default(run):
    """An explicit binding wins over the configured default too."""
    identity.clear()
    identity.bind("sess-bound", "commander")
    try:
        r = run(
            bridge.dispatch(
                "ask",
                {"question": "Escalate?", "options": ["approve", "dismiss"]},
                session_id="sess-bound",
                surface="mcp",
                is_stdio=False,
                default_agent=bridge.MCP_DEFAULT_AGENT,
            )
        )
    finally:
        identity.clear()
    assert r["author_agent"] == "commander"


def test_rest_parity_unchanged_sessionless_is_still_unbound(run):
    """REST parity: a surface that passes NO default_agent still resolves to
    'unbound' when nothing is bound — the fix is additive, not a global change
    of the honest fallback."""
    identity.clear()
    r = run(
        bridge.dispatch(
            "ask",
            {"question": "Escalate?", "options": ["approve", "dismiss"]},
            headers={},
            surface="rest",
        )
    )
    assert r["author_agent"] == identity.UNBOUND_AGENT


# ═══════════════════════════════════════════════════════════════════════════
# BUG 2 — supersede acyclicity + deterministic head
# ═══════════════════════════════════════════════════════════════════════════


def test_supersede_self_edge_is_rejected(call, run):
    """A node cannot supersede itself — a self-edge is a trivial cycle that
    leaves the chain with no head."""
    a = run(call("remember", {"content": "self-referential claim"}))
    bad = run(call("relate", {"from_id": a["id"], "to_id": a["id"], "relation": "supersedes"}))
    assert bad["ok"] is False
    assert bad["code"] == "SUPERSEDE_CYCLE"


def test_supersede_cycle_is_rejected_and_head_stays_resolvable(call, run):
    """`a supersedes b` then `b supersedes a`: the SECOND edge closes a cycle
    and is rejected, so the graph stays a DAG and recall resolves a real head
    instead of the null/undefined verdict a cycle produced."""
    a = run(call("remember", {"content": "claim A", "id": "cyc_a"}))
    b = run(call("remember", {"content": "claim B", "id": "cyc_b"}))

    first = run(call("relate", {"from_id": "cyc_a", "to_id": "cyc_b", "relation": "supersedes"}))
    assert first["ok"] is True

    # the cycle-closing edge is refused with a clear error
    second = run(call("relate", {"from_id": "cyc_b", "to_id": "cyc_a", "relation": "supersedes"}))
    assert second["ok"] is False
    assert second["code"] == "SUPERSEDE_CYCLE"
    assert "cycle" in second["error"].lower()

    # exactly ONE supersedes edge survived — the writer never let the loop form
    r = run(call("recall", {"text": "claim B"}))
    hit_b = [h for h in r["results"] if h["node"]["properties"]["id"] == "cyc_b"][0]
    # cyc_a is the newer node and the resolved HEAD of cyc_b's chain
    assert hit_b["superseded"] is True
    assert hit_b["head"] is not None
    assert hit_b["head"]["properties"]["id"] == "cyc_a"

    # …and cyc_a itself is the head: not superseded, a real (non-null) verdict.
    r2 = run(call("recall", {"text": "claim A"}))
    hit_a = [h for h in r2["results"] if h["node"]["properties"]["id"] == "cyc_a"][0]
    assert hit_a["superseded"] is False


def test_a_longer_cycle_is_also_rejected(call, run):
    """The guard follows the WHOLE reachability chain, not just one hop:
    a→b→c, then c→a would close a 3-node cycle and is refused."""
    run(call("remember", {"content": "v-a", "id": "lc_a"}))
    run(call("remember", {"content": "v-b", "id": "lc_b"}))
    run(call("remember", {"content": "v-c", "id": "lc_c"}))
    run(call("relate", {"from_id": "lc_a", "to_id": "lc_b", "relation": "supersedes"}))
    run(call("relate", {"from_id": "lc_b", "to_id": "lc_c", "relation": "supersedes"}))

    closing = run(call("relate", {"from_id": "lc_c", "to_id": "lc_a", "relation": "supersedes"}))
    assert closing["ok"] is False
    assert closing["code"] == "SUPERSEDE_CYCLE"


def test_non_supersedes_relations_may_still_form_cycles(call, run):
    """The guard is scoped to `supersedes` (the direction-critical, head-bearing
    relation). `references`/`supports`/… legitimately can be mutual, and the
    guard must not break them."""
    run(call("remember", {"content": "paper X", "id": "ref_x"}))
    run(call("remember", {"content": "paper Y", "id": "ref_y"}))
    xy = run(call("relate", {"from_id": "ref_x", "to_id": "ref_y", "relation": "references"}))
    yx = run(call("relate", {"from_id": "ref_y", "to_id": "ref_x", "relation": "references"}))
    assert xy["ok"] is True
    assert yx["ok"] is True


def test_fork_resolves_the_same_head_across_repeated_runs(call, run):
    """new_a→old←new_b is a FORK: two valid heads at equal path length. Head
    selection must be DETERMINISTIC (stable tie-break), or the demo shows a
    different 'current truth' on reload."""
    run(call("remember", {"content": "the original figure", "id": "fk_old"}))
    run(call("remember", {"content": "revision A", "id": "fk_new_a"}))
    run(call("remember", {"content": "revision B", "id": "fk_new_b"}))
    run(call("relate", {"from_id": "fk_new_a", "to_id": "fk_old", "relation": "supersedes"}))
    run(call("relate", {"from_id": "fk_new_b", "to_id": "fk_old", "relation": "supersedes"}))

    heads = set()
    for _ in range(6):
        r = run(call("recall", {"text": "the original figure"}))
        hit = [h for h in r["results"] if h["node"]["properties"]["id"] == "fk_old"][0]
        assert hit["superseded"] is True
        heads.add(hit["head"]["properties"]["id"])
    assert len(heads) == 1, "fork head selection is nondeterministic: {0}".format(heads)


def test_linear_chain_still_resolves_to_its_head(call, run):
    """Regression guard: the acyclicity check must not break the normal case —
    a legitimate linear chain still resolves to its deepest head."""
    v1 = run(call("remember", {"content": "meeting Monday", "id": "ln_1"}))
    v2 = run(call("remember", {"content": "meeting Tuesday", "id": "ln_2"}))
    v3 = run(call("remember", {"content": "meeting Wednesday", "id": "ln_3"}))
    run(call("relate", {"from_id": v2["id"], "to_id": v1["id"], "relation": "supersedes"}))
    run(call("relate", {"from_id": v3["id"], "to_id": v2["id"], "relation": "supersedes"}))

    r = run(call("recall", {"text": "meeting Monday"}))
    hit = [h for h in r["results"] if h["node"]["properties"]["id"] == "ln_1"][0]
    assert hit["superseded"] is True
    assert hit["superseded_by"] == "ln_3"
    assert hit["supersede_depth"] == 2
