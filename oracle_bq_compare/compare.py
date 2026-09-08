"""Set-based comparison in DuckDB plus a datacompy report.

DuckDB does the work that has to scale to millions of rows on each side: aligning
types, anti-joins for rows that exist on one side only, and a keyed join that
flags every differing column.  datacompy is then run either on the full data set
(when it is small enough to fit comfortably in pandas) or on a bounded sample of
the differing rows, so its column-level statistics and human readable report
are always available without ever materialising millions of rows in pandas.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import duckdb
import pandas as pd

from .config import CompareConfig
from .duck import column_types, quote

logger = logging.getLogger(__name__)

NUMERIC_TYPES = {
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "UHUGEINT",
    "FLOAT",
    "DOUBLE",
    "REAL",
}
TEMPORAL_TYPES = {
    "DATE",
    "TIMESTAMP",
    "TIMESTAMP WITH TIME ZONE",
    "TIMESTAMP_S",
    "TIMESTAMP_MS",
    "TIMESTAMP_NS",
    "TIMESTAMPTZ",
}

L, R = "l", "r"
NORM_LEFT, NORM_RIGHT = "_norm_left", "_norm_right"
ONLY_LEFT, ONLY_RIGHT, MISMATCH = "only_left", "only_right", "mismatch"


def _category(dtype: str) -> str:
    base = dtype.upper()
    if base.startswith("DECIMAL") or base in NUMERIC_TYPES:
        return "numeric"
    if base in TEMPORAL_TYPES or base.startswith("TIMESTAMP"):
        return "temporal"
    if base == "BOOLEAN":
        return "boolean"
    if base in ("VARCHAR", "TEXT", "STRING"):
        return "string"
    return "other"


def _is_decimal(dtype: str) -> bool:
    return dtype.upper().startswith("DECIMAL")


def normalized_expression(
    column: str, left_type: str, right_type: str, config: CompareConfig
) -> tuple[str, str]:
    """Return (left_expr, right_expr) SQL expressions that make the two columns comparable."""
    lc, rc = _category(left_type), _category(right_type)
    col = quote(column)
    if lc == rc == "numeric":
        # Oracle NUMBER arrives as DECIMAL; BigQuery INT64/FLOAT64 as BIGINT/DOUBLE.
        if left_type.upper() == right_type.upper() or (
            _is_decimal(left_type) and _is_decimal(right_type)
        ):
            return col, col
        return f"CAST({col} AS DOUBLE)", f"CAST({col} AS DOUBLE)"
    if lc == rc == "temporal":
        if left_type.upper() == right_type.upper():
            return col, col
        return f"CAST({col} AS TIMESTAMP)", f"CAST({col} AS TIMESTAMP)"
    if lc == rc == "boolean":
        return col, col
    if lc == rc == "string" and not (config.ignore_spaces or config.ignore_case):
        return col, col

    def text(expr: str) -> str:
        expr = f"CAST({expr} AS VARCHAR)"
        if config.ignore_spaces:
            expr = f"TRIM({expr})"
        if config.ignore_case:
            expr = f"LOWER({expr})"
        return expr

    return text(col), text(col)


def _diff_predicate(column: str, category: str, config: CompareConfig) -> str:
    lcol, rcol = f"{L}.{quote(column)}", f"{R}.{quote(column)}"
    if category == "numeric" and (config.abs_tol or config.rel_tol):
        return (
            f"CASE WHEN {lcol} IS NULL AND {rcol} IS NULL THEN FALSE "
            f"WHEN {lcol} IS NULL OR {rcol} IS NULL THEN TRUE "
            f"ELSE ABS(CAST({lcol} AS DOUBLE) - CAST({rcol} AS DOUBLE)) > "
            f"({config.abs_tol} + {config.rel_tol} * ABS(CAST({rcol} AS DOUBLE))) END"
        )
    return f"({lcol} IS DISTINCT FROM {rcol})"


@dataclass
class ColumnStats:
    column: str
    left_type: str
    right_type: str
    mismatches: int = 0


@dataclass
class ComparisonResult:
    join_columns: list[str]
    common_columns: list[str]
    left_only_columns: list[str]
    right_only_columns: list[str]
    left_rows: int
    right_rows: int
    left_duplicate_keys: int
    right_duplicate_keys: int
    common_rows: int
    left_only_rows: int
    right_only_rows: int
    mismatch_rows: int
    column_stats: list[ColumnStats]
    datacompy_scope: str
    datacompy_report: str
    datacompy_summary: dict[str, Any] = field(default_factory=dict)
    left_name: str = "oracle"
    right_name: str = "bigquery"

    @property
    def matches(self) -> bool:
        return (
            self.left_only_rows == 0
            and self.right_only_rows == 0
            and self.mismatch_rows == 0
            and not self.left_only_columns
            and not self.right_only_columns
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "matches": self.matches,
            "join_columns": self.join_columns,
            "common_columns": self.common_columns,
            f"{self.left_name}_only_columns": self.left_only_columns,
            f"{self.right_name}_only_columns": self.right_only_columns,
            f"{self.left_name}_row_count": self.left_rows,
            f"{self.right_name}_row_count": self.right_rows,
            f"{self.left_name}_duplicate_keys": self.left_duplicate_keys,
            f"{self.right_name}_duplicate_keys": self.right_duplicate_keys,
            "common_row_count": self.common_rows,
            "rows_matching": self.common_rows - self.mismatch_rows,
            "rows_with_differences": self.mismatch_rows,
            f"{self.left_name}_only_row_count": self.left_only_rows,
            f"{self.right_name}_only_row_count": self.right_only_rows,
            "columns_with_differences": [
                {
                    "column": s.column,
                    self.left_name: s.left_type,
                    self.right_name: s.right_type,
                    "mismatches": s.mismatches,
                }
                for s in self.column_stats
                if s.mismatches
            ],
            "datacompy_scope": self.datacompy_scope,
            "datacompy": self.datacompy_summary,
        }


def resolve_columns(
    con: duckdb.DuckDBPyConnection,
    left: str,
    right: str,
    config: CompareConfig,
    detected_pk: list[str],
) -> tuple[list[str], list[str], list[str], list[str], dict[str, str], dict[str, str]]:
    left_types, right_types = column_types(con, left), column_types(con, right)
    ignore = {c.lower() for c in config.ignore_columns}
    common = [c for c in left_types if c in right_types and c not in ignore]
    left_only = [c for c in left_types if c not in right_types and c not in ignore]
    right_only = [c for c in right_types if c not in left_types and c not in ignore]
    join = [c.lower() for c in config.join_columns] or detected_pk
    if not join:
        raise ValueError(
            "No join columns: set COMPARE_JOIN_COLUMNS (Oracle primary key auto-detection found none)"
        )
    missing = [c for c in join if c not in common]
    if missing:
        raise ValueError(f"Join columns not present on both sides: {missing}")
    if not common:
        raise ValueError("The two tables have no columns in common")
    return join, common, left_only, right_only, left_types, right_types


def build_normalized_views(
    con: duckdb.DuckDBPyConnection,
    left: str,
    right: str,
    columns: list[str],
    left_types: dict[str, str],
    right_types: dict[str, str],
    config: CompareConfig,
) -> None:
    con.execute("SET TimeZone='UTC'")
    left_exprs, right_exprs = [], []
    for col in columns:
        le, re_ = normalized_expression(col, left_types[col], right_types[col], config)
        left_exprs.append(f"{le} AS {quote(col)}")
        right_exprs.append(f"{re_} AS {quote(col)}")
    con.execute(
        f"CREATE OR REPLACE VIEW {NORM_LEFT} AS SELECT {', '.join(left_exprs)} FROM {quote(left)}"
    )
    con.execute(
        f"CREATE OR REPLACE VIEW {NORM_RIGHT} AS SELECT {', '.join(right_exprs)} FROM {quote(right)}"
    )


def _duplicate_keys(con: duckdb.DuckDBPyConnection, view: str, join: list[str]) -> int:
    keys = ", ".join(quote(c) for c in join)
    return con.execute(
        f"SELECT COUNT(*) FROM (SELECT {keys} FROM {view} GROUP BY {keys} HAVING COUNT(*) > 1)"
    ).fetchone()[0]


def build_diff_tables(
    con: duckdb.DuckDBPyConnection,
    join: list[str],
    columns: list[str],
    categories: dict[str, str],
    config: CompareConfig,
) -> None:
    on = " AND ".join(f"{L}.{quote(c)} IS NOT DISTINCT FROM {R}.{quote(c)}" for c in join)
    key_cols = ", ".join(f"{L}.{quote(c)}" for c in join)
    con.execute(
        f"CREATE OR REPLACE TABLE {ONLY_LEFT} AS SELECT {L}.* FROM {NORM_LEFT} {L} "
        f"ANTI JOIN {NORM_RIGHT} {R} ON {on}"
    )
    con.execute(
        f"CREATE OR REPLACE TABLE {ONLY_RIGHT} AS SELECT {R}.* FROM {NORM_RIGHT} {R} "
        f"ANTI JOIN {NORM_LEFT} {L} ON {on}"
    )
    compare_cols = [c for c in columns if c not in join]
    if not compare_cols:
        con.execute(
            f"CREATE OR REPLACE TABLE {MISMATCH} AS SELECT {key_cols} FROM {NORM_LEFT} {L} WHERE FALSE"
        )
        return
    selects, predicates = [key_cols], []
    for col in compare_cols:
        pred = _diff_predicate(col, categories[col], config)
        selects.append(f"{L}.{quote(col)} AS {quote(col + '_' + config.oracle_name)}")
        selects.append(f"{R}.{quote(col)} AS {quote(col + '_' + config.bigquery_name)}")
        selects.append(f"COALESCE({pred}, FALSE) AS {quote(col + '__diff')}")
        predicates.append(f"COALESCE({pred}, FALSE)")
    con.execute(
        f"CREATE OR REPLACE TABLE {MISMATCH} AS SELECT {', '.join(selects)} "
        f"FROM {NORM_LEFT} {L} JOIN {NORM_RIGHT} {R} ON {on} WHERE {' OR '.join(predicates)}"
    )


def _column_mismatch_counts(
    con: duckdb.DuckDBPyConnection, compare_cols: list[str]
) -> dict[str, int]:
    if not compare_cols:
        return {}
    sums = ", ".join(
        f"COALESCE(SUM(CAST({quote(c + '__diff')} AS BIGINT)), 0)" for c in compare_cols
    )
    row = con.execute(f"SELECT {sums} FROM {MISMATCH}").fetchone()
    return dict(zip(compare_cols, (int(v) for v in row)))


def _pandas_compare_class():
    try:
        import datacompy
    except ImportError as exc:  # pragma: no cover
        raise ImportError("datacompy is required: pip install datacompy") from exc
    return getattr(datacompy, "PandasCompare", None) or datacompy.Compare


def run_datacompy(
    con: duckdb.DuckDBPyConnection,
    join: list[str],
    config: CompareConfig,
    left_rows: int,
    right_rows: int,
) -> tuple[str, str, dict[str, Any]]:
    """Run datacompy on the full data set or on a sample of differing keys. Returns (scope, report, summary)."""
    Compare = _pandas_compare_class()
    if max(left_rows, right_rows) <= config.full_datacompy_max_rows:
        scope = "full"
        left_df = con.execute(f"SELECT * FROM {NORM_LEFT}").df()
        right_df = con.execute(f"SELECT * FROM {NORM_RIGHT}").df()
    else:
        scope = f"sample of up to {config.sample_rows} differing keys"
        keys = ", ".join(quote(c) for c in join)
        con.execute(
            f"CREATE OR REPLACE TEMP TABLE _sample_keys AS "
            f"SELECT * FROM (SELECT {keys} FROM {MISMATCH} UNION ALL SELECT {keys} FROM {ONLY_LEFT} "
            f"UNION ALL SELECT {keys} FROM {ONLY_RIGHT}) LIMIT {int(config.sample_rows)}"
        )
        using = f"USING ({keys})"
        left_df = con.execute(
            f"SELECT {NORM_LEFT}.* FROM {NORM_LEFT} SEMI JOIN _sample_keys {using}"
        ).df()
        right_df = con.execute(
            f"SELECT {NORM_RIGHT}.* FROM {NORM_RIGHT} SEMI JOIN _sample_keys {using}"
        ).df()
        if left_df.empty and right_df.empty:
            return scope, "No differences found; datacompy sample is empty.", {"matches": True}

    left_df, right_df = _align_dtypes(left_df, right_df)
    comparison = Compare(
        left_df,
        right_df,
        join_columns=join,
        abs_tol=config.abs_tol,
        rel_tol=config.rel_tol,
        df1_name=config.oracle_name,
        df2_name=config.bigquery_name,
        ignore_spaces=config.ignore_spaces,
        ignore_case=config.ignore_case,
        cast_column_names_lower=True,
    )
    summary = {
        "matches": bool(comparison.matches()),
        "rows_compared": len(comparison.intersect_rows),
        "rows_matching": int(comparison.count_matching_rows()),
        f"{config.oracle_name}_only_rows": len(comparison.df1_unq_rows),
        f"{config.bigquery_name}_only_rows": len(comparison.df2_unq_rows),
        "columns_with_mismatches": sorted(comparison.columns_with_mismatches()),
    }
    return scope, comparison.report(sample_count=min(config.sample_rows, 50)), summary


def _align_dtypes(left: pd.DataFrame, right: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Give both frames identical dtypes (datacompy treats object vs numeric as a mismatch)."""
    for col in left.columns.intersection(right.columns):
        if left[col].dtype != right[col].dtype:
            left[col] = left[col].astype("string")
            right[col] = right[col].astype("string")
    return left, right


