"""Read the Oracle side of the comparison into a pandas DataFrame."""

import logging

import pandas as pd

from .config import OracleConfig

logger = logging.getLogger(__name__)

_thick_mode_initialized = False


def _import_oracledb():
    try:
        import oracledb
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise ImportError(
            "oracledb is required for the Oracle source. "
            "Install it with: pip install '.[compare]'"
        ) from exc
    return oracledb


def build_query(config: OracleConfig) -> str:
    """Build the SELECT statement for the configured Oracle table."""
    if config.query:
        return config.query
    table = f"{config.schema}.{config.table}" if config.schema else config.table
    query = f"SELECT * FROM {table}"
    if config.where:
        query += f" WHERE {config.where}"
    return query


def connect(config: OracleConfig):
    """Open an Oracle connection, enabling thick mode when requested."""
    oracledb = _import_oracledb()
    global _thick_mode_initialized
    if config.thick_mode and not _thick_mode_initialized:
        oracledb.init_oracle_client(lib_dir=config.lib_dir or None)
        _thick_mode_initialized = True
    logger.info("Connecting to Oracle at %s as %s", config.dsn, config.user)
    return oracledb.connect(
        user=config.user, password=config.password, dsn=config.dsn
    )


def fetch_columns(config: OracleConfig) -> list[dict[str, str]]:
    """List the columns of the configured Oracle table with their data types."""
    config.validate()
    query = (
        "SELECT column_name, data_type FROM all_tab_columns "
        "WHERE table_name = :table_name"
    )
    params: dict[str, str] = {"table_name": config.table.upper()}
    if config.schema:
        query += " AND owner = :owner"
        params["owner"] = config.schema.upper()
    query += " ORDER BY column_id"

    with connect(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()
    if not rows:
        raise ValueError(
            f"Oracle table {config.schema + '.' if config.schema else ''}"
            f"{config.table} has no visible columns; check the name and grants."
        )
    return [{"name": name, "type": data_type} for name, data_type in rows]


def fetch_tables(config: OracleConfig) -> list[str]:
    """List tables visible to the configured user, optionally within a schema."""
    config.validate()
    query = "SELECT table_name FROM all_tables"
    params: dict[str, str] = {}
    if config.schema:
        query += " WHERE owner = :owner"
        params["owner"] = config.schema.upper()
    query += " ORDER BY table_name"

    with connect(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            return [row[0] for row in cursor.fetchall()]


def fetch_dataframe(config: OracleConfig) -> pd.DataFrame:
    """Fetch the configured Oracle table/query as a DataFrame."""
    config.validate()
    query = build_query(config)
    logger.info("Running Oracle query: %s", query)
    with connect(config) as connection:
        with connection.cursor() as cursor:
            cursor.arraysize = config.arraysize
            cursor.execute(query)
            columns = [description[0] for description in cursor.description]
            rows = cursor.fetchall()
    logger.info("Fetched %d rows from Oracle", len(rows))
    return pd.DataFrame(rows, columns=columns)
