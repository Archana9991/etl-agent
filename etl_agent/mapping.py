"""Mapping: the rulebook that says how every TARGET column is built from the SOURCE.

In real projects this is the "source-to-target mapping" document (usually
an Excel sheet from the business analyst). Everything in Phase 4 - the SQL
generator, the expected-target builder and the ETL checks - reads this ONE
object, so there is a single source of truth.

Mapping file columns (CSV or Excel; header names are not case-sensitive):
  TARGET_COLUMN   required
  SOURCE_COLUMNS  one or more source columns, separated by |  (e.g. A|B)
  TRANSFORMATION  one of TRANSFORMATIONS below (blank = DIRECT)
  PARAMETER       extra input for the rule (a constant, a default, ...)
  DATA_TYPE       string | integer | decimal | date   (blank = string)
  NULLABLE        Y / N   (blank = Y)
  PRIMARY_KEY     Y / N   (blank = N; a key is never nullable)
  MAX_LENGTH      whole number (optional)

A JSON mapping is also accepted; it can carry the table names too:
  {"source_table": "...", "target_table": "...", "columns": [ {...}, ... ]}

Safety design: transformations are a fixed WHITELIST. A mapping can only
ask for rules we have written and tested; it can never inject its own SQL.
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

# The whitelist. Every rule here must be implemented TWICE:
#   sqlgen.py          -> as SQL text (what the real ETL would run)
#   target_builder.py  -> in pandas   (our independent expected answer)
TRANSFORMATIONS = {
    "DIRECT": "copy the source value unchanged",
    "TRIM": "remove leading and trailing spaces",
    "UPPER": "convert to upper case",
    "LOWER": "convert to lower case",
    "CONSTANT": "fill every row with the PARAMETER text",
    "DEFAULT": "use the PARAMETER text when the source is blank or NULL",
    "CONCAT": "join several source columns; PARAMETER is the separator",
    "SUBSTRING": "PARAMETER 'start:length' (start counts from 1)",
    "LOOKUP": "PARAMETER 'M=Male;F=Female' recodes values; others unchanged",
}
NO_SOURCE = {"CONSTANT"}                       # needs 0 source columns
MANY_SOURCES = {"CONCAT"}                      # needs 1 or more
NEEDS_PARAMETER = {"CONSTANT", "DEFAULT", "SUBSTRING", "LOOKUP"}
DTYPES = ("string", "integer", "decimal", "date")

# Names end up inside SQL text, so only safe characters are allowed.
_COLUMN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\.]*$")   # dot = schema.table


@dataclass
class ColumnMap:
    """One row of the mapping: how ONE target column is produced."""
    target_column: str
    source_columns: list = field(default_factory=list)
    transformation: str = "DIRECT"
    parameter: str = ""
    dtype: str = "string"
    nullable: bool = True
    primary_key: bool = False
    max_length: int | None = None


@dataclass
class Mapping:
    source_table: str
    target_table: str
    columns: list

    @property
    def target_columns(self):
        return [c.target_column for c in self.columns]

    @property
    def primary_key(self):
        return [c.target_column for c in self.columns if c.primary_key]


# ---- parameter parsers (shared by the SQL generator and the builder) ----
def parse_substring(parameter: str):
    """'2:5' -> (2, 5). Raises ValueError with a clear message."""
    try:
        start_text, length_text = parameter.split(":")
        start, length = int(start_text), int(length_text)
    except ValueError:
        raise ValueError(
            f"SUBSTRING parameter must look like 2:5, got {parameter!r}"
        ) from None
    if start < 1 or length < 1:
        raise ValueError(
            f"SUBSTRING start and length must be 1 or more, got {parameter!r}")
    return start, length


def parse_lookup(parameter: str) -> dict:
    """'M=Male;F=Female' -> {'M': 'Male', 'F': 'Female'}."""
    table = {}
    for pair in parameter.split(";"):
        if not pair.strip():
            continue
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise ValueError(
                f"LOOKUP parameter must look like M=Male;F=Female, "
                f"got {parameter!r}")
        table[key.strip()] = value.strip()
    if not table:
        raise ValueError("LOOKUP parameter has no entries")
    return table


# ---- validation ----------------------------------------------------------
def validate_mapping(mapping: Mapping, source_columns=None) -> list:
    """Return a list of problems (empty list = the mapping is usable).

    Reports ALL problems at once, so the tester fixes the sheet in one go
    instead of one error per run.
    """
    problems = []
    if not _TABLE.match(mapping.source_table or ""):
        problems.append(f"unsafe or missing source table {mapping.source_table!r}")
    if not _TABLE.match(mapping.target_table or ""):
        problems.append(f"unsafe or missing target table {mapping.target_table!r}")
    if not mapping.columns:
        problems.append("the mapping has no columns")

    known_sources = {c.upper() for c in source_columns} if source_columns else None
    seen = set()
    for cm in mapping.columns:
        where = f"column {cm.target_column!r}"
        if not _COLUMN.match(cm.target_column or ""):
            problems.append(f"{where}: unsafe or missing target column name")
        if cm.target_column.upper() in seen:
            problems.append(f"{where}: appears more than once")
        seen.add(cm.target_column.upper())
        if cm.transformation not in TRANSFORMATIONS:
            problems.append(
                f"{where}: unknown transformation {cm.transformation!r} "
                f"(allowed: {', '.join(TRANSFORMATIONS)})")
            continue
        if cm.dtype not in DTYPES:
            problems.append(f"{where}: data type must be one of {DTYPES}")
        if cm.max_length is not None and cm.max_length < 1:
            problems.append(f"{where}: max length must be 1 or more")
        for name in cm.source_columns:
            if not _COLUMN.match(name):
                problems.append(f"{where}: unsafe source column {name!r}")
            elif known_sources is not None and name.upper() not in known_sources:
                problems.append(f"{where}: source column {name!r} not found in the source")
        n = len(cm.source_columns)
        t = cm.transformation
        if t in NO_SOURCE and n != 0:
            problems.append(f"{where}: {t} takes no source column")
        elif t in MANY_SOURCES and n < 1:
            problems.append(f"{where}: {t} needs at least one source column")
        elif t not in NO_SOURCE | MANY_SOURCES and n != 1:
            problems.append(f"{where}: {t} needs exactly one source column, got {n}")
        if t in NEEDS_PARAMETER and cm.parameter == "":
            problems.append(f"{where}: {t} needs a PARAMETER")
        try:
            if t == "SUBSTRING" and cm.parameter:
                parse_substring(cm.parameter)
            if t == "LOOKUP" and cm.parameter:
                parse_lookup(cm.parameter)
        except ValueError as exc:
            problems.append(f"{where}: {exc}")
    return problems


# ---- loading -------------------------------------------------------------
def _yes(text, default):
    text = str(text).strip().upper()
    if text == "":
        return default
    return text in ("Y", "YES", "TRUE", "1")


def _rows_from_json(data):
    """JSON entries -> the same dict-of-text shape a CSV row has."""
    rows = []
    for entry in data.get("columns", []):
        row = {}
        for key, value in entry.items():
            if isinstance(value, list):
                value = "|".join(str(v) for v in value)
            elif isinstance(value, bool):
                value = "Y" if value else "N"
            elif value is None:
                value = ""
            row[key.strip().upper()] = str(value)
        rows.append(row)
    return rows


def _rows_from_table(path: Path):
    # Read raw text. We deliberately do NOT strip PARAMETER, because a
    # single space is a perfectly good CONCAT separator.
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, dtype=str, keep_default_na=False,
                         encoding="utf-8-sig")
    else:
        df = pd.read_excel(path, dtype=str, keep_default_na=False)
    df.columns = [str(c).strip().upper() for c in df.columns]
    return df.to_dict("records")


def _column_from_row(row, number, problems):
    def text(key):
        return str(row.get(key, "")).strip()

    primary = _yes(text("PRIMARY_KEY"), False)
    max_text = text("MAX_LENGTH")
    max_length = None
    if max_text:
        if max_text.isdigit():
            max_length = int(max_text)
        else:
            problems.append(f"row {number}: MAX_LENGTH must be a whole number")
    sources = [s.strip() for s in re.split(r"[|,]", text("SOURCE_COLUMNS"))
               if s.strip()]
    return ColumnMap(
        target_column=text("TARGET_COLUMN"),
        source_columns=sources,
        transformation=text("TRANSFORMATION").upper() or "DIRECT",
        parameter=str(row.get("PARAMETER", "")),
        dtype=text("DATA_TYPE").lower() or "string",
        nullable=_yes(text("NULLABLE"), True) and not primary,
        primary_key=primary,
        max_length=max_length,
    )


def load_mapping(path, source_table=None, target_table=None,
                 source_columns=None) -> Mapping:
    """Read a mapping file and validate it. Raises ValueError listing
    every problem found. Table names given here override the file's."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = _rows_from_json(data)
        source_table = source_table or data.get("source_table")
        target_table = target_table or data.get("target_table")
    elif ext in (".csv", ".xlsx", ".xls"):
        rows = _rows_from_table(path)
        if rows and "TARGET_COLUMN" not in rows[0]:
            raise ValueError("the mapping needs a TARGET_COLUMN header")
    else:
        raise ValueError(f"Unsupported mapping type '{ext}' (use csv, xlsx or json)")

    problems = []
    columns = [_column_from_row(r, i, problems) for i, r in enumerate(rows, 2)]
    mapping = Mapping(source_table or "", target_table or "", columns)
    problems += validate_mapping(mapping, source_columns)
    if problems:
        raise ValueError("Mapping has problems:\n"
                         + "\n".join(f"  - {p}" for p in problems))
    return mapping