import { GraphClient } from "./graph-client.js";

/**
 * Human-readable identity, resolved from the same graph Better Auth writes to.
 *
 * An access token is deliberately thin: it carries `sub` and `azp` (the OAuth
 * client id) but no email and no client name. Provenance that reads "agent
 * rFRggzGtSydYcQnaTvKlq… read your record" is useless; "doordash-cli read your
 * record" is the product. Because identity lives in the same graph, that
 * lookup is one hop rather than a userinfo round trip.
 */
export class Directory {
  readonly prefix: string;

  constructor(
    private readonly graph: GraphClient,
    options: { prefix?: string } = {},
  ) {
    this.prefix = options.prefix ?? process.env.AUTH_LABEL_PREFIX ?? "Auth";
  }

  async clientName(clientId: string): Promise<string | null> {
    const row = await this.graph
      .queryOne<{ name: string | null }>(
        `MATCH (c:${this.prefix}OauthClient { clientId: $clientId }) RETURN c.name AS name LIMIT 1`,
        { clientId },
      )
      .catch(() => null);
    return row?.name?.trim() || null;
  }

  async userEmail(userId: string): Promise<string | null> {
    const row = await this.graph
      .queryOne<{ email: string | null }>(
        `MATCH (u:${this.prefix}User { id: $userId }) RETURN u.email AS email LIMIT 1`,
        { userId },
      )
      .catch(() => null);
    return row?.email?.trim() || null;
  }

  async userName(userId: string): Promise<string | null> {
    const row = await this.graph
      .queryOne<{ name: string | null }>(
        `MATCH (u:${this.prefix}User { id: $userId }) RETURN u.name AS name LIMIT 1`,
        { userId },
      )
      .catch(() => null);
    return row?.name?.trim() || null;
  }
}
