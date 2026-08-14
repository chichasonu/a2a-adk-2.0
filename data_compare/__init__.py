"""Compare Oracle and BigQuery tables (e.g. FeedBack) with datacompy."""

__version__ = "0.1.0"

from .api import build_app
from .compare import ComparisonResult, compare_frames, compare_tables
from .config import BigQueryConfig, CompareConfig, OracleConfig, Settings
from .normalize import prepare_frames
from .report import format_console_summary, write_reports

__all__ = [
    "BigQueryConfig",
    "CompareConfig",
    "ComparisonResult",
    "OracleConfig",
    "Settings",
    "build_app",
    "compare_frames",
    "compare_tables",
    "format_console_summary",
    "prepare_frames",
    "write_reports",
]
