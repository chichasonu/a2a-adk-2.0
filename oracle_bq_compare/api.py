"""FastAPI backend: metadata browsing for Oracle / BigQuery and asynchronous comparisons."""

from __future__ import annotations

import logging
import math
import os
import tempfile
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import bigquery_source, oracle_source
from .compare import MISMATCH, ONLY_LEFT, ONLY_RIGHT, ComparisonResult, compare_tables
from .config import CompareConfig, Settings
from .duck import connect
from .selection import ComparisonRequest, SideSelection, build_bigquery_query, build_oracle_query

logger = logging.getLogger(__name__)

MAX_PREVIEW_ROWS = 500


# ---------------------------------------------------------------------------- models
class SideSelectionModel(BaseModel):
    schema_name: str = Field(alias="schema")
    table: str
    columns: list[str]
    date_column: str = ""
    date_from: str = ""
    date_to: str = ""

    model_config = {"populate_by_name": True}

    def to_selection(self) -> SideSelection:
        return SideSelection(
            schema=self.schema_name,
            table=self.table,
            columns=self.columns,
            date_column=self.date_column,
            date_from=self.date_from,
            date_to=self.date_to,
        )


class AnalyseRequest(BaseModel):
    oracle: SideSelectionModel
    bigquery: SideSelectionModel
    join_columns: list[str]
    abs_tol: float = 0.0
    rel_tol: float = 0.0
    ignore_case: bool = False
    ignore_spaces: bool = True
    sample_rows: int = 10_000
    full_datacompy_max_rows: int = 500_000
    preview_rows: int = 200

    def to_request(self) -> ComparisonRequest:
        return ComparisonRequest(
            oracle=self.oracle.to_selection(),
            bigquery=self.bigquery.to_selection(),
            join_columns=self.join_columns,
            abs_tol=self.abs_tol,
            rel_tol=self.rel_tol,
            ignore_case=self.ignore_case,
            ignore_spaces=self.ignore_spaces,
            sample_rows=self.sample_rows,
            full_datacompy_max_rows=self.full_datacompy_max_rows,
        )


# ---------------------------------------------------------------------------- jobs
class Job:
    def __init__(self, job_id: str) -> None:
        self.id = job_id
        self.status = "queued"
        self.stage = "queued"
        self.error: str | None = None
        self.result: dict[str, Any] | None = None
        self.created_at = datetime.now(tz=timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "stage": self.stage,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "result": self.result,
        }


class JobStore:
    def __init__(self, workers: int = 2) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=workers)

    def submit(self, fn, *args) -> Job:
        job = Job(uuid.uuid4().hex)
        with self._lock:
            self._jobs[job.id] = job
        self._executor.submit(self._run, job, fn, *args)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _run(self, job: Job, fn, *args) -> None:
        job.status = "running"
        try:
            job.result = fn(job, *args)
            job.status = "done"
            job.stage = "done"
        except Exception as exc:  # noqa: BLE001
            logger.error("Job %s failed: %s\n%s", job.id, exc, traceback.format_exc())
            job.status = "failed"
            job.error = str(exc)


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return value


def _rows(con: duckdb.DuckDBPyConnection, table: str, limit: int) -> list[dict[str, Any]]:
    cur = con.execute(f"SELECT * FROM {table} LIMIT {int(limit)}")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, (_json_value(v) for v in row))) for row in cur.fetchall()]


def _result_payload(
    con: duckdb.DuckDBPyConnection, result: ComparisonResult, preview_rows: int
) -> dict[str, Any]:
    limit = max(1, min(preview_rows, MAX_PREVIEW_ROWS))
    payload = result.to_dict()
    payload["datacompy_report"] = result.datacompy_report
    payload["column_stats"] = [
        {
            "column": s.column,
            "oracle_type": s.left_type,
            "bigquery_type": s.right_type,
            "mismatches": s.mismatches,
        }
        for s in result.column_stats
    ]
    payload["mismatch_rows"] = _rows(con, MISMATCH, limit)
    payload["oracle_only_rows"] = _rows(con, ONLY_LEFT, limit)
    payload["bigquery_only_rows"] = _rows(con, ONLY_RIGHT, limit)
    payload["preview_limit"] = limit
    return payload


