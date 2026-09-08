"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
import time

from . import bigquery_source, oracle_source
from .compare import compare_tables
from .config import Settings
from .duck import connect
from .report import render_summary, write_outputs


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="oracle-bq-compare",
        description="Compare an Oracle table with a BigQuery table (DuckDB + datacompy). "
        "All options can also be provided through environment variables / .env.",
    )
    p.add_argument("--oracle-table", help="Oracle table (ORACLE_TABLE)")
    p.add_argument("--oracle-schema", help="Oracle schema/owner (ORACLE_SCHEMA)")
    p.add_argument("--bq-dataset", help="BigQuery dataset (BQ_DATASET)")
    p.add_argument("--bq-table", help="BigQuery table (BQ_TABLE)")
    p.add_argument("--bq-credentials", help="Service account JSON file (BQ_CREDENTIALS_JSON)")
    p.add_argument("--join-columns", help="Comma separated key columns (COMPARE_JOIN_COLUMNS)")
    p.add_argument(
        "--ignore-columns", help="Comma separated columns to skip (COMPARE_IGNORE_COLUMNS)"
    )
    p.add_argument("--abs-tol", type=float, help="Absolute numeric tolerance")
    p.add_argument("--rel-tol", type=float, help="Relative numeric tolerance")
    p.add_argument("--output-dir", help="Where to write reports (COMPARE_OUTPUT_DIR)")
    p.add_argument("--duckdb-path", help="DuckDB file for spilling large extracts (DUCKDB_PATH)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def apply_overrides(settings: Settings, args: argparse.Namespace) -> None:
    if args.oracle_table:
        settings.oracle.table = args.oracle_table
    if args.oracle_schema:
        settings.oracle.schema = args.oracle_schema
    if args.bq_dataset:
        settings.bigquery.dataset = args.bq_dataset
    if args.bq_table:
        settings.bigquery.table = args.bq_table
    if args.bq_credentials:
        settings.bigquery.credentials_json = args.bq_credentials
    if args.join_columns:
        settings.compare.join_columns = [
            c.strip() for c in args.join_columns.split(",") if c.strip()
        ]
    if args.ignore_columns:
        settings.compare.ignore_columns = [
            c.strip() for c in args.ignore_columns.split(",") if c.strip()
        ]
    if args.abs_tol is not None:
        settings.compare.abs_tol = args.abs_tol
    if args.rel_tol is not None:
        settings.compare.rel_tol = args.rel_tol
    if args.output_dir:
        settings.compare.output_dir = args.output_dir
    if args.duckdb_path:
        settings.duckdb.path = args.duckdb_path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings()
    apply_overrides(settings, args)
    try:
        settings.validate()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    started = time.time()
    con = connect(settings.duckdb)
    try:
        _, detected_pk = oracle_source.load_into_duckdb(con, settings.oracle)
        bigquery_source.load_into_duckdb(con, settings.bigquery)
        result = compare_tables(con, settings.compare, detected_pk)
        paths = write_outputs(con, result, settings.compare.output_dir)
    finally:
        con.close()

    print(render_summary(result))
    print(f"\nArtefacts: {paths['summary_json']} (+ report and parquet extracts)")
    print(f"Elapsed: {time.time() - started:.1f}s")
    return 0 if result.matches else 1
