import type { AnalyseRequest, ColumnInfo, Health, Job, Side, TableInfo } from "./types";

const BASE = import.meta.env.VITE_API_URL ?? "";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(await errorText(res));
  return (await res.json()) as T;
}

async function errorText(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    return JSON.stringify(body.detail ?? body);
  } catch {
    return `${res.status} ${res.statusText}`;
  }
}

const root = (side: Side) => (side === "oracle" ? "/api/oracle/schemas" : "/api/bigquery/datasets");

export const api = {
  health: () => get<Health>("/api/health"),
  schemas: (side: Side) => get<string[]>(root(side)),
  tables: (side: Side, schema: string) =>
    get<TableInfo[]>(`${root(side)}/${encodeURIComponent(schema)}/tables`),
  columns: (side: Side, schema: string, table: string) =>
    get<ColumnInfo[]>(
      `${root(side)}/${encodeURIComponent(schema)}/tables/${encodeURIComponent(table)}/columns`,
    ),
  analyse: async (body: AnalyseRequest): Promise<Job> => {
    const res = await fetch(`${BASE}/api/analyse`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(await errorText(res));
    return (await res.json()) as Job;
  },
  job: (id: string) => get<Job>(`/api/jobs/${id}`),
};
