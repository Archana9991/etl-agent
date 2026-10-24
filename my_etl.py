"""A tiny example ETL for the agent to test. Usage: python my_etl.py <file>
Exit code 0 = loaded. Exit code 1 = rejected, with an error code printed."""
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

EXPECTED_HEADER = ["ID", "NAME", "CITY"]
NOT_NULL = ["ID", "NAME", "CITY"]
DB_FILE, TABLE = "target.db", "CUSTOMERS"


def reject(code, message):
    print(f"{code}: {message}")
    sys.exit(1)


path = Path(sys.argv[1])
if not re.match(r"^[A-Za-z0-9_\-]+\.csv$", path.name):
    reject("INVALID_FILE_NAME", path.name)
if path.stat().st_size == 0:
    reject("EMPTY_FILE", "file is empty")

df = pd.read_csv(path, dtype=str, keep_default_na=False)
if len(df.columns) != len(EXPECTED_HEADER):
    reject("COLUMN_COUNT_MISMATCH", f"got {len(df.columns)} columns")
if list(df.columns) != EXPECTED_HEADER:
    reject("HEADER_MISMATCH", f"got {list(df.columns)}")
if df.empty:
    reject("NO_DATA_ROWS", "no rows")
for col in NOT_NULL:
    if (df[col].str.strip() == "").any():
        reject("NULL_IN_NOT_NULL_COLUMN", col)
if not df["ID"].str.fullmatch(r"-?\d+").all():
    reject("INVALID_DATA_TYPE", "ID must be a whole number")
if df["ID"].duplicated().any():
    reject("DUPLICATE_PRIMARY_KEY", "ID repeated")

with sqlite3.connect(DB_FILE) as conn:
    df.to_sql(TABLE, conn, if_exists="replace", index=False)
print(f"loaded {len(df)} rows")