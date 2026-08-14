"""FastAPI backend powering the React comparison UI.

Connection details default to the environment configuration; anything sent in a
request overrides it, so the UI can point at a different table without a
restart. Secrets are never returned by the API.
"""

import logging
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import bigquery_source, oracle_source
from .compare import ComparisonResult, compare_frames
from .config import BigQueryConfig, CompareConfig, OracleConfig, Settings
from .report import write_reports

logger = logging.getLogger(__name__)

UI_DIST_DIR = Path(__file__).resolve().parent.parent / "data-compare-ui" / "dist"


class OracleRequest(BaseModel):
    """Oracle overrides for a single request."""

    user: str | None = None
    password: str | None = None
    dsn: str | None = None
    schema_name: str | None = Field(default=None, alias="schema")
    table: str | None = None
    where: str | None = None
    query: str | None = None

    model_config = {"populate_by_name": True}


class BigQueryRequest(BaseModel):
    """BigQuery overrides for a single request."""

    project: str | None = None
    dataset: str | None = None
    table: str | None = None
    location: str | None = None
    where: str | None = None
    query: str | None = None
    credentials_json: str | None = None
    credentials_json_content: str | None = None


class CompareOptions(BaseModel):
    """Comparison options chosen in the UI."""

    join_columns: list[str] = Field(default_factory=list)
    ignore_columns: list[str] = Field(default_factory=list)
    abs_tol: float = 0.0
    rel_tol: float = 0.0
    ignore_case: bool = False
    ignore_spaces: bool = True
    sample_count: int = 10
    max_preview_rows: int = 100
    write_report_files: bool = False


class ColumnsRequest(BaseModel):
    """Request to fetch the columns of the source and target tables."""

    oracle: OracleRequest = Field(default_factory=OracleRequest)
    bigquery: BigQueryRequest = Field(default_factory=BigQueryRequest)


class CompareRequest(ColumnsRequest):
    """Request to run a full comparison."""

    options: CompareOptions = Field(default_factory=CompareOptions)


def build_oracle_config(request: OracleRequest) -> OracleConfig:
    """Merge request overrides onto the environment Oracle configuration."""
    config = OracleConfig()
    for attribute, value in (
        ("user", request.user),
        ("password", request.password),
        ("dsn", request.dsn),
        ("schema", request.schema_name),
        ("table", request.table),
        ("where", request.where),
        ("query", request.query),
    ):
        if value is not None and value != "":
            setattr(config, attribute, value)
    return config


def build_bigquery_config(request: BigQueryRequest) -> BigQueryConfig:
    """Merge request overrides onto the environment BigQuery configuration."""
    config = BigQueryConfig()
    for attribute, value in (
        ("project", request.project),
        ("dataset", request.dataset),
        ("table", request.table),
        ("location", request.location),
        ("where", request.where),
        ("query", request.query),
        ("credentials_json", request.credentials_json),
        ("credentials_json_inline", request.credentials_json_content),
    ):
        if value is not None and value != "":
            setattr(config, attribute, value)
    return config


def build_compare_config(options: CompareOptions) -> CompareConfig:
    """Merge UI options onto the environment comparison configuration."""
    config = CompareConfig()
    if options.join_columns:
        config.join_columns = options.join_columns
    config.ignore_columns = options.ignore_columns
    config.abs_tol = options.abs_tol
    config.rel_tol = options.rel_tol
    config.ignore_case = options.ignore_case
    config.ignore_spaces = options.ignore_spaces
    config.sample_count = options.sample_count
    return config


def frame_to_records(
    frame: pd.DataFrame | None, limit: int
) -> list[dict[str, Any]]:
    """Convert a frame to JSON-safe records, truncated to ``limit`` rows."""
    if frame is None or frame.empty:
        return []
    truncated = frame.head(limit)
    return [
        {
            column: (None if pd.isna(value) else value)
            for column, value in record.items()
        }
        for record in truncated.astype("object").to_dict(orient="records")
    ]


def serialize_result(
    result: ComparisonResult, options: CompareOptions
) -> dict[str, Any]:
    """Build the JSON payload for a comparison result."""
    limit = options.max_preview_rows
    return {
        "matches": result.matches,
        "summary": result.summary,
        "report": result.report,
        "mismatch_rows": frame_to_records(result.mismatch_rows, limit),
        "oracle_only_rows": frame_to_records(result.oracle_only_rows, limit),
        "bigquery_only_rows": frame_to_records(result.bigquery_only_rows, limit),
        "truncated": {
            "mismatch_rows": len(result.mismatch_rows) > limit,
            "oracle_only_rows": len(result.oracle_only_rows) > limit,
            "bigquery_only_rows": len(result.bigquery_only_rows) > limit,
        },
    }


