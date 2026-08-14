"""Persist comparison output as text, JSON and CSV artifacts."""

import json
import logging
from pathlib import Path

from .compare import ComparisonResult

logger = logging.getLogger(__name__)


def write_reports(
    result: ComparisonResult,
    output_dir: str,
    prefix: str = "feedback",
) -> dict[str, str]:
    """Write the datacompy report, JSON summary and difference CSVs.

    Returns a mapping of artifact name to the path written.
    """
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}

    report_path = directory / f"{prefix}_report.txt"
    report_path.write_text(result.report, encoding="utf-8")
    written["report"] = str(report_path)

    summary_path = directory / f"{prefix}_summary.json"
    summary_path.write_text(
        json.dumps(result.summary, indent=2, default=str), encoding="utf-8"
    )
    written["summary"] = str(summary_path)

    for name, frame in (
        ("mismatches", result.mismatch_rows),
        ("oracle_only_rows", result.oracle_only_rows),
        ("bigquery_only_rows", result.bigquery_only_rows),
    ):
        if frame is None or frame.empty:
            continue
        path = directory / f"{prefix}_{name}.csv"
        frame.to_csv(path, index=False)
        written[name] = str(path)

    logger.info("Wrote comparison artifacts: %s", ", ".join(written.values()))
    return written


def format_console_summary(result: ComparisonResult) -> str:
    """Render a compact human-readable summary for stdout."""
    summary = result.summary
    lines = [
        "Oracle vs BigQuery comparison",
        f"  result:                 {'MATCH' if result.matches else 'MISMATCH'}",
        f"  join columns:           {', '.join(summary['join_columns'])}",
        f"  oracle rows:            {summary['oracle_row_count']}",
        f"  bigquery rows:          {summary['bigquery_row_count']}",
        f"  rows in both:           {summary['common_row_count']}",
        f"  rows fully matching:    {summary['rows_matching']}",
        f"  rows with differences:  {summary['rows_with_differences']}",
        f"  oracle-only rows:       {summary['oracle_only_row_count']}",
        f"  bigquery-only rows:     {summary['bigquery_only_row_count']}",
    ]
    if summary["columns_with_differences"]:
        lines.append(
            "  columns w/ differences: "
            + ", ".join(summary["columns_with_differences"])
        )
    if summary["oracle_only_columns"]:
        lines.append(
            "  columns only in oracle: " + ", ".join(summary["oracle_only_columns"])
        )
    if summary["bigquery_only_columns"]:
        lines.append(
            "  columns only in bq:     "
            + ", ".join(summary["bigquery_only_columns"])
        )
    return "\n".join(lines)
