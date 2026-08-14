"""Normalization helpers making Oracle and BigQuery frames comparable."""

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Lowercase and trim column names, collapsing internal whitespace."""
    renamed = {col: "_".join(str(col).strip().lower().split()) for col in df.columns}
    return df.rename(columns=renamed)


def normalize_values(
    df: pd.DataFrame,
    strip_strings: bool = True,
    empty_as_null: bool = True,
) -> pd.DataFrame:
    """Normalize value representations that differ between the two engines.

    Object columns are cast to strings and stripped, timezone-aware datetimes are
    converted to UTC and made naive, and BigQuery ``pd.NA`` is unified with
    Oracle ``None`` so equal rows are not reported as mismatches.
    """
    out = df.copy()
    for column in out.columns:
        series = out[column]
        if isinstance(series.dtype, pd.DatetimeTZDtype):
            out[column] = series.dt.tz_convert("UTC").dt.tz_localize(None)
            continue
        if pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series):
            values = series.astype("object").where(series.notna(), None)
            if strip_strings:
                values = values.map(
                    lambda value: value.strip() if isinstance(value, str) else value
                )
            if empty_as_null:
                values = values.map(
                    lambda value: None if isinstance(value, str) and not value else value
                )
            out[column] = values
    return out


def align_columns(
    left: pd.DataFrame,
    right: pd.DataFrame,
    join_columns: list[str],
    ignore_columns: list[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str], list[str]]:
    """Restrict both frames to their shared columns.

    Returns the aligned frames plus the columns that only exist on one side, so
    the report can call out schema drift instead of silently dropping columns.
    """
    ignored = {column.lower() for column in (ignore_columns or [])}
    left_columns = [c for c in left.columns if c not in ignored]
    right_columns = [c for c in right.columns if c not in ignored]

    shared = [c for c in left_columns if c in set(right_columns)]
    missing_keys = [key for key in join_columns if key not in shared]
    if missing_keys:
        raise ValueError(
            "Join columns missing from one or both sources: "
            + ", ".join(missing_keys)
            + f". Available shared columns: {', '.join(shared) or 'none'}"
        )

    only_left = [c for c in left_columns if c not in set(right_columns)]
    only_right = [c for c in right_columns if c not in set(left_columns)]
    if only_left or only_right:
        logger.warning(
            "Schema drift detected. Only in left: %s. Only in right: %s.",
            only_left or "none",
            only_right or "none",
        )
    return left[shared], right[shared], only_left, only_right


def coerce_shared_dtypes(
    left: pd.DataFrame, right: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Cast columns whose dtypes disagree to a common comparable dtype.

    Numeric/numeric pairs are widened to ``float64``; anything else that
    disagrees falls back to nullable strings, which is how datacompy compares
    non-numeric values anyway.
    """
    left_out, right_out = left.copy(), right.copy()
    for column in left_out.columns:
        left_dtype, right_dtype = left_out[column].dtype, right_out[column].dtype
        if left_dtype == right_dtype:
            continue
        left_numeric = pd.api.types.is_numeric_dtype(left_dtype)
        right_numeric = pd.api.types.is_numeric_dtype(right_dtype)
        if left_numeric and right_numeric:
            left_out[column] = left_out[column].astype("float64")
            right_out[column] = right_out[column].astype("float64")
            continue
        if isinstance(left_dtype, pd.DatetimeTZDtype) or isinstance(
            right_dtype, pd.DatetimeTZDtype
        ):
            continue
        if pd.api.types.is_datetime64_any_dtype(
            left_dtype
        ) and pd.api.types.is_datetime64_any_dtype(right_dtype):
            continue
        logger.info(
            "Column %s has differing dtypes (%s vs %s); comparing as strings.",
            column,
            left_dtype,
            right_dtype,
        )
        left_out[column] = left_out[column].astype("object").where(
            left_out[column].notna(), None
        ).map(lambda value: None if value is None else str(value))
        right_out[column] = right_out[column].astype("object").where(
            right_out[column].notna(), None
        ).map(lambda value: None if value is None else str(value))
    return left_out, right_out


def prepare_frames(
    left: pd.DataFrame,
    right: pd.DataFrame,
    join_columns: list[str],
    ignore_columns: list[str] | None = None,
    strip_strings: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str], list[str]]:
    """Run the full normalization pipeline on both source frames."""
    left = normalize_values(normalize_columns(left), strip_strings=strip_strings)
    right = normalize_values(normalize_columns(right), strip_strings=strip_strings)
    keys = [key.strip().lower() for key in join_columns]
    left, right, only_left, only_right = align_columns(
        left, right, keys, ignore_columns
    )
    left, right = coerce_shared_dtypes(left, right)
    return left, right, only_left, only_right
