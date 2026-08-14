"""Read the BigQuery side of the comparison into a pandas DataFrame.

Authentication always uses a service account: either a credentials JSON file
(``BQ_CREDENTIALS_JSON`` / ``GOOGLE_APPLICATION_CREDENTIALS``) or the JSON
content itself (``BQ_CREDENTIALS_JSON_CONTENT``), which is handy for CI secrets.
"""

import json
import logging

import pandas as pd

from .config import BigQueryConfig

logger = logging.getLogger(__name__)

BIGQUERY_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)


def _import_bigquery():
    try:
        from google.cloud import bigquery
        from google.oauth2 import service_account
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise ImportError(
            "google-cloud-bigquery is required for the BigQuery source. "
            "Install it with: pip install '.[compare]'"
        ) from exc
    return bigquery, service_account


def load_credentials(config: BigQueryConfig):
    """Build service account credentials from a file path or inline JSON."""
    _, service_account = _import_bigquery()
    if config.credentials_json_inline:
        info = json.loads(config.credentials_json_inline)
        logger.info(
            "Using inline BigQuery service account %s",
            info.get("client_email", "unknown"),
        )
        return service_account.Credentials.from_service_account_info(
            info, scopes=list(BIGQUERY_SCOPES)
        )
    logger.info(
        "Using BigQuery service account file %s", config.credentials_json
    )
    return service_account.Credentials.from_service_account_file(
        config.credentials_json, scopes=list(BIGQUERY_SCOPES)
    )


def build_query(config: BigQueryConfig, project: str) -> str:
    """Build the SELECT statement for the configured BigQuery table."""
    if config.query:
        return config.query
    table = f"`{project}.{config.dataset}.{config.table}`"
    query = f"SELECT * FROM {table}"
    if config.where:
        query += f" WHERE {config.where}"
    return query


def build_client(config: BigQueryConfig):
    """Create a BigQuery client authenticated with the service account."""
    bigquery, _ = _import_bigquery()
    credentials = load_credentials(config)
    project = config.project or getattr(credentials, "project_id", "") or ""
    if not project:
        raise ValueError(
            "BigQuery project could not be resolved; set BQ_PROJECT explicitly."
        )
    return (
        bigquery.Client(
            project=project,
            credentials=credentials,
            location=config.location or None,
        ),
        project,
    )


def fetch_columns(config: BigQueryConfig) -> list[dict[str, str]]:
    """List the columns of the configured BigQuery table with their types."""
    config.validate()
    client, project = build_client(config)
    table_id = f"{project}.{config.dataset}.{config.table}"
    try:
        table = client.get_table(table_id)
    finally:
        client.close()
    return [{"name": field.name, "type": field.field_type} for field in table.schema]


def fetch_tables(config: BigQueryConfig) -> list[str]:
    """List the tables of the configured BigQuery dataset."""
    config.validate()
    client, project = build_client(config)
    try:
        tables = [
            table.table_id
            for table in client.list_tables(f"{project}.{config.dataset}")
        ]
    finally:
        client.close()
    return sorted(tables)


def fetch_dataframe(config: BigQueryConfig) -> pd.DataFrame:
    """Fetch the configured BigQuery table/query as a DataFrame."""
    config.validate()
    client, project = build_client(config)
    query = build_query(config, project)
    logger.info("Running BigQuery query: %s", query)
    try:
        dataframe = client.query(query).result().to_dataframe(
            create_bqstorage_client=False
        )
    finally:
        client.close()
    logger.info("Fetched %d rows from BigQuery", len(dataframe))
    return dataframe
