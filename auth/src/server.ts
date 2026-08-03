/**
 * The auth service.
 *
 * Runs as its own process next to the bridge, on its own port, speaking to the
 * SAME FalkorDB instance the bridge uses (127.0.0.1:6401 by default, from
 * memory/config.py's defaults mirrored into the env).
 *
 * It is a separate process on purpose. The bridge is Python and imports
 * cleanly on a bare interpreter by design; bolting a Node runtime into it
 * would cost that property for no gain. The only thing the bridge needs from
 * here is a JWKS URL, which is a URL.
 */
import { serve } from "@hono/node-server";
import { Hono } from "hono";
import { AuthModule } from "./index.js";

const PORT = Number(process.env.AUTH_PORT ?? 8932);
const HOST = process.env.AUTH_HOST ?? "127.0.0.1";
const BASE_URL = process.env.AUTH_BASE_URL ?? `http://${HOST}:${PORT}`;

// The bridge's own origin, so a token minted here is valid for calls made
// there. Missing audiences are the single most common integration failure:
// Better Auth issues an OPAQUE token when it has no audience to put in the
// JWT, and any verifier then rejects it as unparseable.
const BRIDGE_URL = process.env.BRIDGE_URL ?? "http://127.0.0.1:8931";

const auth = new AuthModule({
  baseUrl: BASE_URL,
  brand: "PALIMPSEST",
  audiences: [BASE_URL, BRIDGE_URL, `${BRIDGE_URL}/mcp`],
  graph: {
    url:
      process.env.FALKORDB_URL ??
      `redis://${process.env.FALKORDB_HOST ?? "127.0.0.1"}:${process.env.FALKORDB_PORT ?? "6401"}`,
    // Its own graph key by default. The bridge projects its graph with an
    // unfiltered `MATCH (n)`, so auth nodes living in the warm graph would be
    // rendered in the projector UI and counted in its node totals. Separation
    // costs the cross-graph :READ/:WROTE edges; the AuthEvent timeline still
    // records resourceIds, so the audit trail survives.
    graph: process.env.AUTH_GRAPH_KEY ?? "palimpsest_auth",
    prefix: process.env.AUTH_LABEL_PREFIX ?? "Auth",
  },
});

const app = new Hono();
auth.mount(app as never);

app.get("/health", async (c) => {
  let graphOk = true;
  let error: string | undefined;
  try {
    await auth.graph.query("RETURN 1");
  } catch (e) {
    graphOk = false;
    error = `${(e as Error).name}: ${(e as Error).message}`;
  }

  return c.json(
    {
      ok: graphOk,
      service: "palimpsest-auth",
      issuer: auth.issuer,
      mode: auth.identity.mode,
      falkordb: {
        reachable: graphOk,
        endpoint: auth.graph.endpoint,
        graph: auth.graph.graphName,
        labelPrefix: auth.graph.prefix,
        ...(error ? { error } : {}),
      },
    },
    graphOk ? 200 : 503,
  );
});

app.get("/", (c) => c.redirect("/login"));

serve({ fetch: app.fetch, port: PORT, hostname: HOST }, () => {
  console.log(`
  palimpsest-auth      ${BASE_URL}
  issuer               ${auth.issuer}
  jwks                 ${auth.issuer}/jwks
  discovery            ${BASE_URL}/.well-known/oauth-authorization-server/api/auth
  falkordb             ${auth.graph.endpoint}  graph=${auth.graph.graphName}  labels=${auth.graph.prefix}*
  audiences            ${BRIDGE_URL}, ${BRIDGE_URL}/mcp
  mode                 ${auth.identity.mode}${auth.oauthEnabled ? "" : "  ** dev token accepted **"}
`);
});
