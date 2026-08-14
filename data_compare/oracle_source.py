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
