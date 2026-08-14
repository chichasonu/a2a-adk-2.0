export interface OracleForm {
  user: string
  password: string
  dsn: string
  schema: string
  table: string
  where: string
}

export interface BigQueryForm {
  project: string
  dataset: string
  table: string
  location: string
  where: string
  credentials_json: string
  credentials_json_content: string
}

export interface CompareOptions {
  join_columns: string[]
  ignore_columns: string[]
  abs_tol: number
  rel_tol: number
  ignore_case: boolean
  ignore_spaces: boolean
  sample_count: number
  max_preview_rows: number
  write_report_files: boolean
}

export interface ColumnInfo {
  name: string
  type: string
}

export interface ColumnsResponse {
  oracle_columns: ColumnInfo[]
  bigquery_columns: ColumnInfo[]
  common_columns: string[]
  oracle_only_columns: string[]
  bigquery_only_columns: string[]
}

export interface CompareSummary {
  matches: boolean
  join_columns: string[]
  oracle_row_count: number
  bigquery_row_count: number
  common_row_count: number
  rows_matching: number
  rows_with_differences: number
  mismatch_row_count: number
  oracle_only_row_count: number
  bigquery_only_row_count: number
  all_columns_match: boolean
  common_columns: string[]
  oracle_only_columns: string[]
  bigquery_only_columns: string[]
  columns_with_differences: string[]
}

export type Row = Record<string, string | number | boolean | null>

export interface CompareResponse {
  matches: boolean
  summary: CompareSummary
  report: string
  mismatch_rows: Row[]
  oracle_only_rows: Row[]
  bigquery_only_rows: Row[]
  truncated: {
    mismatch_rows: boolean
    oracle_only_rows: boolean
    bigquery_only_rows: boolean
  }
  artifacts?: Record<string, string>
}

export interface Defaults {
  oracle: {
    user: string
    dsn: string
    schema: string
    table: string
    password_configured: boolean
  }
  bigquery: {
    project: string
    dataset: string
    table: string
    location: string
    credentials_configured: boolean
  }
  compare: {
    join_columns: string[]
    abs_tol: number
    rel_tol: number
    ignore_case: boolean
    ignore_spaces: boolean
    output_dir: string
  }
}
