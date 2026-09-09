"""Environment / CLI driven configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default) or ""


def _env_bool(key: str, default: bool) -> bool:
    raw = _env(key)
    return default if not raw else raw.lower() in ("1", "true", "yes", "y")


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    return int(raw) if raw else default


def _env_float(key: str, default: float) -> float:
    raw = _env(key)
    return float(raw) if raw else default


def _env_list(key: str) -> list[str]:
    return [item.strip() for item in _env(key).split(",") if item.strip()]


@dataclass
class OracleConfig:
    user: str = field(default_factory=lambda: _env("ORACLE_USER"))
    password: str = field(default_factory=lambda: _env("ORACLE_PASSWORD"))
    dsn: str = field(default_factory=lambda: _env("ORACLE_DSN"))
    schema: str = field(default_factory=lambda: _env("ORACLE_SCHEMA"))
    table: str = field(default_factory=lambda: _env("ORACLE_TABLE"))
    where: str = field(default_factory=lambda: _env("ORACLE_WHERE"))
    query: str = field(default_factory=lambda: _env("ORACLE_QUERY"))
    arraysize: int = field(default_factory=lambda: _env_int("ORACLE_ARRAYSIZE", 50_000))
    thick_mode: bool = field(default_factory=lambda: _env_bool("ORACLE_THICK_MODE", False))
    lib_dir: str = field(default_factory=lambda: _env("ORACLE_CLIENT_LIB_DIR"))

    def validate_connection(self) -> None:
        missing = [
            name
            for name, value in (
                ("ORACLE_USER", self.user),
                ("ORACLE_PASSWORD", self.password),
                ("ORACLE_DSN", self.dsn),
            )
            if not value
        ]
        if missing:
            raise ValueError("Missing Oracle configuration: " + ", ".join(missing))

    def validate(self) -> None:
        self.validate_connection()
        if not (self.table or self.query):
            raise ValueError("ORACLE_TABLE or ORACLE_QUERY is required")

    @property
    def qualified_table(self) -> str:
        return f"{self.schema}.{self.table}" if self.schema else self.table


@dataclass
class BigQueryConfig:
    project: str = field(default_factory=lambda: _env("BQ_PROJECT"))
    dataset: str = field(default_factory=lambda: _env("BQ_DATASET"))
    table: str = field(default_factory=lambda: _env("BQ_TABLE"))
    location: str = field(default_factory=lambda: _env("BQ_LOCATION"))
    where: str = field(default_factory=lambda: _env("BQ_WHERE"))
    query: str = field(default_factory=lambda: _env("BQ_QUERY"))
    credentials_json: str = field(
        default_factory=lambda: (
            _env("BQ_CREDENTIALS_JSON") or _env("GOOGLE_APPLICATION_CREDENTIALS")
        )
    )
    credentials_json_content: str = field(
        default_factory=lambda: _env("BQ_CREDENTIALS_JSON_CONTENT")
    )

    def validate_connection(self) -> None:
        if not (self.credentials_json or self.credentials_json_content):
            raise ValueError(
                "BigQuery service account credentials are required: set "
                "BQ_CREDENTIALS_JSON (file path) or BQ_CREDENTIALS_JSON_CONTENT (JSON)"
            )

    def validate(self) -> None:
        self.validate_connection()
        if not self.query and not (self.dataset and self.table):
            raise ValueError("BQ_DATASET and BQ_TABLE (or BQ_QUERY) are required")


@dataclass
class CompareConfig:
    join_columns: list[str] = field(default_factory=lambda: _env_list("COMPARE_JOIN_COLUMNS"))
    ignore_columns: list[str] = field(default_factory=lambda: _env_list("COMPARE_IGNORE_COLUMNS"))
    abs_tol: float = field(default_factory=lambda: _env_float("COMPARE_ABS_TOL", 0.0))
    rel_tol: float = field(default_factory=lambda: _env_float("COMPARE_REL_TOL", 0.0))
    ignore_case: bool = field(default_factory=lambda: _env_bool("COMPARE_IGNORE_CASE", False))
    ignore_spaces: bool = field(default_factory=lambda: _env_bool("COMPARE_IGNORE_SPACES", True))
    sample_rows: int = field(default_factory=lambda: _env_int("COMPARE_SAMPLE_ROWS", 10_000))
    full_datacompy_max_rows: int = field(
        default_factory=lambda: _env_int("COMPARE_FULL_DATACOMPY_MAX_ROWS", 500_000)
    )
    output_dir: str = field(default_factory=lambda: _env("COMPARE_OUTPUT_DIR", "reports"))
    oracle_name: str = "oracle"
    bigquery_name: str = "bigquery"


@dataclass
class DuckDBConfig:
    path: str = field(default_factory=lambda: _env("DUCKDB_PATH", ":memory:"))
    memory_limit: str = field(default_factory=lambda: _env("DUCKDB_MEMORY_LIMIT"))
    threads: int = field(default_factory=lambda: _env_int("DUCKDB_THREADS", 0))
    temp_directory: str = field(default_factory=lambda: _env("DUCKDB_TEMP_DIR"))


@dataclass
class Settings:
    oracle: OracleConfig = field(default_factory=OracleConfig)
    bigquery: BigQueryConfig = field(default_factory=BigQueryConfig)
    compare: CompareConfig = field(default_factory=CompareConfig)
    duckdb: DuckDBConfig = field(default_factory=DuckDBConfig)

    def validate(self) -> None:
        self.oracle.validate()
        self.bigquery.validate()
