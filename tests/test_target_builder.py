import pandas as pd
import pytest

from etl_agent.comparator import compare_dataframes
from etl_agent.db_compare import read_table
from etl_agent.mapping import ColumnMap, Mapping
from etl_agent.sqlgen import get_dialect, select_expected_sql
from etl_agent.target_builder import (apply_transformation,
                                      build_expected_target, load_target)
from conftest import run_sql


def apply(transformation, values, parameter="", extra=None):
    data = {"C": values}
    cols = ["C"]
    if extra:
        data["D"] = extra
        cols = ["C", "D"]
    cm = ColumnMap("T", cols if transformation == "CONCAT" else (
        [] if transformation == "CONSTANT" else ["C"]), transformation, parameter)
    return list(apply_transformation(pd.DataFrame(data), cm))


def test_each_rule_in_pandas():
    assert apply("TRIM", ["  a ", "b"]) == ["a", "b"]
    assert apply("UPPER", ["ab"]) == ["AB"]
    assert apply("LOWER", ["AB"]) == ["ab"]
    assert apply("CONSTANT", ["x", "y"], "IN") == ["IN", "IN"]
    assert apply("DEFAULT", ["a", "", "  "], "NA") == ["a", "NA", "NA"]
    assert apply("SUBSTRING", ["ABCDEF"], "2:3") == ["BCD"]
    assert apply("SUBSTRING", ["AB"], "2:5") == ["B"]          # short value is fine
    assert apply("LOOKUP", ["M", "F", "X"], "M=Male;F=Female") == ["Male", "Female", "X"]
    assert apply("CONCAT", ["a", "b"], "-", extra=["x", ""]) == ["a-x", "b-"]


def test_source_column_names_ignore_case():
    cm = ColumnMap("T", ["name"], "UPPER")
    assert list(apply_transformation(pd.DataFrame({"NAME": ["a"]}), cm)) == ["A"]


def test_missing_source_column_is_a_clear_error(mapping):
    with pytest.raises(ValueError, match="not found"):
        build_expected_target(pd.DataFrame({"CUST_ID": ["1"]}), mapping)


def test_expected_target_for_the_sample(mapping, source_df):
    expected = build_expected_target(source_df, mapping)
    assert list(expected.columns) == mapping.target_columns
    row = expected[expected["ID"] == "1"].iloc[0]
    assert row["FULL_NAME"] == "Asha Rao"
    assert row["CITY"] == "PUNE" and row["GENDER"] == "Female"
    assert row["COUNTRY"] == "IN" and row["REGION_CODE"] == "MH"
    assert list(expected["STATE"]) == ["MH", "UNKNOWN", "UNKNOWN", "GJ"]
    assert expected.loc[3, "GENDER"] == "X"          # not in the lookup


def test_pandas_and_generated_sql_agree(db_url, mapping):
    """The two independent implementations of the rules must give the same
    answer. If they ever differ, one of them has a bug."""
    sql_result = run_sql(db_url, select_expected_sql(mapping, get_dialect("ansi")))
    pandas_result = build_expected_target(read_table(db_url, "CUSTOMERS_RAW"), mapping)
    res = compare_dataframes(pandas_result, sql_result, ["ID"], normalize=True)
    assert res.passed, res.summary()


def test_load_target_creates_and_fills_the_table(db_url, mapping, source_df):
    expected = build_expected_target(source_df, mapping)
    assert load_target(db_url, mapping, expected) == 4
    loaded = read_table(db_url, "DIM_CUSTOMER")
    assert compare_dataframes(expected, loaded, ["ID"], normalize=True).passed


def test_load_target_replaces_an_existing_table(db_url, mapping, source_df):
    expected = build_expected_target(source_df, mapping)
    load_target(db_url, mapping, expected)
    load_target(db_url, mapping, expected)           # second run must not fail
    assert len(read_table(db_url, "DIM_CUSTOMER")) == 4
    with pytest.raises(ValueError, match="already exists"):
        from etl_agent.target_builder import create_target_table
        create_target_table(db_url, mapping, replace=False)


def test_database_enforces_the_mappings_constraints(db_url, mapping, source_df):
    bad = build_expected_target(source_df, mapping)
    bad.loc[0, "FULL_NAME"] = ""                      # NOT NULL column
    with pytest.raises(Exception):
        load_target(db_url, mapping, bad)


def test_non_numeric_value_in_a_number_column_is_refused(db_url, mapping, source_df):
    bad = build_expected_target(source_df, mapping)
    bad.loc[0, "SALARY"] = "lots"
    with pytest.raises(ValueError, match="non-numeric"):
        load_target(db_url, mapping, bad)


def test_unsafe_mapping_cannot_reach_the_database(db_url, source_df):
    evil = Mapping("RAW", "T; DROP TABLE CUSTOMERS_RAW", [ColumnMap("A", ["A"])])
    with pytest.raises(ValueError, match="unsafe"):
        load_target(db_url, evil, pd.DataFrame({"A": ["1"]}))