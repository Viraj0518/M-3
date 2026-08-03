import { betterAuth } from "better-auth";
import { jwt, bearer } from "better-auth/plugins";
import { oauthProvider } from "@better-auth/oauth-provider";
import {
  oauthProviderAuthServerMetadata,
  oauthProviderOpenIdConfigMetadata,
} from "@better-auth/oauth-provider";

import { GraphClient, type GraphClientOptions } from "./graph-client.js";
import { FalkorAuthAdapter } from "./falkor-adapter.js";
import { AgentRegistry } from "./agent-registry.js";
import { ProvenanceLog } from "./provenance-log.js";
import { Directory } from "./directory.js";
import { IdentityResolver, type IdentityResolverOptions } from "./identity-resolver.js";
import { AuthPages } from "./auth-pages.js";

/**
 * Minimal shape of the host HTTP app.
 *
 * Structural rather than an imported Hono type: an MCP framework that bundles
 * its own copy of Hono hands you a different class than the one this package
 * could import, and the nominal types will not unify.
 */
export interface AuthHost {
  get(path: string, handler: (c: any) => any): unknown;
  post(path: string, handler: (c: any) => any): unknown;
  on(methods: string[], path: string, handler: (c: any) => any): unknown;
}

interface BetterAuthConfig {
  database: ReturnType<FalkorAuthAdapter["toBetterAuthAdapter"]>;
  baseUrl: string;
  basePath: string;
  secret: string;
  audiences: string[];
}

/**
 * Built by a free function rather than inline in the constructor: `betterAuth`
 * returns `Auth<TConcreteOptions>`, and a field annotated
 * `ReturnType<typeof betterAuth>` widens to `Auth<BetterAuthOptions>`, which
 * the concrete instance is not assignable to. Inferring from this builder
 * keeps the precise type on the field.
 */
function buildBetterAuth(config: BetterAuthConfig) {
  return betterAuth({
    // Identity lives in the same graph as agents and provenance, so the
    // `user` node created at sign-up is the node the edges hang off.
    database: config.database,
    baseURL: config.baseUrl,
    basePath: config.basePath,
    secret: config.secret,

    emailAndPassword: {
      enabled: true,
      requireEmailVerification: false,
      minPasswordLength: 8,
    },

    // The oauthProvider plugin owns /token; the jwt plugin's own /token would
    // otherwise shadow it.
    disabledPaths: ["/token"],

    plugins: [
      // Required: access-token signatures are verified against this JWKS.
      jwt(),
      // Lets a CLI hold a session token and send it as a bearer header
      // instead of juggling cookies from a terminal.
      bearer(),
      oauthProvider({
        loginPage: "/login",
        consentPage: "/consent",
        // An MCP client self-registers and has no credentials to register
        // WITH, so both flags are required for `claude mcp add` to work.
        allowDynamicClientRegistration: true,
        allowUnauthenticatedClientRegistration: true,
        validAudiences: config.audiences,
        scopes: ["openid", "profile", "email", "offline_access"],
      }),
    ],

    trustedOrigins: [config.baseUrl, "http://localhost:3000", "http://127.0.0.1:3000"],
  });
}

export type BetterAuthInstance = ReturnType<typeof buildBetterAuth>;

/**
 * Only used when BETTER_AUTH_SECRET is unset. Its exact value is load-bearing
 * for local data: it encrypts the JWKS private key, so a graph seeded under
 * one default cannot mint tokens under another.
 */
const DEFAULT_DEV_SECRET = "dev-only-secret-change-me-in-prod";

export interface AuthModuleOptions {
  /** Public origin of this server. */
  baseUrl?: string;
  /** Where Better Auth is mounted, relative to baseUrl. */
  basePath?: string;
  /** Signs sessions and tokens. */
  secret?: string;
  /**
   * Every resource an agent may request a token for.
   *
   * An MCP server on a different path or port MUST be listed here, or Better
   * Auth issues an opaque token instead of a verifiable JWT and the framework
   * rejects it. This is the single most common way the integration fails.
   */
  audiences?: string[];
  graph?: GraphClient | GraphClientOptions;
  identity?: IdentityResolverOptions;
  pages?: AuthPages;
  brand?: string;
}

/**
 * The auth module.
 *
 * Two front doors into one identity:
 *
 *   humans  ->  email + password  ->  Better Auth session
 *   agents  ->  OAuth 2.1 + PKCE  ->  JWT access token
 *
 * Both resolve to the same `User` node. An agent is the pair (user x OAuth
 * client), so one person arriving through two clients is two principals over
 * one store — which is what makes attribution mean anything.
 *
 * Takes no dependency on any MCP framework. It issues and verifies identity;
 * the host consumes it.
 */
export class AuthModule {
  readonly baseUrl: string;
  readonly basePath: string;
  readonly graph: GraphClient;
  readonly agents: AgentRegistry;
  readonly provenance: ProvenanceLog;
  readonly directory: Directory;
  readonly identity: IdentityResolver;
  readonly pages: AuthPages;
  readonly auth: BetterAuthInstance;

