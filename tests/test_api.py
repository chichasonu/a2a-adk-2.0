import time

import pyarrow as pa
import pytest
from fastapi.testclient import TestClient

from oracle_bq_compare import api, bigquery_source, oracle_source
from oracle_bq_compare.duck import load_arrow_batches, normalize_column_names
from oracle_bq_compare.selection import (
    ComparisonRequest,
    SideSelection,
    build_bigquery_query,
    build_oracle_query,
)

ORACLE = pa.table(
    {"ID": [1, 2, 3], "NAME": ["a", "b", "c"], "AMT": [1.0, 2.0, 3.0], "CREATED": ["x"] * 3}
)
BQ = pa.table({"id": [1, 2, 4], "name": ["a", "B", "d"], "amt": [1.0, 2.0, 4.0]})


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ORACLE_USER", "u")
    monkeypatch.setenv("ORACLE_PASSWORD", "p")
    monkeypatch.setenv("ORACLE_DSN", "h/s")
    monkeypatch.setenv("BQ_CREDENTIALS_JSON_CONTENT", '{"type":"service_account"}')

    monkeypatch.setattr(oracle_source, "list_schemas", lambda cfg: ["APP", "HR"])
    monkeypatch.setattr(
        oracle_source, "list_tables", lambda cfg, s: [{"name": f"{s}_T", "type": "TABLE"}]
    )
    monkeypatch.setattr(
        oracle_source,
        "list_columns",
        lambda cfg, s, t: [{"name": "ID", "type": "NUMBER"}, {"name": "NAME", "type": "VARCHAR2"}],
    )
    monkeypatch.setattr(bigquery_source, "list_datasets", lambda cfg: ["analytics"])
    monkeypatch.setattr(
        bigquery_source, "list_tables", lambda cfg, d: [{"name": "customers", "type": "TABLE"}]
    )
    monkeypatch.setattr(
        bigquery_source,
        "list_columns",
        lambda cfg, d, t: [{"name": "id", "type": "INTEGER"}, {"name": "name", "type": "STRING"}],
    )
    monkeypatch.setattr(bigquery_source, "make_client", lambda cfg: (None, None, "proj"))

    captured = {}

    def fake_oracle(con, cfg, sql, params=None, table="oracle"):
        captured["oracle"] = (sql, params)
        load_arrow_batches(con, table, [ORACLE.select([0, 1, 2])])
        normalize_column_names(con, table)
        return 3

    def fake_bq(con, cfg, sql, params=None, table="bigquery"):
        captured["bigquery"] = (sql, params)
        load_arrow_batches(con, table, [BQ])
        normalize_column_names(con, table)
        return 3

    monkeypatch.setattr(oracle_source, "load_query_into_duckdb", fake_oracle)
    monkeypatch.setattr(bigquery_source, "load_query_into_duckdb", fake_bq)
    c = TestClient(api.create_app())
    c.captured = captured
    return c


def test_metadata_endpoints(client):
    assert client.get("/api/health").json()["oracle_configured"] is True
    assert client.get("/api/oracle/schemas").json() == ["APP", "HR"]
    assert client.get("/api/oracle/schemas/APP/tables").json()[0]["name"] == "APP_T"
    cols = client.get("/api/oracle/schemas/APP/tables/APP_T/columns").json()
    assert [c["name"] for c in cols] == ["ID", "NAME"]
    assert client.get("/api/bigquery/datasets").json() == ["analytics"]
    assert client.get("/api/bigquery/datasets/analytics/tables").json()[0]["name"] == "customers"
    cols = client.get("/api/bigquery/datasets/analytics/tables/customers/columns").json()
    assert cols[1] == {"name": "name", "type": "STRING"}


def _wait(client, job_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/jobs/{job_id}").json()
        if data["status"] in ("done", "failed"):
            return data
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_analyse_flow(client):
    body = {
        "oracle": {
            "schema": "APP",
            "table": "CUSTOMERS",
            "columns": ["ID", "NAME", "AMT"],
            "date_column": "CREATED",
            "date_from": "2024-01-01",
            "date_to": "2024-01-31",
        },
        "bigquery": {
            "schema": "analytics",
            "table": "customers",
            "columns": ["id", "name", "amt"],
            "date_column": "created",
            "date_from": "2024-01-01",
            "date_to": "2024-01-31",
        },
        "join_columns": ["ID"],
    }
    resp = client.post("/api/analyse", json=body)
    assert resp.status_code == 202, resp.text
    data = _wait(client, resp.json()["id"])
    assert data["status"] == "done", data["error"]
    result = data["result"]
    assert result["oracle_row_count"] == 3 and result["bigquery_row_count"] == 3
    assert result["common_row_count"] == 2
    assert result["rows_matching"] == 1 and result["rows_with_differences"] == 1
    assert result["oracle_only_row_count"] == 1 and result["bigquery_only_row_count"] == 1
    assert result["mismatch_rows"][0]["id"] == 2
    assert result["mismatch_rows"][0]["name_oracle"] == "b"
    assert result["mismatch_rows"][0]["name_bigquery"] == "B"
    assert result["oracle_only_rows"][0]["id"] == 3
    assert result["bigquery_only_rows"][0]["id"] == 4
    assert "DataComPy" in result["datacompy_report"]

    osql, oparams = client.captured["oracle"]
    assert osql.startswith(
        'SELECT "ID" AS "id", "NAME" AS "name", "AMT" AS "amt" FROM "APP"."CUSTOMERS"'
    )
    assert oparams == {"date_from": "2024-01-01", "date_to": "2024-01-31"}
    bsql, _ = client.captured["bigquery"]
    assert "`proj.analytics.customers`" in bsql and "DATE(@date_from)" in bsql


def test_analyse_validation(client):
    body = {
        "oracle": {"schema": "APP", "table": "T", "columns": ["ID", "NAME"]},
        "bigquery": {"schema": "ds", "table": "t", "columns": ["id"]},
        "join_columns": ["ID"],
    }
    assert client.post("/api/analyse", json=body).status_code == 422
    body["bigquery"]["columns"] = ["id", "name"]
    body["join_columns"] = ["NOPE"]
    assert client.post("/api/analyse", json=body).status_code == 422
    body["join_columns"] = ["ID"]
    body["oracle"]["table"] = 'T"; DROP TABLE X'
    assert client.post("/api/analyse", json=body).status_code == 422
    assert client.get("/api/jobs/nope").status_code == 404


def test_query_builders_alias_bigquery_to_oracle_names():
    req = ComparisonRequest(
        oracle=SideSelection("APP", "T", ["CUST_ID", "Amount"]),
        bigquery=SideSelection("ds", "t", ["customer_id", "amount"], "created", "2024-01-01", ""),
        join_columns=["CUST_ID"],
    )
    req.validate()
    sql, params = build_oracle_query(req.oracle)
    assert sql == 'SELECT "CUST_ID" AS "cust_id", "Amount" AS "amount" FROM "APP"."T"'
    assert params == {}
    sql, params = build_bigquery_query(req.bigquery, "p", req.target_names)
    assert sql == (
        "SELECT `customer_id` AS `cust_id`, `amount` AS `amount` FROM `p.ds.t` "
        "WHERE CAST(`created` AS DATE) >= DATE(@date_from)"
    )
    assert params == {"date_from": "2024-01-01"}
