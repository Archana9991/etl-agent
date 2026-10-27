# ETL Agent

![tests](https://github.com/Archana9991/etl-agent/actions/workflows/tests.yml/badge.svg)

A Python toolkit for automating ETL testing. It compares data across files and databases, generates invalid test files from a schema, and (in progress) runs ETL test cycles on its own.

## Why this project
ETL testers spend a lot of time on repetitive checks: comparing source to target, and hand-making bad files to see whether the pipeline rejects them. This project automates both.

## Roadmap

| Phase | What it does | Status |
|-------|--------------|--------|
| 1 | Compare CSV / Excel / XML files, and tables across two DB environments | Done |
| 2 | Generate negative test data from a schema (invalid header, blank file, bad file name, duplicate PK, nulls in NOT NULL columns, and more) | Done |
| 3 | Autonomous agent that runs the generated files through an ETL, checks the outcome and reports | In progress |

## Setup
```bash
git clone https://github.com/Archana9991/etl-agent.git
cd etl-agent
pip install -r requirements.txt
python sample_data/make_samples.py
pytest -q
```
Python 3.10 or newer is required.

## Phase 1: Compare data

**Files.** The two files can be in different formats:
```bash
python -m etl_agent.cli files sample_data/source.csv sample_data/target_diff.xlsx --keys ID
```

**Database tables across two environments** (any SQLAlchemy-supported database):
```bash
python -m etl_agent.cli tables sqlite:///dev.db sqlite:///qa.db CUSTOMERS --keys ID
```

Example output:
```
Status            : FAIL
Rows (src / tgt)  : 3 / 4
Missing columns   : none
Extra columns     : none
Duplicate keys    : src=0 tgt=0
Only in source    : 0 rows
Only in target    : 1 rows
Value mismatches  : 1 cells
```

What it checks: missing and extra columns, row counts, duplicate primary keys, rows present on only one side, and cell-level value mismatches. It writes `reports/comparison_report.xlsx` with one sheet per defect type. The exit code is `0` on pass and `1` on fail, so it can fail a CI pipeline.

## Phase 2: Generate negative test data

Give it one valid file. It produces one bad file per failure type, plus a `manifest.csv` recording the failure the ETL is expected to raise.

```bash
# 1. Infer a schema (then edit schema.json to add real NOT NULL columns and max lengths)
python -m etl_agent.cli infer-schema sample_data/source.csv --pk ID

# 2. Generate the test files
python -m etl_agent.cli generate sample_data/source.csv --schema schema.json --out test_data
```

| Scenario | What is broken | Expected failure |
|----------|----------------|------------------|
| invalid_header | First column renamed | HEADER_MISMATCH |
| blank_file | No header, no data | EMPTY_FILE |
| header_only | Header with zero rows | NO_DATA_ROWS |
| invalid_file_name | Spaces and special characters in the name | INVALID_FILE_NAME |
| duplicate_pk | First row repeated | DUPLICATE_PRIMARY_KEY |
| null_`<column>` | Blank in a NOT NULL column (one file per column) | NULL_IN_NOT_NULL_COLUMN |
| missing_column / extra_column | Column removed or added | COLUMN_COUNT_MISMATCH |
| bad_type_`<column>` | Text in a number or date column | INVALID_DATA_TYPE |
| too_long_`<column>` | Value over `max_length` | VALUE_TOO_LONG |

Supports CSV, Excel and XML. One failure is injected per file, so a rejection can be traced to exactly one cause.

## Project structure
```
src/etl_agent/
  readers.py       CSV / Excel / XML -> DataFrame
  comparator.py    schema, count, key and value comparison
  db_compare.py    DB table loading and cross-environment comparison
  report.py        text and Excel reports
  schema.py        schema inference, JSON save/load
  generators.py    negative test file generation
  cli.py           command line interface
  mapping.py         mapping file: load, validate, transformation whitelist
  sqlgen.py          SQL generator: select, DDL, checks, dialects
  target_builder.py  expected target in pandas, create and load the table
  etl_checks.py      data-quality and transformation checks, report
  automation.py      Phase 4 workflows used by the CLI and the UI
app.py               Streamlit web UI (project root)
tests/             pytest suite
sample_data/       small example files
docs/              roadmap and learning notes
```
## UI
<img width="1901" height="951" alt="image" src="https://github.com/user-attachments/assets/84d2b049-470a-4fbd-838f-e683dd744a7c" />


## Design notes
- All formats are normalised to the same DataFrame shape, so the comparison logic doesn't depend on the source format.
- Data is read as text so values like `007` aren't silently changed to `7`.
- Table names are validated before they go into SQL, to prevent SQL injection.
- Database credentials should be passed through environment variables, not stored in code.

## Tech stack
Python, pandas, SQLAlchemy, openpyxl, lxml, pytest


## Phase 4: ETL automation (mapping-driven)

Everything starts from a **mapping file** (CSV, Excel or JSON): one row per target column saying which source column(s) feed it and which rule applies. See `sample_data/customer_mapping.csv`.

| Rule | What it does |
|------|--------------|
| DIRECT / TRIM / UPPER / LOWER | copy, trim, change case |
| CONSTANT | fill with a fixed value (`PARAMETER`) |
| DEFAULT | use `PARAMETER` when the source is blank or NULL |
| CONCAT | join source columns (`PARAMETER` = separator) |
| SUBSTRING | `PARAMETER` = `start:length` (start from 1) |
| LOOKUP | `PARAMETER` = `M=Male;F=Female` |

```bash
# 1. SQL query generator
python -m etl_agent.cli mapping-sql sample_data/customer_mapping.csv \
    --source-table CUSTOMERS_RAW --target-table DIM_CUSTOMER --dialect oracle --out sql_out

# 2. Build the target (expected data file, and optionally a real table)
python -m etl_agent.cli build-target sample_data/customer_mapping.csv \
    --source sample_data/customers_raw.csv --source-table CUSTOMERS_RAW \
    --target-table DIM_CUSTOMER --db-url sqlite:///target.db

# 3. ETL checks
python -m etl_agent.cli etl-check sample_data/customer_mapping.csv \
    --source sample_data/customers_raw.csv --source-table CUSTOMERS_RAW \
    --target-table DIM_CUSTOMER --target-url sqlite:///target.db --run-sql

# 4. The same three workflows in a web UI
pip install -r requirements-ui.txt
streamlit run app.py
```

Dialects: `ansi`, `sqlite`, `postgres`, `oracle`, `mssql`. Exit codes: `0` all ok, `1` a check failed, `2` setup problem, `3` sensitive data suspected.

## Author
Archana: [GitHub](https://github.com/Archana9991)
