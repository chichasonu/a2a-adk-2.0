"""Build the Oracle and BigQuery extraction queries from a UI/API column selection.

Columns are matched positionally: ``bigquery.columns[i]`` is compared against
``oracle.columns[i]`` and is aliased to the (lower-cased) Oracle name so the DuckDB
comparison sees identical column names on both sides.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]*$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def check_identifier(name: str, what: str = "identifier") -> str:
    if not IDENTIFIER.match(name or ""):
        raise ValueError(f"Invalid {what}: {name!r}")
    return name


def check_date(value: str, what: str) -> str:
    if not DATE.match(value or ""):
        raise ValueError(f"{what} must be YYYY-MM-DD, got {value!r}")
    return value


@dataclass
class SideSelection:
    schema: str  # Oracle owner / BigQuery dataset
    table: str
    columns: list[str]
    date_column: str = ""
    date_from: str = ""
    date_to: str = ""

    def validate(self, side: str) -> None:
        check_identifier(self.schema, f"{side} schema")
        check_identifier(self.table, f"{side} table")
        if not self.columns:
            raise ValueError(f"Select at least one {side} column")
        for col in self.columns:
            check_identifier(col, f"{side} column")
        if len({c.lower() for c in self.columns}) != len(self.columns):
            raise ValueError(f"Duplicate {side} columns selected")
        if self.date_column:
            check_identifier(self.date_column, f"{side} date column")
            if not (self.date_from or self.date_to):
                raise ValueError(f"{side}: date column selected without a from/to date")
        if self.date_from:
            check_date(self.date_from, f"{side} date_from")
        if self.date_to:
            check_date(self.date_to, f"{side} date_to")


@dataclass
class ComparisonRequest:
    oracle: SideSelection
    bigquery: SideSelection
    join_columns: list[str] = field(default_factory=list)  # Oracle column names
    abs_tol: float = 0.0
    rel_tol: float = 0.0
    ignore_case: bool = False
    ignore_spaces: bool = True
    sample_rows: int = 10_000
    full_datacompy_max_rows: int = 500_000

    def validate(self) -> None:
        self.oracle.validate("Oracle")
        self.bigquery.validate("BigQuery")
        if len(self.oracle.columns) != len(self.bigquery.columns):
            raise ValueError(
                "Select the same number of Oracle and BigQuery columns "
                f"({len(self.oracle.columns)} vs {len(self.bigquery.columns)})"
            )
        if not self.join_columns:
            raise ValueError("Select at least one join column")
        lower = {c.lower() for c in self.oracle.columns}
        missing = [c for c in self.join_columns if c.lower() not in lower]
        if missing:
            raise ValueError(f"Join columns must be among the selected Oracle columns: {missing}")

    @property
    def target_names(self) -> list[str]:
        return [c.lower() for c in self.oracle.columns]


def build_oracle_query(sel: SideSelection) -> tuple[str, dict[str, Any]]:
    cols = ", ".join(f'"{c}" AS "{c.lower()}"' for c in sel.columns)
    sql = f'SELECT {cols} FROM "{sel.schema}"."{sel.table}"'
    params: dict[str, Any] = {}
    clauses = []
    if sel.date_column and sel.date_from:
        clauses.append(f"TRUNC(\"{sel.date_column}\") >= TO_DATE(:date_from, 'YYYY-MM-DD')")
        params["date_from"] = sel.date_from
    if sel.date_column and sel.date_to:
        clauses.append(f"TRUNC(\"{sel.date_column}\") <= TO_DATE(:date_to, 'YYYY-MM-DD')")
        params["date_to"] = sel.date_to
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    return sql, params


def build_bigquery_query(
    sel: SideSelection, project: str, target_names: list[str]
) -> tuple[str, dict[str, Any]]:
    cols = ", ".join(f"`{c}` AS `{alias}`" for c, alias in zip(sel.columns, target_names))
    sql = f"SELECT {cols} FROM `{project}.{sel.schema}.{sel.table}`"
    params: dict[str, Any] = {}
    clauses = []
    if sel.date_column and sel.date_from:
        clauses.append(f"CAST(`{sel.date_column}` AS DATE) >= DATE(@date_from)")
        params["date_from"] = sel.date_from
    if sel.date_column and sel.date_to:
        clauses.append(f"CAST(`{sel.date_column}` AS DATE) <= DATE(@date_to)")
        params["date_to"] = sel.date_to
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    return sql, params