def compare_tables(
    con: duckdb.DuckDBPyConnection,
    config: CompareConfig,
    detected_pk: list[str] | None = None,
    left: str = "oracle",
    right: str = "bigquery",
) -> ComparisonResult:
    join, common, left_only_cols, right_only_cols, left_types, right_types = resolve_columns(
        con, left, right, config, detected_pk or []
    )
    logger.info("Join columns: %s; comparing %d common columns", join, len(common))
    build_normalized_views(con, left, right, common, left_types, right_types, config)
    categories = {
        c: _category(left_types[c])
        if _category(left_types[c]) == _category(right_types[c])
        else "other"
        for c in common
    }
    build_diff_tables(con, join, common, categories, config)

    left_rows = con.execute(f"SELECT COUNT(*) FROM {quote(left)}").fetchone()[0]
    right_rows = con.execute(f"SELECT COUNT(*) FROM {quote(right)}").fetchone()[0]
    only_left = con.execute(f"SELECT COUNT(*) FROM {ONLY_LEFT}").fetchone()[0]
    only_right = con.execute(f"SELECT COUNT(*) FROM {ONLY_RIGHT}").fetchone()[0]
    mismatches = con.execute(f"SELECT COUNT(*) FROM {MISMATCH}").fetchone()[0]
    compare_cols = [c for c in common if c not in join]
    per_column = _column_mismatch_counts(con, compare_cols)

    scope, report, summary = run_datacompy(con, join, config, left_rows, right_rows)

    return ComparisonResult(
        join_columns=join,
        common_columns=common,
        left_only_columns=left_only_cols,
        right_only_columns=right_only_cols,
        left_rows=left_rows,
        right_rows=right_rows,
        left_duplicate_keys=_duplicate_keys(con, NORM_LEFT, join),
        right_duplicate_keys=_duplicate_keys(con, NORM_RIGHT, join),
        common_rows=left_rows - only_left,
        left_only_rows=only_left,
        right_only_rows=only_right,
        mismatch_rows=mismatches,
        column_stats=[
            ColumnStats(c, left_types[c], right_types[c], per_column.get(c, 0))
            for c in compare_cols
        ],
        datacompy_scope=scope,
        datacompy_report=report,
        datacompy_summary=summary,
        left_name=config.oracle_name,
        right_name=config.bigquery_name,
    )
