import os

import pandas as pd
import pytest

from etl_agent.generators import generate_all
from etl_agent.readers import read_file
from etl_agent.schema import TableSchema, infer_dtype, infer_schema

GOOD = pd.DataFrame({"ID": ["1", "2", "3"], "NAME": ["Asha", "Ravi", "Meera"],
                     "JOINED": ["2024-01-05", "2024-02-10", "2024-03-15"], "NOTE": ["a", "", "c"]})


def test_infer_dtype():
    assert infer_dtype(["1", "2"]) == "integer"
    assert infer_dtype(["1", "2.5"]) == "decimal"
    assert infer_dtype(["2024-01-05"]) == "date"
    assert infer_dtype(["a", "1"]) == "string"
    assert infer_dtype(["", ""]) == "string"


def test_infer_schema_pk_and_nullability():
    s = infer_schema(GOOD)
    assert s.primary_key == ["ID"]
    by = {c.name: c for c in s.columns}
    assert by["NOTE"].nullable and not by["NAME"].nullable and not by["ID"].nullable
    assert by["JOINED"].dtype == "date"


def test_schema_json_roundtrip(tmp_path):
    s = infer_schema(GOOD)
    s.save(tmp_path / "s.json")
    assert TableSchema.load(tmp_path / "s.json") == s


@pytest.mark.parametrize("ext", [".csv", ".xlsx"])
def test_every_generated_file_breaks_its_rule(tmp_path, ext):
    src = tmp_path / f"customers{ext}"
    (GOOD.to_csv if ext == ".csv" else GOOD.to_excel)(src, index=False)
    schema = infer_schema(GOOD)
    schema.columns[1].max_length = 10
    m = generate_all(str(src), schema, str(tmp_path / "out"))
    out = tmp_path / "out"
    files = dict(zip(m["scenario"], m["file"]))

    assert list(read_file(str(out / files["invalid_header"])).columns)[0] == "ID_X"
    assert os.path.getsize(out / files["blank_file"]) == 0 or ext == ".xlsx"
    assert read_file(str(out / files["header_only"])).empty
    assert " " in files["invalid_file_name"]
    assert read_file(str(out / files["duplicate_pk"]))["ID"].duplicated().sum() == 1
    assert read_file(str(out / files["null_NAME"])).loc[0, "NAME"] == ""
    assert "NOTE" not in read_file(str(out / files["missing_column"])).columns
    assert "UNEXPECTED_COL" in read_file(str(out / files["extra_column"])).columns
    assert read_file(str(out / files["bad_type_ID"])).loc[0, "ID"] == "ABC"
    assert len(read_file(str(out / files["too_long_NAME"])).loc[0, "NAME"]) == 11
    assert (out / "manifest.csv").exists()
    assert read_file(str(out / files["valid_baseline"])).equals(read_file(str(src)))


def test_generate_xml(tmp_path):
    src = tmp_path / "c.xml"
    GOOD.to_xml(src, index=False, root_name="data", row_name="record")
    m = generate_all(str(src), infer_schema(GOOD), str(tmp_path / "o"))
    assert "header_only" not in set(m["scenario"])
    assert len(m) > 8


def test_empty_source_rejected(tmp_path):
    src = tmp_path / "e.csv"
    GOOD.iloc[0:0].to_csv(src, index=False)
    with pytest.raises(ValueError):
        generate_all(str(src), infer_schema(GOOD), str(tmp_path / "o"))