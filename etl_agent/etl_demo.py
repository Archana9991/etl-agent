"""A small demo ETL, so the agent can be demonstrated without your real pipeline.
It validates a file against the schema and loads it into a database table.
`disabled_checks` switches validations off on purpose, to simulate an ETL with bugs."""
import re
from pathlib import Path

from sqlalchemy import create_engine

from .readers import read_file
from .schema import TableSchema

NAME_RULE = re.compile(r"^[A-Za-z0-9_\-]+\.(csv|xlsx|xml)$")
_TYPE_RULES = {
    "integer": re.compile(r"^-?\d+$"),
    "decimal": re.compile(r"^-?\d+(\.\d+)?$"),
    "date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
}


class EtlRejected(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def demo_etl(file: str, schema: TableSchema, db_url: str, table: str, disabled_checks=()) -> int:
    path = Path(file)
    on = lambda code: code not in disabled_checks   # noqa: E731

    if on("INVALID_FILE_NAME") and not NAME_RULE.match(path.name):
        raise EtlRejected("INVALID_FILE_NAME", f"'{path.name}' does not match the naming rule")
    if on("EMPTY_FILE") and path.stat().st_size == 0:
        raise EtlRejected("EMPTY_FILE", "File is empty")
    try:
        df = read_file(file)
    except Exception as exc:
        if on("EMPTY_FILE"):
            raise EtlRejected("EMPTY_FILE", f"File could not be read: {exc}") from exc
        raise
    if on("EMPTY_FILE") and len(df.columns) == 0:
        raise EtlRejected("EMPTY_FILE", "File has no columns")

    expected = schema.column_names
    if on("COLUMN_COUNT_MISMATCH") and len(df.columns) != len(expected):
        raise EtlRejected("COLUMN_COUNT_MISMATCH", f"Expected {len(expected)} columns, got {len(df.columns)}")
    if on("HEADER_MISMATCH") and list(df.columns) != expected:
        raise EtlRejected("HEADER_MISMATCH", f"Expected header {expected}, got {list(df.columns)}")
    if on("NO_DATA_ROWS") and df.empty:
        raise EtlRejected("NO_DATA_ROWS", "File has a header but no data rows")

    for col in schema.columns:
        if on("NULL_IN_NOT_NULL_COLUMN") and not col.nullable and (df[col.name] == "").any():
            raise EtlRejected("NULL_IN_NOT_NULL_COLUMN", f"Blank value in NOT NULL column {col.name}")
    for col in schema.columns:
        rule = _TYPE_RULES.get(col.dtype)
        if on("INVALID_DATA_TYPE") and rule:
            bad = df[(df[col.name] != "") & ~df[col.name].str.match(rule)]
            if not bad.empty:
                raise EtlRejected("INVALID_DATA_TYPE", f"Column {col.name} has non-{col.dtype} value '{bad.iloc[0][col.name]}'")
        if on("VALUE_TOO_LONG") and col.max_length and (df[col.name].str.len() > col.max_length).any():
            raise EtlRejected("VALUE_TOO_LONG", f"Column {col.name} exceeds {col.max_length} characters")
    if on("DUPLICATE_PRIMARY_KEY") and schema.primary_key and df.duplicated(subset=schema.primary_key).any():
        raise EtlRejected("DUPLICATE_PRIMARY_KEY", f"Duplicate key {schema.primary_key}")

    engine = create_engine(db_url)
    try:
        df.to_sql(table, engine, if_exists="replace", index=False)
    finally:
        engine.dispose()
    return len(df)