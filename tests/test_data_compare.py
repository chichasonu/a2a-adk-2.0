"""Tests for the Oracle vs BigQuery datacompy comparison application."""

import json

import pandas as pd
import pytest

from data_compare import bigquery_source, oracle_source
from data_compare.cli import apply_overrides, build_parser, main
from data_compare.compare import compare_frames
from data_compare.config import BigQueryConfig, CompareConfig, OracleConfig, Settings
from data_compare.normalize import prepare_frames
from data_compare.report import format_console_summary, write_reports

FEEDBACK_COLUMNS = ["CHATMESSAGEID", "SESSIONID", "USER_TEXT", "LAYER_TEXT"]


def oracle_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            (1, "s1", "hello  ", "layer-a"),
            (2, "s2", "how are you", "layer-b"),
            (3, "s3", "oracle only", "layer-c"),
            (4, "s4", "same", "layer-d"),
        ],
        columns=FEEDBACK_COLUMNS,
    )


def bigquery_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            (1, "s1", "hello", "layer-a"),
            (2, "s2", "how are you?", "layer-b"),
            (5, "s5", "bigquery only", "layer-e"),
            (4, "s4", "same", "layer-d"),
        ],
        columns=["chatmessageid", "sessionid", "user_text", "layer_text"],
    )


def compare_config(**overrides) -> CompareConfig:
    config = CompareConfig()
    config.join_columns = ["chatmessageid"]
    config.ignore_columns = []
    config.abs_tol = 0.0
    config.rel_tol = 0.0
    config.ignore_case = False
    config.ignore_spaces = True
    config.sample_count = 5
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def test_prepare_frames_normalizes_names_and_reports_drift():
    left = oracle_frame()
    left["ORACLE_ONLY_COL"] = "x"
    right = bigquery_frame()
    right["bq_only_col"] = "y"

    aligned_left, aligned_right, only_left, only_right = prepare_frames(
        left, right, ["ChatMessageId"]
    )

    assert list(aligned_left.columns) == list(aligned_right.columns)
    assert "chatmessageid" in aligned_left.columns
    assert only_left == ["oracle_only_col"]
    assert only_right == ["bq_only_col"]


def test_prepare_frames_requires_join_columns_on_both_sides():
    with pytest.raises(ValueError, match="Join columns missing"):
        prepare_frames(oracle_frame(), bigquery_frame(), ["missing_key"])


def test_compare_frames_detects_row_and_value_differences():
    result = compare_frames(oracle_frame(), bigquery_frame(), compare_config())

    assert result.matches is False
    summary = result.summary
    assert summary["oracle_row_count"] == 4
    assert summary["bigquery_row_count"] == 4
    assert summary["common_row_count"] == 3
    assert summary["oracle_only_row_count"] == 1
    assert summary["bigquery_only_row_count"] == 1
    assert summary["rows_with_differences"] == 1
    assert summary["columns_with_differences"] == ["user_text"]
    assert not result.mismatch_rows.empty


def test_trailing_whitespace_is_not_a_mismatch():
    result = compare_frames(oracle_frame(), bigquery_frame(), compare_config())
    mismatched_ids = set(result.mismatch_rows["chatmessageid"])

    assert 1 not in mismatched_ids
    assert 2 in mismatched_ids


def test_identical_frames_match_with_different_column_case_and_dtypes():
    left = oracle_frame().iloc[:2].copy()
    right = bigquery_frame().iloc[:2].copy()
    right.loc[right.index[0], "user_text"] = "hello"
    right.loc[right.index[1], "user_text"] = "how are you"
    right["chatmessageid"] = right["chatmessageid"].astype("Int64")

    result = compare_frames(left, right, compare_config())

    assert result.matches is True
    assert result.summary["rows_with_differences"] == 0
    assert result.mismatch_rows.empty


def test_ignore_columns_excludes_column_from_comparison():
    config = compare_config(ignore_columns=["user_text"])
    left = oracle_frame().iloc[[0, 1, 3]]
    right = bigquery_frame().iloc[[0, 1, 3]]

    result = compare_frames(left, right, config)

    assert result.matches is True
    assert "user_text" not in result.summary["common_columns"]


