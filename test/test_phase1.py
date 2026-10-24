import pandas as pd
import pytest
from sqlalchemy import create_engine
from etl_agent.cli import main
from etl_agent.comparator import compare_dataframes
from etl_agent.db_compare import compare_tables
from etl_agent.readers import read_file

BASE = pd.DataFrame({"ID": ["1", "2", "3"], "NAME": ["A", "B", "C"]})


def test_identical_frames_pass():
    assert compare_dataframes(BASE, BASE.copy(), ["ID"]).passed


def test_detects_value_mismatch():
    tgt = BASE.copy(); tgt.loc[1, "NAME"] = "X"
    res = compare_dataframes(BASE, tgt, ["ID"])
    assert not res.passed and res.mismatches.iloc[0]["column"] == "NAME"


def test_detects_missing_and_extra_rows():
    tgt = pd.concat([BASE.iloc[:2], pd.DataFrame({"ID": ["9"], "NAME": ["Z"]})])
    res = compare_dataframes(BASE, tgt, ["ID"])
    assert list(res.only_in_source["ID"]) == ["3"]
    assert list(res.only_in_target["ID"]) == ["9"]


def test_detects_schema_difference():
    tgt = BASE.drop(columns=["NAME"]).assign(EXTRA="x")
    res = compare_dataframes(BASE, tgt, ["ID"])
    assert res.missing_columns == ["NAME"] and res.extra_columns == ["EXTRA"]


def test_detects_duplicate_keys():
    tgt = pd.concat([BASE, BASE.iloc[[0]]])
    assert compare_dataframes(BASE, tgt, ["ID"]).duplicate_keys_target == 1


def test_column_case_ignored():
    assert compare_dataframes(BASE, BASE.rename(columns={"ID": "id", "NAME": "name"}), ["ID"]).passed


def test_missing_key_column_raises():
    with pytest.raises(ValueError):
        compare_dataframes(BASE, BASE, ["NOPE"])


def test_csv_vs_excel_vs_xml_match():
    csv = read_file("sample_data/source.csv")
    for other in ("sample_data/target_same.xlsx", "sample_data/target_same.xml"):
        assert compare_dataframes(csv, read_file(other), ["ID"]).passed, other


def test_unsupported_extension():
    with pytest.raises(ValueError):
        read_file("data.json")


def test_two_db_environments(tmp_path):
    dev, qa = f"sqlite:///{tmp_path/'dev.db'}", f"sqlite:///{tmp_path/'qa.db'}"
    BASE.to_sql("CUSTOMERS", create_engine(dev), index=False)
    qa_df = BASE.copy(); qa_df.loc[0, "NAME"] = "CHANGED"
    qa_df.to_sql("CUSTOMERS", create_engine(qa), index=False)
    res = compare_tables(dev, qa, "CUSTOMERS", ["ID"])
    assert not res.passed and len(res.mismatches) == 1


def test_sql_injection_in_table_name_blocked(tmp_path):
    url = f"sqlite:///{tmp_path/'x.db'}"
    with pytest.raises(ValueError):
        compare_tables(url, url, "T; DROP TABLE T", ["ID"])


def test_cli_exit_codes_and_report(tmp_path):
    out = str(tmp_path / "rep")
    assert main(["files", "sample_data/source.csv", "sample_data/target_same.xml", "--keys", "ID", "--out", out]) == 0
    assert main(["files", "sample_data/source.csv", "sample_data/target_diff.xlsx", "--keys", "ID", "--out", out]) == 1
    assert (tmp_path / "rep" / "comparison_report.xlsx").exists()