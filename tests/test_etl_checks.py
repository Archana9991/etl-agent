import pandas as pd

from etl_agent.etl_checks import (check_referential_integrity, checks_passed,
                                  run_etl_checks, write_checks_report)
from etl_agent.target_builder import build_expected_target


def verdict(results, check, column=""):
    rows = results[(results["check"] == check) & (results["column"] == column)]
    assert len(rows) == 1, f"expected one row for {check}/{column}\n{results}"
    return rows.iloc[0]["status"]


def good_target(mapping, source_df):
    return build_expected_target(source_df, mapping)


def test_a_correct_target_passes_everything(mapping, source_df):
    res = run_etl_checks(source_df, good_target(mapping, source_df), mapping)
    assert checks_passed(res), res
    assert (res["status"] == "FAIL").sum() == 0


def test_formatting_noise_is_not_a_defect(mapping, source_df):
    tgt = good_target(mapping, source_df)
    tgt["SALARY"] = ["139913.40", "61276.20", "50000.00", ""]
    assert checks_passed(run_etl_checks(source_df, tgt, mapping))


def test_lost_row(mapping, source_df):
    tgt = good_target(mapping, source_df).iloc[:-1]
    res = run_etl_checks(source_df, tgt, mapping)
    assert verdict(res, "row_count") == "FAIL"
    assert verdict(res, "rows_missing_in_target") == "FAIL"
    assert not checks_passed(res)


def test_wrong_transformation_names_the_exact_column(mapping, source_df):
    tgt = good_target(mapping, source_df)
    tgt.loc[0, "CITY"] = "Pune"                       # should be PUNE
    res = run_etl_checks(source_df, tgt, mapping)
    assert verdict(res, "transformation", "CITY") == "FAIL"
    assert verdict(res, "transformation", "GENDER") == "PASS"
    detail = res[(res["check"] == "transformation") & (res["column"] == "CITY")].iloc[0]["detail"]
    assert "expected 'PUNE'" in detail and "got 'Pune'" in detail


def test_null_in_not_null_column(mapping, source_df):
    tgt = good_target(mapping, source_df)
    tgt.loc[1, "FULL_NAME"] = ""
    assert verdict(run_etl_checks(source_df, tgt, mapping), "not_null", "FULL_NAME") == "FAIL"


def test_duplicate_key(mapping, source_df):
    tgt = pd.concat([good_target(mapping, source_df)] * 2, ignore_index=True)
    assert verdict(run_etl_checks(source_df, tgt, mapping), "duplicate_key", "ID") == "FAIL"


def test_wrong_data_type_and_length(mapping, source_df):
    tgt = good_target(mapping, source_df)
    tgt.loc[0, "SALARY"] = "ABC"
    tgt.loc[1, "COUNTRY"] = "INDIA"                   # max_length is 2
    res = run_etl_checks(source_df, tgt, mapping)
    assert verdict(res, "data_type", "SALARY") == "FAIL"
    assert verdict(res, "max_length", "COUNTRY") == "FAIL"


def test_sum_reconciliation(mapping, source_df):
    tgt = good_target(mapping, source_df)
    tgt.loc[0, "SALARY"] = "100"
    assert verdict(run_etl_checks(source_df, tgt, mapping), "sum", "SALARY") == "FAIL"


def test_missing_target_column_is_reported_once_and_others_skip(mapping, source_df):
    tgt = good_target(mapping, source_df).drop(columns=["CITY"])
    res = run_etl_checks(source_df, tgt, mapping)
    assert verdict(res, "columns_present") == "FAIL"
    assert verdict(res, "transformation", "CITY") == "SKIP"
    assert not checks_passed(res)


def test_database_style_column_case_is_accepted(mapping, source_df):
    tgt = good_target(mapping, source_df)
    tgt.columns = [c.lower() for c in tgt.columns]
    assert checks_passed(run_etl_checks(source_df, tgt, mapping))


def test_referential_integrity():
    tgt = pd.DataFrame({"CITY": ["PUNE", "DELHI", "ATLANTIS", ""]})
    ref = pd.DataFrame({"NAME": ["PUNE", "DELHI"]})
    row = check_referential_integrity(tgt, "CITY", ref, "NAME")[0]
    assert row["status"] == "FAIL" and row["failing"] == 1
    assert "ATLANTIS" in row["detail"]


def test_references_are_wired_into_the_runner(mapping, source_df):
    tgt = good_target(mapping, source_df)
    ref = pd.DataFrame({"CITY_NAME": ["PUNE", "DELHI"]})      # CHENNAI missing
    res = run_etl_checks(source_df, tgt, mapping,
                         references={"CITY": (ref, "CITY_NAME")})
    assert verdict(res, "referential", "CITY") == "FAIL"


def test_report_files_are_written(tmp_path, mapping, source_df):
    res = run_etl_checks(source_df, good_target(mapping, source_df), mapping)
    html = write_checks_report(res, tmp_path / "r")
    assert html.exists() and (tmp_path / "r" / "etl_checks.csv").exists()
    assert "passed" in html.read_text()