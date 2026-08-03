/**
 * @memory/auth — authentication and provenance for agent-facing services.
 *
 * Two front doors into one identity:
 *
 *   humans  ->  email + password  ->  Better Auth session
 *   agents  ->  OAuth 2.1 + PKCE  ->  JWT access token
 *
 * Both resolve to the same `User` node in FalkorDB. An agent is the pair
 * (user x OAuth client), so one person arriving through Claude Code and
 * through a CLI is two distinct principals over one store. Every read and
 * write is attributed to the agent that made it.
 *
 * This package depends on no MCP framework. It issues and verifies identity;
 * whoever builds the server consumes it.
 *
 * ---------------------------------------------------------------------------
 * Integrating a server (three calls)
 * ---------------------------------------------------------------------------
 *
 *   import { AuthModule } from "@memory/auth";
 *
 *   const auth = new AuthModule({ baseUrl: "http://localhost:3000" });
 *
 *   // 1 — point the MCP framework's OAuth provider at the issuer
 *   const server = new MCPServer({
 *     oauth: auth.oauthEnabled
 *       ? oauthBetterAuthProvider({ authURL: auth.issuer })
 *       : undefined,
 *   });
 *
 *   // 2 — mount every identity route
 *   auth.mount(server.app);
 *
 *   // 3 — resolve the caller inside any handler
 *   const who = await auth.identity.resolve(
 *     ctx.auth,
 *     IdentityResolver.bearerFrom(ctx.req?.raw?.headers),
 *   );
 *   if (!who) return error("Unauthorized");
 *   who.principal.userId;   // scope your data by this
 *   who.agent.id;           // attribute reads and writes to this
 *
 *   // 4 — record what it did
 *   await auth.provenance.record({
 *     userId: who.principal.userId,
 *     agentId: who.agent.id,
 *     eventType: "resource.read",
 *     tool: "search",
 *     query: q,
 *     resources: hits.map((h) => ({ id: h.id, title: h.title })),
 *     latencyMs,
 *   });
 */

export { AuthModule, type AuthModuleOptions, type AuthHost } from "./auth-module.js";

export {
  GraphClient,
  type GraphClientOptions,
  encodeValue,
  newId,
  nowIso,
} from "./graph-client.js";

export { FalkorAuthAdapter, type CleanedWhere } from "./falkor-adapter.js";

export {
  AgentRegistry,
  type Agent,
  type AgentRef,
  type UpsertAgentInput,
} from "./agent-registry.js";

export {
  ProvenanceLog,
  type AuthEventType,
  type ResourceRef,
  type RecordEventInput,
  type ProvenanceRow,
} from "./provenance-log.js";

export { Directory } from "./directory.js";

export {
  IdentityResolver,
  type AuthInfo,
  type Principal,
  type ResolvedCaller,
  type AuthMode,
  type IdentityResolverOptions,
} from "./identity-resolver.js";

export { AuthPages, type AuthPagesOptions } from "./auth-pages.js";
