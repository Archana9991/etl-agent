"""Readers: turn CSV / Excel / XML files into a pandas DataFrame.

Design idea: every format is converted to the SAME shape (a DataFrame),
so the comparison code never needs to know where the data came from.
This is called "normalising" and is the core pattern of ETL.
"""
from pathlib import Path

import pandas as pd


def read_csv(path: str) -> pd.DataFrame:
    # dtype=str keeps "007" as "007" instead of turning it into the number 7.
    # keep_default_na=False keeps empty cells as "" so we can detect
    # blanks ourselves.
    opts = dict(dtype=str, keep_default_na=False)
    try:
        return pd.read_csv(path, encoding="utf-8-sig", **opts)
    except UnicodeDecodeError:
        # Many Windows / Excel exports are cp1252, not UTF-8.
        return pd.read_csv(path, encoding="cp1252", **opts)


def read_excel(path: str, sheet_name=0) -> pd.DataFrame:
    return pd.read_excel(path, sheet_name=sheet_name, dtype=str,
                         keep_default_na=False)


def read_xml(path: str, xpath: str = ".//*[count(*)=0]/..") -> pd.DataFrame:
    """Read repeating XML records into rows.

    The default xpath means: "find every element that directly contains
    leaf elements" (e.g. each <record> holding <id>, <name>...). Pass
    your own xpath if your XML is structured differently.
    """
    return pd.read_xml(path, xpath=xpath, dtype=str)


READERS = {
    ".csv": read_csv,
    ".xlsx": read_excel,
    ".xls": read_excel,
    ".xml": read_xml,
}


def read_file(path: str) -> pd.DataFrame:
    """Pick the right reader from the file extension."""
    ext = Path(path).suffix.lower()
    if ext not in READERS:
        raise ValueError(
            f"Unsupported file type '{ext}'. Supported: {sorted(READERS)}")
    df = READERS[ext](path)
    # Normalise: strip whitespace from headers and cell values,
    # and fill XML gaps with "".
    df.columns = [str(c).strip() for c in df.columns]
    return df.fillna("").apply(lambda col: col.astype(str).str.strip())