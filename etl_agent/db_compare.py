"""Compare the same table across two environments (DEV vs QA).
URL examples:
  sqlite:///dev.db
  postgresql+psycopg2://user:pwd@host:5432/db
  oracle+oracledb://user:pwd@host:1521/?service_name=ORCL
  mssql+pyodbc://user:pwd@dsn
Keep passwords in environment variables, never in code."""
import re
import pandas as pd
from sqlalchemy import create_engine, inspect
from .comparator import compare_dataframes

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_\.]*$")


def _check_identifier(name):
    if not _IDENT.match(name):
        raise ValueError(f"Unsafe table name: {name!r}")
    return name


def read_table(url, table, where=None):
    engine = create_engine(url)
    try:
        query = f"SELECT * FROM {_check_identifier(table)}"
        if where:
            query += f" WHERE {where}"
        df = pd.read_sql_query(query, engine)
    finally:
        engine.dispose()
    return df.fillna("").astype(str).apply(lambda c: c.str.strip())


def table_schema(url, table):
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