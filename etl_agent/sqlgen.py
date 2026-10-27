"""SQL generator: turn a mapping into the SQL an ETL tester writes by hand.

It produces:
  * the "expected" SELECT   - the source data transformed per the mapping
  * the target CREATE TABLE - built from the mapping's types and keys
  * a set of ETL checks     - count, MINUS/EXCEPT, duplicates, NULLs,
                              length and sum reconciliation

Design ideas:
  1. SQL is TEXT. We build it from small, tested pieces (one function per
     transformation) rather than one giant f-string.
  2. Databases disagree on small things (|| versus +, MINUS versus EXCEPT,
     FROM DUAL...). Those differences live in a Dialect object, so the
     generating code stays the same for every database.
  3. Every check says what a PASS looks like (the `expect` field), so the
     same list can be printed as a .sql script for humans OR executed by
     run_sql_checks() for automation.
"""
from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy import create_engine, text

from .mapping import (Mapping, parse_lookup, parse_substring,
                      validate_mapping)


# ---- dialects ------------------------------------------------------------
@dataclass(frozen=True)
class Dialect:
    name: str
    concat: str = "||"          # string join operator
    substr: str = "SUBSTR"      # substring function
    length: str = "LENGTH"      # string length function
    except_kw: str = "EXCEPT"   # "rows in A not in B"
    dual: str = ""              # Oracle needs FROM DUAL on a bare SELECT
    types: dict = field(default_factory=lambda: {
        "string": "VARCHAR({n})", "integer": "INTEGER",
        "decimal": "DECIMAL(18,4)", "date": "DATE"})


DIALECTS = {
    "ansi": Dialect("ansi"),
    "sqlite": Dialect("sqlite"),
    "postgres": Dialect("postgres"),
    "oracle": Dialect("oracle", except_kw="MINUS", dual=" FROM DUAL",
                      types={"string": "VARCHAR2({n})",
                             "integer": "NUMBER(38)",
                             "decimal": "NUMBER(18,4)", "date": "DATE"}),
    "mssql": Dialect("mssql", concat="+", substr="SUBSTRING", length="LEN",
                     types={"string": "VARCHAR({n})", "integer": "INT",
                            "decimal": "DECIMAL(18,4)", "date": "DATE"}),
}


def get_dialect(name: str) -> Dialect:
    try:
        return DIALECTS[name.lower()]
    except KeyError:
        raise ValueError(
            f"Unknown dialect {name!r}. Choose from: {', '.join(DIALECTS)}"
        ) from None


def sql_literal(value: str) -> str:
    """Quote text for SQL. A quote inside the text is doubled ('' ), which
    stops a value like  O'Brien  or  x'; DROP TABLE t; --  from breaking
    out of the string."""
    return "'" + str(value).replace("'", "''") + "'"


# ---- expressions ---------------------------------------------------------
def column_expression(cm, d: Dialect) -> str:
    """The SQL expression for ONE target column (without the alias)."""
    t = cm.transformation
    cols = cm.source_columns
    c = cols[0] if cols else ""
    if t == "DIRECT":
        return c
    if t == "TRIM":
        return f"TRIM({c})"
    if t == "UPPER":
        return f"UPPER({c})"
    if t == "LOWER":
        return f"LOWER({c})"
    if t == "CONSTANT":
        return sql_literal(cm.parameter)
    if t == "DEFAULT":
        return (f"CASE WHEN {c} IS NULL OR TRIM({c}) = '' "
                f"THEN {sql_literal(cm.parameter)} ELSE {c} END")
    if t == "CONCAT":
        # NULL || 'x' is NULL in SQL, so each part is wrapped in COALESCE.
        parts = [f"COALESCE({name}, '')" for name in cols]
        glue = f" {d.concat} "
        if cm.parameter:
            glue = f" {d.concat} {sql_literal(cm.parameter)} {d.concat} "
        return glue.join(parts)
    if t == "SUBSTRING":
        start, length = parse_substring(cm.parameter)
        return f"{d.substr}({c}, {start}, {length})"
    if t == "LOOKUP":
        whens = " ".join(f"WHEN {sql_literal(k)} THEN {sql_literal(v)}"
                         for k, v in parse_lookup(cm.parameter).items())
        return f"CASE {c} {whens} ELSE {c} END"
    raise ValueError(f"No SQL for transformation {t!r}")


def _require_valid(mapping: Mapping):
    problems = validate_mapping(mapping)
    if problems:
        raise ValueError("Mapping has problems:\n"
                         + "\n".join(f"  - {p}" for p in problems))


def select_expected_sql(mapping: Mapping, d: Dialect) -> str:
    """SELECT that shows what the target SHOULD contain."""
    _require_valid(mapping)
    items = ",\n".join(
        f"    {column_expression(cm, d)} AS {cm.target_column}"
        for cm in mapping.columns)
    return f"SELECT\n{items}\nFROM {mapping.source_table}"


def _select_target_sql(mapping: Mapping) -> str:
    cols = ", ".join(mapping.target_columns)
    return f"SELECT {cols}\nFROM {mapping.target_table}"


def create_table_sql(mapping: Mapping, d: Dialect) -> str:
    """CREATE TABLE for the target, from the mapping's types and keys."""
    _require_valid(mapping)
    lines = []
    for cm in mapping.columns:
        sql_type = d.types[cm.dtype].format(n=cm.max_length or 255)
        not_null = "" if cm.nullable else " NOT NULL"
        lines.append(f"    {cm.target_column} {sql_type}{not_null}")
    if mapping.primary_key:
        lines.append(f"    PRIMARY KEY ({', '.join(mapping.primary_key)})")
    return f"CREATE TABLE {mapping.target_table} (\n" + ",\n".join(lines) + "\n)"