def test_ignore_case_option():
    left = oracle_frame().iloc[[0]].copy()
    right = bigquery_frame().iloc[[0]].copy()
    right.loc[right.index[0], "layer_text"] = "LAYER-A"

    assert compare_frames(left, right, compare_config()).matches is False
    assert (
        compare_frames(left, right, compare_config(ignore_case=True)).matches is True
    )


def test_write_reports_creates_artifacts(tmp_path):
    result = compare_frames(oracle_frame(), bigquery_frame(), compare_config())

    written = write_reports(result, str(tmp_path), prefix="feedback")

    assert set(written) == {
        "report",
        "summary",
        "mismatches",
        "oracle_only_rows",
        "bigquery_only_rows",
    }
    summary = json.loads((tmp_path / "feedback_summary.json").read_text())
    assert summary["matches"] is False
    assert (tmp_path / "feedback_report.txt").read_text()
    assert "MISMATCH" in format_console_summary(result)


def test_oracle_build_query_uses_schema_and_where():
    config = OracleConfig()
    config.schema = "APP"
    config.table = "FEEDBACK"
    config.where = "layer_text = 'layer-a'"

    assert (
        oracle_source.build_query(config)
        == "SELECT * FROM APP.FEEDBACK WHERE layer_text = 'layer-a'"
    )

    config.query = "SELECT chatmessageid FROM APP.FEEDBACK"
    assert oracle_source.build_query(config) == config.query


def test_bigquery_build_query_quotes_fully_qualified_table():
    config = BigQueryConfig()
    config.dataset = "analytics"
    config.table = "FeedBack"
    config.where = "sessionid = 's1'"

    assert bigquery_source.build_query(config, "my-project") == (
        "SELECT * FROM `my-project.analytics.FeedBack` WHERE sessionid = 's1'"
    )


def test_bigquery_config_requires_service_account(monkeypatch):
    for variable in (
        "BQ_CREDENTIALS_JSON",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "BQ_CREDENTIALS_JSON_CONTENT",
    ):
        monkeypatch.delenv(variable, raising=False)
    config = BigQueryConfig()
    config.dataset = "analytics"
    config.table = "FeedBack"
    config.credentials_json = ""
    config.credentials_json_inline = ""

    with pytest.raises(ValueError, match="service account is required"):
        config.validate()


def test_cli_overrides_env_settings():
    args = build_parser().parse_args(
        [
            "--table",
            "FeedBack",
            "--bq-project",
            "proj",
            "--bq-dataset",
            "ds",
            "--credentials-json",
            "/tmp/sa.json",
            "--join-columns",
            "chatmessageid, sessionid",
            "--ignore-columns",
            "layer_text",
            "--abs-tol",
            "0.001",
            "--ignore-case",
            "--no-ignore-spaces",
        ]
    )
    settings = apply_overrides(Settings(), args)

    assert settings.oracle.table == "FeedBack"
    assert settings.bigquery.table == "FeedBack"
    assert settings.bigquery.project == "proj"
    assert settings.bigquery.credentials_json == "/tmp/sa.json"
    assert settings.compare.join_columns == ["chatmessageid", "sessionid"]
    assert settings.compare.ignore_columns == ["layer_text"]
    assert settings.compare.abs_tol == 0.001
    assert settings.compare.ignore_case is True
    assert settings.compare.ignore_spaces is False


def test_cli_end_to_end_with_stubbed_sources(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        oracle_source, "fetch_dataframe", lambda config: oracle_frame()
    )
    monkeypatch.setattr(
        bigquery_source, "fetch_dataframe", lambda config: bigquery_frame()
    )
    monkeypatch.setattr(Settings, "validate", lambda self: None)

    exit_code = main(
        [
            "--table",
            "FeedBack",
            "--join-columns",
            "chatmessageid",
            "--output-dir",
            str(tmp_path),
            "--report-prefix",
            "feedback",
            "--print-report",
        ]
    )
    captured = capsys.readouterr().out

    assert exit_code == 1
    assert "MISMATCH" in captured
    assert (tmp_path / "feedback_summary.json").exists()
    assert (tmp_path / "feedback_mismatches.csv").exists()


def test_cli_returns_config_error_code(monkeypatch, capsys):
    monkeypatch.setattr(
        Settings,
        "validate",
        lambda self: (_ for _ in ()).throw(ValueError("Missing Oracle configuration")),
    )

    assert main(["--table", "FeedBack", "--no-report-files"]) == 2
