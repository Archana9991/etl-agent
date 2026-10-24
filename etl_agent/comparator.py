"""Compare two DataFrames and return a structured result.

Checks, in order (cheap checks first):
  1. Schema: missing / extra columns
  2. Row counts
  3. Duplicate keys
  4. Rows only in source / only in target
  5. Cell-level value mismatches
"""
import re
from dataclasses import dataclass, field
from decimal import Decimal

import pandas as pd

# Numbers WITHOUT leading zeros, so codes like "007" are never merged
# with "7". Dates: a midnight timestamp equals the plain date.
_NUMBER = re.compile(r"^-?(0|[1-9]\d*)(\.\d+)?$")
_MIDNIGHT = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T]00:00:00(\.0+)?$")


def normalize_value(value: str) -> str:
    """Make harmless formatting differences disappear.

    139913.4 == 139913.40 == 139913.400
    2020-04-18 == 2020-04-18 00:00:00
    Text, and numbers with leading zeros (007), are left alone.
    """
    match = _MIDNIGHT.match(value)
    if match:
        return match.group(1)
    if _NUMBER.match(value):
        plain = format(Decimal(value).normalize(), "f")
        return "0" if plain == "-0" else plain
    return value


@dataclass
class CompareResult:
    source_rows: int = 0
    target_rows: int = 0
    missing_columns: list = field(default_factory=list)  # in source only
    extra_columns: list = field(default_factory=list)    # in target only
    duplicate_keys_source: int = 0
    duplicate_keys_target: int = 0
    only_in_source: pd.DataFrame = field(default_factory=pd.DataFrame)
    only_in_target: pd.DataFrame = field(default_factory=pd.DataFrame)
    mismatches: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def passed(self) -> bool:
        return (
            not self.missing_columns
            and not self.extra_columns
            and self.source_rows == self.target_rows
            and self.duplicate_keys_source == 0
            and self.duplicate_keys_target == 0
            and self.only_in_source.empty
            and self.only_in_target.empty
            and self.mismatches.empty
        )

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        lines = [
            f"Status            : {status}",
            f"Rows (src / tgt)  : {self.source_rows} / {self.target_rows}",
            f"Missing columns   : {self.missing_columns or 'none'}",
            f"Extra columns     : {self.extra_columns or 'none'}",
            f"Duplicate keys    : src={self.duplicate_keys_source} "
            f"tgt={self.duplicate_keys_target}",
            f"Only in source    : {len(self.only_in_source)} rows",
            f"Only in target    : {len(self.only_in_target)} rows",
            f"Value mismatches  : {len(self.mismatches)} cells",
        ]
        return "\n".join(lines)


def compare_dataframes(source: pd.DataFrame, target: pd.DataFrame,
                       keys: list, ignore_columns: list | None = None,
                       case_sensitive_columns: bool = False,
                       normalize: bool = False) -> CompareResult:
    ignore = set(ignore_columns or [])
    source, target = source.copy(), target.copy()

    if not case_sensitive_columns:
        # DB engines often return upper/lower-case names; treat as equal.
        source.columns = [c.upper() for c in source.columns]
        target.columns = [c.upper() for c in target.columns]
        keys = [k.upper() for k in keys]
        ignore = {c.upper() for c in ignore}

    res = CompareResult(source_rows=len(source), target_rows=len(target))

    # 1. Schema comparison
    res.missing_columns = [c for c in source.columns
                           if c not in target.columns]
    res.extra_columns = [c for c in target.columns
                         if c not in source.columns]

    # Without the key columns on both sides we cannot match rows.
    if any(k not in source.columns or k not in target.columns
           for k in keys):
        raise ValueError(
            f"Key column(s) {keys} must exist in both source and target")

    # 2. Duplicate keys (a primary key should be unique)
    res.duplicate_keys_source = int(source.duplicated(subset=keys).sum())
    res.duplicate_keys_target = int(target.duplicated(subset=keys).sum())

    # 3. Row presence: outer merge; indicator=True says which side.
    merged = source.merge(target, on=keys, how="outer",
                          suffixes=("_src", "_tgt"), indicator=True)
    left_only = merged.loc[merged["_merge"] == "left_only", keys]
    right_only = merged.loc[merged["_merge"] == "right_only", keys]
    res.only_in_source = source.merge(left_only, on=keys)
    res.only_in_target = target.merge(right_only, on=keys)

    # 4. Cell-by-cell comparison on rows found in both
    both = merged[merged["_merge"] == "both"]
    common = [c for c in source.columns
              if c in target.columns and c not in keys
              and c not in ignore]
    records = []
    for col in common:
        s_col, t_col = f"{col}_src", f"{col}_tgt"
        diff = both[both[s_col] != both[t_col]]
        if normalize and not diff.empty:
            # Only re-check the rows that already differ (fast).
            still = diff[s_col].map(normalize_value) != \
                diff[t_col].map(normalize_value)
            diff = diff[still]
        for _, row in diff.iterrows():
            records.append({
                "key": "|".join(str(row[k]) for k in keys),
                "column": col,
                "source_value": row[s_col],
                "target_value": row[t_col],
            })
    res.mismatches = pd.DataFrame(
        records, columns=["key", "column", "source_value", "target_value"])
    return res