"""Stream a BigQuery table into DuckDB using a service account and the Storage Read API."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import duckdb
import pyarrow as pa

from .config import BigQueryConfig
from .duck import load_arrow_batches, normalize_column_names

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def _import_bigquery():
    try:
        from google.cloud import bigquery
        from google.oauth2 import service_account
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "google-cloud-bigquery is required: pip install google-cloud-bigquery "
            "google-cloud-bigquery-storage"
        ) from exc
    return bigquery, service_account


def load_credentials(config: BigQueryConfig):
    _, service_account = _import_bigquery()
    if config.credentials_json_content:
        info = json.loads(config.credentials_json_content)
        logger.info("BigQuery service account (inline): %s", info.get("client_email", "?"))
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    logger.info("BigQuery service account file: %s", config.credentials_json)
    return service_account.Credentials.from_service_account_file(
        config.credentials_json, scopes=SCOPES
    )


def build_query(config: BigQueryConfig, project: str) -> str:
    if config.query:
        return config.query
    query = f"SELECT * FROM `{project}.{config.dataset}.{config.table}`"
    if config.where:
        query += f" WHERE {config.where}"
    return query


def make_client(config: BigQueryConfig):
    bigquery, _ = _import_bigquery()
    credentials = load_credentials(config)
    project = config.project or credentials.project_id
    if not project:
        raise ValueError("BQ_PROJECT is required when the service account has no project_id")
    client = bigquery.Client(
        project=project, credentials=credentials, location=config.location or None
    )
    return client, credentials, project


def _storage_client(credentials):
    try:
        from google.cloud import bigquery_storage
    except ImportError:  # pragma: no cover
        logger.warning("bigquery-storage not installed; falling back to slower REST paging")
        return None
    return bigquery_storage.BigQueryReadClient(credentials=credentials)


def _arrow_batches(client, credentials, sql: str) -> Iterator[pa.RecordBatch]:
    job = client.query(sql)
    result = job.result()
    logger.info("BigQuery job %s finished, %s rows", job.job_id, f"{result.total_rows or 0:,}")
    bqstorage = _storage_client(credentials)
    yield from result.to_arrow_iterable(bqstorage_client=bqstorage)


def load_into_duckdb(
    con: duckdb.DuckDBPyConnection, config: BigQueryConfig, table: str = "bigquery"
) -> int:
    config.validate()
    client, credentials, project = make_client(config)
    sql = build_query(config, project)
    logger.info("BigQuery extract: %s", sql)
    rows = load_arrow_batches(con, table, _arrow_batches(client, credentials, sql))
    normalize_column_names(con, table)
    return rows
