"""Verified agent identity for the bridge — the missing half of identity.py.

``identity.py`` is deliberately auth-free: it is a ``session-id -> selector``
map, and its own docstring says so. That gives ATTRIBUTION (every write carries
``author_agent``) but not AUTHENTICATION — its precedence ladder ends at the
``x-palimpsest-agent`` header, which any caller can set to any value. A client
can therefore claim to be the commander and the graph will record it as fact.

This module closes that hole WITHOUT touching identity.py. It verifies a bearer
JWT minted by the auth service (``auth/``, OAuth 2.1 + PKCE) against that
service's JWKS and returns a selector that is CRYPTOGRAPHICALLY EARNED. The
dispatcher tries this first; when there is no token, or the token is bad,
identity.py's existing ladder runs exactly as before.

DESIGN NOTES
────────────────────────────────────────────────────────────────────────────
* **Fail closed on a BAD token, open on NO token.** A malformed or expired
  token yields ``None`` and the request falls through to the unauthenticated
  ladder — it does NOT get to keep a header-claimed identity, because
  :func:`resolve_verified` is consulted BEFORE the header and a rejected token
  never produces a selector. A request with no token at all is unchanged from
  today, so turning this module on cannot break the existing demo.

* **Offline by default.** With no ``AUTH_JWKS_URL`` configured the module is
  inert and every call returns ``None`` in ~1 microsecond. The bridge has no
  new runtime dependency and no new failure mode unless auth is deliberately
  switched on.

* **JWKS is cached with a TTL** and refetched at most once per miss, so a key
  rotation heals within one request rather than hammering the auth service on
  every call — and a dead auth service degrades to "no verified identity"
  rather than blocking the dispatcher.

* **stdlib + httpx only.** ``httpx`` is already a declared bridge dependency.
  Signature verification uses ``cryptography`` when present (EdDSA/RS256); with
  neither, the module refuses to verify rather than trusting an unchecked
  token — see :func:`_verify_signature`.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from typing import Any, Dict, Mapping, Optional, Tuple

# ── configuration ───────────────────────────────────────────────────────────

#: JWKS endpoint of the auth service. UNSET == this module is inert.
JWKS_URL: str = os.environ.get("AUTH_JWKS_URL", "").strip()

#: Expected `iss`. Empty == do not check (useful only in local dev).
ISSUER: str = os.environ.get("AUTH_ISSUER", "").strip()

#: Expected `aud`. Empty == do not check.
AUDIENCE: str = os.environ.get("AUTH_AUDIENCE", "").strip()

#: Seconds a fetched JWKS stays warm.
JWKS_TTL_S: float = float(os.environ.get("AUTH_JWKS_TTL_S", "300"))

#: Seconds of tolerance on exp/nbf, for modest clock skew between processes.
LEEWAY_S: int = int(os.environ.get("AUTH_LEEWAY_S", "60"))

#: How a verified subject becomes an agent selector. `azp` is the OAuth client
#: id — which is exactly the granularity identity.py wants, because two clients
#: acting for one human are two agents.
SELECTOR_CLAIM: str = os.environ.get("AUTH_SELECTOR_CLAIM", "azp")

AUTH_HEADER = "authorization"


def enabled() -> bool:
    """True when a JWKS URL is configured. Everything else is a no-op if not."""
    return bool(JWKS_URL)


# ── JWKS cache ──────────────────────────────────────────────────────────────

_lock = threading.Lock()
_jwks: Dict[str, Any] = {}
_jwks_fetched_at: float = 0.0


def _fetch_jwks(force: bool = False) -> Dict[str, Any]:
    """The auth service's public keys, cached for :data:`JWKS_TTL_S`."""
    global _jwks, _jwks_fetched_at

    with _lock:
        fresh = (time.time() - _jwks_fetched_at) < JWKS_TTL_S
        if _jwks and fresh and not force:
            return _jwks

    try:
        import httpx  # declared in app/bridge/requirements.txt

        response = httpx.get(JWKS_URL, timeout=2.0)
        response.raise_for_status()
        keys = response.json()
    except Exception:  # noqa: BLE001 — a dead auth service must not 500 the bridge
        return _jwks or {}

    with _lock:
        _jwks = keys if isinstance(keys, dict) else {}
        _jwks_fetched_at = time.time()
        return _jwks


def reset_cache() -> None:
    """Drop the cached JWKS. For tests and a future admin/rotate hook."""
    global _jwks, _jwks_fetched_at
    with _lock:
        _jwks = {}
        _jwks_fetched_at = 0.0


# ── JWT ─────────────────────────────────────────────────────────────────────


