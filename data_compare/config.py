"""Configuration for the Oracle vs BigQuery data comparison application."""

import logging
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _get_env(key: str, default: str | None = None) -> str:
    return os.environ.get(key, default) or ""


def _get_bool(key: str, default: str = "false") -> bool:
    return _get_env(key, default).lower() in ("true", "1", "yes")


def _get_list(key: str, default: str = "") -> list[str]:
    raw = _get_env(key, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class OracleConfig:
    """Connection settings for the Oracle source."""

    user: str = field(default_factory=lambda: _get_env("ORACLE_USER"))
    password: str = field(default_factory=lambda: _get_env("ORACLE_PASSWORD"))
    dsn: str = field(default_factory=lambda: _get_env("ORACLE_DSN"))
    schema: str = field(default_factory=lambda: _get_env("ORACLE_SCHEMA"))
    table: str = field(default_factory=lambda: _get_env("ORACLE_TABLE", "FEEDBACK"))
    where: str = field(default_factory=lambda: _get_env("ORACLE_WHERE"))
    query: str = field(default_factory=lambda: _get_env("ORACLE_QUERY"))
    arraysize: int = field(
        default_factory=lambda: int(_get_env("ORACLE_ARRAYSIZE", "5000") or "5000")
    )
    thick_mode: bool = field(default_factory=lambda: _get_bool("ORACLE_THICK_MODE"))
    lib_dir: str = field(default_factory=lambda: _get_env("ORACLE_CLIENT_LIB_DIR"))

    def validate(self) -> None:
        """Raise if required Oracle settings are missing."""
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
            raise ValueError(
                "Missing Oracle configuration: " + ", ".join(missing)
            )
        if not self.query and not self.table:
            raise ValueError("Either ORACLE_TABLE or ORACLE_QUERY is required.")


@dataclass
class BigQueryConfig:
    """Connection settings for the BigQuery source."""

    project: str = field(default_factory=lambda: _get_env("BQ_PROJECT"))
    dataset: str = field(default_factory=lambda: _get_env("BQ_DATASET"))
    table: str = field(default_factory=lambda: _get_env("BQ_TABLE", "FeedBack"))
    location: str = field(default_factory=lambda: _get_env("BQ_LOCATION"))
    where: str = field(default_factory=lambda: _get_env("BQ_WHERE"))
    query: str = field(default_factory=lambda: _get_env("BQ_QUERY"))
    credentials_json: str = field(
        default_factory=lambda: _get_env(
            "BQ_CREDENTIALS_JSON",
            _get_env("GOOGLE_APPLICATION_CREDENTIALS"),
        )
    )
    credentials_json_inline: str = field(
        default_factory=lambda: _get_env("BQ_CREDENTIALS_JSON_CONTENT")
    )

    def validate(self) -> None:
        """Raise if required BigQuery settings are missing."""
        if not self.query:
            missing = [
                name
                for name, value in (
                    ("BQ_DATASET", self.dataset),
                    ("BQ_TABLE", self.table),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    "Missing BigQuery configuration: " + ", ".join(missing)
                )
        if not self.credentials_json and not self.credentials_json_inline:
            raise ValueError(
                "A service account is required: set BQ_CREDENTIALS_JSON (path), "
                "GOOGLE_APPLICATION_CREDENTIALS (path) or "
                "BQ_CREDENTIALS_JSON_CONTENT (inline JSON)."
            )


@dataclass
class CompareConfig:
    """Settings controlling how the two datasets are compared."""

    join_columns: list[str] = field(
        default_factory=lambda: _get_list("COMPARE_JOIN_COLUMNS", "chatmessageid")
    )
    ignore_columns: list[str] = field(
        default_factory=lambda: _get_list("COMPARE_IGNORE_COLUMNS")
    )
    abs_tol: float = field(
        default_factory=lambda: float(_get_env("COMPARE_ABS_TOL", "0") or "0")
    )
    rel_tol: float = field(
        default_factory=lambda: float(_get_env("COMPARE_REL_TOL", "0") or "0")
    )
    ignore_case: bool = field(
        default_factory=lambda: _get_bool("COMPARE_IGNORE_CASE")
    )
    ignore_spaces: bool = field(
        default_factory=lambda: _get_bool("COMPARE_IGNORE_SPACES", "true")
    )
    sample_count: int = field(
        default_factory=lambda: int(_get_env("COMPARE_SAMPLE_COUNT", "10") or "10")
    )
    output_dir: str = field(
        default_factory=lambda: _get_env("COMPARE_OUTPUT_DIR", "compare_reports")
    )
    oracle_name: str = field(
        default_factory=lambda: _get_env("COMPARE_ORACLE_NAME", "oracle")
    )
    bigquery_name: str = field(
        default_factory=lambda: _get_env("COMPARE_BIGQUERY_NAME", "bigquery")
    )

    def validate(self) -> None:
        """Raise if no join columns were configured."""
        if not self.join_columns:
            raise ValueError(
                "At least one join column is required "
                "(COMPARE_JOIN_COLUMNS or --join-columns)."
            )


@dataclass
class Settings:
    """Full application settings."""

    oracle: OracleConfig = field(default_factory=OracleConfig)
    bigquery: BigQueryConfig = field(default_factory=BigQueryConfig)
    compare: CompareConfig = field(default_factory=CompareConfig)
    log_level: str = field(default_factory=lambda: _get_env("LOG_LEVEL", "INFO"))

    def validate(self) -> None:
        """Validate every configuration section."""
        self.oracle.validate()
        self.bigquery.validate()
        self.compare.validate()


def configure_logging(log_level: str = "INFO") -> None:
    """Configure root logging for CLI runs."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
