"""Shared test setup for Phase 4 (pytest 'fixtures').

A fixture is a function pytest runs for you and hands to any test that
asks for it by name. Each test gets a fresh tmp_path, so tests never
interfere with each other.
"""
import sqlite3

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from etl_agent.mapping import load_mapping

MAPPING_CSV = (
    "TARGET_COLUMN,SOURCE_COLUMNS,TRANSFORMATION,PARAMETER,DATA_TYPE,NULLABLE,PRIMARY_KEY,MAX_LENGTH\n"
    "ID,CUST_ID,DIRECT,,integer,N,Y,\n"
    "FULL_NAME,FIRST_NAME|LAST_NAME,CONCAT, ,string,N,N,50\n"
    "CITY,CITY,UPPER,,string,Y,N,30\n"
    "GENDER,GENDER,LOOKUP,M=Male;F=Female,string,Y,N,10\n"
    "SALARY,SALARY,DIRECT,,decimal,Y,N,\n"
    "COUNTRY,,CONSTANT,IN,string,N,N,2\n"
    "STATE,STATE,DEFAULT,UNKNOWN,string,N,N,20\n"
    "REGION_CODE,BRANCH_CODE,SUBSTRING,1:2,string,Y,N,2\n"
)

SOURCE_ROWS = [
    (1, "Asha", "Rao", "F", "Pune", 139913.4, "MH", "MH01"),
    (2, "Ravi", "Kumar", "M", "Delhi", 61276.2, "", "DL02"),      # blank state
    (3, "Meera", "Iyer", "F", "Chennai", 50000.0, None, "TN03"),  # NULL state
    (4, "Kiran", "Shah", "X", None, None, "GJ", "GJ04"),          # unmapped gender, NULLs
]


@pytest.fixture
def mapping_file(tmp_path):
    path = tmp_path / "customer_mapping.csv"
    path.write_text(MAPPING_CSV, encoding="utf-8")
    return str(path)


@pytest.fixture
def mapping(mapping_file):
    return load_mapping(mapping_file, "CUSTOMERS_RAW", "DIM_CUSTOMER")


@pytest.fixture
def source_df():
    """The source as the readers would give it: all text, NULL = blank."""
    rows = [["" if v is None else str(v) for v in r] for r in SOURCE_ROWS]
    cols = ["CUST_ID", "FIRST_NAME", "LAST_NAME", "GENDER", "CITY",
            "SALARY", "STATE", "BRANCH_CODE"]
    return pd.DataFrame(rows, columns=cols)


@pytest.fixture
def source_csv(tmp_path, source_df):
    path = tmp_path / "customers_raw.csv"
    source_df.to_csv(path, index=False)
    return str(path)


@pytest.fixture
def db_url(tmp_path):
    """A SQLite database holding a properly TYPED source table."""
    path = tmp_path / "etl.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE CUSTOMERS_RAW (CUST_ID INTEGER, FIRST_NAME TEXT, "
                 "LAST_NAME TEXT, GENDER TEXT, CITY TEXT, SALARY REAL, "
                 "STATE TEXT, BRANCH_CODE TEXT)")
    conn.executemany("INSERT INTO CUSTOMERS_RAW VALUES (?,?,?,?,?,?,?,?)",
                     SOURCE_ROWS)
    conn.commit()
    conn.close()
    return f"sqlite:///{path.as_posix()}"


def run_sql(url, sql):
    """Run SQL and return the result as text, like the readers do."""
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            df = pd.read_sql_query(text(sql), conn)
    finally:
        engine.dispose()
    df = df.astype(object).where(df.notna(), "")
    return df.astype(str)


def execute(url, *statements):
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            for s in statements:
                conn.execute(text(s))
    finally:
        engine.dispose()