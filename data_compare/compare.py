"""datacompy-based comparison of an Oracle table against a BigQuery table."""

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .config import CompareConfig
from .normalize import prepare_frames

logger = logging.getLogger(__name__)


def _import_datacompy():
    try:
        import datacompy
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise ImportError(
            "datacompy is required. Install it with: pip install '.[compare]'"
        ) from exc
    return datacompy


def _compare_class(datacompy_module: Any) -> Any:
    """Return the pandas comparison class across datacompy 0.x and 1.x."""
    return getattr(
        datacompy_module, "PandasCompare", getattr(datacompy_module, "Compare", None)
    )


@dataclass
class ComparisonResult:
    """Outcome of a single table comparison."""

    comparison: Any
    matches: bool
    summary: dict[str, Any]
    report: str
    mismatch_rows: pd.DataFrame
    oracle_only_rows: pd.DataFrame
    bigquery_only_rows: pd.DataFrame
    oracle_only_columns: list[str] = field(default_factory=list)
    bigquery_only_columns: list[str] = field(default_factory=list)


def compare_frames(
    oracle_df: pd.DataFrame,
    bigquery_df: pd.DataFrame,
    config: CompareConfig,
) -> ComparisonResult:
    """Compare two already-fetched frames with datacompy."""
    config.validate()
    datacompy = _import_datacompy()

    left, right, only_left, only_right = prepare_frames(
        oracle_df,
        bigquery_df,
        config.join_columns,
        config.ignore_columns,
        strip_strings=config.ignore_spaces,
    )
    join_columns = [key.strip().lower() for key in config.join_columns]

    comparison = _compare_class(datacompy)(
        left,
        right,
        join_columns=join_columns,
        abs_tol=config.abs_tol,
        rel_tol=config.rel_tol,
        df1_name=config.oracle_name,
        df2_name=config.bigquery_name,
        ignore_spaces=config.ignore_spaces,
        ignore_case=config.ignore_case,
        cast_column_names_lower=True,
    )

    matches = bool(comparison.matches())
    mismatch_rows = comparison.all_mismatch(ignore_matching_cols=True)
    summary = build_summary(
        comparison,
        matches=matches,
        join_columns=join_columns,
        oracle_only_columns=only_left,
        bigquery_only_columns=only_right,
        mismatch_row_count=len(mismatch_rows),
    )
    return ComparisonResult(
        comparison=comparison,
        matches=matches,
        summary=summary,
        report=comparison.report(sample_count=config.sample_count),
        mismatch_rows=mismatch_rows,
        oracle_only_rows=comparison.df1_unq_rows,
        bigquery_only_rows=comparison.df2_unq_rows,
        oracle_only_columns=only_left,
        bigquery_only_columns=only_right,
    )


def build_summary(
    comparison: Any,
    matches: bool,
    join_columns: list[str],
    oracle_only_columns: list[str],
    bigquery_only_columns: list[str],
    mismatch_row_count: int,
) -> dict[str, Any]:
    """Build a machine-readable summary of a datacompy comparison."""
    intersect_rows = len(comparison.intersect_rows)
    matching_rows = int(comparison.count_matching_rows())
    unequal_columns = list(comparison.columns_with_mismatches())
    return {
        "matches": matches,
        "join_columns": join_columns,
        "oracle_row_count": len(comparison.df1),
        "bigquery_row_count": len(comparison.df2),
        "common_row_count": intersect_rows,
        "rows_matching": matching_rows,
        "rows_with_differences": intersect_rows - matching_rows,
        "mismatch_row_count": mismatch_row_count,
        "oracle_only_row_count": len(comparison.df1_unq_rows),
        "bigquery_only_row_count": len(comparison.df2_unq_rows),
        "all_columns_match": bool(comparison.all_columns_match()),
        "common_columns": list(comparison.intersect_columns()),
        "oracle_only_columns": oracle_only_columns,
        "bigquery_only_columns": bigquery_only_columns,
        "columns_with_differences": unequal_columns,
    }


def compare_tables(settings: Any) -> ComparisonResult:
    """Fetch both tables and compare them, using the given Settings object."""
    from . import bigquery_source, oracle_source

    settings.validate()
    oracle_df = oracle_source.fetch_dataframe(settings.oracle)
    bigquery_df = bigquery_source.fetch_dataframe(settings.bigquery)
    return compare_frames(oracle_df, bigquery_df, settings.compare)
