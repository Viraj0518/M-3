"""The verified-identity layer.

Two properties matter and both are tested against real signatures, not mocks:

  1. A VALID token produces the selector it claims — otherwise auth is
     decorative.
  2. Everything else produces ``None`` — expired, wrong issuer, wrong audience,
     wrong key, tampered payload, absent header. A verifier that accepts any
     of those is worse than no verifier, because the logs then say "verified".

The suite generates its own Ed25519 keypair and serves it as a JWKS, so it
needs neither the auth service nor a network.
"""

from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.bridge import auth as authmod  # noqa: E402
from app.bridge import identity  # noqa: E402

cryptography = pytest.importorskip(
    "cryptography",
    reason="signature verification needs `cryptography` (a transitive of httpx's stack)",
)
from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa: E402

ISSUER = "http://127.0.0.1:8932/api/auth"
AUDIENCE = "http://127.0.0.1:8931/mcp"


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@pytest.fixture()
def keypair():
    private = ed25519.Ed25519PrivateKey.generate()
    public = private.public_key()
    from cryptography.hazmat.primitives import serialization

    raw = public.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    jwk = {"kty": "OKP", "crv": "Ed25519", "kid": "test-key", "alg": "EdDSA", "x": b64u(raw)}
    return private, {"keys": [jwk]}


def mint(private, *, kid="test-key", **overrides) -> str:
    header = {"alg": "EdDSA", "typ": "JWT", "kid": kid}
    now = int(time.time())
    payload = {
        "sub": "usr_abc",
        "azp": "doordash-cli",
        "iss": ISSUER,
        "aud": [AUDIENCE],
        "iat": now,
        "exp": now + 3600,
    }
    payload.update(overrides)

    signing_input = "{0}.{1}".format(
        b64u(json.dumps(header).encode()),
        b64u(json.dumps(payload).encode()),
    ).encode("ascii")
    signature = private.sign(signing_input)
    return "{0}.{1}".format(signing_input.decode("ascii"), b64u(signature))


@pytest.fixture(autouse=True)
def configured(monkeypatch, keypair):
    """Point the module at an in-process JWKS and a known issuer/audience."""
    _private, jwks = keypair
    monkeypatch.setattr(authmod, "JWKS_URL", "http://auth.invalid/jwks")
    monkeypatch.setattr(authmod, "ISSUER", ISSUER)
    monkeypatch.setattr(authmod, "AUDIENCE", AUDIENCE)
    monkeypatch.setattr(authmod, "_fetch_jwks", lambda force=False: jwks)
    authmod.reset_cache()
    yield
    authmod.reset_cache()


# ── the happy path ──────────────────────────────────────────────────────────


def test_a_valid_token_yields_its_client_as_the_selector(keypair):
    private, _ = keypair
    headers = {"Authorization": "Bearer " + mint(private)}
    assert authmod.resolve_verified(headers) == "doordash-cli"


def test_claims_are_available_to_a_handler(keypair):
    private, _ = keypair
    claims = authmod.claims_for({"authorization": "Bearer " + mint(private)})
    assert claims["sub"] == "usr_abc"
    assert claims["azp"] == "doordash-cli"


def test_header_lookup_is_case_insensitive(keypair):
    private, _ = keypair
    token = mint(private)
    assert authmod.bearer_from({"AUTHORIZATION": f"Bearer {token}"}) == token
    assert authmod.bearer_from({"authorization": f"bearer {token}"}) == token


# ── everything that must NOT authenticate ───────────────────────────────────


def test_no_header_is_not_an_identity():
    assert authmod.resolve_verified({}) is None
    assert authmod.resolve_verified(None) is None


def test_a_non_bearer_header_is_ignored():
    assert authmod.bearer_from({"authorization": "Basic abc123"}) is None


def test_an_expired_token_is_refused(keypair):
    private, _ = keypair
    stale = mint(private, exp=int(time.time()) - 10_000)
    assert authmod.resolve_verified({"authorization": f"Bearer {stale}"}) is None


def test_a_token_from_another_issuer_is_refused(keypair):
    private, _ = keypair
    other = mint(private, iss="http://evil.invalid/api/auth")
    assert authmod.resolve_verified({"authorization": f"Bearer {other}"}) is None


def test_a_token_for_another_audience_is_refused(keypair):
    private, _ = keypair
    other = mint(private, aud=["http://someone-else.invalid"])
    assert authmod.resolve_verified({"authorization": f"Bearer {other}"}) is None


def test_a_token_signed_by_a_different_key_is_refused(keypair):
    _, _jwks = keypair
    attacker = ed25519.Ed25519PrivateKey.generate()
    forged = mint(attacker)
    assert authmod.resolve_verified({"authorization": f"Bearer {forged}"}) is None


def test_an_unknown_kid_is_refused(keypair):
    private, _ = keypair
    token = mint(private, kid="not-a-key-we-know")
    assert authmod.resolve_verified({"authorization": f"Bearer {token}"}) is None


def test_a_tampered_payload_is_refused(keypair):
    private, _ = keypair
    head, payload, sig = mint(private).split(".")
    swapped = json.loads(authmod._b64url(payload))
    swapped["azp"] = "commander"          # privilege escalation attempt
    forged = "{0}.{1}.{2}".format(head, b64u(json.dumps(swapped).encode()), sig)
    assert authmod.resolve_verified({"authorization": f"Bearer {forged}"}) is None


@pytest.mark.parametrize("garbage", ["", "not-a-jwt", "a.b", "a.b.c.d", "...", "a.b.c"])
def test_malformed_tokens_are_refused(garbage):
    assert authmod.resolve_verified({"authorization": f"Bearer {garbage}"}) is None


# ── inertness, so enabling this cannot break the existing demo ──────────────


def test_the_module_is_inert_without_a_jwks_url(monkeypatch, keypair):
    private, _ = keypair
    monkeypatch.setattr(authmod, "JWKS_URL", "")
    assert authmod.enabled() is False
    assert authmod.resolve_verified({"authorization": "Bearer " + mint(private)}) is None


def test_a_refused_token_falls_through_to_the_existing_ladder(keypair):
    """The dispatcher's `verified or identity.resolve(...)` contract.

    A bad token must not strip a caller of the identity it would have had
    without one — it simply fails to grant a better one.
    """
    private, _ = keypair
    forged = mint(ed25519.Ed25519PrivateKey.generate())
    headers = {"authorization": f"Bearer {forged}", "x-palimpsest-agent": "analyst"}

    verified = authmod.resolve_verified(headers)
    assert verified is None

    session_id, header_selector = identity.selector_from_headers(headers)
    fallback = identity.resolve(session_id, header_selector=header_selector)
    assert fallback == "analyst"


def test_a_verified_token_outranks_a_header_claim(keypair):
    """The whole point: a header cannot impersonate a verified client."""
    private, _ = keypair
    headers = {
        "authorization": "Bearer " + mint(private),
        "x-palimpsest-agent": "commander",   # a claim the caller made up
    }

    verified = authmod.resolve_verified(headers)
    session_id, header_selector = identity.selector_from_headers(headers)
    resolved = verified or identity.resolve(session_id, header_selector=header_selector)

    assert resolved == "doordash-cli"
    assert resolved != "commander"


def test_a_dead_auth_service_degrades_rather_than_raising(monkeypatch, keypair):
    private, _ = keypair
    monkeypatch.setattr(authmod, "_fetch_jwks", lambda force=False: {})
    assert authmod.resolve_verified({"authorization": "Bearer " + mint(private)}) is None
