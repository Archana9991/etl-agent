"""Compare two database tables (two environments, or two tables).

SQLAlchemy gives one API for many databases. Only the URL changes:
  sqlite:///dev.db
  mssql+pyodbc://@localhost/mydb?driver=ODBC+Driver+17+for+SQL+Server
      &trusted_connection=yes                  (SQL Server, Windows login)
  postgresql+psycopg2://user:pwd@host:5432/db
  oracle+oracledb://user:pwd@host:1521/?service_name=ORCL

Keep passwords in environment variables, never in code.
"""
import re

import pandas as pd
from sqlalchemy import create_engine, inspect, text

from .comparator import compare_dataframes
from .readers import read_file

# Table names cannot be passed as SQL parameters, so only allow safe
# characters. A dot is allowed for schema.table (e.g. dbo.customers).
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_\.]*$")


def _check_identifier(name):
    if not _IDENT.match(name):
        raise ValueError(f"Unsafe table name: {name!r}")
    return name


def read_table(url, table, where=None):
    """Load a table as text, ready for comparison.

    NULL becomes an empty string. Whole-number columns that contain
    NULLs stay whole numbers (5, not 5.0).
    """
    engine = create_engine(url)
    try:
        query = f"SELECT * FROM {_check_identifier(table)}"
        if where:
            query += f" WHERE {where}"   # trusted input from the tester
        df = pd.read_sql_query(query, engine, coerce_float=False,
                               dtype_backend="numpy_nullable")
    finally:
        engine.dispose()
    df = df.astype(object).where(df.notna(), "")
    return df.astype(str).apply(lambda c: c.str.strip())


def check_connection(url, schema=None):
    """Connect, run a trivial query, and list table names (no data)."""
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        names = inspect(engine).get_table_names(schema=schema)
        dialect = engine.dialect.name
    finally:
        engine.dispose()
    return dialect, sorted(names)


def table_schema(url, table):
    """Return {column_name: type_string}."""
    engine = create_engine(url)
    try:
        cols = inspect(engine).get_columns(table)
    finally:
        engine.dispose()
    return {c["name"].upper(): str(c["type"]).upper() for c in cols}


def compare_tables(src_url, tgt_url, table, keys, tgt_table=None, **kwargs):
    src = read_table(src_url, table)
    tgt = read_table(tgt_url, tgt_table or table)
    return compare_dataframes(src, tgt, keys, **kwargs)


def compare_file_to_table(file, url, table, keys, **kwargs):
    """Source = a file (csv/xlsx/xml), target = a database table."""
    return compare_dataframes(read_file(file), read_table(url, table),
                              keys, **kwargs)