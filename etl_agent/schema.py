"""Schema: what a 'valid' file looks like (columns, types, NOT NULL, primary key).
Inferred from a good sample, saved as JSON, then editable by a human."""
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

_INT = re.compile(r"^-?\d+$")
_DEC = re.compile(r"^-?\d+\.\d+$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class ColumnSpec:
    name: str
    dtype: str = "string"          # integer | decimal | date | string
    nullable: bool = True
    max_length: int | None = None  # set by hand in the JSON


@dataclass
class TableSchema:
    columns: list
    primary_key: list = field(default_factory=list)

    @property
    def column_names(self):
        return [c.name for c in self.columns]

    def save(self, path):
        data = {"primary_key": self.primary_key, "columns": [asdict(c) for c in self.columns]}
        Path(path).write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text())
        return cls([ColumnSpec(**c) for c in data["columns"]], data.get("primary_key", []))


def infer_dtype(values) -> str:
    vals = [v for v in values if v != ""]
    if not vals:
        return "string"
    if all(_INT.match(v) for v in vals):
        return "integer"
    if all(_INT.match(v) or _DEC.match(v) for v in vals):
        return "decimal"
    if all(_DATE.match(v) for v in vals):
        return "date"
    return "string"


def guess_primary_key(df: pd.DataFrame) -> list:
    """First column that is fully filled and unique. Only a guess: pass --pk when you know."""
    for col in df.columns:
        if (df[col] != "").all() and df[col].is_unique:
            return [col]
    return []


def infer_schema(df: pd.DataFrame, primary_key=None) -> TableSchema:
    pk = primary_key or guess_primary_key(df)
    cols = []
    for name in df.columns:
        has_blank = bool((df[name] == "").any())
        cols.append(ColumnSpec(name=name,
                               dtype=infer_dtype(df[name]),
                               nullable=bool((name not in pk) and has_blank)))
    return TableSchema(cols, pk)