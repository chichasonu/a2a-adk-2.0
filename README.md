# oracle-bq-compare

Compare the records of an Oracle table against a BigQuery table that has the same
column names. Built for tables with millions of rows on either side:

* **DuckDB** does the heavy lifting. Both tables are streamed in Arrow batches into a
  DuckDB database (on disk if `DUCKDB_PATH` is set, so extracts spill instead of
  exhausting RAM). Type alignment, rows-only-on-one-side (anti joins) and the keyed
  column-by-column diff all run as DuckDB SQL.
* **datacompy** produces the familiar column statistics / sample report. It runs on
  the full data when both sides are small enough (`COMPARE_FULL_DATACOMPY_MAX_ROWS`,
  default 500k), otherwise on a bounded sample of the differing keys
  (`COMPARE_SAMPLE_ROWS`, default 10k) so pandas never has to hold millions of rows.
* **BigQuery** is read with a service account JSON key (file path or inline content)
  via the Storage Read API; **Oracle** via python-oracledb (thin mode by default).

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Configure

Copy `.env.example` to `.env` and fill in the Oracle connection, the BigQuery
project/dataset/table and `BQ_CREDENTIALS_JSON` (path to the service account key) or
`BQ_CREDENTIALS_JSON_CONTENT` (the key's JSON). Every setting can also be passed on
the command line (`oracle-bq-compare --help`).

Join (key) columns come from `COMPARE_JOIN_COLUMNS`; if omitted, the Oracle primary
key of `ORACLE_TABLE` is used.

## Run

```bash
oracle-bq-compare --oracle-table CUSTOMERS --bq-dataset analytics --bq-table customers \
  --join-columns CUSTOMER_ID --duckdb-path compare.duckdb --output-dir reports
```

Exit code `0` means the tables match, `1` means differences were found, `2` a
configuration error. `reports/` contains:

| file | content |
| --- | --- |
| `report.txt` | human readable summary + datacompy report |
| `summary.json` | machine readable counts (rows per side, only-left/right, per-column mismatches) |
| `mismatch_rows.parquet` | every key present on both sides with at least one differing column, with `<col>_oracle`, `<col>_bigquery` and `<col>__diff` |
| `oracle_only_rows.parquet` / `bigquery_only_rows.parquet` | full rows that exist on one side only |

## API + React UI

```bash
oracle-bq-compare-api --port 8000          # FastAPI (only ORACLE_USER/PASSWORD/DSN + BQ_PROJECT/credentials needed)
cd ui && npm install && npm run dev        # React dev server on :5173, proxies /api to :8000
```

For a single-process deployment run `npm run build` and set `UI_DIST_DIR=ui/dist`;
the API then serves the UI at `/`.

| endpoint | purpose |
| --- | --- |
| `GET /api/oracle/schemas` → `/{schema}/tables` → `/{table}/columns` | browse Oracle |
| `GET /api/bigquery/datasets` → `/{dataset}/tables` → `/{table}/columns` | browse BigQuery |
| `POST /api/analyse` | start a comparison job (`202`, returns job id) |
| `GET /api/jobs/{id}` | poll status/stage; `result` holds counts, per-column mismatches, mismatched / one-sided record previews and the datacompy report |

In the UI pick schema → table on each side, tick the Oracle columns to compare
(BigQuery columns are auto-matched by name, override per row), mark join key(s),
optionally set a date-filter column + range per side, then click **Analyse**.

UI libraries are pinned one major behind current: React 18, TanStack Query 4,
Vite 7, TypeScript 6, `@vitejs/plugin-react` 5.

## How values are compared

Column names are lower-cased on both sides. For each common column:

* numeric vs numeric (Oracle `NUMBER` → `DECIMAL`, BigQuery `INT64`/`FLOAT64`) are compared
  natively, or as `DOUBLE` when the types differ; `COMPARE_ABS_TOL` / `COMPARE_REL_TOL` apply.
* temporal vs temporal are compared as UTC `TIMESTAMP`.
* everything else is compared as text, trimmed (`COMPARE_IGNORE_SPACES`, default on)
  and optionally lower-cased (`COMPARE_IGNORE_CASE`).
* `NULL` equals `NULL`; columns in `COMPARE_IGNORE_COLUMNS` are skipped; columns that
  exist on only one side are reported, not compared.

## Tests

```bash
pytest          # includes a 1M-row DuckDB scale test, no Oracle/BigQuery needed
ruff check . && ruff format --check .
```
