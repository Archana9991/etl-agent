"""Small helpers for the web UI (app.py).

They live here, and not inside app.py, for one reason: they contain NO
Streamlit code, so pytest can test them quickly. The rule is the same
as cli.py: the screen is a thin layer, the logic is testable.
"""
import io
import re
import zipfile
from pathlib import Path

import pandas as pd

from .schema import ColumnSpec, TableSchema

DTYPES = ["string", "integer", "decimal", "date"]


def split_names(text):
    """'id, name' or 'id name' -> ['id', 'name']. Empty -> []."""
    return [p for p in re.split(r"[,\s]+", text or "") if p]


def clean_path(text):
    """Windows 'Copy as path' wraps a path in quotes. Remove them."""
    return (text or "").strip().strip('"').strip("'").strip()


def safe_name(name):
    """Make an uploaded file name safe for the ETL naming rule.

    The demo ETL only accepts letters, digits, _ and -. A name like
    'my data (1).csv' would make the GOOD baseline file fail, so we
    clean it first.
    """
    p = Path(name)
    stem = re.sub(r"[^A-Za-z0-9_\-]+", "_", p.stem).strip("_") or "file"
    return stem + p.suffix.lower()


def save_upload(name, data, folder):
    """Write uploaded bytes to disk (our readers need a real path)."""
    path = Path(folder) / safe_name(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def schema_to_frame(schema):
    """TableSchema -> a table the user can edit on screen."""
    return pd.DataFrame({
        "column": [c.name for c in schema.columns],
        "type": [c.dtype for c in schema.columns],
        "nullable": [bool(c.nullable) for c in schema.columns],
        "max_length": pd.array([c.max_length for c in schema.columns],
                               dtype="Int64"),
    })


def frame_to_schema(frame, primary_key):
    """The edited table -> TableSchema (the reverse of the above)."""
    cols = []
    for row in frame.itertuples(index=False):
        size = row.max_length
        has_size = pd.notna(size) and int(size) > 0
        cols.append(ColumnSpec(
            name=str(row.column),
            dtype=str(row.type),
            nullable=bool(row.nullable),
            max_length=int(size) if has_size else None,
        ))
    return TableSchema(cols, list(primary_key))


def zip_folder(folder):
    """All files in a folder -> bytes of one .zip (for a download)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(Path(folder).rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(folder))
    return buffer.getvalue()