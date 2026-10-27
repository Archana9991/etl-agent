"""ETL checks: is the loaded TARGET correct, judged against the SOURCE
and the MAPPING?

Every check is a small function that returns result rows, and
run_etl_checks() simply calls them all. Adding a new check later means
writing one function and adding one line.

Result row: check | column | status | failing | detail
  status is PASS, FAIL, or SKIP (SKIP = the check could not run, e.g.
  the column it needs is missing - that is reported by another check).

Checks:
  row_count          same number of rows in source and target
  columns_present    every mapped target column exists
  duplicate_key      the primary key is unique
  not_null           NOT NULL columns have no blanks
  data_type          integer / decimal / date columns hold the right kind
  max_length         text never exceeds the allowed length
  sum                numeric totals reconcile (DIRECT columns)
  transformation     target value == what the mapping rule produces
  referential        every value exists in a reference (parent) list
"""
import math
import re
from html import escape
from pathlib import Path

import pandas as pd

from .comparator import compare_dataframes
from .mapping import Mapping, validate_mapping
from .target_builder import build_expected_target

COLUMNS = ["check", "column", "status", "failing", "detail"]

_TYPE_PATTERNS = {
    "integer": re.compile(r"^-?\d+$"),
    "decimal": re.compile(r"^-?\d+(\.\d+)?$"),
    "date": re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}:\d{2}(\.\d+)?)?$"),
}


def _row(check, column, ok, failing=0, detail="", status=None):
    return {"check": check, "column": column,
            "status": status or ("PASS" if ok else "FAIL"),
            "failing": int(failing), "detail": detail}


