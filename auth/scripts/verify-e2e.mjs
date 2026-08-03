#!/usr/bin/env node
/**
 * End-to-end proof for the auth lane.
 *
 * Runs the real OAuth 2.1 flow against a live auth service, then hands the
 * resulting JWT to the BRIDGE'S OWN PYTHON VERIFIER and asserts it resolves to
 * the right agent selector.
 *
 * That last step is the point. The two halves of this feature are written in
 * different languages by different libraries — TypeScript/Better Auth mints
 * EdDSA tokens, Python/`cryptography` verifies them — and every interop bug
 * that matters (base64url padding, JWKS `kid` matching, the `azp` claim, the
 * audience list) lives exactly on that seam. A suite that only exercised the
 * TypeScript side would be green while the bridge rejected every token.
 *
 *   node auth/scripts/verify-e2e.mjs
 *
 * Needs: the auth service up (default :8932) and `.venv` built for the bridge.
 */
import crypto from "node:crypto";
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "../..");
const AUTH = process.env.AUTH_BASE_URL ?? "http://127.0.0.1:8932";
const BRIDGE = process.env.BRIDGE_URL ?? "http://127.0.0.1:8931";
const REDIRECT = "http://127.0.0.1:8765/callback";
const PYTHON = resolve(REPO, ".venv/bin/python");

const STAMP = crypto.randomBytes(4).toString("hex");
const EMAIL = `e2e-${STAMP}@example.com`;
// Random per run. A fixed literal is harmless against localhost but creates an
// account with a publicly-known password the moment this is pointed anywhere
// shared — and the script takes AUTH_BASE_URL from the environment.
const PASSWORD = `e2e-${crypto.randomBytes(18).toString("base64url")}`;
const CLIENT_NAME = `bridge-agent-${STAMP}`;

let pass = 0, fail = 0;
const ok = (n, d = "") => { pass++; console.log(`  \x1b[32m✓\x1b[0m ${n}${d ? `  \x1b[2m${d}\x1b[0m` : ""}`); };
const bad = (n, d = "") => { fail++; console.log(`  \x1b[31m✗\x1b[0m ${n}  \x1b[31m${d}\x1b[0m`); };
const must = (c, m) => { if (!c) throw new Error(m); };
async function step(name, fn) {
  try { ok(name, (await fn()) ?? ""); } catch (e) { bad(name, e.message); }
}

const jar = new Map();
async function req(url, init = {}) {
  const res = await fetch(url.startsWith("http") ? url : AUTH + url, {
    ...init,
    redirect: "manual",
    headers: {
      cookie: [...jar].map(([k, v]) => `${k}=${v}`).join("; "),
      origin: AUTH,
      ...(init.headers ?? {}),
    },
  });
  for (const sc of res.headers.getSetCookie?.() ?? []) {
    const [pair] = sc.split(";");
    const i = pair.indexOf("=");
    if (i > 0) jar.set(pair.slice(0, i).trim(), pair.slice(i + 1).trim());
  }
  return res;
}
const json = (body) => ({
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
});
async function mustOk(r, label) {
  if (r.ok) return;
  throw new Error(`${label} ${r.status}: ${(await r.text()).slice(0, 140)}`);
}

console.log(`\n\x1b[1mAUTH E2E\x1b[0m  ${AUTH}  ->  bridge verifier`);
console.log(`\x1b[2midentity for this run: ${EMAIL}\x1b[0m\n`);

let clientId, verifier, token;

await step("auth service is up and reaches FalkorDB", async () => {
  const r = await fetch(`${AUTH}/health`);
  const d = await r.json();
  must(r.ok, `health ${r.status}: ${JSON.stringify(d).slice(0, 120)}`);
  must(d.falkordb.reachable, "FalkorDB unreachable");
  return `${d.falkordb.endpoint} graph=${d.falkordb.graph} labels=${d.falkordb.labelPrefix}*`;
});

await step("discovery advertises the endpoints a client needs", async () => {
  const r = await fetch(`${AUTH}/.well-known/oauth-authorization-server/api/auth`);
  await mustOk(r, "discovery");
  const d = await r.json();
  for (const k of ["issuer", "authorization_endpoint", "token_endpoint", "registration_endpoint", "jwks_uri"]) {
    must(d[k], `missing ${k}`);
  }
  return d.issuer;
});

await step("a human can sign up", async () => {
  const r = await req("/api/auth/sign-up/email", json({ email: EMAIL, password: PASSWORD, name: "e2e" }));
  await mustOk(r, "sign-up");
  const d = await r.json();
  must(d.user?.id, "no user id");
  return d.user.email;
});

await step("an agent registers itself (DCR, no credentials)", async () => {
  const r = await req("/api/auth/oauth2/register", json({
    client_name: CLIENT_NAME,
    redirect_uris: [REDIRECT],
    grant_types: ["authorization_code", "refresh_token"],
    response_types: ["code"],
    token_endpoint_auth_method: "none",
  }));
  await mustOk(r, "register");
  clientId = (await r.json()).client_id;
  return clientId.slice(0, 14) + "…";
});

await step("authorize + consent yields a code", async () => {
  verifier = crypto.randomBytes(32).toString("base64url");
  const challenge = crypto.createHash("sha256").update(verifier).digest("base64url");

  const r = await req(`/api/auth/oauth2/authorize?` + new URLSearchParams({
    client_id: clientId, redirect_uri: REDIRECT, response_type: "code",
    scope: "openid profile email offline_access", state: STAMP,
    code_challenge: challenge, code_challenge_method: "S256",
    // RFC 8707. Without a resource indicator Better Auth mints an OPAQUE
    // token and the bridge verifier has nothing to parse.
    resource: `${BRIDGE}/mcp`,
  }), { headers: { accept: "text/html" } });

  const j = await r.json().catch(() => ({}));
  let next = r.headers.get("location") || j.url || j.redirect || j.redirectURI;
  must(next, `authorize returned nothing (${r.status})`);

  if (next.startsWith(REDIRECT)) {
    globalThis.__code = new URL(next).searchParams.get("code");
    return "auto-approved";
  }
  const cr = await req("/api/auth/oauth2/consent", json({
    accept: true, scope: "openid profile email offline_access",
    oauth_query: next.slice(next.indexOf("?") + 1),
  }));
  await mustOk(cr, "consent");
  const cd = await cr.json();
  globalThis.__code = new URL(cd.redirectURI || cd.url).searchParams.get("code");
  must(globalThis.__code, "no authorization code");
  return "consent granted";
});

await step("the code exchanges for a verifiable JWT", async () => {
  const r = await req("/api/auth/oauth2/token", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code", code: globalThis.__code,
      redirect_uri: REDIRECT, client_id: clientId,
      code_verifier: verifier, resource: `${BRIDGE}/mcp`,
    }),
  });
  await mustOk(r, "token");
  const d = await r.json();
  token = d.access_token;
  must(token?.split(".").length === 3, "token is opaque, not a JWT");
  const claims = JSON.parse(Buffer.from(token.split(".")[1], "base64url").toString());
  must(claims.azp === clientId, `azp ${claims.azp} != ${clientId}`);
  return `EdDSA JWT · azp=${CLIENT_NAME}`;
});

