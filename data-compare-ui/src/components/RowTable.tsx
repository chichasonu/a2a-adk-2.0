import type { Row } from '../types'

interface Props {
  title: string
  rows: Row[]
  truncated?: boolean
  emptyLabel?: string
}

export function RowTable({ title, rows, truncated, emptyLabel }: Props) {
  if (rows.length === 0) {
    return (
      <section className="panel">
        <h3>{title}</h3>
        <p className="muted">{emptyLabel ?? 'No rows.'}</p>
      </section>
    )
  }
  const columns = Object.keys(rows[0])
  return (
    <section className="panel">
      <h3>
        {title} <span className="badge">{rows.length}</span>
      </h3>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column}>{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index}>
                {columns.map((column) => (
                  <td key={column}>{row[column] === null ? '∅' : String(row[column])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {truncated && <p className="muted">Preview truncated; download the CSV artifacts for all rows.</p>}
    </section>
  )
}
