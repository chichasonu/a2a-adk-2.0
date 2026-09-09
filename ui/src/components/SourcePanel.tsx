import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import type { ColumnInfo, Side } from "../types";

export interface SourceState {
  schema: string;
  table: string;
  dateColumn: string;
  dateFrom: string;
  dateTo: string;
}

interface Props {
  side: Side;
  title: string;
  state: SourceState;
  onChange: (next: SourceState) => void;
  columns: ColumnInfo[] | undefined;
  columnsLoading: boolean;
  columnsError: unknown;
  children?: React.ReactNode;
}

const labels: Record<Side, { schema: string; table: string }> = {
  oracle: { schema: "Schema", table: "Table / view" },
  bigquery: { schema: "Dataset", table: "Table" },
};

export function useColumns(side: Side, schema: string, table: string) {
  return useQuery({
    queryKey: ["columns", side, schema, table],
    queryFn: () => api.columns(side, schema, table),
    enabled: !!schema && !!table,
  });
}

export function SourcePanel({
  side,
  title,
  state,
  onChange,
  columns,
  columnsLoading,
  columnsError,
  children,
}: Props) {
  const schemas = useQuery({ queryKey: ["schemas", side], queryFn: () => api.schemas(side) });
  const tables = useQuery({
    queryKey: ["tables", side, state.schema],
    queryFn: () => api.tables(side, state.schema),
    enabled: !!state.schema,
  });
  const dateCandidates = (columns ?? []).filter((c) => /DATE|TIME/i.test(c.type));

  return (
    <section className={`panel panel-${side}`}>
      <h2>{title}</h2>
      <label>
        {labels[side].schema}
        <select
          value={state.schema}
          onChange={(e) =>
            onChange({ ...state, schema: e.target.value, table: "", dateColumn: "" })
          }
        >
          <option value="">{schemas.isLoading ? "Loading…" : "Select…"}</option>
          {(schemas.data ?? []).map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>
      {schemas.error ? <p className="error">{String((schemas.error as Error).message)}</p> : null}

      <label>
        {labels[side].table}
        <select
          value={state.table}
          disabled={!state.schema}
          onChange={(e) => onChange({ ...state, table: e.target.value, dateColumn: "" })}
        >
          <option value="">{tables.isLoading ? "Loading…" : "Select…"}</option>
          {(tables.data ?? []).map((t) => (
            <option key={t.name} value={t.name}>
              {t.name} {t.type !== "TABLE" ? `(${t.type.toLowerCase()})` : ""}
            </option>
          ))}
        </select>
      </label>
      {tables.error ? <p className="error">{String((tables.error as Error).message)}</p> : null}

      <fieldset className="date-filter" disabled={!state.table}>
        <legend>Date filter (optional)</legend>
        <label>
          Column
          <select
            value={state.dateColumn}
            onChange={(e) => onChange({ ...state, dateColumn: e.target.value })}
          >
            <option value="">None</option>
            {(dateCandidates.length ? dateCandidates : columns ?? []).map((c) => (
              <option key={c.name} value={c.name}>
                {c.name} ({c.type})
              </option>
            ))}
          </select>
        </label>
        <div className="date-range">
          <label>
            From
            <input
              type="date"
              value={state.dateFrom}
              disabled={!state.dateColumn}
              onChange={(e) => onChange({ ...state, dateFrom: e.target.value })}
            />
          </label>
          <label>
            To
            <input
              type="date"
              value={state.dateTo}
              disabled={!state.dateColumn}
              onChange={(e) => onChange({ ...state, dateTo: e.target.value })}
            />
          </label>
        </div>
      </fieldset>

      {columnsLoading ? <p className="muted">Loading columns…</p> : null}
      {columnsError ? <p className="error">{String((columnsError as Error).message)}</p> : null}
      {children}
    </section>
  );
}
