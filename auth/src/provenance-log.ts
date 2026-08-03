import { GraphClient, newId, nowIso } from "./graph-client.js";

export type AuthEventType =
  | "auth.granted"
  | "agent.registered"
  | "resource.read"
  | "resource.written";

export interface ResourceRef {
  id: string;
  title?: string;
  kind?: string;
  score?: number;
}

export interface RecordEventInput {
  userId: string;
  agentId?: string | null;
  eventType: AuthEventType;
  tool?: string | null;
  query?: string | null;
  resources?: ResourceRef[];
  latencyMs?: number | null;
}

export interface ProvenanceRow {
  id: string;
  agentId: string | null;
  agentName: string | null;
  color: string | null;
  eventType: string;
  tool: string | null;
  query: string | null;
  resourceIds: string;
  latencyMs: number | null;
  createdAt: string;
}

/**
 * What each agent actually did, recorded as graph edges rather than an audit
 * column.
 *
 * Edges are matched on node `id` WITHOUT a label on purpose: the auth module
 * should not need to know what the host application calls its records. Point
 * it at `:Memory`, `:Claim`, `:Document` — the traversal is identical.
 */
export class ProvenanceLog {
  readonly prefix: string;

  constructor(
    private readonly graph: GraphClient,
    options: { prefix?: string } = {},
  ) {
    this.prefix = options.prefix ?? process.env.AUTH_LABEL_PREFIX ?? "Auth";
  }

  private get eventLabel(): string { return `${this.prefix}Event`; }
  private get agentLabel(): string { return `${this.prefix}Agent`; }

  /**
   * Record one event. Writes a timeline node, and — for reads and writes —
   * an edge per resource touched, which is what makes lineage a traversal.
   */
  async record(input: RecordEventInput): Promise<void> {
    const at = nowIso();
    const resources = input.resources ?? [];

    await this.graph.query(
      `CREATE (e:${this.eventLabel} {
         id: $id, userId: $userId, agentId: $agentId, eventType: $eventType,
         tool: $tool, query: $query, resourceIds: $resourceIds,
         latencyMs: $latencyMs, createdAt: $createdAt
       })`,
      {
        id: newId("ev"),
        userId: input.userId,
        agentId: input.agentId ?? null,
        eventType: input.eventType,
        tool: input.tool ?? null,
        query: input.query ?? null,
        resourceIds: JSON.stringify(resources.map((r) => r.id)),
        latencyMs: input.latencyMs ?? null,
        createdAt: at,
      },
    );

    if (!input.agentId || !resources.length) return;

    // Edges are best-effort by design. When auth runs in its own graph key —
    // which is the right choice against a host that projects with an
    // unfiltered `MATCH (n)` — the resource nodes simply are not reachable and
    // this matches nothing. The timeline node above still carries resourceIds,
    // so the audit trail survives either way; only the traversal shortcut is
    // lost. Failing loudly here would make a correct configuration look broken.
    const edge = input.eventType === "resource.written" ? "WROTE" : "READ";
    await this.graph.query(
      `MATCH (a:${this.agentLabel} { id: $agentId })
       UNWIND $ids AS rid
       MATCH (r { id: rid })
       CREATE (a)-[:${edge} { at: $at, query: $query, tool: $tool }]->(r)`,
      {
        agentId: input.agentId,
        ids: resources.map((r) => r.id),
        at,
        query: input.query ?? "",
        tool: input.tool ?? "",
      },
    );
  }

  /** The attributed timeline, newest first. */
  async recent(userId: string, limit = 20): Promise<ProvenanceRow[]> {
    return this.graph.query<ProvenanceRow>(
      `MATCH (e:${this.eventLabel} { userId: $userId })
       OPTIONAL MATCH (a:${this.agentLabel} { id: e.agentId })
       RETURN e.id AS id, e.agentId AS agentId, a.displayName AS agentName,
              a.color AS color, e.eventType AS eventType, e.tool AS tool,
              e.query AS query, e.resourceIds AS resourceIds,
              e.latencyMs AS latencyMs, e.createdAt AS createdAt
       ORDER BY e.createdAt DESC
       LIMIT $limit`,
      { userId, limit },
    );
  }

  /**
   * Lineage of one resource: who wrote it, who has read it.
   *
   * Two hops in opposite directions across the same node — the query this
   * whole design exists to make trivial.
   */
  async lineage(userId: string, resourceId: string) {
    const writtenBy = await this.graph.queryOne<{
      id: string;
      displayName: string;
      color: string;
      kind: string;
    }>(
      `MATCH (a:${this.agentLabel} { userId: $userId })-[:WROTE]->(r { id: $resourceId })
       RETURN a.id AS id, a.displayName AS displayName, a.color AS color, a.kind AS kind
       LIMIT 1`,
      { userId, resourceId },
    );

    const readBy = await this.graph.query<{
      id: string;
      displayName: string;
      color: string;
      kind: string;
      reads: number;
      lastRead: string;
    }>(
      `MATCH (a:${this.agentLabel} { userId: $userId })-[r:READ]->(x { id: $resourceId })
       RETURN a.id AS id, a.displayName AS displayName, a.color AS color,
              a.kind AS kind, count(r) AS reads, max(r.at) AS lastRead
       ORDER BY lastRead DESC`,
      { userId, resourceId },
    );

    return { writtenBy, readBy };
  }

  /**
   * The cross-agent question: what one agent wrote that another has read.
   * A self-join over an audit table anywhere else; two edges here.
   */
  async crossAgentReads(userId: string, limit = 20) {
    return this.graph.query<{
      resourceId: string;
      writer: string;
      reader: string;
      readerColor: string;
      at: string;
    }>(
      `MATCH (w:${this.agentLabel} { userId: $userId })-[:WROTE]->(r)<-[rd:READ]-(x:${this.agentLabel} { userId: $userId })
        WHERE w.id <> x.id
       RETURN r.id AS resourceId, w.displayName AS writer,
              x.displayName AS reader, x.color AS readerColor, rd.at AS at
       ORDER BY rd.at DESC
       LIMIT $limit`,
      { userId, limit },
    );
  }
}
