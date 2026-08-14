import { useEffect, useState } from 'react'
import './App.css'
import {
  fetchBigQueryTables,
  fetchColumns,
  fetchDefaults,
  fetchOracleTables,
  runComparison,
} from './api'
import { ColumnPicker } from './components/ColumnPicker'
import { ResultsView } from './components/ResultsView'
import type {
  BigQueryForm,
  ColumnsResponse,
  CompareOptions,
  CompareResponse,
  OracleForm,
} from './types'

const emptyOracle: OracleForm = {
  user: '',
  password: '',
  dsn: '',
  schema: '',
  table: '',
  where: '',
}

const emptyBigQuery: BigQueryForm = {
  project: '',
  dataset: '',
  table: '',
  location: '',
  where: '',
  credentials_json: '',
  credentials_json_content: '',
}

const defaultOptions: CompareOptions = {
  join_columns: [],
  ignore_columns: [],
  abs_tol: 0,
  rel_tol: 0,
  ignore_case: false,
  ignore_spaces: true,
  sample_count: 10,
  max_preview_rows: 100,
  write_report_files: false,
}

export default function App() {
  const [oracle, setOracle] = useState<OracleForm>(emptyOracle)
  const [bigquery, setBigQuery] = useState<BigQueryForm>(emptyBigQuery)
  const [options, setOptions] = useState<CompareOptions>(defaultOptions)
  const [oracleTables, setOracleTables] = useState<string[]>([])
  const [bigqueryTables, setBigQueryTables] = useState<string[]>([])
  const [columns, setColumns] = useState<ColumnsResponse | null>(null)
  const [result, setResult] = useState<CompareResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  useEffect(() => {
    fetchDefaults()
      .then((defaults) => {
        setOracle((current) => ({
          ...current,
          user: defaults.oracle.user,
          dsn: defaults.oracle.dsn,
          schema: defaults.oracle.schema,
          table: defaults.oracle.table,
        }))
        setBigQuery((current) => ({
          ...current,
          project: defaults.bigquery.project,
          dataset: defaults.bigquery.dataset,
          table: defaults.bigquery.table,
          location: defaults.bigquery.location,
        }))
        setOptions((current) => ({
          ...current,
          abs_tol: defaults.compare.abs_tol,
          rel_tol: defaults.compare.rel_tol,
          ignore_case: defaults.compare.ignore_case,
          ignore_spaces: defaults.compare.ignore_spaces,
        }))
      })
      .catch((cause: Error) => setError(cause.message))
  }, [])

  async function withBusy(label: string, action: () => Promise<void>) {
    setBusy(label)
    setError(null)
    try {
      await action()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(null)
    }
  }

  const loadOracleTables = () =>
    withBusy('oracle-tables', async () => {
      const { tables } = await fetchOracleTables(oracle)
      setOracleTables(tables)
    })

  const loadBigQueryTables = () =>
    withBusy('bigquery-tables', async () => {
      const { tables } = await fetchBigQueryTables(bigquery)
      setBigQueryTables(tables)
    })

  const loadColumns = () =>
    withBusy('columns', async () => {
      const response = await fetchColumns(oracle, bigquery)
      setColumns(response)
      setResult(null)
      setOptions((current) => ({
        ...current,
        join_columns: current.join_columns.filter((column) =>
          response.common_columns.includes(column),
        ),
        ignore_columns: current.ignore_columns.filter((column) =>
          response.common_columns.includes(column),
        ),
      }))
    })

  const compare = () =>
    withBusy('compare', async () => {
      setResult(await runComparison(oracle, bigquery, options))
    })

  const toggle = (list: string[], column: string) =>
    list.includes(column) ? list.filter((item) => item !== column) : [...list, column]

  return (
    <div className="app">
      <header>
        <h1>Oracle ↔ BigQuery data comparison</h1>
        <p className="muted">
          Powered by datacompy. Fields are prefilled from the server environment; anything you
          type here overrides it for this run only.
        </p>
      </header>

      {error && <div className="error">{error}</div>}

      <section className="panel">
        <h2>1. Source — Oracle</h2>
        <div className="grid">
          <label>
            User
            <input
              value={oracle.user}
              onChange={(event) => setOracle({ ...oracle, user: event.target.value })}
            />
          </label>
          <label>
            Password
            <input
              type="password"
              placeholder="leave empty to use ORACLE_PASSWORD"
              value={oracle.password}
              onChange={(event) => setOracle({ ...oracle, password: event.target.value })}
            />
          </label>
          <label>
            DSN
            <input
              placeholder="host:port/service"
              value={oracle.dsn}
              onChange={(event) => setOracle({ ...oracle, dsn: event.target.value })}
            />
          </label>
          <label>
            Schema
            <input
              value={oracle.schema}
              onChange={(event) => setOracle({ ...oracle, schema: event.target.value })}
            />
          </label>
          <label>
            Source table
            <input
              list="oracle-tables"
              placeholder="FEEDBACK"
              value={oracle.table}
              onChange={(event) => setOracle({ ...oracle, table: event.target.value })}
            />
            <datalist id="oracle-tables">
              {oracleTables.map((table) => (
                <option key={table} value={table} />
              ))}
            </datalist>
          </label>
          <label>
            WHERE (optional)
            <input
              value={oracle.where}
              onChange={(event) => setOracle({ ...oracle, where: event.target.value })}
            />
          </label>
        </div>
        <button onClick={loadOracleTables} disabled={busy !== null}>
          {busy === 'oracle-tables' ? 'Loading…' : 'List Oracle tables'}
        </button>
      </section>

      <section className="panel">
        <h2>2. Target — BigQuery</h2>
        <div className="grid">
          <label>
            Project
            <input
              value={bigquery.project}
              onChange={(event) => setBigQuery({ ...bigquery, project: event.target.value })}
            />
          </label>
          <label>
            Dataset
            <input
              value={bigquery.dataset}
              onChange={(event) => setBigQuery({ ...bigquery, dataset: event.target.value })}
            />
          </label>
          <label>
            Target table
            <input
              list="bigquery-tables"
              placeholder="FeedBack"
              value={bigquery.table}
              onChange={(event) => setBigQuery({ ...bigquery, table: event.target.value })}
            />
            <datalist id="bigquery-tables">
              {bigqueryTables.map((table) => (
                <option key={table} value={table} />
              ))}
            </datalist>
          </label>
          <label>
            Location
            <input
              value={bigquery.location}
              onChange={(event) => setBigQuery({ ...bigquery, location: event.target.value })}
            />
          </label>
          <label>
            Service account JSON path
            <input
              placeholder="leave empty to use BQ_CREDENTIALS_JSON"
              value={bigquery.credentials_json}
              onChange={(event) =>
                setBigQuery({ ...bigquery, credentials_json: event.target.value })
              }
            />
          </label>
          <label>
            WHERE (optional)
            <input
              value={bigquery.where}
              onChange={(event) => setBigQuery({ ...bigquery, where: event.target.value })}
            />
          </label>
        </div>
        <div className="actions">
          <button onClick={loadBigQueryTables} disabled={busy !== null}>
            {busy === 'bigquery-tables' ? 'Loading…' : 'List BigQuery tables'}
          </button>
          <button className="primary" onClick={loadColumns} disabled={busy !== null}>
            {busy === 'columns' ? 'Fetching columns…' : 'Fetch columns'}
          </button>
        </div>
      </section>

      {columns && (
        <>
          <ColumnPicker
            columns={columns}
            joinColumns={options.join_columns}
            ignoreColumns={options.ignore_columns}
            onToggleJoin={(column) =>
              setOptions({
                ...options,
                join_columns: toggle(options.join_columns, column),
                ignore_columns: options.ignore_columns.filter((item) => item !== column),
              })
            }
            onToggleIgnore={(column) =>
              setOptions({ ...options, ignore_columns: toggle(options.ignore_columns, column) })
            }
          />

          <section className="panel">
            <h2>4. Options</h2>
            <div className="grid">
              <label>
                Absolute tolerance
                <input
                  type="number"
                  step="any"
                  value={options.abs_tol}
                  onChange={(event) =>
                    setOptions({ ...options, abs_tol: Number(event.target.value) })
                  }
                />
              </label>
              <label>
                Relative tolerance
                <input
                  type="number"
                  step="any"
                  value={options.rel_tol}
                  onChange={(event) =>
                    setOptions({ ...options, rel_tol: Number(event.target.value) })
                  }
                />
              </label>
              <label>
                Preview rows
                <input
                  type="number"
                  min="1"
                  value={options.max_preview_rows}
                  onChange={(event) =>
                    setOptions({ ...options, max_preview_rows: Number(event.target.value) })
                  }
                />
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={options.ignore_case}
                  onChange={(event) =>
                    setOptions({ ...options, ignore_case: event.target.checked })
                  }
                />
                Ignore case
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={options.ignore_spaces}
                  onChange={(event) =>
                    setOptions({ ...options, ignore_spaces: event.target.checked })
                  }
                />
                Ignore surrounding spaces
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={options.write_report_files}
                  onChange={(event) =>
                    setOptions({ ...options, write_report_files: event.target.checked })
                  }
                />
                Write report/CSV files on the server
              </label>
            </div>
            <button
              className="primary"
              onClick={compare}
              disabled={busy !== null || options.join_columns.length === 0}
            >
              {busy === 'compare' ? 'Comparing…' : 'Run comparison'}
            </button>
            {options.join_columns.length === 0 && (
              <p className="warning">Select at least one join key column.</p>
            )}
          </section>
        </>
      )}

      {result && <ResultsView result={result} />}
    </div>
  )
}
