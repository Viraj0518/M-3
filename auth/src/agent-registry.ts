import { GraphClient, newId, nowIso } from "./graph-client.js";

export interface Agent {
  id: string;
  userId: string;
  clientId: string;
  displayName: string;
  kind: "agent" | "human";
  color: string;
  firstSeen: string;
  lastSeen: string;
}

export interface AgentRef {
  id: string;
  name: string;
  color: string;
  kind: "agent" | "human";
}

export interface UpsertAgentInput {
  userId: string;
  clientId: string;
  displayName?: string;
  kind?: "agent" | "human";
}

/**
 * The registry of principals acting on one user's behalf.
 *
 * An agent is the pair (user x OAuth client): the same person arriving through
 * two different clients produces two Agent nodes hanging off one User. That
 * split is the whole reason attribution is worth recording — without it every
 * write collapses to "the user did it" and the audit trail says nothing.
 */
export class AgentRegistry {
  /** Matches FalkorAuthAdapter's prefix so `:AuthUser` joins `:AuthAgent`. */
  readonly prefix: string;

  /** Deterministic identity colours, so an agent looks the same every run. */
  private static readonly PALETTE = [
    "#7cc4ff", "#ff7ce0", "#7cffb2", "#ffd97c",
    "#c07cff", "#ff9a7c", "#7cfff0", "#ff7c9a",
  ];

  constructor(
    private readonly graph: GraphClient,
    options: { prefix?: string } = {},
  ) {
    this.prefix = options.prefix ?? process.env.AUTH_LABEL_PREFIX ?? "Auth";
  }

  private get userLabel(): string { return `${this.prefix}User`; }
  private get agentLabel(): string { return `${this.prefix}Agent`; }

  static colorFor(clientId: string): string {
    let h = 0;
    for (let i = 0; i < clientId.length; i++) {
      h = (h * 31 + clientId.charCodeAt(i)) >>> 0;
    }
    return AgentRegistry.PALETTE[h % AgentRegistry.PALETTE.length];
  }

  static toRef(agent: Agent): AgentRef {
    return { id: agent.id, name: agent.displayName, color: agent.color, kind: agent.kind };
  }

  /** Register (or touch) the agent behind a call, and attach it to its human. */
  async upsert(input: UpsertAgentInput): Promise<Agent> {
    const ts = nowIso();
    const displayName = input.displayName?.trim() || input.clientId;

    const row = await this.graph.queryOne<{ a: { properties: Agent } }>(
      `MERGE (u:${this.userLabel} { id: $userId })
       MERGE (a:${this.agentLabel} { userId: $userId, clientId: $clientId })
         ON CREATE SET a.id = $newId, a.firstSeen = $ts, a.kind = $kind,
                       a.color = $color, a.displayName = $displayName
         ON MATCH  SET a.displayName = $displayName
       SET a.lastSeen = $ts
       MERGE (u)-[:HAS_AGENT]->(a)
       RETURN a`,
      {
        userId: input.userId,
        clientId: input.clientId,
        newId: newId("ag"),
        ts,
        kind: input.kind ?? "agent",
        color: AgentRegistry.colorFor(input.clientId),
        displayName,
      },
    );

    return row!.a.properties;
  }

  /** Every principal that currently holds access to a user's data. */
  async list(userId: string): Promise<Agent[]> {
    const rows = await this.graph.query<{ a: { properties: Agent } }>(
      `MATCH (:${this.userLabel} { id: $userId })-[:HAS_AGENT]->(a:${this.agentLabel})
       RETURN a ORDER BY a.lastSeen DESC`,
      { userId },
    );
    return rows.map((r) => r.a.properties);
  }

  async find(userId: string, clientId: string): Promise<Agent | null> {
    const row = await this.graph.queryOne<{ a: { properties: Agent } }>(
      `MATCH (a:${this.agentLabel} { userId: $userId, clientId: $clientId }) RETURN a LIMIT 1`,
      { userId, clientId },
    );
    return row?.a.properties ?? null;
  }

  /** Revoke an agent: drops the node and every edge it owns. */
  async revoke(userId: string, agentId: string): Promise<boolean> {
    const row = await this.graph.queryOne<{ c: number }>(
      `MATCH (a:${this.agentLabel} { id: $agentId, userId: $userId })
       WITH a, count(a) AS c
       DETACH DELETE a
       RETURN c`,
      { userId, agentId },
    );
    return Number(row?.c ?? 0) > 0;
  }
}
