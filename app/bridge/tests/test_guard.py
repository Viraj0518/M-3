"""The public-exposure guard.

NO DATABASE. Every assertion here is about the dispatcher's refusal decision,
which happens BEFORE any handler runs, so this file is the one part of the
bridge suite that passes on a bare interpreter with FalkorDB down.

The load-bearing test is :func:`test_every_table_verb_is_classified`. The guard
classifies by ALLOWLIST — an unclassified verb is treated as a write — and that
default is only safe if someone notices when a new verb lands. This test is the
someone.
"""

from __future__ import annotations

import asyncio

import pytest

from app.bridge import guard
from app.bridge import server as bridge


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every test starts from the DEFAULT posture (guard off).

    Without this, one test setting the variable would silently arm the guard for
    every test after it — and a guard test suite that leaks its own state is a
    guard test suite that cannot be trusted about the default.
    """
    monkeypatch.delenv(guard.ENV_VAR, raising=False)


def _dispatch(verb: str, args: dict | None = None, *, surface: str = "rest") -> dict:
    return asyncio.run(bridge.dispatch(verb, args or {}, surface=surface))


# ── the default is genuinely inert ──────────────────────────────────────────

def test_default_is_off():
    assert guard.mode() == guard.MODE_OFF
    assert guard.enabled() is False
    assert guard.refusal("remember") is None
    assert guard.refusal("recall") is None


def test_empty_string_is_off_not_closed(monkeypatch):
    """An exported-but-empty variable is how shell scripts spell "unset"."""
    monkeypatch.setenv(guard.ENV_VAR, "")
    assert guard.mode() == guard.MODE_OFF
    assert guard.refusal("remember") is None


# ── readonly ────────────────────────────────────────────────────────────────

def test_readonly_refuses_writes(monkeypatch):
    monkeypatch.setenv(guard.ENV_VAR, "readonly")
    refused = guard.refusal("remember")
    assert refused is not None
    assert refused["ok"] is False
    assert refused["isError"] is True
    assert refused["code"] == "PUBLIC_READONLY"


def test_readonly_allows_reads(monkeypatch):
    monkeypatch.setenv(guard.ENV_VAR, "readonly")
    for verb in guard.READ_VERBS:
        assert guard.refusal(verb) is None, verb


def test_readonly_refuses_every_write_verb(monkeypatch):
    monkeypatch.setenv(guard.ENV_VAR, "readonly")
    for verb in guard.WRITE_VERBS:
        refused = guard.refusal(verb)
        assert refused is not None and refused["code"] == "PUBLIC_READONLY", verb


def test_case_and_whitespace_tolerant(monkeypatch):
    monkeypatch.setenv(guard.ENV_VAR, "  ReadOnly  ")
    assert guard.mode() == guard.MODE_READONLY


# ── closed ──────────────────────────────────────────────────────────────────

def test_closed_refuses_reads_too(monkeypatch):
    monkeypatch.setenv(guard.ENV_VAR, "closed")
    for verb in ("recall", "graph", "remember"):
        refused = guard.refusal(verb)
        assert refused is not None and refused["code"] == "PUBLIC_CLOSED", verb


# ── fail-safe ───────────────────────────────────────────────────────────────

def test_typo_fails_closed_not_open(monkeypatch):
    """`read-only` with a hyphen must not silently mean `off`.

    This is the entire reason the unrecognised-value branch exists: a deployment
    config typo that leaves an unauthenticated write surface open is worse than
    one that takes the endpoint down, because nobody notices the first one.
    """
    monkeypatch.setenv(guard.ENV_VAR, "read-only")
    assert guard.mode() == guard.MODE_CLOSED
    assert guard.refusal("recall")["code"] == "PUBLIC_CLOSED"


def test_unknown_verb_is_treated_as_a_write(monkeypatch):
    monkeypatch.setenv(guard.ENV_VAR, "readonly")
    refused = guard.refusal("some_verb_added_next_week")
    assert refused is not None and refused["code"] == "PUBLIC_READONLY"


# ── the classification must not drift away from the table ───────────────────

def test_every_table_verb_is_classified():
    """Every verb in `_VERB_DISPATCH` is in exactly one of the two sets.

    A new verb that nobody classified still fails SAFE (it is refused in
    readonly mode, because READ_VERBS is an allowlist) — but a read verb
    wrongly refused breaks a demo, so the drift should be caught here rather
    than on stage.
    """
    table = set(bridge.VERBS)
    classified = guard.READ_VERBS | guard.WRITE_VERBS
    assert table - classified == set(), (
        "verbs in the dispatch table with no guard classification: "
        "{0} — add each to guard.READ_VERBS or guard.WRITE_VERBS".format(
            sorted(table - classified)
        )
    )
    assert classified - table == set(), (
        "guard classifies verbs that are not in the dispatch table: "
        "{0}".format(sorted(classified - table))
    )
    assert guard.READ_VERBS & guard.WRITE_VERBS == set(), (
        "a verb cannot be both a read and a write"
    )


# ── the dispatcher actually consults it (all surfaces at once) ──────────────

def test_dispatch_refuses_a_write_when_readonly(monkeypatch):
    """The chokepoint, not just the predicate.

    `dispatch` is the ONE function REST, MCP, the OpenAPI/Guild integration and
    the CLI all enter through, so asserting here asserts all four surfaces.
    """
    monkeypatch.setenv(guard.ENV_VAR, "readonly")
    out = _dispatch("remember", {"content": "should never be written"})
    assert out["code"] == "PUBLIC_READONLY"
    # Refused BEFORE the handler: no graph fields on the envelope at all.
    assert "id" not in out and "node" not in out


def test_dispatch_refuses_the_prefixed_mcp_tool_name(monkeypatch):
    """The MCP surface calls in as `palimpsest_remember`, not `remember`."""
    monkeypatch.setenv(guard.ENV_VAR, "readonly")
    out = _dispatch(bridge.tool_name("remember"), {"content": "x"})
    assert out["code"] == "PUBLIC_READONLY"


def test_dispatch_still_reports_unknown_tools_first(monkeypatch):
    """An unknown tool is UNKNOWN_TOOL even under the guard.

    Ordering matters for the error a client sees: `PUBLIC_READONLY` on a
    misspelled tool name would send them hunting for a permissions problem they
    do not have.
    """
    monkeypatch.setenv(guard.ENV_VAR, "closed")
    out = _dispatch("no_such_verb_at_all")
    assert out["code"] == "UNKNOWN_TOOL"


def test_describe_is_secret_free():
    described = guard.describe()
    assert set(described) == {
        "mode",
        "enforcing",
        "env",
        "writes_refused",
        "reads_refused",
        "exempt_surfaces",
    }
    assert described["mode"] == guard.MODE_OFF
    assert described["enforcing"] is False


# ── local surfaces are exempt; network surfaces are not ─────────────────────

def test_cli_surface_is_exempt(monkeypatch):
    """`surface="cli"` must survive the guard.

    `seed_demo.py` and `realtime/ablation.py` both drive the router with
    surface="cli", and `fly/entrypoint.sh` runs seed_demo AT BOOT. If the guard
    blocked it, a guarded deployment would come up with an empty warm graph —
    a dead demo — which is how a safety feature gets reverted instead of used.
    """
    monkeypatch.setenv(guard.ENV_VAR, "closed")
    assert guard.refusal("remember", surface="cli") is None
    assert guard.refusal("ablation", surface="cli") is None


def test_network_surfaces_are_not_exempt(monkeypatch):
    monkeypatch.setenv(guard.ENV_VAR, "readonly")
    for surface in ("rest", "mcp"):
        refused = guard.refusal("remember", surface=surface)
        assert refused is not None and refused["code"] == "PUBLIC_READONLY", surface


def test_missing_surface_is_guarded(monkeypatch):
    """No surface is NOT the same as a local surface.

    A caller that forgets the argument must get the guarded behaviour; the
    exemption has to be opted into explicitly or it is a hole.
    """
    monkeypatch.setenv(guard.ENV_VAR, "readonly")
    assert guard.refusal("remember") is not None
    assert guard.refusal("remember", surface=None) is not None
    assert guard.refusal("remember", surface="something-new") is not None


def test_dispatch_honours_the_cli_exemption(monkeypatch):
    monkeypatch.setenv(guard.ENV_VAR, "readonly")
    out = _dispatch("remember", {"content": "x"}, surface="cli")
    assert out.get("code") != "PUBLIC_READONLY"


def test_ablation_is_classified_as_a_write():
    """It looks like a read and it is not.

    `realtime.ablation.run_ablation` runs with reset=True, which drop_graph()s
    and re-seeds palimpsest_ablation_warm/_cold on EVERY call. As a "read" on a
    public URL that is an anonymous DoS lever.
    """
    assert "ablation" in guard.WRITE_VERBS
    assert "ablation" not in guard.READ_VERBS
