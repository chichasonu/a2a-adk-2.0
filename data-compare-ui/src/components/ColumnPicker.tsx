import type { ColumnsResponse } from '../types'

interface Props {
  columns: ColumnsResponse
  joinColumns: string[]
  ignoreColumns: string[]
  onToggleJoin: (column: string) => void
  onToggleIgnore: (column: string) => void
}

export function ColumnPicker({
  columns,
  joinColumns,
  ignoreColumns,
  onToggleJoin,
  onToggleIgnore,
}: Props) {
  return (
    <section className="panel">
      <h2>3. Pick key and ignored columns</h2>
      <p className="muted">
        {columns.common_columns.length} common column(s). Join (key) columns identify a row;
        ignored columns are excluded from the comparison.
      </p>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Column</th>
              <th>Oracle type</th>
              <th>BigQuery type</th>
              <th>Join key</th>
              <th>Ignore</th>
            </tr>
          </thead>
          <tbody>
            {columns.common_columns.map((column) => {
              const oracleType = columns.oracle_columns.find(
                (candidate) => candidate.name.toLowerCase() === column,
              )?.type
              const bigqueryType = columns.bigquery_columns.find(
                (candidate) => candidate.name.toLowerCase() === column,
              )?.type
              return (
                <tr key={column}>
                  <td>{column}</td>
                  <td>{oracleType ?? '—'}</td>
                  <td>{bigqueryType ?? '—'}</td>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`Use ${column} as join key`}
                      checked={joinColumns.includes(column)}
                      onChange={() => onToggleJoin(column)}
                    />
                  </td>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`Ignore ${column}`}
                      checked={ignoreColumns.includes(column)}
                      disabled={joinColumns.includes(column)}
                      onChange={() => onToggleIgnore(column)}
                    />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {(columns.oracle_only_columns.length > 0 ||
        columns.bigquery_only_columns.length > 0) && (
        <p className="warning">
          Not comparable — only in Oracle: {columns.oracle_only_columns.join(', ') || 'none'};
          only in BigQuery: {columns.bigquery_only_columns.join(', ') || 'none'}
        </p>
      )}
    </section>
  )
}
