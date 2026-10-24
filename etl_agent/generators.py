"""Create deliberately BAD input files from a good one, one file per failure type.
Every file is listed in manifest.csv with the failure the ETL is expected to raise."""
from pathlib import Path

import pandas as pd

from .readers import read_file
from .schema import TableSchema


def write_file(df: pd.DataFrame, path: Path) -> None:
    ext = path.suffix.lower()
    if ext == ".csv":
        df.to_csv(path, index=False)
    elif ext in (".xlsx", ".xls"):
        df.to_excel(path, index=False)
    elif ext == ".xml":
        df.to_xml(path, index=False, root_name="data", row_name="record")
    else:
        raise ValueError(f"Cannot write '{ext}' files")


def write_blank(path: Path) -> None:
    """A truly empty file: no header and no rows."""
    ext = path.suffix.lower()
    if ext == ".csv":
        path.write_bytes(b"")
    elif ext in (".xlsx", ".xls"):
        from openpyxl import Workbook
        Workbook().save(path)
    elif ext == ".xml":
        path.write_text("<data/>")


def generate_all(source: str, schema: TableSchema, out_dir: str) -> pd.DataFrame:
    src = Path(source)
    ext, stem = src.suffix.lower(), src.stem
    df = read_file(source)
    if df.empty:
        raise ValueError("Source file needs at least one data row to build test files from")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest = []

    def emit(scenario, data, expected, description, filename=None):
        name = filename or f"{stem}__{scenario}{ext}"
        if data is None:
            write_blank(out / name)
        else:
            write_file(data, out / name)
        manifest.append({"scenario": scenario, "file": name,
                         "expected_failure": expected, "description": description})

    emit("valid_baseline", df, "LOAD_SUCCESS", "Untouched copy of the input; must load cleanly")

    # 1. Invalid header: rename the first column
    bad = df.copy()
    cols = list(bad.columns)
    cols[0] = cols[0] + "_X"
    bad.columns = cols
    emit("invalid_header", bad, "HEADER_MISMATCH", f"Column '{df.columns[0]}' renamed to '{cols[0]}'")

    # 2. Blank file, and header-only file (not supported for xml)
    emit("blank_file", None, "EMPTY_FILE", "File with no header and no data")
    if ext != ".xml":
        emit("header_only", df.iloc[0:0], "NO_DATA_ROWS", "Header present but zero data rows")

    # 3. Invalid file name
    emit("invalid_file_name", df, "INVALID_FILE_NAME",
         "File name has spaces and special characters", filename=f"bad name@#!{ext}")

    # 4. Duplicate primary key
    if schema.primary_key:
        dup = pd.concat([df, df.iloc[[0]]], ignore_index=True)
        emit("duplicate_pk", dup, "DUPLICATE_PRIMARY_KEY",
             f"First row repeated; key {schema.primary_key} now appears twice")

    # 5. Null in each NOT NULL column (one file per column)
    for col in schema.columns:
        if not col.nullable:
            nul = df.copy()
            nul.loc[0, col.name] = ""
            emit(f"null_{col.name}", nul, "NULL_IN_NOT_NULL_COLUMN",
                 f"Row 1 has blank value in NOT NULL column '{col.name}'")

    # 6. Missing / extra column
    droppable = [c for c in df.columns if c not in schema.primary_key]
    if droppable:
        emit("missing_column", df.drop(columns=[droppable[-1]]), "COLUMN_COUNT_MISMATCH",
             f"Column '{droppable[-1]}' removed")
    emit("extra_column", df.assign(UNEXPECTED_COL="x"), "COLUMN_COUNT_MISMATCH",
         "Unexpected column UNEXPECTED_COL added")

    # 7. Wrong data type and over-length values
    for col in schema.columns:
        if col.dtype in ("integer", "decimal", "date"):
            wrong = df.copy()
            wrong.loc[0, col.name] = "ABC"
            emit(f"bad_type_{col.name}", wrong, "INVALID_DATA_TYPE",
                 f"'ABC' placed in {col.dtype} column '{col.name}'")
        if col.max_length:
            long = df.copy()
            long.loc[0, col.name] = "X" * (col.max_length + 1)
            emit(f"too_long_{col.name}", long, "VALUE_TOO_LONG",
                 f"Value longer than max_length {col.max_length} in '{col.name}'")

    m = pd.DataFrame(manifest)
    m.to_csv(out / "manifest.csv", index=False)
    return m