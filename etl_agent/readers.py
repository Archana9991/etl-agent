"""Turn CSV / Excel / XML files into a pandas DataFrame.
Every format becomes the SAME shape, so comparison code never cares about the source."""
from pathlib import Path
import pandas as pd


def read_csv(path):
    # dtype=str keeps "007" as "007"; keep_default_na=False keeps blanks as ""
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def read_excel(path, sheet_name=0):
    return pd.read_excel(path, sheet_name=sheet_name, dtype=str, keep_default_na=False)


def read_xml(path, xpath=".//*[count(*)=0]/.."):
    # default xpath: every element that directly holds leaf elements (each <record>)
    return pd.read_xml(path, xpath=xpath, dtype=str)


READERS = {".csv": read_csv, ".xlsx": read_excel, ".xls": read_excel, ".xml": read_xml}


def read_file(path):
    ext = Path(path).suffix.lower()
    if ext not in READERS:
        raise ValueError(f"Unsupported file type '{ext}'. Supported: {sorted(READERS)}")
    df = READERS[ext](path)
    df.columns = [str(c).strip() for c in df.columns]
    return df.fillna("").apply(lambda col: col.astype(str).str.strip())