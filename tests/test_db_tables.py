import sqlite3

import pandas as pd

from etl_agent.cli import main
from etl_agent.db_compare import check_connection, compare_tables, read_table


def make_db(tmp_path, tables):
    """tables = {name: [(id, age, city), ...]}"""
    path = tmp_path / "test.db"
    conn = sqlite3.connect(path)
    for name, rows in tables.items():
        conn.execute(f"CREATE TABLE {name} "
                     "(id INTEGER, age INTEGER, city TEXT)")
        conn.executemany(f"INSERT INTO {name} VALUES (?,?,?)", rows)
    conn.commit()
    conn.close()
    return f"sqlite:///{path.as_posix()}"


SRC = [(1, 49, "Pune"), (2, 30, "Delhi"), (3, None, "Goa"),
       (4, 25, "Agra"), (5, 61, "Surat")]
TGT = [(1, None, "Pune"), (2, 30, "Mumbai"), (3, None, "Goa"),
       (4, 25, "Agra"), (6, 22, "Kochi")]


def test_two_tables_in_one_database(tmp_path):
    url = make_db(tmp_path, {"src": SRC, "tgt": TGT})
    res = compare_tables(url, url, "src", ["id"], tgt_table="tgt")
    assert not res.passed
    assert list(res.only_in_source["ID"]) == ["5"]
    assert list(res.only_in_target["ID"]) == ["6"]
    found = set(zip(res.mismatches["key"], res.mismatches["column"]))
    assert found == {("1", "AGE"), ("2", "CITY")}


def test_equal_row_counts_do_not_hide_differences(tmp_path):
    url = make_db(tmp_path, {"src": SRC, "tgt": TGT})
    res = compare_tables(url, url, "src", ["id"], tgt_table="tgt")
    assert res.source_rows == res.target_rows == 5
    assert not res.passed


def test_null_integer_is_not_shown_as_float(tmp_path):
    url = make_db(tmp_path, {"src": SRC})
    df = read_table(url, "src")
    assert list(df["age"]) == ["49", "30", "", "25", "61"]


def test_identical_tables_pass_even_with_nulls(tmp_path):
    url = make_db(tmp_path, {"a": SRC, "b": SRC})
    assert compare_tables(url, url, "a", ["id"], tgt_table="b").passed


def test_cli_with_env_urls_and_tgt_table(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_DB_URL", make_db(tmp_path, {"s": SRC, "t": TGT}))
    out = str(tmp_path / "rep")
    argv = ["tables", "env:TEST_DB_URL", "env:TEST_DB_URL", "s",
            "--tgt-table", "t", "--keys", "id", "--out", out]
    assert main(argv) == 1
    sheets = pd.ExcelFile(tmp_path / "rep" / "comparison_report.xlsx")
    assert "Value_mismatches" in sheets.sheet_names


def test_cli_missing_env_variable_is_a_clear_error(monkeypatch, capsys):
    monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
    argv = ["tables", "env:NOT_SET_ANYWHERE", "env:NOT_SET_ANYWHERE",
            "t", "--keys", "id"]
    assert main(argv) == 2
    assert "NOT_SET_ANYWHERE" in capsys.readouterr().out


def test_db_check_lists_tables(tmp_path, capsys):
    url = make_db(tmp_path, {"src": SRC, "tgt": TGT})
    assert main(["db-check", url]) == 0
    out = capsys.readouterr().out
    assert "Connected OK" in out and "src" in out and "tgt" in out


def test_db_check_reports_a_bad_connection(capsys):
    bad = "sqlite:////nonexistent_folder_xyz/none.db"
    assert main(["db-check", bad]) == 2
    assert "could not connect" in capsys.readouterr().out
    assert check_connection is not None


def test_unsafe_table_name_still_blocked(tmp_path):
    url = make_db(tmp_path, {"src": SRC})
    argv = ["tables", url, url, "src; DROP TABLE src", "--keys", "id"]
    assert main(argv) == 2
    assert len(read_table(url, "src")) == 5       # table untouched