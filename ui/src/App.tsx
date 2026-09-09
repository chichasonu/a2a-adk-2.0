import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { ColumnMapping, autoMap, type Mapping } from "./components/ColumnMapping";
import { ResultsView } from "./components/ResultsView";
import { SourcePanel, useColumns, type SourceState } from "./components/SourcePanel";
import type { AnalyseRequest, Job } from "./types";

const empty: SourceState = { schema: "", table: "", dateColumn: "", dateFrom: "", dateTo: "" };

export default function App() {
  const [oracle, setOracle] = useState<SourceState>(empty);
  const [bigquery, setBigquery] = useState<SourceState>(empty);
  const [mappings, setMappings] = useState<Mapping[]>([]);
  const [options, setOptions] = useState({
    abs_tol: 0,
    rel_tol: 0,
    ignore_case: false,
    ignore_spaces: true,
    preview_rows: 200,
  });
  const [job, setJob] = useState<Job | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const oracleCols = useColumns("oracle", oracle.schema, oracle.table);
  const bqCols = useColumns("bigquery", bigquery.schema, bigquery.table);

  useEffect(() => {
    if (oracleCols.data && bqCols.data) {
      setMappings(autoMap(oracleCols.data, bqCols.data).filter((m) => m.bigquery));
    } else {
      setMappings([]);
    }
  }, [oracleCols.data, bqCols.data]);

  useEffect(() => {
    if (!job || job.status === "done" || job.status === "failed") return;
    const timer = setInterval(async () => {
      try {
        setJob(await api.job(job.id));
      } catch (err) {
        setSubmitError((err as Error).message);
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [job]);

  const validation = useMemo(() => {
    if (!oracle.table) return "Select an Oracle table";
    if (!bigquery.table) return "Select a BigQuery table";
    if (!mappings.length) return "Select at least one column to compare";
    if (mappings.some((m) => !m.bigquery)) return "Every selected column needs a BigQuery column";
    if (!mappings.some((m) => m.join)) return "Mark at least one join key";
    for (const [side, s] of [
      ["Oracle", oracle],
      ["BigQuery", bigquery],
    ] as const) {
      if (s.dateColumn && !s.dateFrom && !s.dateTo) return `${side}: enter a date range`;
    }
    return null;
  }, [oracle, bigquery, mappings]);

  const running = job?.status === "queued" || job?.status === "running";

  const analyse = async () => {
    setSubmitError(null);
    const body: AnalyseRequest = {
      oracle: {
        schema: oracle.schema,
        table: oracle.table,
        columns: mappings.map((m) => m.oracle),
        date_column: oracle.dateColumn,
        date_from: oracle.dateColumn ? oracle.dateFrom : "",
        date_to: oracle.dateColumn ? oracle.dateTo : "",
      },
      bigquery: {
        schema: bigquery.schema,
        table: bigquery.table,
        columns: mappings.map((m) => m.bigquery),
        date_column: bigquery.dateColumn,
        date_from: bigquery.dateColumn ? bigquery.dateFrom : "",
        date_to: bigquery.dateColumn ? bigquery.dateTo : "",
      },
      join_columns: mappings.filter((m) => m.join).map((m) => m.oracle),
      ...options,
    };
    try {
      setJob(await api.analyse(body));
    } catch (err) {
      setSubmitError((err as Error).message);
    }
  };

  return (
    <main>
      <header>
        <h1>Oracle ↔ BigQuery record comparison</h1>
        <p className="muted">
          DuckDB + datacompy ·{" "}
          {health.data
            ? `Oracle ${health.data.oracle_configured ? "configured" : "NOT configured"} · BigQuery ${
                health.data.bigquery_configured ? "configured" : "NOT configured"
              }`
            : health.error
              ? `API unreachable: ${(health.error as Error).message}`
              : "checking API…"}
        </p>
      </header>

      <div className="sources">
        <SourcePanel
          side="oracle"
          title="Oracle"
          state={oracle}
          onChange={setOracle}
          columns={oracleCols.data}
          columnsLoading={oracleCols.isLoading}
          columnsError={oracleCols.error}
        />
        <SourcePanel
          side="bigquery"
          title="BigQuery"
          state={bigquery}
          onChange={setBigquery}
          columns={bqCols.data}
          columnsLoading={bqCols.isLoading}
          columnsError={bqCols.error}
        />
      </div>

      {oracleCols.data && bqCols.data ? (
        <ColumnMapping
          oracleColumns={oracleCols.data}
          bigqueryColumns={bqCols.data}
          mappings={mappings}
          onChange={setMappings}
        />
      ) : null}

      <section className="panel options">
        <h2>Options</h2>
        <div className="options-grid">
          <label>
            Absolute tolerance
            <input
              type="number"
              step="any"
              min={0}
              value={options.abs_tol}
              onChange={(e) => setOptions({ ...options, abs_tol: Number(e.target.value) })}
            />
          </label>
          <label>
            Relative tolerance
            <input
              type="number"
              step="any"
              min={0}
              value={options.rel_tol}
              onChange={(e) => setOptions({ ...options, rel_tol: Number(e.target.value) })}
            />
          </label>
          <label>
            Preview rows
            <input
              type="number"
              min={1}
              max={500}
              value={options.preview_rows}
              onChange={(e) => setOptions({ ...options, preview_rows: Number(e.target.value) })}
            />
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={options.ignore_spaces}
              onChange={(e) => setOptions({ ...options, ignore_spaces: e.target.checked })}
            />
            Ignore leading/trailing spaces
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={options.ignore_case}
              onChange={(e) => setOptions({ ...options, ignore_case: e.target.checked })}
            />
            Ignore case
          </label>
        </div>
        <div className="analyse-row">
          <button
            type="button"
            className="primary"
            disabled={!!validation || running}
            onClick={analyse}
          >
            {running ? "Analysing…" : "Analyse"}
          </button>
          {validation ? <span className="muted">{validation}</span> : null}
          {running ? (
            <span className="muted">
              <span className="spinner" /> {job?.stage}
            </span>
          ) : null}
        </div>
        {submitError ? <p className="error">{submitError}</p> : null}
        {job?.status === "failed" ? <p className="error">Comparison failed: {job.error}</p> : null}
      </section>

      {job?.status === "done" && job.result ? <ResultsView result={job.result} /> : null}
    </main>
  );
}
