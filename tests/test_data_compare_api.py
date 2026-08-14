"""Tests for the comparison API backing the React UI."""

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from data_compare import api, bigquery_source, oracle_source

ORACLE_ROWS = pd.DataFrame(
    [
        (1, "s1", "hello", "layer-a"),
        (2, "s2", "how are you", "layer-b"),
        (3, "s3", "oracle only", "layer-c"),
    ],
    columns=["CHATMESSAGEID", "SESSIONID", "USER_TEXT", "LAYER_TEXT"],
)

BIGQUERY_ROWS = pd.DataFrame(
    [
        (1, "s1", "hello", "layer-a"),
        (2, "s2", "how are you?", "layer-b"),
    ],
    columns=["chatmessageid", "sessionid", "user_text", "layer_text"],
)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        oracle_source,
        "fetch_columns",
        lambda config: [
            {"name": name, "type": "VARCHAR2"} for name in ORACLE_ROWS.columns
        ],
    )
    monkeypatch.setattr(
        bigquery_source,
        "fetch_columns",
        lambda config: [
            {"name": name, "type": "STRING"} for name in BIGQUERY_ROWS.columns
        ],
    )
    monkeypatch.setattr(
        oracle_source, "fetch_tables", lambda config: ["FEEDBACK", "SESSIONS"]
    )
    monkeypatch.setattr(bigquery_source, "fetch_tables", lambda config: ["FeedBack"])
    monkeypatch.setattr(
        oracle_source, "fetch_dataframe", lambda config: ORACLE_ROWS.copy()
    )
    monkeypatch.setattr(
        bigquery_source, "fetch_dataframe", lambda config: BIGQUERY_ROWS.copy()
    )
    return TestClient(api.build_app())


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_defaults_never_expose_secrets(client):
    payload = client.get("/api/defaults").json()

    assert set(payload) == {"oracle", "bigquery", "compare"}
    assert "password" not in payload["oracle"]
    assert "credentials_json" not in payload["bigquery"]
    assert isinstance(payload["oracle"]["password_configured"], bool)


def test_list_tables(client):
    assert client.post("/api/oracle/tables", json={}).json()["tables"] == [
        "FEEDBACK",
        "SESSIONS",
    ]
    assert client.post("/api/bigquery/tables", json={}).json()["tables"] == ["FeedBack"]


def test_columns_endpoint_returns_intersection(client):
    payload = client.post(
        "/api/columns",
        json={
            "oracle": {"table": "FEEDBACK", "schema": "APP"},
            "bigquery": {"dataset": "analytics", "table": "FeedBack"},
        },
    ).json()

    assert payload["common_columns"] == [
        "chatmessageid",
        "layer_text",
        "sessionid",
        "user_text",
    ]
    assert payload["oracle_only_columns"] == []
    assert payload["bigquery_only_columns"] == []
    assert {"name": "USER_TEXT", "type": "VARCHAR2"} in payload["oracle_columns"]


def test_compare_endpoint_reports_differences(client):
    payload = client.post(
        "/api/compare",
        json={
            "oracle": {"table": "FEEDBACK"},
            "bigquery": {"dataset": "analytics", "table": "FeedBack"},
            "options": {"join_columns": ["chatmessageid"]},
        },
    ).json()

    assert payload["matches"] is False
    assert payload["summary"]["oracle_only_row_count"] == 1
    assert payload["summary"]["columns_with_differences"] == ["user_text"]
    assert payload["mismatch_rows"][0]["chatmessageid"] == 2
    assert payload["oracle_only_rows"][0]["chatmessageid"] == 3
    assert "artifacts" not in payload
    assert payload["report"]


def test_compare_endpoint_writes_artifacts_when_requested(client, tmp_path, monkeypatch):
    monkeypatch.setenv("COMPARE_OUTPUT_DIR", str(tmp_path))

    payload = client.post(
        "/api/compare",
        json={
            "oracle": {"table": "FEEDBACK"},
            "bigquery": {"dataset": "analytics", "table": "FeedBack"},
            "options": {
                "join_columns": ["chatmessageid"],
                "write_report_files": True,
            },
        },
    ).json()

    assert payload["artifacts"]["summary"].startswith(str(tmp_path))
    assert (tmp_path / "feedback_summary.json").exists()


def test_compare_endpoint_rejects_missing_join_column(client):
    response = client.post(
        "/api/compare",
        json={
            "oracle": {"table": "FEEDBACK"},
            "bigquery": {"dataset": "analytics", "table": "FeedBack"},
            "options": {"join_columns": ["nope"]},
        },
    )

    assert response.status_code == 400
    assert "Join columns missing" in response.json()["detail"]


def test_source_errors_become_bad_requests(client, monkeypatch):
    def boom(config):
        raise ValueError("ORA-12541: TNS:no listener")

    monkeypatch.setattr(oracle_source, "fetch_columns", boom)
    response = client.post("/api/columns", json={})

    assert response.status_code == 400
    assert "Oracle error" in response.json()["detail"]


def test_request_overrides_are_applied_to_configs():
    oracle = api.build_oracle_config(
        api.OracleRequest(user="u", dsn="d", schema="APP", table="FEEDBACK")
    )
    bigquery = api.build_bigquery_config(
        api.BigQueryRequest(
            project="p", dataset="ds", table="FeedBack", credentials_json="/tmp/sa.json"
        )
    )
    options = api.build_compare_config(
        api.CompareOptions(join_columns=["chatmessageid"], ignore_case=True)
    )

    assert (oracle.user, oracle.schema, oracle.table) == ("u", "APP", "FEEDBACK")
    assert (bigquery.project, bigquery.credentials_json) == ("p", "/tmp/sa.json")
    assert options.join_columns == ["chatmessageid"]
    assert options.ignore_case is True
