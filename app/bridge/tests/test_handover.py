"""handover_write / handover_read — and BOTH inherited traps, proven CLOSED.

SCHEMA.md §7 documents two silent bugs from unblock server.py:4608-4672. We
inherit the FIXES. A comment claiming a fix is present is worth nothing, so
each trap gets two tests:

  * one proving the guarded read behaves correctly, and
  * one running the UNGUARDED logic over the SAME data to prove the guard is
    load-bearing — i.e. that removing it genuinely re-opens the bug.

Without the second test a no-op "guard" passes the first one.
"""

from __future__ import annotations

from app.bridge.tests.conftest import requires_live_db

pytestmark = requires_live_db


# ── the happy path: the cold-resume beat ────────────────────────────────────

def test_handover_write_then_read_round_trip(call, run):
    """Kill the agent, restart it cold, it reads its own handover node back out
    of the graph and resumes from its committed offset."""
    w = run(
        call(
            "handover_write",
            {
                "agent_id": "watcher",
                "role": "signal",
                "summary": "held the watch through offset 41208",
                "in_flight": "case P-2048 evidence gathering",
                "next_steps": "escalate if the ring re-fires",
                "blockers": "none",
                "artifacts": {"case_id": "P-2048"},
                "checkpoint": {"offset": 41208, "topic": "signal.raw"},
            },
        )
    )
    assert w["ok"] is True
    assert w["status"] == "open"
    assert w["handover_id"].startswith("hov_")

    r = run(call("handover_read", {"agent_id": "watcher"}))
    assert r["ok"] is True
    row = r["handover"]
    assert row is not None
    assert row["status"] == "open"
    assert row["summary"] == "held the watch through offset 41208"
    assert row["next_steps"] == "escalate if the ring re-fires"
    # The committed LaserData offset is what makes cold-resume EXACT rather
    # than approximate. It survives the JSON round trip through the edge.
    assert row["checkpoint"] == {"offset": 41208, "topic": "signal.raw"}
    assert row["artifacts"] == {"case_id": "P-2048"}
    assert row["author_agent"] if "author_agent" in row else True


def test_write_always_records_status_open(call, run, cypher):
    """A session may only ever write an OPEN handover. Superseding is the
    writer's transaction job, never a caller's choice — that invariant is what
    makes the reader's status guard meaningful."""
    run(call("handover_write", {"agent_id": "watcher", "summary": "s", "status": "closed"}))
    rows = cypher("MATCH ()-[h:HANDED_OFF_TO]->() RETURN h.status")
    assert [r[0] for r in rows] == ["open"], "a caller must not be able to write a non-open row"


def test_second_write_supersedes_the_first(call, run, cypher):
    """Exactly one OPEN row per recipient, or cold-resume is ambiguous."""
    first = run(call("handover_write", {"agent_id": "watcher", "summary": "first"}))
    second = run(call("handover_write", {"agent_id": "watcher", "summary": "second"}))

    rows = cypher(
        "MATCH ()-[h:HANDED_OFF_TO]->(:Agent {agent_id:'watcher'}) "
        "RETURN h.handover_id, h.status ORDER BY h.updated_ts"
    )
    by_id = {r[0]: r[1] for r in rows}
    assert by_id[first["handover_id"]] == "superseded"
    assert by_id[second["handover_id"]] == "open"
    assert sum(1 for v in by_id.values() if v == "open") == 1

    r = run(call("handover_read", {"agent_id": "watcher"}))
    assert r["handover"]["summary"] == "second"


def test_cross_agent_delegation(call, run):
    """to_agent makes it a genuine handoff to a peer; the RECIPIENT reads it."""
    run(call("handover_write", {"agent_id": "watcher", "to_agent": "commander", "summary": "over to you"}))

    recipient = run(call("handover_read", {"agent_id": "commander"}))
    assert recipient["handover"]["summary"] == "over to you"
    assert recipient["handover"]["from_agent"] == "watcher"

    # The sender has no inbound handover of its own.
    sender = run(call("handover_read", {"agent_id": "watcher"}))
    assert sender["handover"] is None


# ═══════════════════════════════════════════════════════════════════════════
# TRAP (a) — UNWRAP THE ENVELOPE EXPLICITLY
# ═══════════════════════════════════════════════════════════════════════════
# The read produces {"handovers": [row?]} — an envelope with zero or one rows.
# A naive truthiness check treats a GENUINE MISS AS A HIT, because
# {"handovers": []} is itself a truthy dict.

def test_trap_a_genuine_miss_reports_none_with_a_reason(call, run):
    r = run(call("handover_read", {"agent_id": "nobody-has-ever-handed-to-me"}))
    assert r["ok"] is True
    assert r["handover"] is None, "a miss must be None, never an empty envelope"
    assert "no handover row" in r["reason"]