def build_app(allow_origins: list[str] | None = None) -> FastAPI:
    """Create the FastAPI application serving the comparison API and the UI."""
    app = FastAPI(
        title="Oracle vs BigQuery data comparison",
        description="Fetch table columns and compare data with datacompy.",
        version="0.1.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins or ["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/defaults")
    def defaults() -> dict[str, Any]:
        """Environment-derived defaults for prefilling the UI (no secrets)."""
        settings = Settings()
        return {
            "oracle": {
                "user": settings.oracle.user,
                "dsn": settings.oracle.dsn,
                "schema": settings.oracle.schema,
                "table": settings.oracle.table,
                "password_configured": bool(settings.oracle.password),
            },
            "bigquery": {
                "project": settings.bigquery.project,
                "dataset": settings.bigquery.dataset,
                "table": settings.bigquery.table,
                "location": settings.bigquery.location,
                "credentials_configured": bool(
                    settings.bigquery.credentials_json
                    or settings.bigquery.credentials_json_inline
                ),
            },
            "compare": {
                "join_columns": settings.compare.join_columns,
                "abs_tol": settings.compare.abs_tol,
                "rel_tol": settings.compare.rel_tol,
                "ignore_case": settings.compare.ignore_case,
                "ignore_spaces": settings.compare.ignore_spaces,
                "output_dir": settings.compare.output_dir,
            },
        }

    @app.post("/api/oracle/tables")
    def oracle_tables(request: OracleRequest) -> dict[str, list[str]]:
        with _handled_errors("Oracle"):
            return {"tables": oracle_source.fetch_tables(build_oracle_config(request))}

    @app.post("/api/bigquery/tables")
    def bigquery_tables(request: BigQueryRequest) -> dict[str, list[str]]:
        with _handled_errors("BigQuery"):
            return {
                "tables": bigquery_source.fetch_tables(build_bigquery_config(request))
            }

    @app.post("/api/columns")
    def columns(request: ColumnsRequest) -> dict[str, Any]:
        """Fetch source and target columns plus their intersection."""
        with _handled_errors("Oracle"):
            oracle_columns = oracle_source.fetch_columns(
                build_oracle_config(request.oracle)
            )
        with _handled_errors("BigQuery"):
            bigquery_columns = bigquery_source.fetch_columns(
                build_bigquery_config(request.bigquery)
            )
        oracle_names = {column["name"].lower() for column in oracle_columns}
        bigquery_names = {column["name"].lower() for column in bigquery_columns}
        return {
            "oracle_columns": oracle_columns,
            "bigquery_columns": bigquery_columns,
            "common_columns": sorted(oracle_names & bigquery_names),
            "oracle_only_columns": sorted(oracle_names - bigquery_names),
            "bigquery_only_columns": sorted(bigquery_names - oracle_names),
        }

    @app.post("/api/compare")
    def compare(request: CompareRequest) -> dict[str, Any]:
        oracle_config = build_oracle_config(request.oracle)
        bigquery_config = build_bigquery_config(request.bigquery)
        compare_config = build_compare_config(request.options)
        with _handled_errors("Oracle"):
            oracle_df = oracle_source.fetch_dataframe(oracle_config)
        with _handled_errors("BigQuery"):
            bigquery_df = bigquery_source.fetch_dataframe(bigquery_config)
        with _handled_errors("comparison"):
            result = compare_frames(oracle_df, bigquery_df, compare_config)
        payload = serialize_result(result, request.options)
        if request.options.write_report_files:
            payload["artifacts"] = write_reports(
                result,
                compare_config.output_dir,
                (oracle_config.table or "comparison").lower(),
            )
        return payload

    if UI_DIST_DIR.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=UI_DIST_DIR / "assets"),
            name="assets",
        )

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(UI_DIST_DIR / "index.html")

    return app


class _handled_errors:
    """Translate source/config errors into HTTP 400 responses."""

    def __init__(self, source: str) -> None:
        self.source = source

    def __enter__(self) -> "_handled_errors":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if exc is None:
            return False
        if isinstance(exc, HTTPException):
            return False
        logger.exception("%s request failed", self.source)
        raise HTTPException(
            status_code=400, detail=f"{self.source} error: {exc}"
        ) from exc


app = build_app()
