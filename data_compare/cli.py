"""CLI for comparing an Oracle table with a BigQuery table via datacompy."""

import argparse
import logging
import sys

from .compare import compare_tables
from .config import Settings, configure_logging
from .report import format_console_summary, write_reports

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser; every flag defaults to its env variable."""
    parser = argparse.ArgumentParser(
        description=(
            "Compare data between an Oracle table and a BigQuery table using "
            "datacompy (e.g. the FeedBack table keyed on chatmessageid)."
        )
    )
    parser.add_argument(
        "--table",
        help="Table name present in both systems (sets Oracle and BigQuery table).",
    )
    parser.add_argument("--oracle-table", help="Oracle table name.")
    parser.add_argument("--oracle-schema", help="Oracle schema/owner.")
    parser.add_argument("--oracle-where", help="WHERE clause for the Oracle query.")
    parser.add_argument(
        "--oracle-query", help="Full Oracle SELECT statement (overrides table/where)."
    )
    parser.add_argument("--bq-project", help="BigQuery project id.")
    parser.add_argument("--bq-dataset", help="BigQuery dataset id.")
    parser.add_argument("--bq-table", help="BigQuery table name.")
    parser.add_argument("--bq-location", help="BigQuery dataset location.")
    parser.add_argument("--bq-where", help="WHERE clause for the BigQuery query.")
    parser.add_argument(
        "--bq-query", help="Full BigQuery SELECT statement (overrides table/where)."
    )
    parser.add_argument(
        "--credentials-json",
        help="Path to the BigQuery service account credentials JSON.",
    )
    parser.add_argument(
        "--join-columns",
        help="Comma-separated key columns, e.g. chatmessageid,sessionid.",
    )
    parser.add_argument(
        "--ignore-columns",
        help="Comma-separated columns to exclude from the comparison.",
    )
    parser.add_argument("--abs-tol", type=float, help="Absolute numeric tolerance.")
    parser.add_argument("--rel-tol", type=float, help="Relative numeric tolerance.")
    parser.add_argument(
        "--ignore-case",
        action="store_true",
        help="Compare string values case-insensitively.",
    )
    parser.add_argument(
        "--no-ignore-spaces",
        action="store_true",
        help="Keep leading/trailing whitespace significant.",
    )
    parser.add_argument(
        "--sample-count",
        type=int,
        help="Number of sample mismatch rows in the datacompy report.",
    )
    parser.add_argument(
        "--output-dir", help="Directory for the report, summary and difference CSVs."
    )
    parser.add_argument(
        "--report-prefix",
        help="Filename prefix for the artifacts (default: the table name).",
    )
    parser.add_argument(
        "--no-report-files",
        action="store_true",
        help="Print the summary only, without writing artifacts to disk.",
    )
    parser.add_argument(
        "--print-report",
        action="store_true",
        help="Print the full datacompy report to stdout.",
    )
    parser.add_argument("--log-level", help="Logging level (default: INFO).")
    return parser


def apply_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    """Apply CLI overrides on top of the environment-derived settings."""
    if args.table:
        settings.oracle.table = args.table
        settings.bigquery.table = args.table
    for attribute, value in (
        ("table", args.oracle_table),
        ("schema", args.oracle_schema),
        ("where", args.oracle_where),
        ("query", args.oracle_query),
    ):
        if value:
            setattr(settings.oracle, attribute, value)
    for attribute, value in (
        ("project", args.bq_project),
        ("dataset", args.bq_dataset),
        ("table", args.bq_table),
        ("location", args.bq_location),
        ("where", args.bq_where),
        ("query", args.bq_query),
        ("credentials_json", args.credentials_json),
    ):
        if value:
            setattr(settings.bigquery, attribute, value)

    if args.join_columns:
        settings.compare.join_columns = [
            column.strip() for column in args.join_columns.split(",") if column.strip()
        ]
    if args.ignore_columns:
        settings.compare.ignore_columns = [
            column.strip()
            for column in args.ignore_columns.split(",")
            if column.strip()
        ]
    if args.abs_tol is not None:
        settings.compare.abs_tol = args.abs_tol
    if args.rel_tol is not None:
        settings.compare.rel_tol = args.rel_tol
    if args.ignore_case:
        settings.compare.ignore_case = True
    if args.no_ignore_spaces:
        settings.compare.ignore_spaces = False
    if args.sample_count is not None:
        settings.compare.sample_count = args.sample_count
    if args.output_dir:
        settings.compare.output_dir = args.output_dir
    if args.log_level:
        settings.log_level = args.log_level
    return settings


def main(argv: list[str] | None = None) -> int:
    """Run a comparison; exit code 0 when the datasets match, 1 otherwise."""
    args = build_parser().parse_args(argv)
    settings = apply_overrides(Settings(), args)
    configure_logging(settings.log_level)

    try:
        result = compare_tables(settings)
    except (ValueError, ImportError) as exc:
        logger.error("%s", exc)
        return 2

    print(format_console_summary(result))
    if args.print_report:
        print()
        print(result.report)
    if not args.no_report_files:
        prefix = args.report_prefix or (settings.oracle.table or "comparison").lower()
        written = write_reports(result, settings.compare.output_dir, prefix)
        print()
        for name, path in written.items():
            print(f"{name}: {path}")
    return 0 if result.matches else 1


if __name__ == "__main__":
    sys.exit(main())
