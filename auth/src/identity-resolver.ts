import { AgentRegistry, type Agent } from "./agent-registry.js";
import { Directory } from "./directory.js";

/**
 * A verified token, however the host framework surfaces it.
 *
 * Structural on purpose: this module takes no dependency on any MCP library.
 * It matches mcp-use's `AuthInfo`, and anything else shaped the same way.
 */
export interface AuthInfo {
  user: { userId: string; email?: string; name?: string; [k: string]: unknown };
  payload?: Record<string, unknown>;
  scopes?: string[];
  permissions?: string[];
  [k: string]: unknown;
}

export interface Principal {
  userId: string;
  email?: string;
  name?: string;
  clientId: string;
  clientName: string;
  scopes: string[];
  viaDevToken: boolean;
}

export interface ResolvedCaller {
  principal: Principal;
  agent: Agent;
}

export type AuthMode = "strict" | "dev";

export interface IdentityResolverOptions {
  mode?: AuthMode;
  devToken?: string;
  devUserId?: string;
  devUserEmail?: string;
  devClientId?: string;
  devClientName?: string;
}

/**
 * Turns a verified token into "who is calling, and as which agent".
 *
 * The single chokepoint the host application should use. A caller never names
 * its own identity — it is resolved server-side from the token, exactly so a
 * request body cannot claim to be someone else.
 */
export class IdentityResolver {
  readonly mode: AuthMode;
  private readonly devToken: string;
  private readonly devUserId: string;
  private readonly devUserEmail: string;
  private readonly devClientId: string;
  private readonly devClientName: string;

  constructor(
    private readonly directory: Directory,
    private readonly agents: AgentRegistry,
    options: IdentityResolverOptions = {},
  ) {
    this.mode = options.mode ?? ((process.env.MEMORY_AUTH_MODE as AuthMode) || "strict");
    this.devToken = options.devToken ?? process.env.MEMORY_DEV_TOKEN ?? "dev-token";
    this.devUserId = options.devUserId ?? process.env.MEMORY_DEV_USER_ID ?? "usr_demo";
    this.devUserEmail = options.devUserEmail ?? process.env.MEMORY_DEV_USER_EMAIL ?? "demo@example.com";
    this.devClientId = options.devClientId ?? process.env.MEMORY_DEV_CLIENT_ID ?? "dev-cli";
    this.devClientName = options.devClientName ?? process.env.MEMORY_DEV_CLIENT_NAME ?? "dev cli";
  }

  /**
   * Whether the transport should enforce OAuth.
   *
   * False only in dev mode, where a static token stands in for the whole flow.
   * The host should read THIS rather than checking the mode itself: OAuth has
   * to be disabled at the transport, because a framework that verifies bearers
   * as JWTs will reject a static token before any handler runs. The gate moves
   * to `resolve()`; it does not disappear.
   */
  get oauthEnabled(): boolean {
    return this.mode !== "dev";
  }

  /** Pull a bearer token off a Headers object. */
  static bearerFrom(headers: Headers | undefined): string | undefined {
    const raw = headers?.get?.("authorization") ?? undefined;
    return raw?.toLowerCase().startsWith("bearer ") ? raw.slice(7).trim() : undefined;
  }

  /**
   * `azp` (authorized party) is where an OAuth client id lives. `aud` is the
   * RESOURCE being called, never the client — checked last, and only as a
   * defensive fallback for issuers that spell things differently.
   */
  private static clientIdFrom(auth: AuthInfo): string {
    const p = auth.payload ?? {};
    const candidate =
      (p.client_id as string) ??
      (p.azp as string) ??
      (typeof p.aud === "string" ? p.aud : undefined);
    return candidate || "unknown-client";
  }

  /** `mcp-client-abc123` -> `mcp client`. A last resort for the display name. */
  private static prettyClientId(clientId: string): string {
    if (/^https?:\/\//.test(clientId)) {
      try {
        return new URL(clientId).host;
      } catch {
        /* fall through */
      }
    }
    return (
      clientId.replace(/[-_]/g, " ").replace(/\s*[0-9a-f]{8,}\s*$/i, "").trim() || clientId
    );
  }

  private async clientNameFor(auth: AuthInfo, clientId: string): Promise<string> {
    const fromToken = auth.payload?.client_name as string | undefined;
    return (
      fromToken ??
      (await this.directory.clientName(clientId)) ??
      IdentityResolver.prettyClientId(clientId)
    );
  }

  /** Resolve identity only. Does not register anything. */
  async resolvePrincipal(
    auth: AuthInfo | undefined,
    bearer?: string,
  ): Promise<Principal | null> {
    if (auth?.user?.userId) {
      const clientId = IdentityResolver.clientIdFrom(auth);
      const userId = auth.user.userId;
      return {
        userId,
        // Access tokens carry `sub` but not the profile, so fall back to the
        // local record rather than surfacing an opaque id.
        email: auth.user.email ?? (await this.directory.userEmail(userId)) ?? undefined,
        name: auth.user.name ?? (await this.directory.userName(userId)) ?? undefined,
        clientId,
        clientName: await this.clientNameFor(auth, clientId),
        scopes: auth.scopes ?? [],
        viaDevToken: false,
      };
    }

    if (this.mode === "dev" && bearer && bearer === this.devToken) {
      return {
        userId: this.devUserId,
        email: this.devUserEmail,
        name: "Dev Principal",
        clientId: this.devClientId,
        clientName: this.devClientName,
        scopes: ["openid", "profile", "email"],
        viaDevToken: true,
      };
    }

    return null;
  }

  /** Resolve the caller AND register the agent behind it, in one step. */
  async resolve(auth: AuthInfo | undefined, bearer?: string): Promise<ResolvedCaller | null> {
    const principal = await this.resolvePrincipal(auth, bearer);
    if (!principal) return null;

    const agent = await this.agents.upsert({
      userId: principal.userId,
      clientId: principal.clientId,
      displayName: principal.clientName,
      kind: "agent",
    });

    return { principal, agent };
  }
}
