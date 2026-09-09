export type Side = "oracle" | "bigquery";

export interface TableInfo {
  name: string;
  type: string;
}

export interface ColumnInfo {
  name: string;
  type: string;
}

export interface SideSelection {
  schema: string;
  table: string;
  columns: string[];
  date_column: string;
  date_from: string;
  date_to: string;
}

export interface AnalyseRequest {
  oracle: SideSelection;
  bigquery: SideSelection;
  join_columns: string[];
  abs_tol: number;
  rel_tol: number;
  ignore_case: boolean;
  ignore_spaces: boolean;
  preview_rows: number;
}

export interface ColumnStat {
  column: string;
  oracle_type: string;
  bigquery_type: string;
  mismatches: number;
}

export type Row = Record<string, string | number | boolean | null>;

export interface AnalyseResult {
  matches: boolean;
  join_columns: string[];
  common_columns: string[];
  oracle_row_count: number;
  bigquery_row_count: number;
  oracle_duplicate_keys: number;
  bigquery_duplicate_keys: number;
  common_row_count: number;
  rows_matching: number;
  rows_with_differences: number;
  oracle_only_row_count: number;
  bigquery_only_row_count: number;
  column_stats: ColumnStat[];
  datacompy_scope: string;
  datacompy_report: string;
  mismatch_rows: Row[];
  oracle_only_rows: Row[];
  bigquery_only_rows: Row[];
  preview_limit: number;
}

export interface Job {
  id: string;
  status: "queued" | "running" | "done" | "failed";
  stage: string;
  error: string | null;
  result: AnalyseResult | null;
}

export interface Health {
  status: string;
  oracle_configured: boolean;
  bigquery_configured: boolean;
}
