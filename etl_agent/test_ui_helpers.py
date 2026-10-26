import io
import zipfile

import pandas as pd

from etl_agent.schema import ColumnSpec, TableSchema
from etl_agent.ui_helpers import (clean_path, frame_to_schema, safe_name,
                                  save_upload, schema_to_frame,
                                  split_names, zip_folder)


def test_split_names_accepts_commas_and_spaces():
    assert split_names("id, name  city") == ["id", "name", "city"]
    assert split_names("") == []
    assert split_names(None) == []


def test_clean_path_removes_windows_quotes():
    raw = '  "C:\\data\\my file.csv" '
    assert clean_path(raw) == "C:\\data\\my file.csv"


def test_safe_name_keeps_the_etl_naming_rule_happy():
    assert safe_name("my data (1).csv") == "my_data_1.csv"
    assert safe_name("a.b.CSV") == "a_b.csv"
    assert safe_name("###.xlsx") == "file.xlsx"


def test_save_upload_writes_bytes(tmp_path):
    path = save_upload("x y.csv", b"ID\n1\n", tmp_path / "sub")
    assert path.endswith("x_y.csv")
    assert open(path, "rb").read() == b"ID\n1\n"


def test_schema_round_trip_through_the_editor_table():
    schema = TableSchema([
        ColumnSpec("ID", "integer", False, None),
        ColumnSpec("NAME", "string", True, 20),
    ], ["ID"])
    frame = schema_to_frame(schema)
    back = frame_to_schema(frame, ["ID"])
    assert back == schema


def test_blank_or_zero_max_length_becomes_none():
    frame = pd.DataFrame({
        "column": ["A", "B"], "type": ["string", "string"],
        "nullable": [True, True],
        "max_length": pd.array([None, 0], dtype="Int64")})
    cols = frame_to_schema(frame, []).columns
    assert [c.max_length for c in cols] == [None, None]


def test_zip_folder_contains_every_file(tmp_path):
    (tmp_path / "a.csv").write_text("x")
    (tmp_path / "b.csv").write_text("y")
    data = zip_folder(tmp_path)
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert sorted(names) == ["a.csv", "b.csv"]