  constructor(options: AuthModuleOptions = {}) {
    this.baseUrl = options.baseUrl ?? process.env.BASE_URL ?? "http://localhost:3000";
    this.basePath = options.basePath ?? "/api/auth";

    this.graph =
      options.graph instanceof GraphClient
        ? options.graph
        : new GraphClient(options.graph ?? {});

    const prefix = this.graph.prefix;
    this.agents = new AgentRegistry(this.graph, { prefix });
    this.provenance = new ProvenanceLog(this.graph, { prefix });
    this.directory = new Directory(this.graph, { prefix });
    this.identity = new IdentityResolver(this.directory, this.agents, options.identity);
    this.pages = options.pages ?? new AuthPages({ brand: options.brand });

    const audiences =
      options.audiences ??
      (process.env.AUTH_AUDIENCES
        ? process.env.AUTH_AUDIENCES.split(",").map((a) => a.trim()).filter(Boolean)
        : [this.baseUrl, `${this.baseUrl}/mcp`]);

    this.auth = buildBetterAuth({
      database: new FalkorAuthAdapter(this.graph, { prefix }).toBetterAuthAdapter(),
      baseUrl: this.baseUrl,
      basePath: this.basePath,
      // Stable default on purpose. BETTER_AUTH_SECRET encrypts the JWKS
      // private key, so changing it strands every previously issued key with
      // "Failed to decrypt private key" and the token endpoint 500s. Rotating
      // it is a real operation: delete the Jwks nodes in the same change.
      // `|| DEFAULT_DEV_SECRET`, not `??`: docker-compose's `${VAR:-}`
      // delivers an EMPTY STRING when the host var is unset, and an empty
      // secret must fall through to the dev fallback, not encrypt the JWKS.
      secret: options.secret ?? (process.env.BETTER_AUTH_SECRET || DEFAULT_DEV_SECRET),
      audiences,
    });
  }

  /** The one value an MCP server needs in order to delegate authentication. */
  get issuer(): string {
    return `${this.baseUrl}${this.basePath}`;
  }

  /** Whether the transport should enforce OAuth. False only in dev mode. */
  get oauthEnabled(): boolean {
    return this.identity.oauthEnabled;
  }

  /** Resolve the signed-in human from a request, or null. */
  async sessionFrom(req: Request) {
    return this.auth.api.getSession({ headers: req.headers });
  }

  /**
   * Mount every identity route onto a host app.
   *
   *   /.well-known/*     OAuth 2.1 discovery (RFC 8414)
   *   /api/auth/*        Better Auth: sign-in, DCR, authorize, token, JWKS
   *   /login /consent    the human pages in an agent's OAuth flow
   *   /account           who holds access, and what they did with it
   *   /api/me            identity + the agents that can act for you
   *   /api/provenance    the attributed timeline
   *   /api/graph         cross-agent reads
   */
  mount(app: AuthHost): void {
    this.mountDiscovery(app);
    this.mountBetterAuth(app);
    this.mountPages(app);
    this.mountApi(app);
  }

  /**
   * RFC 8414 §3 puts the metadata at
   * `/.well-known/oauth-authorization-server/<issuer path>` — the well-known
   * segment goes BETWEEN host and issuer path, not on the end. Better Auth
   * cannot serve a root-level path from under its own basePath, so its
   * exported handlers are mounted here. Without this, client discovery 404s
   * and nothing can register.
   */
  protected mountDiscovery(app: AuthHost): void {
    const authServer = oauthProviderAuthServerMetadata(this.auth as never);
    const openId = oauthProviderOpenIdConfigMetadata(this.auth as never);

    app.get(`/.well-known/oauth-authorization-server${this.basePath}`, (c) =>
      authServer(c.req.raw),
    );
    app.get(`/.well-known/openid-configuration${this.basePath}`, (c) => openId(c.req.raw));
  }

  protected mountBetterAuth(app: AuthHost): void {
    app.on(["GET", "POST"], `${this.basePath}/*`, (c) => this.auth.handler(c.req.raw));
  }

  protected mountPages(app: AuthHost): void {
    app.get("/login", (c) => c.html(this.pages.login()));
    app.get("/consent", (c) => c.html(this.pages.consent(c.req.url)));
    app.get("/account", (c) => c.html(this.pages.account()));
  }

  protected mountApi(app: AuthHost): void {
    const session = (c: any) => this.auth.api.getSession({ headers: c.req.raw.headers });

    app.get("/api/auth-health", (c) =>
      c.json({ ok: true, authMode: this.identity.mode, at: new Date().toISOString() }),
    );

    app.get("/api/me", async (c: any) => {
      const s = await session(c);
      if (!s) return c.json({ error: "not signed in" }, 401);

      // The human is a principal too — registering the CLI as an agent of
      // kind "human" puts it in the same graph as the agents.
      const self = await this.agents.upsert({
        userId: s.user.id,
        clientId: "mem-cli",
        displayName: "mem cli",
        kind: "human",
      });

      return c.json({
        user: { id: s.user.id, email: s.user.email, name: s.user.name },
        self: { id: self.id, name: self.displayName, color: self.color },
        agents: (await this.agents.list(s.user.id)).map((a) => ({
          id: a.id,
          name: a.displayName,
          kind: a.kind,
          color: a.color,
          firstSeen: a.firstSeen,
          lastSeen: a.lastSeen,
        })),
      });
    });

    app.get("/api/provenance", async (c: any) => {
      const s = await session(c);
      if (!s) return c.json({ error: "not signed in" }, 401);
      return c.json({
        events: await this.provenance.recent(s.user.id, Number(c.req.query("limit") ?? 20)),
      });
    });

    app.get("/api/graph", async (c: any) => {
      const s = await session(c);
      if (!s) return c.json({ error: "not signed in" }, 401);
      return c.json({
        agents: (await this.agents.list(s.user.id)).map((a) => ({
          id: a.id,
          name: a.displayName,
          kind: a.kind,
          color: a.color,
        })),
        crossAgent: await this.provenance.crossAgentReads(
          s.user.id,
          Number(c.req.query("limit") ?? 20),
        ),
      });
    });
  }
}
