import type {
  BigQueryForm,
  ColumnsResponse,
  CompareOptions,
  CompareResponse,
  Defaults,
  OracleForm,
} from './types'

async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    throw new Error(payload?.detail ?? `Request to ${path} failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

export async function fetchDefaults(): Promise<Defaults> {
  const response = await fetch('/api/defaults')
  if (!response.ok) throw new Error('Could not load defaults from the API.')
  return response.json() as Promise<Defaults>
}

export function fetchOracleTables(oracle: OracleForm): Promise<{ tables: string[] }> {
  return post('/api/oracle/tables', oracle)
}

export function fetchBigQueryTables(
  bigquery: BigQueryForm,
): Promise<{ tables: string[] }> {
  return post('/api/bigquery/tables', bigquery)
}

export function fetchColumns(
  oracle: OracleForm,
  bigquery: BigQueryForm,
): Promise<ColumnsResponse> {
  return post('/api/columns', { oracle, bigquery })
}

export function runComparison(
  oracle: OracleForm,
  bigquery: BigQueryForm,
  options: CompareOptions,
): Promise<CompareResponse> {
  return post('/api/compare', { oracle, bigquery, options })
}