def run_comparison(job: Job, request: ComparisonRequest, preview_rows: int) -> dict[str, Any]:
    request.validate()
    settings = Settings()
    settings.oracle.validate_connection()
    settings.bigquery.validate_connection()
    db_dir = Path(os.environ.get("DUCKDB_JOB_DIR") or tempfile.gettempdir())
    db_dir.mkdir(parents=True, exist_ok=True)
    settings.duckdb.path = str(db_dir / f"compare-{job.id}.duckdb")
    con = connect(settings.duckdb)
    try:
        job.stage = "extracting oracle"
        sql, params = build_oracle_query(request.oracle)
        oracle_source.load_query_into_duckdb(con, settings.oracle, sql, params)

        job.stage = "extracting bigquery"
        _, _, project = bigquery_source.make_client(settings.bigquery)
        sql, params = build_bigquery_query(request.bigquery, project, request.target_names)
        bigquery_source.load_query_into_duckdb(con, settings.bigquery, sql, params)

        job.stage = "comparing"
        config = CompareConfig(
            join_columns=[c.lower() for c in request.join_columns],
            abs_tol=request.abs_tol,
            rel_tol=request.rel_tol,
            ignore_case=request.ignore_case,
            ignore_spaces=request.ignore_spaces,
            sample_rows=request.sample_rows,
            full_datacompy_max_rows=request.full_datacompy_max_rows,
        )
        result = compare_tables(con, config)
        return _result_payload(con, result, preview_rows)
    finally:
        con.close()
        try:
            os.remove(settings.duckdb.path)
        except OSError:
            pass


# ---------------------------------------------------------------------------- app
def create_app() -> FastAPI:
    app = FastAPI(title="Oracle vs BigQuery compare", version="0.2.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    jobs = JobStore(workers=int(os.environ.get("COMPARE_WORKERS", "2")))

    def oracle_cfg():
        cfg = Settings().oracle
        try:
            cfg.validate_connection()
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return cfg

    def bigquery_cfg():
        cfg = Settings().bigquery
        try:
            cfg.validate_connection()
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return cfg

    def guarded(fn, *args):
        try:
            return fn(*args)
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Metadata call failed")
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        s = Settings()
        return {
            "status": "ok",
            "oracle_configured": bool(s.oracle.user and s.oracle.password and s.oracle.dsn),
            "bigquery_configured": bool(
                s.bigquery.credentials_json or s.bigquery.credentials_json_content
            ),
        }

    @app.get("/api/oracle/schemas")
    def oracle_schemas() -> list[str]:
        return guarded(oracle_source.list_schemas, oracle_cfg())

    @app.get("/api/oracle/schemas/{schema}/tables")
    def oracle_tables(schema: str) -> list[dict[str, str]]:
        return guarded(oracle_source.list_tables, oracle_cfg(), schema)

    @app.get("/api/oracle/schemas/{schema}/tables/{table}/columns")
    def oracle_columns(schema: str, table: str) -> list[dict[str, str]]:
        return guarded(oracle_source.list_columns, oracle_cfg(), schema, table)

    @app.get("/api/bigquery/datasets")
    def bigquery_datasets() -> list[str]:
        return guarded(bigquery_source.list_datasets, bigquery_cfg())

    @app.get("/api/bigquery/datasets/{dataset}/tables")
    def bigquery_tables(dataset: str) -> list[dict[str, str]]:
        return guarded(bigquery_source.list_tables, bigquery_cfg(), dataset)

    @app.get("/api/bigquery/datasets/{dataset}/tables/{table}/columns")
    def bigquery_columns(dataset: str, table: str) -> list[dict[str, str]]:
        return guarded(bigquery_source.list_columns, bigquery_cfg(), dataset, table)

    @app.post("/api/analyse", status_code=202)
    def analyse(body: AnalyseRequest) -> dict[str, Any]:
        request = body.to_request()
        try:
            request.validate()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        job = jobs.submit(run_comparison, request, body.preview_rows)
        return job.to_dict()

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str, include_result: bool = Query(True)) -> dict[str, Any]:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job")
        data = job.to_dict()
        if not include_result:
            data["result"] = None
        return data

    static_dir = Path(os.environ.get("UI_DIST_DIR", Path(__file__).parent.parent / "ui" / "dist"))
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="ui")

    return app


app = create_app()
