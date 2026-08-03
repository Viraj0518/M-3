import { FalkorDB, type Graph } from "falkordb";

export interface GraphClientOptions {
  /** Redis-protocol URL of the FalkorDB instance. */
  url?: string;
  /** Graph key to select. */
  graph?: string;
  /** Prefix applied to every label this module owns. */
  prefix?: string;
}

/**
 * Owns the FalkorDB connection and every Cypher round trip.
 *
 * A class rather than module-level state so a process can hold more than one
 * (a test graph beside a live one), and so the connection is an injected
 * dependency of the repositories rather than an ambient global they reach for.
 */
export class GraphClient {
  private readonly url: string;
  private readonly graphKey: string;
  readonly prefix: string;
  private db: FalkorDB | null = null;
  private graph: Graph | null = null;
  private bootstrapped = false;
  private connecting: Promise<Graph> | null = null;

  constructor(options: GraphClientOptions = {}) {
    this.url = options.url ?? process.env.FALKORDB_URL ?? "redis://127.0.0.1:6379";
    // Co-located with the host's domain graph by DEFAULT, because that is what
    // lets provenance write real :READ/:WROTE edges to the host's own records.
    // Point AUTH_GRAPH_KEY at a separate graph when the host projects its graph
    // with an unfiltered `MATCH (n)` — auth nodes would otherwise show up in
    // its UI and inflate its node counts. See the trade in auth/README.md.
    this.graphKey = options.graph ?? process.env.AUTH_GRAPH_KEY ?? "memory";
    this.prefix = options.prefix ?? process.env.AUTH_LABEL_PREFIX ?? "Auth";
  }

  get graphName(): string {
    return this.graphKey;
  }

  get endpoint(): string {
    return this.url;
  }

  /**
   * Resolve the live graph handle, connecting on first use.
   *
   * Concurrent callers share one in-flight connect — without that, the first
   * few queries at boot each open their own socket and race to create indexes.
   */
  async handle(): Promise<Graph> {
    if (this.graph) return this.graph;
    if (this.connecting) return this.connecting;

    this.connecting = (async () => {
      this.db = await FalkorDB.connect({ url: this.url });
      this.graph = this.db.selectGraph(this.graphKey);
      await this.ensureIndexes(this.graph);
      return this.graph;
    })();

    try {
      return await this.connecting;
    } finally {
      this.connecting = null;
    }
  }

  /** Run a Cypher query, returning rows keyed by their RETURN alias. */
  async query<T = Record<string, unknown>>(
    cypher: string,
    params: Record<string, unknown> = {},
  ): Promise<T[]> {
    const g = await this.handle();
    const res = await g.query(cypher, { params: params as never });
    return ((res?.data ?? []) as T[]) ?? [];
  }

  /** Run a Cypher query and return the first row, or null. */
  async queryOne<T = Record<string, unknown>>(
    cypher: string,
    params: Record<string, unknown> = {},
  ): Promise<T | null> {
    const rows = await this.query<T>(cypher, params);
    return rows.length ? rows[0] : null;
  }

  /**
   * Wipe every node while keeping the graph itself alive.
   *
   * Deliberately NOT `GRAPH.DELETE`. Dropping the graph resets its
   * property-key table server-side while any connected client still caches
   * that table, after which reads decode onto the WRONG keys — a user's name
   * arrives as its id, silently, until the process restarts. Writes look fine
   * on disk the whole time, which makes it a genuinely nasty bug to chase.
   */
  async reset(): Promise<void> {
    await this.query("MATCH (n) DETACH DELETE n");
    this.bootstrapped = false;
    await this.ensureIndexes(await this.handle());
  }

  async close(): Promise<void> {
    await this.db?.close();
    this.db = null;
    this.graph = null;
    this.bootstrapped = false;
  }

  /** Indexes the auth module relies on. FalkorDB has no IF NOT EXISTS. */
  private async ensureIndexes(g: Graph): Promise<void> {
    if (this.bootstrapped) return;
    this.bootstrapped = true;

    const p = this.prefix;
    const statements = [
      `CREATE INDEX FOR (n:${p}User) ON (n.id)`,
      `CREATE INDEX FOR (n:${p}User) ON (n.email)`,
      `CREATE INDEX FOR (n:${p}Session) ON (n.token)`,
      `CREATE INDEX FOR (n:${p}Account) ON (n.userId)`,
      `CREATE INDEX FOR (n:${p}OauthClient) ON (n.clientId)`,
      `CREATE INDEX FOR (n:${p}OauthAccessToken) ON (n.token)`,
      `CREATE INDEX FOR (n:${p}OauthRefreshToken) ON (n.token)`,
      `CREATE INDEX FOR (n:${p}Verification) ON (n.identifier)`,
      `CREATE INDEX FOR (n:${p}Agent) ON (n.id)`,
      `CREATE INDEX FOR (n:${p}Agent) ON (n.userId, n.clientId)`,
      `CREATE INDEX FOR (n:${p}Event) ON (n.userId, n.createdAt)`,
    ];

    for (const statement of statements) {
      await g.query(statement).catch(() => {
        /* already exists */
      });
    }
  }
}

/** FalkorDB stores scalars only; anything structured is encoded on the way in. */
export function encodeValue(v: unknown): string | number | boolean | null {
  if (v === null || v === undefined) return null;
  if (v instanceof Date) return v.toISOString();
  if (typeof v === "bigint") return Number(v);
  if (typeof v === "object") return JSON.stringify(v);
  return v as string | number | boolean;
}

export function newId(prefix: string): string {
  return `${prefix}_${crypto.randomUUID().replace(/-/g, "").slice(0, 12)}`;
}

export function nowIso(): string {
  return new Date().toISOString();
}
