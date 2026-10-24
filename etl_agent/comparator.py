"""Compare two DataFrames: schema, row counts, duplicate keys, missing rows, values."""
from dataclasses import dataclass, field
import pandas as pd


@dataclass
class CompareResult:
    source_rows: int = 0
    target_rows: int = 0
    missing_columns: list = field(default_factory=list)
    extra_columns: list = field(default_factory=list)
    duplicate_keys_source: int = 0
    duplicate_keys_target: int = 0
    only_in_source: pd.DataFrame = field(default_factory=pd.DataFrame)
    only_in_target: pd.DataFrame = field(default_factory=pd.DataFrame)
    mismatches: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def passed(self):
        return (not self.missing_columns and not self.extra_columns
                and self.source_rows == self.target_rows
                and self.duplicate_keys_source == 0 and self.duplicate_keys_target == 0
                and self.only_in_source.empty and self.only_in_target.empty
                and self.mismatches.empty)

    def summary(self):
        return "\n".join([
            f"Status            : {'PASS' if self.passed else 'FAIL'}",
            f"Rows (src / tgt)  : {self.source_rows} / {self.target_rows}",
            f"Missing columns   : {self.missing_columns or 'none'}",
            f"Extra columns     : {self.extra_columns or 'none'}",
            f"Duplicate keys    : src={self.duplicate_keys_source} tgt={self.duplicate_keys_target}",
            f"Only in source    : {len(self.only_in_source)} rows",
            f"Only in target    : {len(self.only_in_target)} rows",
            f"Value mismatches  : {len(self.mismatches)} cells",
        ])


def compare_dataframes(source, target, keys, ignore_columns=None, case_sensitive_columns=False):
    ignore = set(ignore_columns or [])
    source, target = source.copy(), target.copy()

    if not case_sensitive_columns:
        source.columns = [c.upper() for c in source.columns]
        target.columns = [c.upper() for c in target.columns]
        keys = [k.upper() for k in keys]
        ignore = {c.upper() for c in ignore}

    res = CompareResult(source_rows=len(source), target_rows=len(target))

    # 1. Schema
    res.missing_columns = [c for c in source.columns if c not in target.columns]
    res.extra_columns = [c for c in target.columns if c not in source.columns]

    if any(k not in source.columns or k not in target.columns for k in keys):
        raise ValueError(f"Key column(s) {keys} must exist in both source and target")

    # 2. Duplicate keys
    res.duplicate_keys_source = int(source.duplicated(subset=keys).sum())
    res.duplicate_keys_target = int(target.duplicated(subset=keys).sum())

    # 3. Row presence: outer merge, indicator=True says which side each row came from
    merged = source.merge(target, on=keys, how="outer", suffixes=("_src", "_tgt"), indicator=True)
    res.only_in_source = source.merge(merged.loc[merged["_merge"] == "left_only", keys], on=keys)
    res.only_in_target = target.merge(merged.loc[merged["_merge"] == "right_only", keys], on=keys)

    # 4. Cell-by-cell comparison on rows present in both
    both = merged[merged["_merge"] == "both"]
    common = [c for c in source.columns if c in target.columns and c not in keys and c not in ignore]
    records = []
    for col in common:
        diff = both[both[f"{col}_src"] != both[f"{col}_tgt"]]
        for _, row in diff.iterrows():
            records.append({"key": "|".join(str(row[k]) for k in keys), "column": col,
                            "source_value": row[f"{col}_src"], "target_value": row[f"{col}_tgt"]})
    res.mismatches = pd.DataFrame(records, columns=["key", "column", "source_value", "target_value"])
    return res