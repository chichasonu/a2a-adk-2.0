"""DuckDB connection helpers and batch loading."""

from __future__ import annotations

import logging
from collections.abc import Iterable

import duckdb
import pyarrow as pa

from .config import DuckDBConfig

logger = logging.getLogger(__name__)


def connect(config: DuckDBConfig) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(config.path)
    if config.memory_limit:
        con.execute(f"SET memory_limit='{config.memory_limit}'")
    if config.threads:
        con.execute(f"SET threads={int(config.threads)}")
    if config.temp_directory:
        con.execute(f"SET temp_directory='{config.temp_directory}'")
    con.execute("SET preserve_insertion_order=false")
    return con


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def load_arrow_batches(
    con: duckdb.DuckDBPyConnection,
    table: str,
    batches: Iterable[pa.Table | pa.RecordBatch],
) -> int:
    """Stream Arrow batches into ``table`` (created from the first batch). Returns row count."""
    con.execute(f"DROP TABLE IF EXISTS {quote(table)}")
    total = 0
    created = False
    for batch in batches:
        arrow_table = batch if isinstance(batch, pa.Table) else pa.Table.from_batches([batch])
        if arrow_table.num_rows == 0 and created:
            continue
        con.register("_incoming_batch", arrow_table)
        if not created:
            con.execute(f"CREATE TABLE {quote(table)} AS SELECT * FROM _incoming_batch")
            created = True
        else:
            con.execute(f"INSERT INTO {quote(table)} SELECT * FROM _incoming_batch")
        con.unregister("_incoming_batch")
        total += arrow_table.num_rows
        if total and total % 1_000_000 < arrow_table.num_rows:
            logger.info("%s: %s rows loaded", table, f"{total:,}")
    if not created:
        raise ValueError(f"No data received for {table}")
    logger.info("%s: %s rows loaded", table, f"{total:,}")
    return total


def column_types(con: duckdb.DuckDBPyConnection, table: str) -> dict[str, str]:
    rows = con.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = ? ORDER BY ordinal_position",
        [table],
    ).fetchall()
    return {name: dtype for name, dtype in rows}


def normalize_column_names(con: duckdb.DuckDBPyConnection, table: str) -> None:
    """Lower-case and trim column names so Oracle (UPPER) and BigQuery (any) line up."""
    for name in list(column_types(con, table)):
        target = name.strip().lower()
        if target != name:
            con.execute(
                f"ALTER TABLE {quote(table)} RENAME COLUMN {quote(name)} TO {quote(target)}"
            )