def _b64url(segment: str) -> bytes:
    """Decode a base64url segment, restoring the padding JWTs strip."""
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _split(token: str) -> Optional[Tuple[dict, dict, bytes, bytes]]:
    """`(header, payload, signature, signing_input)` or None if malformed."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        header = json.loads(_b64url(parts[0]))
        payload = json.loads(_b64url(parts[1]))
        signature = _b64url(parts[2])
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(header, dict) or not isinstance(payload, dict):
        return None
    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    return header, payload, signature, signing_input


def _key_for(kid: Optional[str], keys: Dict[str, Any]) -> Optional[dict]:
    entries = keys.get("keys") if isinstance(keys, dict) else None
    if not isinstance(entries, list) or not entries:
        return None
    if kid:
        for entry in entries:
            if isinstance(entry, dict) and entry.get("kid") == kid:
                return entry
        return None
    # No kid: unambiguous only when there is exactly one key. Guessing among
    # several is how a rotated-out key gets accepted.
    return entries[0] if len(entries) == 1 else None


def _verify_signature(header: dict, jwk: dict, signing_input: bytes, signature: bytes) -> bool:
    """Verify EdDSA (Ed25519) or RSA. Returns False when it cannot be sure.

    Refusing rather than skipping is the whole point: a verifier that silently
    accepts an unverified token is worse than no verifier, because it looks
    like security in the logs.
    """
    alg = str(header.get("alg", ""))

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
        from cryptography.hazmat.primitives import hashes
    except ImportError:
        return False

    try:
        if alg == "EdDSA" and jwk.get("kty") == "OKP":
            public = ed25519.Ed25519PublicKey.from_public_bytes(_b64url(jwk["x"]))
            public.verify(signature, signing_input)
            return True

        if alg.startswith("RS") and jwk.get("kty") == "RSA":
            n = int.from_bytes(_b64url(jwk["n"]), "big")
            e = int.from_bytes(_b64url(jwk["e"]), "big")
            public = rsa.RSAPublicNumbers(e, n).public_key()
            digest = {"RS256": hashes.SHA256(), "RS384": hashes.SHA384(), "RS512": hashes.SHA512()}
            if alg not in digest:
                return False
            public.verify(signature, signing_input, padding.PKCS1v15(), digest[alg])
            return True
    except (InvalidSignature, KeyError, ValueError):
        return False

    return False


def verify(token: str) -> Optional[dict]:
    """Verified claims, or ``None``. Never raises, never guesses."""
    if not enabled() or not token:
        return None

    parsed = _split(token)
    if parsed is None:
        return None
    header, payload, signature, signing_input = parsed

    jwk = _key_for(header.get("kid"), _fetch_jwks())
    if jwk is None:
        # One forced refetch covers a key rotated in since the cache warmed.
        jwk = _key_for(header.get("kid"), _fetch_jwks(force=True))
    if jwk is None:
        return None

    if not _verify_signature(header, jwk, signing_input, signature):
        return None

    now = time.time()
    exp = payload.get("exp")
    if isinstance(exp, (int, float)) and now > exp + LEEWAY_S:
        return None
    nbf = payload.get("nbf")
    if isinstance(nbf, (int, float)) and now + LEEWAY_S < nbf:
        return None

    if ISSUER and payload.get("iss") != ISSUER:
        return None

    if AUDIENCE:
        aud = payload.get("aud")
        allowed = aud if isinstance(aud, list) else [aud]
        if AUDIENCE not in allowed:
            return None

    return payload


# ── the bridge-facing surface ───────────────────────────────────────────────


def bearer_from(headers: Optional[Mapping[str, str]]) -> Optional[str]:
    """Pull a bearer token out of request headers, case-insensitively."""
    if not headers:
        return None
    lowered = {str(k).lower(): v for k, v in headers.items()}
    raw = lowered.get(AUTH_HEADER)
    if not raw or not isinstance(raw, str):
        return None
    if not raw.lower().startswith("bearer "):
        return None
    return raw[7:].strip() or None


def resolve_verified(headers: Optional[Mapping[str, str]]) -> Optional[str]:
    """The CRYPTOGRAPHICALLY VERIFIED agent selector for a request, or None.

    ``None`` means "no verified identity" — the caller falls through to
    :func:`identity.resolve`, which is the behaviour the bridge has today.
    It never means "trust the header instead"; the header is only ever
    consulted by identity.py, and only after this has declined.
    """
    claims = verify(bearer_from(headers) or "")
    if not claims:
        return None

    selector = claims.get(SELECTOR_CLAIM) or claims.get("azp") or claims.get("sub")
    if not selector or not isinstance(selector, str):
        return None
    return selector


def claims_for(headers: Optional[Mapping[str, str]]) -> Optional[dict]:
    """Full verified claims, for a handler that wants the subject or scopes."""
    return verify(bearer_from(headers) or "")


__all__ = [
    "AUTH_HEADER",
    "bearer_from",
    "claims_for",
    "enabled",
    "reset_cache",
    "resolve_verified",
    "verify",
]
