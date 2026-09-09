import type { ColumnInfo } from "../types";

export interface Mapping {
  oracle: string;
  bigquery: string;
  join: boolean;
}

interface Props {
  oracleColumns: ColumnInfo[];
  bigqueryColumns: ColumnInfo[];
  mappings: Mapping[];
  onChange: (next: Mapping[]) => void;
}

export function autoMap(oracleColumns: ColumnInfo[], bigqueryColumns: ColumnInfo[]): Mapping[] {
  const byLower = new Map(bigqueryColumns.map((c) => [c.name.toLowerCase(), c.name]));
  return oracleColumns.map((c) => ({
    oracle: c.name,
    bigquery: byLower.get(c.name.toLowerCase()) ?? "",
    join: false,
  }));
}

export function ColumnMapping({ oracleColumns, bigqueryColumns, mappings, onChange }: Props) {
  const selected = new Set(mappings.map((m) => m.oracle));
  const bqTypes = new Map(bigqueryColumns.map((c) => [c.name, c.type]));

  const toggle = (col: ColumnInfo) => {
    if (selected.has(col.name)) {
      onChange(mappings.filter((m) => m.oracle !== col.name));
    } else {
      const [auto] = autoMap([col], bigqueryColumns);
      onChange([...mappings, auto]);
    }
  };
  const update = (oracle: string, patch: Partial<Mapping>) =>
    onChange(mappings.map((m) => (m.oracle === oracle ? { ...m, ...patch } : m)));
  const selectAll = () => onChange(autoMap(oracleColumns, bigqueryColumns));
  const clear = () => onChange([]);

  return (
    <section className="panel panel-mapping">
      <div className="panel-header">
        <h2>Columns to compare</h2>
        <div className="actions">
          <button type="button" className="link" onClick={selectAll}>
            Select all (auto-match)
          </button>
          <button type="button" className="link" onClick={clear}>
            Clear
          </button>
        </div>
      </div>
      <p className="muted">
        Tick the Oracle columns to compare, choose the matching BigQuery column for each, and mark
        at least one as the join key.
      </p>
      <table className="mapping">
        <thead>
          <tr>
            <th></th>
            <th>Oracle column</th>
            <th>BigQuery column</th>
            <th>Join key</th>
          </tr>
        </thead>
        <tbody>
          {oracleColumns.map((col) => {
            const m = mappings.find((x) => x.oracle === col.name);
            return (
              <tr key={col.name} className={m ? "selected" : ""}>
                <td>
                  <input
                    type="checkbox"
                    checked={!!m}
                    onChange={() => toggle(col)}
                    aria-label={`compare ${col.name}`}
                  />
                </td>
                <td>
                  <code>{col.name}</code> <span className="type">{col.type}</span>
                </td>
                <td>
                  <select
                    value={m?.bigquery ?? ""}
                    disabled={!m}
                    className={m && !m.bigquery ? "missing" : ""}
                    onChange={(e) => update(col.name, { bigquery: e.target.value })}
                  >
                    <option value="">— pick column —</option>
                    {bigqueryColumns.map((b) => (
                      <option key={b.name} value={b.name}>
                        {b.name} ({b.type})
                      </option>
                    ))}
                  </select>
                  {m?.bigquery ? <span className="type">{bqTypes.get(m.bigquery)}</span> : null}
                </td>
                <td>
                  <input
                    type="checkbox"
                    disabled={!m}
                    checked={!!m?.join}
                    onChange={(e) => update(col.name, { join: e.target.checked })}
                    aria-label={`join on ${col.name}`}
                  />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}