// ── the seam: does PYTHON accept what TYPESCRIPT minted? ────────────────────

function bridgeVerify(bearer, { issuer, audience } = {}) {
  must(existsSync(PYTHON), `bridge venv missing at ${PYTHON} — see app/bridge/requirements.txt`);
  const script = `
import json, os, sys
sys.path.insert(0, ${JSON.stringify(REPO)})
os.environ["AUTH_JWKS_URL"] = ${JSON.stringify(`${AUTH}/api/auth/jwks`)}
os.environ["AUTH_ISSUER"] = ${JSON.stringify(issuer ?? `${AUTH}/api/auth`)}
os.environ["AUTH_AUDIENCE"] = ${JSON.stringify(audience ?? `${BRIDGE}/mcp`)}
import importlib
from app.bridge import auth as a
importlib.reload(a)
headers = {"authorization": "Bearer " + ${JSON.stringify(bearer)}}
print(json.dumps({"selector": a.resolve_verified(headers), "enabled": a.enabled()}))
`;
  const out = execFileSync(PYTHON, ["-c", script], { encoding: "utf8", timeout: 20000 });
  return JSON.parse(out.trim().split("\n").pop());
}

await step("THE SEAM — the bridge's Python verifier accepts the token", () => {
  const r = bridgeVerify(token);
  must(r.enabled, "verifier inert — AUTH_JWKS_URL not picked up");
  must(r.selector === clientId, `bridge resolved ${JSON.stringify(r.selector)}, expected the client id`);
  return `resolve_verified() -> ${r.selector.slice(0, 14)}…`;
});

await step("the bridge refuses a token minted for another audience", () => {
  const r = bridgeVerify(token, { audience: "http://somewhere-else.invalid" });
  must(r.selector === null, `accepted a wrong-audience token: ${r.selector}`);
  return "null";
});

await step("the bridge refuses a token from another issuer", () => {
  const r = bridgeVerify(token, { issuer: "http://evil.invalid/api/auth" });
  must(r.selector === null, `accepted a wrong-issuer token: ${r.selector}`);
  return "null";
});

await step("the bridge refuses a tampered token", () => {
  const [h, p, s] = token.split(".");
  const claims = JSON.parse(Buffer.from(p, "base64url").toString());
  claims.azp = "commander";                       // privilege escalation
  const forged = `${h}.${Buffer.from(JSON.stringify(claims)).toString("base64url")}.${s}`;
  const r = bridgeVerify(forged);
  must(r.selector === null, `accepted a tampered token as ${r.selector}`);
  return "null";
});

await step("auth wrote only prefixed labels into its own graph", async () => {
  const r = await fetch(`${AUTH}/health`);
  const d = await r.json();
  must(d.falkordb.graph !== "palimpsest", "auth must not share the warm demo graph");
  return `graph=${d.falkordb.graph}, prefix=${d.falkordb.labelPrefix}`;
});

console.log(`\n${fail === 0 ? "\x1b[32m" : "\x1b[31m"}${pass} passed, ${fail} failed\x1b[0m\n`);
process.exit(fail ? 1 : 0);
