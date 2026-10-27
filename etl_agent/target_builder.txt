"""Build the TARGET from a mapping: the expected data and the table itself.

Why build the expected target in pandas, when sqlgen.py already writes SQL?
Because the two are INDEPENDENT implementations of the same rules:
  * the real ETL (or the generated SQL) produces the ACTUAL target
  * this module produces the EXPECTED target
If they disagree, one of them has a bug - and that is exactly the kind of
defect an ETL tester is hunting for. (Testers call the source of truth
the "test oracle".)

Two jobs:
  1. build_expected_target : source DataFrame + mapping -> expected rows
  2. load_target           : create the target table from the mapping and
                             fill it (a golden copy, or a target for an
                             ETL that does not exist yet)
"""
import pandas as pd
from sqlalchemy import create_engine, inspect, text

from .mapping import (Mapping, parse_lookup, parse_substring,
                      validate_mapping)
from .sqlgen import create_table_sql, get_dialect


def _source_series(df: pd.DataFrame, name: str) -> pd.Series:
    """Find a source column ignoring upper/lower case (databases differ)."""
    lookup = {str(c).upper(): c for c in df.columns}
    if name.upper() not in lookup:
        raise ValueError(f"source column {name!r} not found")
    return df[lookup[name.upper()]].astype(str)


def apply_transformation(df: pd.DataFrame, cm) -> pd.Series:
    """Apply ONE mapping rule to the whole source. No row loops: pandas
    works on a whole column at once, which is fast and easy to read."""
    t = cm.transformation
    if t == "CONSTANT":
        return pd.Series([cm.parameter] * len(df), index=df.index, dtype=object)
    cols = [_source_series(df, n) for n in cm.source_columns]
    s = cols[0]
    if t == "DIRECT":
        return s
    if t == "TRIM":
        return s.str.strip()
    if t == "UPPER":
        return s.str.upper()
    if t == "LOWER":
        return s.str.lower()
    if t == "DEFAULT":
        return s.where(s.str.strip() != "", cm.parameter)
    if t == "CONCAT":
        out = cols[0]
        for more in cols[1:]:
            out = out + cm.parameter + more
        return out
    if t == "SUBSTRING":
        start, length = parse_substring(cm.parameter)
        return s.str.slice(start - 1, start - 1 + length)
    if t == "LOOKUP":
        table = parse_lookup(cm.parameter)
        return s.map(lambda v: table.get(v, v))
    raise ValueError(f"No implementation for transformation {t!r}")


def _require_valid(mapping: Mapping):
    problems = validate_mapping(mapping)
    if problems:
        raise ValueError("Mapping has problems:\n"
                         + "\n".join(f"  - {p}" for p in problems))


def build_expected_target(source_df: pd.DataFrame, mapping: Mapping) -> pd.DataFrame:
    """The target as it SHOULD look, as text (blank = NULL), ready for the
    Phase 1 comparator."""
    _require_valid(mapping)
    out = pd.DataFrame({cm.target_column: apply_transformation(source_df, cm)
                        for cm in mapping.columns}, index=source_df.index)
    return out.astype(str).reset_index(drop=True)


# ---- creating and loading the table ---------------------------------------
def _split_table(name: str):
    schema, _, table = name.rpartition(".")
    return (schema or None), table


def _typed_for_load(df: pd.DataFrame, mapping: Mapping) -> pd.DataFrame:
    """Blank text -> NULL, and numbers -> real numbers, so the database
    gets proper values rather than text everywhere."""
    out = df.astype(object).mask(df == "", None)
    for cm in mapping.columns:
        col = cm.target_column
        try:
            if cm.dtype == "integer":
                out[col] = pd.to_numeric(out[col]).astype("Int64")
            elif cm.dtype == "decimal":
                out[col] = pd.to_numeric(out[col]).astype("float64")
        except (ValueError, TypeError):
            raise ValueError(
                f"column {col} is declared {cm.dtype} but holds non-numeric values"
            ) from None
    return out


def create_target_table(url: str, mapping: Mapping, dialect: str = "ansi",
                        replace: bool = True) -> str:
    """Run the generated CREATE TABLE. Returns the SQL that was executed."""
    _require_valid(mapping)
    ddl = create_table_sql(mapping, get_dialect(dialect))
    schema, table = _split_table(mapping.target_table)
    engine = create_engine(url)
    try:
        exists = inspect(engine).has_table(table, schema=schema)
        if exists and not replace:
            raise ValueError(f"table {mapping.target_table} already exists")
        with engine.begin() as conn:          # begin() = commit on success
            if exists:
                conn.execute(text(f"DROP TABLE {mapping.target_table}"))
            conn.execute(text(ddl))
    finally:
        engine.dispose()
    return ddl


def load_target(url: str, mapping: Mapping, expected: pd.DataFrame,
                dialect: str = "ansi", replace: bool = True) -> int:
    """Create the target table from the mapping, then insert the rows.

    A NULL in a NOT NULL column, or a duplicate key, is refused by the
    database itself - which is useful: the mapping's constraints are real.
    """
    typed = _typed_for_load(expected, mapping)     # fail early on bad numbers
    create_target_table(url, mapping, dialect, replace)
    schema, table = _split_table(mapping.target_table)
    engine = create_engine(url)
    try:
        typed.to_sql(table, engine, schema=schema, if_exists="append",
                     index=False)
    finally:
        engine.dispose()
    return len(typed)