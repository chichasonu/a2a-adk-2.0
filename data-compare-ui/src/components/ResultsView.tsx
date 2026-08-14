import type { CompareResponse } from '../types'
import { RowTable } from './RowTable'

interface Props {
  result: CompareResponse
}

export function ResultsView({ result }: Props) {
  const { summary } = result
  const stats: [string, string | number][] = [
    ['Oracle rows', summary.oracle_row_count],
    ['BigQuery rows', summary.bigquery_row_count],
    ['Rows in both', summary.common_row_count],
    ['Rows matching', summary.rows_matching],
    ['Rows differing', summary.rows_with_differences],
    ['Oracle-only rows', summary.oracle_only_row_count],
    ['BigQuery-only rows', summary.bigquery_only_row_count],
    ['Join columns', summary.join_columns.join(', ')],
  ]

  return (
    <>
      <section className={`panel verdict ${result.matches ? 'match' : 'mismatch'}`}>
        <h2>{result.matches ? 'Tables match' : 'Differences found'}</h2>
        <div className="stats">
          {stats.map(([label, value]) => (
            <div className="stat" key={label}>
              <span className="stat-label">{label}</span>
              <span className="stat-value">{value}</span>
            </div>
          ))}
        </div>
        {summary.columns_with_differences.length > 0 && (
          <p>
            Columns with differences:{' '}
            <strong>{summary.columns_with_differences.join(', ')}</strong>
          </p>
        )}
        {(summary.oracle_only_columns.length > 0 ||
          summary.bigquery_only_columns.length > 0) && (
          <p className="muted">
            Schema drift — only in Oracle: {summary.oracle_only_columns.join(', ') || 'none'};
            only in BigQuery: {summary.bigquery_only_columns.join(', ') || 'none'}
          </p>
        )}
        {result.artifacts && (
          <ul className="artifacts">
            {Object.entries(result.artifacts).map(([name, path]) => (
              <li key={name}>
                {name}: <code>{path}</code>
              </li>
            ))}
          </ul>
        )}
      </section>

      <RowTable
        title="Rows with differing values"
        rows={result.mismatch_rows}
        truncated={result.truncated.mismatch_rows}
        emptyLabel="Every common row matches."
      />
      <RowTable
        title="Rows only in Oracle"
        rows={result.oracle_only_rows}
        truncated={result.truncated.oracle_only_rows}
      />
      <RowTable
        title="Rows only in BigQuery"
        rows={result.bigquery_only_rows}
        truncated={result.truncated.bigquery_only_rows}
      />

      <section className="panel">
        <h3>datacompy report</h3>
        <pre className="report">{result.report}</pre>
      </section>
    </>
  )
}
