"""Phase 4 workflows: the one place where the building blocks are joined.

WHY THIS FILE EXISTS
--------------------
The command line (cli.py) and the web page (app.py) must do exactly the
same thing when you press "build" or "check". If each wrote its own copy of
the logic, they would slowly drift apart and you would fix every bug twice.

So both are THIN layers: they collect input, call a function from this file,
and show the answer. All real work lives here, and all the heavy lifting
lives in the modules below (each one knows how to do ONE thing):

    mapping.py         load + validate the mapping file
    sqlgen.py          generate SQL (select, CREATE TABLE, checks)
    target_builder.py  build the expected target with pandas, load a table
    etl_checks.py      pandas data-quality / transformation checks
    sensitive.py       spot columns that look like sensitive identifiers

This file adds no new rules. It only decides the ORDER of the steps and the
shape of what comes back (a tuple, a dict, a DataFrame).

Five functions:
    load_dataset          file or database table  -> DataFrame
    stop_if_sensitive     safety guard            -> True means "stop now"
    generate_sql_package  mapping                 -> 3 .sql files
    build_target          mapping + source        -> expected target (+ table)
    run_checks            mapping + source + target -> results table + report
"""
from pathlib import Path

import pandas as pd

from .db_compare import read_table
from .etl_checks import COLUMNS, run_etl_checks, write_checks_report
from .readers import read_file
from .sensitive import find_sensitive
from .sqlgen import (create_table_sql, generate_sql_checks,
                     generate_sql_script, get_dialect, run_sql_checks,
                     select_expected_sql)
from .target_builder import build_expected_target, load_target


# ---- 1. reading data ---------------------------------------------------------
def load_dataset(file=None, url=None, table=None) -> pd.DataFrame:
    """Read a dataset from a FILE or from a database TABLE.

    A file wins when both are given. Everything comes back as text (the
    readers do that on purpose), so "007" stays "007" and never becomes 7.
    """
    if file:
        return read_file(file)
    if url:
        if not table:
            raise ValueError("a database URL was given but no table name")
        return read_table(url, table)
    raise ValueError("give a file or a database URL")


# ---- 2. the safety guard -----------------------------------------------------
def stop_if_sensitive(df: pd.DataFrame, allow: bool = False, log=print) -> bool:
    """Return True if the run must STOP because the data looks sensitive.

    Building an expected target COPIES real rows into new files, so this
    runs first (the same rule as Phase 3). Only column NAMES and the kind of
    pattern are reported, never the values themselves.

    `log` is a function that receives each message line. The CLI passes
    print; the web page passes list.append so it can show the lines in boxes.
    The first line is always the headline.
    """
    if allow:
        return False
    hits = find_sensitive(df)
    if not hits:
        return False
    log("STOPPED: the source looks like it holds sensitive data.")
    for column, reason in hits:
        log(f"  column {column!r}: {reason}")
    log("Use a masked or synthetic extract instead.")
    log("If these columns are safe, add --allow-sensitive.")
    return True


# ---- 3. SQL package ----------------------------------------------------------
def generate_sql_package(mapping, dialect: str, out_dir) -> dict:
    """Write three .sql files and return what was written.

    Returns {"files": [Path, ...], "checks": [SqlCheck, ...]}.
    An unknown dialect raises ValueError BEFORE any file is written.
    """
    d = get_dialect(dialect)                       # fail early, write nothing
    checks = generate_sql_checks(mapping, d)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    contents = {
        "expected_select.sql": select_expected_sql(mapping, d) + ";\n",
        "create_target.sql": create_table_sql(mapping, d) + ";\n",
        "checks.sql": generate_sql_script(mapping, d, checks),
    }
    files = []
    for name, text in contents.items():
        path = out / name
        path.write_text(text, encoding="utf-8")
        files.append(path)
    return {"files": files, "checks": checks}


# ---- 4. build the expected target -------------------------------------------
def build_target(mapping, source_df: pd.DataFrame, out_dir,
                 db_url=None, dialect: str = "ansi"):
    """Apply every mapping rule to the source.

    Returns (expected_df, csv_path, rows_loaded).
    rows_loaded is 0 unless db_url is given, in which case the target table
    is dropped, recreated and filled (the database enforces NOT NULL and
    primary-key rules, so bad rows are refused there).

    The database step runs BEFORE the CSV is written: if it fails, you are
    not left with a half-finished output folder.
    """
    get_dialect(dialect)                           # fail early on a typo
    expected = build_expected_target(source_df, mapping)
    loaded = load_target(db_url, mapping, expected, dialect) if db_url else 0
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "expected_target.csv"
    expected.to_csv(path, index=False)
    return expected, path, loaded


# ---- 5. run the checks -------------------------------------------------------
def run_checks(mapping, source_df: pd.DataFrame, target_df: pd.DataFrame,
               out_dir, sql_url=None, dialect: str = "ansi"):
    """Judge a loaded target. Returns (results_df, html_report_path).

    results_df has the columns: check | column | status | failing | detail.

    If sql_url is given, the generated SQL checks are ALSO run against that
    database (source and target tables must both be in it). Their rows are
    labelled "sql:<name>" so you can tell them from the pandas checks.
    """
    results = run_etl_checks(source_df, target_df, mapping)
    if sql_url:
        sql_checks = generate_sql_checks(mapping, get_dialect(dialect))
        sql_results = run_sql_checks(sql_url, sql_checks)
        sql_results["check"] = "sql:" + sql_results["check"]
        sql_results["column"] = ""                 # SQL checks are table-level
        results = pd.concat([results, sql_results[COLUMNS]], ignore_index=True)
    report = write_checks_report(results, out_dir)
    return results, report