# ---- the checks ----------------------------------------------------------
@dataclass
class SqlCheck:
    name: str
    description: str
    sql: str
    expect: str    # no_rows: PASS if 0 rows | zero: first value is 0 | equal: two values match


def generate_sql_checks(mapping: Mapping, d: Dialect) -> list:
    _require_valid(mapping)
    src, tgt = mapping.source_table, mapping.target_table
    checks = []

    checks.append(SqlCheck(
        "row_count", "Source and target hold the same number of rows",
        f"SELECT (SELECT COUNT(*) FROM {src}) AS source_count,\n"
        f"       (SELECT COUNT(*) FROM {tgt}) AS target_count{d.dual}",
        "equal"))

    expected = select_expected_sql(mapping, d)
    target = _select_target_sql(mapping)
    checks.append(SqlCheck(
        "source_minus_target",
        "Rows the ETL should have loaded but did not (or loaded wrongly)",
        f"{expected}\n{d.except_kw}\n{target}", "no_rows"))
    checks.append(SqlCheck(
        "target_minus_source",
        "Rows in the target that the mapping cannot explain",
        f"{target}\n{d.except_kw}\n{expected}", "no_rows"))

    if mapping.primary_key:
        keys = ", ".join(mapping.primary_key)
        checks.append(SqlCheck(
            "duplicate_keys", f"No primary key value ({keys}) appears twice",
            f"SELECT {keys}, COUNT(*) AS row_count\nFROM {tgt}\n"
            f"GROUP BY {keys}\nHAVING COUNT(*) > 1", "no_rows"))

    for cm in mapping.columns:
        col = cm.target_column
        if not cm.nullable:
            blank = f" OR TRIM({col}) = ''" if cm.dtype == "string" else ""
            checks.append(SqlCheck(
                f"null_{col}", f"{col} is NOT NULL, so no NULL or blank allowed",
                f"SELECT COUNT(*) AS null_count\nFROM {tgt}\n"
                f"WHERE {col} IS NULL{blank}", "zero"))
        if cm.dtype == "string" and cm.max_length:
            checks.append(SqlCheck(
                f"too_long_{col}", f"{col} never exceeds {cm.max_length} characters",
                f"SELECT COUNT(*) AS too_long\nFROM {tgt}\n"
                f"WHERE {d.length}({col}) > {cm.max_length}", "zero"))
        if (cm.dtype in ("integer", "decimal")
                and cm.transformation == "DIRECT"):
            s = cm.source_columns[0]
            checks.append(SqlCheck(
                f"sum_{col}", f"Total of {col} matches the source total of {s}",
                f"SELECT (SELECT COALESCE(SUM({s}), 0) FROM {src}) AS source_sum,\n"
                f"       (SELECT COALESCE(SUM({col}), 0) FROM {tgt}) AS target_sum{d.dual}",
                "equal"))
    return checks


def generate_sql_script(mapping: Mapping, d: Dialect, checks=None) -> str:
    """One readable .sql file: every check with a comment above it."""
    checks = checks if checks is not None else generate_sql_checks(mapping, d)
    out = [f"-- ETL checks for {mapping.source_table} -> {mapping.target_table}",
           f"-- dialect: {d.name}", ""]
    for c in checks:
        out += [f"-- {c.name}: {c.description}",
                f"-- PASS when: {_pass_text(c.expect)}",
                c.sql + ";", ""]
    return "\n".join(out)


def _pass_text(expect):
    return {"no_rows": "the query returns no rows",
            "zero": "the number returned is 0",
            "equal": "the two numbers returned are equal"}[expect]


# ---- running them --------------------------------------------------------
def run_sql_checks(url: str, checks) -> pd.DataFrame:
    """Execute the checks against a database. Source and target tables
    must be reachable from this one connection."""
    rows = []
    engine = create_engine(url)
    try:
        for c in checks:
            status, failing, detail = _run_one(engine, c)
            rows.append({"check": c.name, "status": status,
                         "failing": failing, "detail": detail})
    finally:
        engine.dispose()
    return pd.DataFrame(rows, columns=["check", "status", "failing", "detail"])


def _run_one(engine, c):
    # text() treats ":name" as a bind parameter, even inside quotes, so a
    # literal colon (e.g. SUBSTRING, or a time) must be escaped as \:
    statement = text(c.sql.replace(":", r"\:"))
    try:
        with engine.connect() as conn:       # a fresh connection per check
            fetched = conn.execute(statement).fetchall()
    except Exception as exc:
        return "ERROR", 0, str(exc).splitlines()[0][:300]
    if c.expect == "no_rows":
        ok = len(fetched) == 0
        return ("PASS" if ok else "FAIL"), len(fetched), f"{len(fetched)} rows returned"
    first = fetched[0] if fetched else (0,)
    if c.expect == "zero":
        n = int(first[0] or 0)
        return ("PASS" if n == 0 else "FAIL"), n, f"value {n}"
    a, b = float(first[0] or 0), float(first[1] or 0)
    ok = abs(a - b) <= 1e-6 * max(1.0, abs(a))
    return ("PASS" if ok else "FAIL"), (0 if ok else 1), f"source {a:g} vs target {b:g}"