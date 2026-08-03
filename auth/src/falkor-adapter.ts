import { createAdapterFactory } from "better-auth/adapters";
import { GraphClient, encodeValue } from "./graph-client.js";

/** One clause of a Better Auth where-list, after the factory has cleaned it. */
export interface CleanedWhere {
  field: string;
  value: unknown;
  operator?: string;
  connector?: "AND" | "OR";
  mode?: "sensitive" | "insensitive";
}

/**
 * Better Auth storage, backed by FalkorDB.
 *
 * Better Auth's adapter contract is plain CRUD over named models, so each
 * model becomes a node label and each field a node property — nothing here
 * needs a relational engine.
 *
 * Putting identity in the same graph as agents and provenance is the reason to
 * do it at all: the `user` node written on sign-up is the SAME node the
 * provenance edges hang off, so "which agent read this person's data" is one
 * traversal rather than a join across two stores.
 */
export class FalkorAuthAdapter {
  /**
   * Prefixed on every label this adapter writes.
   *
   * Load-bearing when the module shares a graph with a host application: a
   * bare `:User` or `:Agent` will collide with whatever the host already means
   * by those words, and the collision is silent — the host's queries simply
   * start returning nodes that have none of its expected properties. Prefixing
   * makes co-tenancy safe by construction.
   */
  readonly prefix: string;

  constructor(
    private readonly graph: GraphClient,
    options: { prefix?: string } = {},
  ) {
    this.prefix = options.prefix ?? process.env.AUTH_LABEL_PREFIX ?? "Auth";
  }

  /** `user` -> `AuthUser`, `oauthClient` -> `AuthOauthClient`. */
  label(model: string): string {
    const clean = model.replace(/[^A-Za-z0-9_]/g, "");
    return `${this.prefix}${clean.charAt(0).toUpperCase()}${clean.slice(1)}`;
  }

  /** Compile a where-list into a Cypher predicate, filling `params`. */
  static buildWhere(
    where: CleanedWhere[] | undefined,
    params: Record<string, unknown>,
    alias = "n",
  ): string {
    if (!where?.length) return "";

    const parts = where.map((clause, i) => {
      const key = `w${i}`;
      const insensitive = clause.mode === "insensitive" && typeof clause.value === "string";

      // FalkorDB has no case-insensitive comparison operator, so both sides
      // get folded to lower case.
      const field = insensitive ? `toLower(${alias}.${clause.field})` : `${alias}.${clause.field}`;
      const value = Array.isArray(clause.value)
        ? clause.value.map((v) =>
            insensitive && typeof v === "string" ? v.toLowerCase() : encodeValue(v),
          )
        : insensitive
          ? (clause.value as string).toLowerCase()
          : encodeValue(clause.value);

      params[key] = value;
      const p = `$${key}`;

      let predicate: string;
      switch (clause.operator ?? "eq") {
        case "ne":          predicate = `${field} <> ${p}`; break;
        case "lt":          predicate = `${field} < ${p}`; break;
        case "lte":         predicate = `${field} <= ${p}`; break;
        case "gt":          predicate = `${field} > ${p}`; break;
        case "gte":         predicate = `${field} >= ${p}`; break;
        case "in":          predicate = `${field} IN ${p}`; break;
        case "not_in":      predicate = `NOT ${field} IN ${p}`; break;
        case "contains":    predicate = `${field} CONTAINS ${p}`; break;
        case "starts_with": predicate = `${field} STARTS WITH ${p}`; break;
        case "ends_with":   predicate = `${field} ENDS WITH ${p}`; break;
        // `= null` is never true in Cypher; equality against null must be IS NULL.
        default:
          predicate = clause.value === null ? `${field} IS NULL` : `${field} = ${p}`;
          break;
      }

      return { predicate, connector: clause.connector ?? "AND", first: i === 0 };
    });

    const expr = parts
      .map((p) => (p.first ? p.predicate : ` ${p.connector} ${p.predicate}`))
      .join("");

    return `WHERE ${expr}`;
  }

