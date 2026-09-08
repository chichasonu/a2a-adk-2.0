"""Write comparison artefacts: summary JSON, text report and difference extracts."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import duckdb

from .compare import MISMATCH, ONLY_LEFT, ONLY_RIGHT, ComparisonResult

logger = logging.getLogger(__name__)


def render_summary(result: ComparisonResult) -> str:
    ln, rn = result.left_name, result.right_name
    lines = [
        "Oracle vs BigQuery comparison",
        "=" * 30,
        f"Result: {'MATCH' if result.matches else 'DIFFERENCES FOUND'}",
        f"Join columns: {', '.join(result.join_columns)}",
        f"{ln} rows: {result.left_rows:,}  (duplicate keys: {result.left_duplicate_keys:,})",
        f"{rn} rows: {result.right_rows:,}  (duplicate keys: {result.right_duplicate_keys:,})",
        f"Rows in both: {result.common_rows:,}",
        f"  matching: {result.common_rows - result.mismatch_rows:,}",
        f"  with differences: {result.mismatch_rows:,}",
        f"Rows only in {ln}: {result.left_only_rows:,}",
        f"Rows only in {rn}: {result.right_only_rows:,}",
        f"Common columns compared: {len(result.common_columns)}",
    ]
    if result.left_only_columns:
        lines.append(f"Columns only in {ln}: {', '.join(result.left_only_columns)}")
    if result.right_only_columns:
        lines.append(f"Columns only in {rn}: {', '.join(result.right_only_columns)}")
    diff_cols = [s for s in result.column_stats if s.mismatches]
    if diff_cols:
        lines.append("")
        lines.append(f"{'Column':<40}{ln + ' type':<28}{rn + ' type':<28}{'# mismatches':>14}")
        for s in diff_cols:
            lines.append(f"{s.column:<40}{s.left_type:<28}{s.right_type:<28}{s.mismatches:>14,}")
    lines += ["", f"datacompy report ({result.datacompy_scope})", "-" * 30, result.datacompy_report]
    return "\n".join(lines)


def write_outputs(
    con: duckdb.DuckDBPyConnection, result: ComparisonResult, output_dir: str
) -> dict[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "summary_json": out / "summary.json",
        "report_txt": out / "report.txt",
        "mismatch_rows": out / "mismatch_rows.parquet",
        f"{result.left_name}_only_rows": out / f"{result.left_name}_only_rows.parquet",
        f"{result.right_name}_only_rows": out / f"{result.right_name}_only_rows.parquet",
    }
    paths["summary_json"].write_text(json.dumps(result.to_dict(), indent=2, default=str))
    paths["report_txt"].write_text(render_summary(result))
    for table, key in (
        (MISMATCH, "mismatch_rows"),
        (ONLY_LEFT, f"{result.left_name}_only_rows"),
        (ONLY_RIGHT, f"{result.right_name}_only_rows"),
    ):
        con.execute(f"COPY {table} TO '{paths[key].as_posix()}' (FORMAT PARQUET)")
    logger.info("Outputs written to %s", out.resolve())
    return {k: str(v) for k, v in paths.items()}
