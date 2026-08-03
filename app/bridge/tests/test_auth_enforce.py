"""Enforcement mode — the half that makes verification *required*.

#2 added a way to be verified. On its own that does not stop the forgery it
was written to stop: omit the Authorization header entirely and identity.py's
ladder still honours `x-palimpsest-agent`, so a caller can still name itself
the commander and the graph records it as fact.

Two properties are tested here, and the second matters as much as the first:

  1. Under enforcement, an unverified caller CANNOT assert an identity on a
     write — it is either refused (`require`) or landed as `unbound`
     (`attribute`).

  2. With enforcement OFF, behaviour is byte-for-byte what it is today. The
     judge-facing bridge at `palimpsest-bridge.fly.dev/mcp` is deliberately
     unauthenticated, and a security feature that silently 403s a public demo
     is a worse outcome for this project than the forgery it prevents.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.bridge import auth as authmod  # noqa: E402
from app.bridge import identity  # noqa: E402

VERIFIED = "doordash-cli"
FORGED_HEADERS = {"x-palimpsest-agent": "commander"}


def configure(monkeypatch, *, enforce, jwks="http://auth.invalid/jwks"):
    monkeypatch.setattr(authmod, "ENFORCE", enforce)
    monkeypatch.setattr(authmod, "JWKS_URL", jwks)


def resolved(verb, verified, headers):
    """Reproduce the dispatcher's contract in one place.

    Mirrors `server.py`'s call site exactly; the dispatcher is thin on purpose
    so the policy can be tested without standing up a transport.
    """
    if authmod.refuse(verb, verified) is not None:
        return "REFUSED"
    session_id, header_selector = identity.selector_from_headers(headers)
    return verified or identity.resolve(
        session_id,
        header_selector=None if authmod.suppress_header(verb, verified) else header_selector,
    )


# ── the hole this closes ────────────────────────────────────────────────────


def test_without_enforcement_a_header_still_forges_an_identity(monkeypatch):
    """The status quo, asserted so the fix has something to be measured against."""
    configure(monkeypatch, enforce="off")
    assert resolved("remember", None, FORGED_HEADERS) == "commander"


def test_attribute_mode_refuses_to_let_a_write_claim_a_name(monkeypatch):
    configure(monkeypatch, enforce="attribute")
    assert resolved("remember", None, FORGED_HEADERS) == identity.UNBOUND_AGENT


def test_require_mode_refuses_the_write_outright(monkeypatch):
    configure(monkeypatch, enforce="require")
    assert resolved("remember", None, FORGED_HEADERS) == "REFUSED"


@pytest.mark.parametrize("verb", sorted(authmod.WRITE_VERBS))
def test_every_write_verb_is_covered(monkeypatch, verb):
    configure(monkeypatch, enforce="require")
    assert resolved(verb, None, FORGED_HEADERS) == "REFUSED"


def test_act_is_gated_because_it_fires_real_side_effects(monkeypatch):
    """`act` posts to Discord and opens GitHub issues. If any verb must be
    covered, it is this one."""
    assert "act" in authmod.WRITE_VERBS
    configure(monkeypatch, enforce="require")
    assert resolved("act", None, FORGED_HEADERS) == "REFUSED"


# ── a verified caller is unaffected ─────────────────────────────────────────


@pytest.mark.parametrize("mode", ["off", "attribute", "require"])
def test_a_verified_caller_writes_in_every_mode(monkeypatch, mode):
    configure(monkeypatch, enforce=mode)
    assert resolved("remember", VERIFIED, FORGED_HEADERS) == VERIFIED


def test_a_verified_token_still_beats_a_header_claim(monkeypatch):
    configure(monkeypatch, enforce="attribute")
    got = resolved("remember", VERIFIED, FORGED_HEADERS)
    assert got == VERIFIED and got != "commander"


# ── reads are never gated ───────────────────────────────────────────────────


@pytest.mark.parametrize("verb", ["recall", "graph", "ring", "ask", "stream_tail", "handover_read"])
@pytest.mark.parametrize("mode", ["attribute", "require"])
def test_reads_are_never_refused(monkeypatch, verb, mode):
    """Gating `graph` or `recall` breaks the projector UI, and reading is not
    the operation that forges attribution."""
    configure(monkeypatch, enforce=mode)
    assert resolved(verb, None, {}) != "REFUSED"


def test_a_read_may_still_use_the_header(monkeypatch):
    configure(monkeypatch, enforce="require")
    assert resolved("graph", None, FORGED_HEADERS) == "commander"


# ── the public demo must not regress ────────────────────────────────────────


def test_enforcement_is_off_by_default():
    import importlib, os
    for key in ("AUTH_ENFORCE", "AUTH_JWKS_URL"):
        os.environ.pop(key, None)
    fresh = importlib.reload(authmod)
    assert fresh.ENFORCE == "off"
    assert fresh.enforcing() is False
    assert fresh.refuse("remember", None) is None
    assert fresh.suppress_header("remember", None) is False


def test_enforcement_without_a_verifier_cannot_lock_everyone_out(monkeypatch):
    """`require` with no JWKS URL would refuse every write with no way to
    satisfy it — a hostage situation, not a security posture."""
    configure(monkeypatch, enforce="require", jwks="")
    assert authmod.enforcing() is False
    assert resolved("remember", None, FORGED_HEADERS) == "commander"


@pytest.mark.parametrize("mode", ["off", "", "nonsense", "REQUIRE_TYPO"])
def test_an_unrecognised_mode_fails_open_not_shut(monkeypatch, mode):
    """A typo in an env var must not take the public bridge down."""
    configure(monkeypatch, enforce=mode)
    assert authmod.enforcing() is False
    assert resolved("remember", None, FORGED_HEADERS) == "commander"


def test_the_refusal_says_how_to_satisfy_it(monkeypatch):
    configure(monkeypatch, enforce="require")
    code, message = authmod.refuse("act", None)
    assert code == "AUTH_REQUIRED"
    assert "bearer token" in message
    assert "Reads are not gated" in message
