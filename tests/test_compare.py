import json
from decimal import Decimal

import duckdb
import pyarrow as pa
import pytest

from oracle_bq_compare.compare import MISMATCH, ONLY_LEFT, ONLY_RIGHT, compare_tables
from oracle_bq_compare.config import CompareConfig
from oracle_bq_compare.duck import load_arrow_batches, normalize_column_names
from oracle_bq_compare.report import render_summary, write_outputs


@pytest.fixture
def con():
    c = duckdb.connect()
    yield c
    c.close()


def _load(con, table, arrow):
    load_arrow_batches(con, table, [arrow])
    normalize_column_names(con, table)


def test_detects_row_and_column_differences(con):
    # Oracle side: upper-case names, NUMBER -> DECIMAL, DATE -> TIMESTAMP
    _load(
        con,
        "oracle",
        pa.table(
            {
                "ID": pa.array([1, 2, 3, 4], pa.decimal128(38, 0)),
                "NAME": ["alice ", "bob", "carol", "dave"],
                "AMOUNT": pa.array(
                    [Decimal(v) for v in ("10.00", "20.50", "30.00", "40.00")], pa.decimal128(18, 2)
                ),
                "CREATED": pa.array([1_700_000_000] * 4, pa.timestamp("s")),
            }
        ),
    )
    _load(
        con,
        "bigquery",
        pa.table(
            {
                "id": pa.array([1, 2, 3, 5], pa.int64()),
                "name": ["alice", "bob", "CAROL", "eve"],
                "amount": pa.array([10.0, 20.5, 30.01, 50.0], pa.float64()),
                "created": pa.array([1_700_000_000] * 4, pa.timestamp("s", tz="UTC")),
                "extra": [1, 2, 3, 4],
            }
        ),
    )
    result = compare_tables(con, CompareConfig(join_columns=["ID"], sample_rows=100))

    assert not result.matches
    assert result.join_columns == ["id"]
    assert result.left_rows == 4 and result.right_rows == 4
    assert result.left_only_rows == 1 and result.right_only_rows == 1
    assert result.common_rows == 3
    assert result.right_only_columns == ["extra"]
    stats = {s.column: s.mismatches for s in result.column_stats}
    assert stats["name"] == 1  # 'carol' vs 'CAROL' (trailing space ignored)
    assert stats["amount"] == 1  # 30.00 vs 30.01
    assert stats["created"] == 0
    assert result.mismatch_rows == 1  # both differences are on id 3
    assert result.datacompy_scope == "full"
    assert "DataComPy Comparison" in result.datacompy_report
    assert result.datacompy_summary["matches"] is False

    text = render_summary(result)
    assert "DIFFERENCES FOUND" in text
    assert "Rows only in oracle: 1" in text


def test_tolerance_and_case_options(con):
    _load(con, "oracle", pa.table({"id": [1, 2], "v": [1.00, 2.00], "s": ["A", "b"]}))
    _load(con, "bigquery", pa.table({"id": [1, 2], "v": [1.001, 2.00], "s": ["a", "B"]}))
    strict = compare_tables(con, CompareConfig(join_columns=["id"]))
    assert strict.mismatch_rows == 2
    lenient = compare_tables(
        con, CompareConfig(join_columns=["id"], abs_tol=0.01, ignore_case=True)
    )
    assert lenient.matches


def test_uses_detected_primary_key_and_ignore_columns(con):
    _load(con, "oracle", pa.table({"k": [1, 2], "v": [1, 2], "load_ts": [1, 2]}))
    _load(con, "bigquery", pa.table({"k": [1, 2], "v": [1, 2], "load_ts": [9, 9]}))
    with pytest.raises(ValueError, match="No join columns"):
        compare_tables(con, CompareConfig())
    result = compare_tables(con, CompareConfig(ignore_columns=["LOAD_TS"]), detected_pk=["k"])
    assert result.join_columns == ["k"]
    assert result.matches


def test_large_tables_use_duckdb_and_sampled_datacompy(con):
    n = 1_000_000
    con.execute(
        "CREATE TABLE oracle AS SELECT i AS id, 'name_' || i AS name, i * 1.5 AS amount "
        "FROM range(1, ?) t(i)",
        [n + 1],
    )
    con.execute(
        "CREATE TABLE bigquery AS SELECT i AS id, 'name_' || i AS name, "
        "CASE WHEN i % 100000 = 0 THEN i * 2.0 ELSE i * 1.5 END AS amount "
        "FROM range(1, ?) t(i) WHERE i <> 7",
        [n + 2],
    )
    config = CompareConfig(join_columns=["id"], full_datacompy_max_rows=10_000, sample_rows=500)
    result = compare_tables(con, config)

    assert result.left_rows == n and result.right_rows == n
    assert result.left_only_rows == 1  # id 7
    assert result.right_only_rows == 1  # id n+1
    assert result.mismatch_rows == 10
    assert {s.column: s.mismatches for s in result.column_stats}["amount"] == 10
    assert result.datacompy_scope.startswith("sample")
    assert result.datacompy_summary["rows_compared"] == 10
    assert result.datacompy_summary["oracle_only_rows"] == 1
    assert result.datacompy_summary["bigquery_only_rows"] == 1


def test_write_outputs(con, tmp_path):
    _load(con, "oracle", pa.table({"id": [1, 2], "v": [1, 2]}))
    _load(con, "bigquery", pa.table({"id": [1, 3], "v": [9, 3]}))
    result = compare_tables(con, CompareConfig(join_columns=["id"]))
    paths = write_outputs(con, result, str(tmp_path))
    with open(paths["summary_json"]) as fh:
        summary = json.load(fh)
    assert summary["matches"] is False
    assert summary["rows_with_differences"] == 1
    assert con.execute(f"SELECT COUNT(*) FROM '{paths['mismatch_rows']}'").fetchone()[0] == 1
    assert con.execute(f"SELECT COUNT(*) FROM '{paths['oracle_only_rows']}'").fetchone()[0] == 1
    assert con.execute(f"SELECT COUNT(*) FROM '{paths['bigquery_only_rows']}'").fetchone()[0] == 1
    for t in (MISMATCH, ONLY_LEFT, ONLY_RIGHT):
        assert con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 1
