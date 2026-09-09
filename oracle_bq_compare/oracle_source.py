"""Stream an Oracle table into DuckDB in Arrow batches."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import duckdb
import pyarrow as pa

from .config import OracleConfig
from .duck import load_arrow_batches, normalize_column_names

logger = logging.getLogger(__name__)


def _import_oracledb():
    try:
        import oracledb
    except ImportError as exc:  # pragma: no cover
        raise ImportError("python-oracledb is required: pip install oracledb") from exc
    return oracledb


def build_query(config: OracleConfig) -> str:
    if config.query:
        return config.query
    query = f"SELECT * FROM {config.qualified_table}"
    if config.where:
        query += f" WHERE {config.where}"
    return query


def connect(config: OracleConfig):
    oracledb = _import_oracledb()
    if config.thick_mode:
        oracledb.init_oracle_client(lib_dir=config.lib_dir or None)
    return oracledb.connect(user=config.user, password=config.password, dsn=config.dsn)


def fetch_primary_key(connection, config: OracleConfig) -> list[str]:
    """Return the primary key columns of the configured table (empty if none / custom query)."""
    if config.query or not config.table:
        return []
    sql = (
        "SELECT cc.column_name FROM all_constraints c "
        "JOIN all_cons_columns cc ON c.owner = cc.owner AND c.constraint_name = cc.constraint_name "
        "WHERE c.constraint_type = 'P' AND c.table_name = :tbl "
        + ("AND c.owner = :owner " if config.schema else "")
        + "ORDER BY cc.position"
    )
    params = {"tbl": config.table.upper()}
    if config.schema:
        params["owner"] = config.schema.upper()
    with connection.cursor() as cur:
        cur.execute(sql, params)
        return [row[0] for row in cur.fetchall()]


def _arrow_batches(
    connection, sql: str, arraysize: int, params: dict[str, Any] | None = None
) -> Iterator[pa.Table]:
    """Yield Arrow tables; uses oracledb's native Arrow path when available (oracledb >= 3)."""
    params = params or {}
    if hasattr(connection, "fetch_df_batches"):
        for odf in connection.fetch_df_batches(sql, params, size=arraysize):
            yield pa.table(odf)
        return

    with connection.cursor() as cur:
        cur.arraysize = arraysize
        cur.prefetchrows = arraysize + 1
        cur.execute(sql, params)
        names = [d[0] for d in cur.description]
        while True:
            rows = cur.fetchmany(arraysize)
            if not rows:
                break
            columns = list(zip(*rows)) if rows else [[] for _ in names]
            yield pa.table({name: pa.array(col) for name, col in zip(names, columns)})


def load_into_duckdb(
    con: duckdb.DuckDBPyConnection, config: OracleConfig, table: str = "oracle"
) -> tuple[int, list[str]]:
    """Load the Oracle extract into DuckDB table ``table``. Returns (row_count, primary_key)."""
    config.validate()
    sql = build_query(config)
    logger.info("Oracle extract: %s", sql)
    connection = connect(config)
    try:
        pk = fetch_primary_key(connection, config)
        rows = load_arrow_batches(con, table, _arrow_batches(connection, sql, config.arraysize))
    finally:
        connection.close()
    normalize_column_names(con, table)
    return rows, [c.lower() for c in pk]


def load_query_into_duckdb(
    con: duckdb.DuckDBPyConnection,
    config: OracleConfig,
    sql: str,
    params: dict[str, Any] | None = None,
    table: str = "oracle",
) -> int:
    """Load an arbitrary (bind-parameterised) Oracle query into DuckDB table ``table``."""
    logger.info("Oracle extract: %s %s", sql, params or "")
    connection = connect(config)
    try:
        rows = load_arrow_batches(
            con, table, _arrow_batches(connection, sql, config.arraysize, params)
        )
    finally:
        connection.close()
    normalize_column_names(con, table)
    return rows


def list_schemas(config: OracleConfig) -> list[str]:
    connection = connect(config)
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT DISTINCT owner FROM all_tables ORDER BY owner")
            return [row[0] for row in cur.fetchall()]
    finally:
        connection.close()


def list_tables(config: OracleConfig, schema: str) -> list[dict[str, str]]:
    connection = connect(config)
    try:
        with connection.cursor() as cur:
            cur.execute(
                "SELECT table_name, 'TABLE' FROM all_tables WHERE owner = :o "
                "UNION ALL SELECT view_name, 'VIEW' FROM all_views WHERE owner = :o ORDER BY 1",
                {"o": schema.upper()},
            )
            return [{"name": name, "type": kind} for name, kind in cur.fetchall()]
    finally:
        connection.close()


def list_columns(config: OracleConfig, schema: str, table: str) -> list[dict[str, str]]:
    connection = connect(config)
    try:
        with connection.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type FROM all_tab_columns "
                "WHERE owner = :o AND table_name = :t ORDER BY column_id",
                {"o": schema.upper(), "t": table.upper()},
            )
            return [{"name": name, "type": dtype} for name, dtype in cur.fetchall()]
    finally:
        connection.close()
