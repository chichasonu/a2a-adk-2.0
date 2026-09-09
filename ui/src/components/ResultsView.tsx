import { useState } from "react";
import type { AnalyseResult } from "../types";
import { RowTable } from "./RowTable";

type Tab = "mismatch" | "oracle" | "bigquery" | "report";

const n = (v: number) => v.toLocaleString();

export function ResultsView({ result }: { result: AnalyseResult }) {
  const [tab, setTab] = useState<Tab>("mismatch");
  const diffCols = result.column_stats.filter((s) => s.mismatches > 0);

  return (
    <section className="panel results">
      <div className="panel-header">
        <h2>Results</h2>
        <span className={`badge ${result.matches ? "ok" : "bad"}`}>
          {result.matches ? "MATCH" : "DIFFERENCES FOUND"}
        </span>
      </div>

      <div className="stats">
        <Stat label="Oracle rows" value={n(result.oracle_row_count)} />
        <Stat label="BigQuery rows" value={n(result.bigquery_row_count)} />
        <Stat label="Rows in both" value={n(result.common_row_count)} />
        <Stat label="Matching" value={n(result.rows_matching)} tone="ok" />
        <Stat label="Mismatched" value={n(result.rows_with_differences)} tone="bad" />
        <Stat label="Only in Oracle" value={n(result.oracle_only_row_count)} tone="warn" />
        <Stat label="Only in BigQuery" value={n(result.bigquery_only_row_count)} tone="warn" />
      </div>
      <p className="muted">
        Join: {result.join_columns.join(", ")} · duplicate keys: Oracle{" "}
        {n(result.oracle_duplicate_keys)}, BigQuery {n(result.bigquery_duplicate_keys)} · datacompy
        ran on {result.datacompy_scope}
      </p>

      {diffCols.length ? (
        <table className="column-stats">
          <thead>
            <tr>
              <th>Column</th>
              <th>Oracle type</th>
              <th>BigQuery type</th>
              <th className="num"># mismatches</th>
            </tr>
          </thead>
          <tbody>
            {diffCols.map((s) => (
              <tr key={s.column}>
                <td>
                  <code>{s.column}</code>
                </td>
                <td>{s.oracle_type}</td>
                <td>{s.bigquery_type}</td>
                <td className="num">{n(s.mismatches)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      <div className="tabs">
        <TabButton id="mismatch" tab={tab} setTab={setTab}>
          Mismatched records ({n(result.rows_with_differences)})
        </TabButton>
        <TabButton id="oracle" tab={tab} setTab={setTab}>
          Only in Oracle ({n(result.oracle_only_row_count)})
        </TabButton>
        <TabButton id="bigquery" tab={tab} setTab={setTab}>
          Only in BigQuery ({n(result.bigquery_only_row_count)})
        </TabButton>
        <TabButton id="report" tab={tab} setTab={setTab}>
          datacompy report
        </TabButton>
      </div>
      <p className="muted">Showing up to {result.preview_limit} records per list.</p>

      {tab === "mismatch" ? (
        <RowTable rows={result.mismatch_rows} emptyText="No mismatched records." highlightDiff />
      ) : null}
      {tab === "oracle" ? (
        <RowTable rows={result.oracle_only_rows} emptyText="No Oracle-only records." />
      ) : null}
      {tab === "bigquery" ? (
        <RowTable rows={result.bigquery_only_rows} emptyText="No BigQuery-only records." />
      ) : null}
      {tab === "report" ? <pre className="report">{result.datacompy_report}</pre> : null}
    </section>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className={`stat ${tone ?? ""}`}>
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}

function TabButton({
  id,
  tab,
  setTab,
  children,
}: {
  id: Tab;
  tab: Tab;
  setTab: (t: Tab) => void;
  children: React.ReactNode;
}) {
  return (
    <button type="button" className={tab === id ? "active" : ""} onClick={() => setTab(id)}>
      {children}
    </button>
  );
}