def _skip(check, column, why):
    return _row(check, column, True, 0, why, status="SKIP")


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Same shape the readers produce: all text, NULL = blank, trimmed."""
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]
    return out.fillna("").astype(str).apply(lambda c: c.str.strip())


def _find(df: pd.DataFrame, name: str):
    """The real column name, ignoring case (or None)."""
    for c in df.columns:
        if c.upper() == name.upper():
            return c
    return None


def _examples(values, limit=3):
    shown = ", ".join(repr(v) for v in list(values)[:limit])
    return f" e.g. {shown}" if shown else ""


# ---- individual checks -----------------------------------------------------
def check_row_count(source, target):
    ok = len(source) == len(target)
    return [_row("row_count", "", ok, abs(len(source) - len(target)),
                 f"source {len(source)} rows, target {len(target)} rows")]


def check_columns_present(target, mapping):
    missing = [c for c in mapping.target_columns if _find(target, c) is None]
    return [_row("columns_present", "", not missing, len(missing),
                 f"missing: {missing}" if missing else "all mapped columns exist")]


def check_duplicate_keys(target, pk):
    if not pk:
        return [_skip("duplicate_key", "", "no primary key in the mapping")]
    actual = [_find(target, k) for k in pk]
    if None in actual:
        return [_skip("duplicate_key", ", ".join(pk), "key column missing")]
    dups = int(target.duplicated(subset=actual).sum())
    return [_row("duplicate_key", ", ".join(pk), dups == 0, dups,
                 f"{dups} repeated key rows")]


def check_not_null(target, cm):
    if cm.nullable:
        return []
    col = _find(target, cm.target_column)
    if col is None:
        return [_skip("not_null", cm.target_column, "column missing")]
    n = int((target[col] == "").sum())
    return [_row("not_null", cm.target_column, n == 0, n, f"{n} blank values")]


def check_data_type(target, cm):
    pattern = _TYPE_PATTERNS.get(cm.dtype)
    if pattern is None:
        return []
    col = _find(target, cm.target_column)
    if col is None:
        return [_skip("data_type", cm.target_column, "column missing")]
    values = target[col][target[col] != ""]
    bad = values[~values.str.match(pattern)]
    return [_row("data_type", cm.target_column, bad.empty, len(bad),
                 f"{len(bad)} values are not {cm.dtype}" + _examples(bad))]


def check_max_length(target, cm):
    if not cm.max_length:
        return []
    col = _find(target, cm.target_column)
    if col is None:
        return [_skip("max_length", cm.target_column, "column missing")]
    long = target[col][target[col].str.len() > cm.max_length]
    return [_row("max_length", cm.target_column, long.empty, len(long),
                 f"{len(long)} values longer than {cm.max_length}")]


def check_sums(source, target, mapping):
    rows = []
    for cm in mapping.columns:
        if cm.dtype not in ("integer", "decimal") or cm.transformation != "DIRECT":
            continue
        s_col, t_col = _find(source, cm.source_columns[0]), _find(target, cm.target_column)
        if s_col is None or t_col is None:
            rows.append(_skip("sum", cm.target_column, "column missing"))
            continue
        a = pd.to_numeric(source[s_col], errors="coerce").sum()
        b = pd.to_numeric(target[t_col], errors="coerce").sum()
        ok = math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)
        rows.append(_row("sum", cm.target_column, ok, 0 if ok else 1,
                         f"source total {a:g}, target total {b:g}"))
    return rows


def check_transformations(source, target, mapping):
    """Build the expected target, compare it with the real one, and report
    per target column - so the tester sees WHICH mapping rule is wrong."""
    pk = mapping.primary_key
    if not pk:
        return [_skip("transformation", "", "needs a primary key to match rows")]
    if any(_find(target, k) is None for k in pk):
        return [_skip("transformation", "", "key column missing from target")]
    expected = build_expected_target(source, mapping)
    res = compare_dataframes(expected, target, pk, normalize=True)

    rows = [
        _row("rows_missing_in_target", "", res.only_in_source.empty,
             len(res.only_in_source), f"{len(res.only_in_source)} expected rows not loaded"),
        _row("rows_unexpected_in_target", "", res.only_in_target.empty,
             len(res.only_in_target), f"{len(res.only_in_target)} rows the mapping cannot explain"),
    ]
    for cm in mapping.columns:
        if cm.primary_key:
            continue
        if _find(target, cm.target_column) is None:
            rows.append(_skip("transformation", cm.target_column, "column missing"))
            continue
        bad = res.mismatches[res.mismatches["column"] == cm.target_column.upper()]
        sample = "; ".join(f"key {r.key}: expected {r.source_value!r}, got {r.target_value!r}"
                           for r in bad.head(3).itertuples())
        rows.append(_row("transformation", cm.target_column, bad.empty, len(bad),
                         f"{len(bad)} wrong values" + (f" ({sample})" if sample else "")))
    return rows


def check_referential_integrity(target, column, ref_df, ref_column):
    col, ref = _find(target, column), _find(ref_df, ref_column)
    if col is None or ref is None:
        return [_skip("referential", column, "column missing")]
    allowed = set(ref_df[ref])
    values = target[col][target[col] != ""]
    orphans = values[~values.isin(allowed)]
    return [_row("referential", column, orphans.empty, len(orphans),
                 f"{len(orphans)} values not found in reference {ref_column}"
                 + _examples(orphans.unique()))]


# ---- the runner ------------------------------------------------------------
def run_etl_checks(source_df, target_df, mapping: Mapping,
                   references=None) -> pd.DataFrame:
    """Run every check.

    references: optional {target_column: (reference_df, reference_column)}
    for foreign-key style checks.
    """
    problems = validate_mapping(mapping)
    if problems:
        raise ValueError("Mapping has problems:\n"
                         + "\n".join(f"  - {p}" for p in problems))
    source, target = _clean(source_df), _clean(target_df)

    rows = []
    rows += check_row_count(source, target)
    rows += check_columns_present(target, mapping)
    rows += check_duplicate_keys(target, mapping.primary_key)
    for cm in mapping.columns:
        rows += check_not_null(target, cm)
        rows += check_data_type(target, cm)
        rows += check_max_length(target, cm)
    rows += check_sums(source, target, mapping)
    rows += check_transformations(source, target, mapping)
    for column, (ref_df, ref_col) in (references or {}).items():
        rows += check_referential_integrity(target, column, _clean(ref_df), ref_col)
    return pd.DataFrame(rows, columns=COLUMNS)


def checks_passed(results: pd.DataFrame) -> bool:
    """SKIP does not fail a run; FAIL and ERROR do."""
    return not results["status"].isin(["FAIL", "ERROR"]).any()


def write_checks_report(results: pd.DataFrame, out_dir) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    results.to_csv(out / "etl_checks.csv", index=False)
    passed = int((results["status"] == "PASS").sum())
    failed = int(results["status"].isin(["FAIL", "ERROR"]).sum())
    colour = {"PASS": "ok", "FAIL": "bad", "ERROR": "bad", "SKIP": "skip"}
    rows = "".join(
        f"<tr class='{colour.get(r.status, '')}'>"
        + "".join(f"<td>{escape(str(v))}</td>" for v in r) + "</tr>"
        for r in results.itertuples(index=False))
    head = "".join(f"<th>{escape(c)}</th>" for c in results.columns)
    style = ("body{font-family:sans-serif;margin:2em}table{border-collapse:collapse}"
             "td,th{border:1px solid #ccc;padding:6px 10px;text-align:left}"
             ".ok{background:#e6f4ea}.bad{background:#fce8e6}.skip{background:#eee}")
    html = ("<html><head><meta charset='utf-8'><title>ETL Checks</title>"
            f"<style>{style}</style></head><body><h1>ETL Checks</h1>"
            f"<p><b>{passed} passed, {failed} failed, "
            f"{len(results) - passed - failed} skipped.</b></p>"
            f"<table><tr>{head}</tr>{rows}</table></body></html>")
    (out / "etl_checks.html").write_text(html, encoding="utf-8")
    return out / "etl_checks.html"