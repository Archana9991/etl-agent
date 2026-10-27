"""Phase 4 workflows: the three things a tester does with a mapping.

    mapping  --> generate_sql_package : write the SQL scripts
    mapping + source --> build_target : expected target (file, and DB table)
    mapping + source + target --> run_checks : judge the loaded target

The CLI (cli.py) is a thin layer over these functions, the same pattern
as Phase 3's orchestrator: logic lives here, so it can be tested and
reused without the command line.
"""
from pathlib import Path

import pandas as pd

from .db_compare import read_table
from .etl_checks import run_etl_checks, write_checks_report
from .generators import write_file
from .mapping import Mapping
from .readers import read_file
from .sensitive import find_sensitive
from .sqlgen import (create_table_sql, generate_sql_checks,
                     generate_sql_script, get_dialect, run_sql_checks,
                     select_expected_sql)
from .target_builder import build_expected_target, load_target


def load_dataset(file=None, url=None, table=None) -> pd.DataFrame:
    """A dataset is either a file (csv/xlsx/xml) or a database table."""
    if file:
        return read_file(file)
    if url and table:
        return read_table(url, table)
    raise ValueError("give either a file, or a database URL and a table name")


def stop_if_sensitive(df, allow=False, log=print) -> bool:
    """True = stop. Same guard as Phase 3: expected targets and reports
    copy real rows, so check the source BEFORE anything is written."""
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


def generate_sql_package(mapping: Mapping, dialect: str, out_dir) -> dict:
    """Write create_target.sql, expected_select.sql and checks.sql."""
    d = get_dialect(dialect)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    checks = generate_sql_checks(mapping, d)
    files = {
        "create_target.sql": create_table_sql(mapping, d) + ";\n",
        "expected_select.sql": select_expected_sql(mapping, d) + ";\n",
        "checks.sql": generate_sql_script(mapping, d, checks),
    }
    for name, content in files.items():
        (out / name).write_text(content, encoding="utf-8")
    return {"files": [out / n for n in files], "checks": checks}


def build_target(mapping: Mapping, source_df, out_dir, url=None,
                 dialect="ansi"):
    """Expected target -> expected_target.csv, and (optionally) a real
    table in the database. Returns (DataFrame, file path, rows loaded)."""
    expected = build_expected_target(source_df, mapping)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "expected_target.csv"
    write_file(expected, path)
    loaded = load_target(url, mapping, expected, dialect) if url else 0
    return expected, path, loaded


def run_checks(mapping: Mapping, source_df, target_df, out_dir,
               references=None, sql_url=None, dialect="ansi"):
    """Pandas checks, plus (optionally) the generated SQL run in a database
    that holds both the source and target tables. Returns (results, report)."""
    results = run_etl_checks(source_df, target_df, mapping, references)
    if sql_url:
        sql = run_sql_checks(sql_url, generate_sql_checks(mapping, get_dialect(dialect)))
        sql.insert(0, "column", "")
        sql["check"] = "sql:" + sql["check"]
        results = pd.concat([results, sql[results.columns]], ignore_index=True)
    return results, write_checks_report(results, out_dir)