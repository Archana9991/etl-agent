"""Safety net: spot columns that look like sensitive identifiers.

Run BEFORE any test file is generated, because the generators copy
real rows into new files. Only column names and pattern names are
reported - never the values themselves.

This is a safety net, not a guarantee. Always prefer a masked or
synthetic extract of production data.
"""
import re

import pandas as pd

# Whole words inside a column name (split on _ - space, etc.)
SENSITIVE_WORDS = {
    "ssn", "aadhaar", "aadhar", "pan", "passport", "cvv", "pii",
    "mrn", "diagnosis",
}
# Long, unambiguous fragments searched inside the compacted name.
SENSITIVE_FRAGMENTS = (
    "aadhaar", "aadhar", "passport", "licence", "license",
    "creditcard", "debitcard", "cardnumber", "cardno", "patient",
    "socialsecurity",
)
VALUE_PATTERNS = {
    "PAN-like value": re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$"),
    "Aadhaar-like value": re.compile(r"^\d{4}\s\d{4}\s\d{4}$"),
    "card-number-like value": re.compile(r"^\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{4}$"),
    "SSN-like value": re.compile(r"^\d{3}-\d{2}-\d{4}$"),
}


def _name_hint(column: str):
    lowered = column.lower()
    words = set(re.split(r"[^a-z0-9]+", lowered))
    compact = re.sub(r"[^a-z0-9]", "", lowered)
    if words & SENSITIVE_WORDS:
        return "column name looks sensitive"
    if any(f in compact for f in SENSITIVE_FRAGMENTS):
        return "column name looks sensitive"
    return None


def find_sensitive(df: pd.DataFrame, max_rows: int = 2000):
    """Return [(column, reason)]. Values are never included."""
    hits = []
    head = df.head(max_rows)
    for col in df.columns:
        reason = _name_hint(str(col))
        if reason is None:
            values = head[col].astype(str)
            for label, pattern in VALUE_PATTERNS.items():
                if values.str.match(pattern).any():
                    reason = f"contains {label}s"
                    break
        if reason:
            hits.append((str(col), reason))
    return hits