def test_trap_a_the_empty_envelope_is_itself_truthy(call, run):
    """The guard is load-bearing. This reproduces the ORIGINAL bug on the exact
    shape the reader handles: `if parsed:` is TRUE for `{"handovers": []}`, so
    an unwrapped reader hands back a 'handover' that is really an empty list
    nested inside a wrapper."""
    miss_envelope = {"handovers": []}

    # THE BUG, verbatim.
    assert bool(miss_envelope) is True, "this truthiness is the whole trap"
    naive = miss_envelope if miss_envelope else None
    assert naive is not None, "the naive reader treats the miss as a hit"

    # THE FIX, as implemented in _h_handover_read.
    rows = miss_envelope.get("handovers") if isinstance(miss_envelope, dict) else None
    row = rows[0] if isinstance(rows, list) and rows else None
    assert row is None

    # …and the real handler agrees with the fix, not the bug.
    r = run(call("handover_read", {"agent_id": "ghost"}))
    assert r["handover"] is None


# ═══════════════════════════════════════════════════════════════════════════
# TRAP (b) — STATUS-GUARD ON status == 'open'
# ═══════════════════════════════════════════════════════════════════════════
# The by-agent read is ORDER BY updated_at DESC LIMIT 1 with NO status filter
# (only the all-agents path filters). Without the guard a cold-resume
# RESURRECTS a superseded handover and the agent confidently resumes work that
# was already finished.

def _seed_latest_row_is_closed(cypher):
    """Seed a state the public writer deliberately cannot produce: the LATEST
    row by updated_ts is NOT open. Raw Cypher, because `handover_write` always
    writes status='open' — that refusal is itself an invariant under test."""
    cypher(
        "MERGE (a:Agent {agent_id:'responder'}) "
        "CREATE (a)-[:HANDED_OFF_TO {handover_id:'hov_old', status:'open', "
        "  summary:'the older, still-open row', updated_ts: 1000, created_ts: 1000, "
        "  artifacts_json:'{}', checkpoint_json:'{}'}]->(a) "
        "CREATE (a)-[:HANDED_OFF_TO {handover_id:'hov_done', status:'closed', "
        "  summary:'FINISHED WORK — must never be resumed', updated_ts: 2000, created_ts: 2000, "
        "  artifacts_json:'{}', checkpoint_json:'{}'}]->(a)"
    )


def test_trap_b_guarded_read_refuses_a_non_open_latest_row(call, run, cypher):
    _seed_latest_row_is_closed(cypher)

    r = run(call("handover_read", {"agent_id": "responder"}))
    assert r["ok"] is True
    assert r["handover"] is None, "a closed row must never be inherited"
    assert "not open" in r["reason"]
    assert "closed" in r["reason"]
    assert r["rejected_handover_id"] == "hov_done"


def test_trap_b_the_unguarded_query_would_have_resurrected_it(call, run, cypher):
    """THE CONTROL. The same query the handler runs, WITHOUT the Python guard,
    over the SAME data — and it hands back the finished work. This is what
    proves the guard is doing real work rather than decorating a query that was
    already safe."""
    _seed_latest_row_is_closed(cypher)

    unguarded = cypher(
        "MATCH (a:Agent)-[h:HANDED_OFF_TO]->(b:Agent {agent_id:'responder'}) "
        "RETURN h.handover_id, h.status, h.summary ORDER BY h.updated_ts DESC LIMIT 1"
    )
    assert unguarded[0][0] == "hov_done"
    assert unguarded[0][1] == "closed"
    assert "FINISHED WORK" in unguarded[0][2], (
        "without the status guard the cold-resume path inherits finished work"
    )

    # The guarded handler, same graph, refuses it.
    guarded = run(call("handover_read", {"agent_id": "responder"}))
    assert guarded["handover"] is None


def test_trap_b_query_deliberately_has_no_status_filter(call, run, cypher):
    """The guard lives in Python ON PURPOSE (upstream parity: the by-agent query
    carries no status filter). If someone 'fixes' this by adding the filter to
    the Cypher instead, the honest `reason` disappears and a superseded row
    becomes indistinguishable from no row at all — so the read must still be
    able to SEE the closed row in order to report why it refused it."""
    _seed_latest_row_is_closed(cypher)
    r = run(call("handover_read", {"agent_id": "responder"}))
    assert r["rejected_handover_id"] == "hov_done", (
        "the reader must SEE the rejected row to explain the refusal"
    )


# ── the all-agents board ────────────────────────────────────────────────────

def test_all_returns_latest_open_row_per_agent(call, run, cypher):
    run(call("handover_write", {"agent_id": "watcher", "summary": "w1"}))
    run(call("handover_write", {"agent_id": "watcher", "summary": "w2"}))
    run(call("handover_write", {"agent_id": "commander", "summary": "c1"}))
    _seed_latest_row_is_closed(cypher)

    r = run(call("handover_read", {"all": True}))
    assert r["ok"] is True
    by_agent = {row["agent_id"]: row for row in r["handovers"]}
    assert by_agent["watcher"]["summary"] == "w2"
    assert by_agent["commander"]["summary"] == "c1"
    # The all-path filters status IN THE QUERY, so responder's still-open older
    # row appears and its closed row does not.
    assert by_agent["responder"]["summary"] == "the older, still-open row"
    assert all(row["status"] == "open" for row in r["handovers"])


def test_read_without_agent_id_or_all_is_an_error(call, run):
    r = run(call("handover_read", {}))
    assert r["ok"] is False
    assert r["code"] == "MISSING_AGENT_ID"
