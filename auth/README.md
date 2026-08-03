# auth — verified agent identity

`app/bridge/identity.py` gives PALIMPSEST **attribution**: every write carries
`author_agent`, and that is what turns "the analyst asserted X, the watcher
contradicted it" into a graph fact. Its own docstring is explicit that it has
**no auth** — a pure `session-id -> selector` map.

The consequence is in its precedence ladder, which ends at the
`x-palimpsest-agent` header. Any caller can set that header to any value:

```bash
curl -H 'x-palimpsest-agent: commander' .../v1/remember   # recorded as the commander
```

So today the graph records *a claim about* who acted, not *who acted*. Against
GOAL.md's "the agent knows **who it is**", that is the gap this lane closes.

This service issues OAuth 2.1 + PKCE tokens; the bridge verifies them and gets
a selector that was **cryptographically earned** rather than asserted.

## Two front doors, one identity

```
humans  →  email + password  →  session      →  /login, /account
agents  →  OAuth 2.1 + PKCE  →  JWT (EdDSA)  →  Authorization: Bearer …
```

Both resolve to the same `:AuthUser`. An agent is the pair *(user × OAuth
client)*, so one human arriving through two clients is two principals — which
is exactly the granularity `author_agent` wants.

## Run it

```bash
docker compose -f auth/docker-compose.auth.yml up -d      # FalkorDB + auth
# or, against a FalkorDB you already have on 6401:
cd auth && npm install && npm start
```

Then turn verification **on** for the bridge (it is off until you do):

```bash
export AUTH_JWKS_URL=http://127.0.0.1:8932/api/auth/jwks
export AUTH_ISSUER=http://127.0.0.1:8932/api/auth
export AUTH_AUDIENCE=http://127.0.0.1:8931/mcp
```

## Verify it

```bash
node auth/scripts/verify-e2e.mjs          # 11 checks, needs the service up
.venv/bin/python -m pytest app/bridge/tests/test_auth.py   # 21 checks, no network
```

`verify-e2e.mjs` runs the real flow — sign-up, dynamic client registration,
PKCE authorize, consent, token — and then hands the resulting JWT to **the
bridge's own Python verifier**. That last step is the one that matters: the two
halves are different languages and different crypto libraries, and every
interop bug that bites (base64url padding, JWKS `kid` matching, the `azp`
claim, audience lists) lives on that seam. It also asserts the verifier
*refuses* wrong-audience, wrong-issuer and tampered tokens — a verifier that
accepts those is worse than none, because the logs then say "verified".

## How it touches the bridge

Two logical lines in `app/bridge/server.py`, at the single place identity is
resolved:

```python
agent = authmod.resolve_verified(headers) or identity.resolve(
    session_id or hdr_session, is_stdio=is_stdio, header_selector=hdr_agent,
)
```

`identity.py` is **not modified**. The new module is a precedence rung above
it, not a replacement.

- **No token** → `None` → the existing ladder runs exactly as today.
- **Bad token** → `None` → same. A forged token cannot *remove* an identity the
  caller would otherwise have had; it just fails to grant a better one.
- **Valid token** → the verified selector, outranking any header claim.
- **`AUTH_JWKS_URL` unset** → the module is inert and returns in microseconds.

So merging this changes nothing until you deliberately switch it on.

## Why it does not collide with the graph

Two independent guards, because either alone would be a latent bug:

**1. Its own graph key** (`palimpsest_auth`, never `palimpsest`). The bridge
projects with an unfiltered `MATCH (n) … RETURN n` and counts with
`MATCH (n) RETURN count(n)`. Auth nodes in the warm graph would therefore be
rendered as stray blobs in the projector and inflate the node count **on
stage**.

**2. Every label prefixed** (`AuthUser`, `AuthAgent`, `AuthSession`, …). Bare
`:User` is harmless today but bare `:Agent` would collide head-on with
`memory/SCHEMA.md`'s `:Agent {agent_id, role}`, and that collision is silent —
existing queries just start returning nodes with none of the expected
properties. Prefixing makes co-tenancy safe by construction, so the separate
graph key stays a *policy* choice rather than the only thing holding it up.

The cost of separation: provenance cannot write `:READ`/`:WROTE` edges to nodes
in another graph. The `:AuthEvent` timeline still records `resourceIds`, so the
audit trail survives; only the traversal shortcut is lost. Set
`AUTH_GRAPH_KEY=palimpsest` to trade that back — but only once the projector
filters by label.

## Design

| class | responsibility |
|---|---|
| `GraphClient` | the FalkorDB connection and every Cypher round trip |
| `FalkorAuthAdapter` | Better Auth storage compiled to Cypher (all 11 where-operators) |
| `AgentRegistry` | the *(user × client)* principals |
| `ProvenanceLog` | who read and wrote what |
| `Directory` | token subject → human-readable identity |
| `IdentityResolver` | verified token → acting principal + agent |
| `AuthPages` | `/login`, `/consent`, `/account` — subclass to reskin |
| `AuthModule` | the facade a host mounts |

Three traps encoded here, each found the hard way:

- **RFC 8707 resource indicator is mandatory.** Without `resource=…` on the
  authorize and token calls, Better Auth issues an **opaque** token and every
  JWT verifier rejects it as unparseable. Add your MCP URL to `audiences`.
- **`supportsJSON: false` means do not decode on read.** Better Auth serialises
  structured fields itself and expects the same string back; decoding in the
  adapter hands it an object it tries to parse again, which is how OAuth
  authorization codes come back "malformed".
- **`BETTER_AUTH_SECRET` encrypts the JWKS private key.** Changing it strands
  every existing key with "Failed to decrypt private key" and 500s the token
  endpoint. Rotating it means deleting the `:AuthJwks` nodes in the same change.

## Security

- No credentials in this directory. `.env.example` documents every variable;
  `BETTER_AUTH_SECRET` is bring-your-own and has a local-dev-only fallback.
- FalkorDB is bound to **loopback only** in the compose file. Docker's default
  publishes on `0.0.0.0`, and the instance has no password.
- Refresh tokens rotate; a spent one is refused on replay.
- The verifier **fails closed on a bad token** and refuses to verify at all if
  `cryptography` is unavailable, rather than trusting an unchecked signature.