  /**
   * Node properties, returned exactly as stored.
   *
   * Deliberately no JSON parsing: with `supportsJSON: false` Better Auth
   * serialises structured fields itself and expects the same string back.
   * Decoding here hands it an object it then tries to parse again, which is
   * how OAuth authorization-code payloads end up "malformed".
   */
  static toRecord(node: unknown, select?: string[]): Record<string, unknown> | null {
    const props = (node as { properties?: unknown })?.properties ?? node;
    if (!props || typeof props !== "object") return null;

    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props as Record<string, unknown>)) {
      if (select?.length && !select.includes(k)) continue;
      out[k] = v;
    }
    return out;
  }

  static setClause(
    update: Record<string, unknown>,
    params: Record<string, unknown>,
  ): string {
    const assignments = Object.keys(update).map((field, i) => {
      params[`u${i}`] = encodeValue(update[field]);
      return `n.${field} = $u${i}`;
    });
    return assignments.length ? `SET ${assignments.join(", ")}` : "";
  }

  /** The object Better Auth's `database` option expects. */
  toBetterAuthAdapter() {
    const graph = this.graph;
    const label = (model: string) => this.label(model);
    const buildWhere = FalkorAuthAdapter.buildWhere;
    const toRecord = FalkorAuthAdapter.toRecord;
    const setClause = (u: Record<string, unknown>, p: Record<string, unknown>) =>
      FalkorAuthAdapter.setClause(u, p);

    return createAdapterFactory({
      config: {
        adapterId: "falkordb",
        adapterName: "FalkorDB",
        usePlural: false,
        // FalkorDB stores scalars only, so let Better Auth flatten these
        // rather than reimplementing the coercions here.
        supportsJSON: false,
        supportsDates: false,
        supportsArrays: false,
        supportsBooleans: true,
        supportsNumericIds: false,
        // No multi-statement transactions over the Redis protocol; the factory
        // falls back to sequential execution.
        transaction: false,
      },

      adapter: ({ debugLog }) => ({
        async create({ model, data, select }) {
          const params: Record<string, unknown> = {};
          const props = Object.keys(data as Record<string, unknown>).map((field, i) => {
            params[`c${i}`] = encodeValue((data as Record<string, unknown>)[field]);
            return `${field}: $c${i}`;
          });

          const row = await graph.queryOne<{ n: unknown }>(
            `CREATE (n:${label(model)} { ${props.join(", ")} }) RETURN n`,
            params,
          );
          return (toRecord(row?.n, select) ?? data) as never;
        },

        async findOne({ model, where, select, join }) {
          if (join) debugLog("falkordb: join requested but not supported; ignoring", join);
          const params: Record<string, unknown> = {};
          const clause = buildWhere(where as CleanedWhere[], params);

          const row = await graph.queryOne<{ n: unknown }>(
            `MATCH (n:${label(model)}) ${clause} RETURN n LIMIT 1`,
            params,
          );
          return (toRecord(row?.n, select) ?? null) as never;
        },

        async findMany({ model, where, limit, select, sortBy, offset, join }) {
          if (join) debugLog("falkordb: join requested but not supported; ignoring", join);
          const params: Record<string, unknown> = {};
          const clause = buildWhere(where as CleanedWhere[], params);

          const order = sortBy
            ? `ORDER BY n.${sortBy.field} ${sortBy.direction === "desc" ? "DESC" : "ASC"}`
            : "";
          const skip = offset ? `SKIP ${Number(offset)}` : "";
          const take = limit ? `LIMIT ${Number(limit)}` : "";

          const rows = await graph.query<{ n: unknown }>(
            `MATCH (n:${label(model)}) ${clause} RETURN n ${order} ${skip} ${take}`,
            params,
          );
          return rows.map((r) => toRecord(r.n, select)).filter(Boolean) as never;
        },

        async update({ model, where, update }) {
          const params: Record<string, unknown> = {};
          const clause = buildWhere(where as CleanedWhere[], params);
          const set = setClause(update as Record<string, unknown>, params);
          if (!set) return null as never;

          // LIMIT before SET, so a broad where-clause cannot touch more rows
          // than the single-row contract promises.
          const row = await graph.queryOne<{ n: unknown }>(
            `MATCH (n:${label(model)}) ${clause} WITH n LIMIT 1 ${set} RETURN n`,
            params,
          );
          return (toRecord(row?.n) ?? null) as never;
        },

        async updateMany({ model, where, update }) {
          const params: Record<string, unknown> = {};
          const clause = buildWhere(where as CleanedWhere[], params);
          const set = setClause(update, params);
          if (!set) return 0;

          const rows = await graph.query<{ c: number }>(
            `MATCH (n:${label(model)}) ${clause} ${set} RETURN count(n) AS c`,
            params,
          );
          return Number(rows?.[0]?.c ?? 0);
        },

        async delete({ model, where }) {
          const params: Record<string, unknown> = {};
          const clause = buildWhere(where as CleanedWhere[], params);
          // DETACH, so deleting a user also drops its provenance edges rather
          // than leaving dangling relationships behind.
          await graph.query(
            `MATCH (n:${label(model)}) ${clause} WITH n LIMIT 1 DETACH DELETE n`,
            params,
          );
        },

        async deleteMany({ model, where }) {
          const params: Record<string, unknown> = {};
          const clause = buildWhere(where as CleanedWhere[], params);
          const rows = await graph.query<{ c: number }>(
            `MATCH (n:${label(model)}) ${clause}
             WITH collect(n) AS ns, count(n) AS c
             FOREACH (x IN ns | DETACH DELETE x)
             RETURN c`,
            params,
          );
          return Number(rows?.[0]?.c ?? 0);
        },

        async count({ model, where }) {
          const params: Record<string, unknown> = {};
          const clause = buildWhere(where as CleanedWhere[], params);
          const rows = await graph.query<{ c: number }>(
            `MATCH (n:${label(model)}) ${clause} RETURN count(n) AS c`,
            params,
          );
          return Number(rows?.[0]?.c ?? 0);
        },
      }),
    });
  }
}
