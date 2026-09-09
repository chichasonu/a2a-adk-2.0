import type { Row } from "../types";

interface Props {
  rows: Row[];
  emptyText: string;
  highlightDiff?: boolean;
}

function fmt(v: Row[string]): string {
  if (v === null || v === undefined) return "∅";
  return String(v);
}

export function RowTable({ rows, emptyText, highlightDiff = false }: Props) {
  if (!rows.length) return <p className="muted">{emptyText}</p>;
  const columns = Object.keys(rows[0]).filter((c) => !c.endsWith("__diff"));
  return (
    <div className="table-scroll">
      <table className="rows">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {columns.map((c) => {
                const base = c.replace(/_(oracle|bigquery)$/, "");
                const diff = highlightDiff && base !== c && row[`${base}__diff`] === true;
                return (
                  <td key={c} className={diff ? "diff" : ""}>
                    {fmt(row[c])}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
