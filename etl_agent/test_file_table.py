import sqlite3

import pandas as pd
import pytest

from etl_agent.cli import main
from etl_agent.comparator import compare_dataframes, normalize_value
from etl_agent.db_compare import compare_file_to_table


@pytest.mark.parametrize("raw, expected", [
    ("139913.4", "139913.4"),
    ("139913.40", "139913.4"),
    ("139913.400", "139913.4"),
    ("100", "100"),
    ("100.00", "100"),
    ("-5.50", "-5.5"),
    ("-0", "0"),
    ("0.50", "0.5"),
    ("2020-04-18 00:00:00", "2020-04-18"),
    ("2020-04-18T00:00:00.000", "2020-04-18"),
    ("2020-04-18 10:30:00", "2020-04-18 10:30:00"),
    ("007", "007"),
    ("00.5", "00.5"),
    ("abc", "abc"),
    ("", ""),
])
def test_normalize_value(raw, expected):
    assert normalize_value(raw) == expected


def frame(**cols):
    return pd.DataFrame(cols)


def test_normalize_hides_only_formatting_differences():
    src = frame(ID=["1", "2"], SALARY=["139913.4", "61276.2"])
    tgt = frame(ID=["1", "2"], SALARY=["139913.40", "61276.20"])
    assert not compare_dataframes(src, tgt, ["ID"]).passed
    assert compare_dataframes(src, tgt, ["ID"], normalize=True).passed


def test_normalize_still_catches_real_value_changes():
    src = frame(ID=["1"], SALARY=["139913.4"])
    tgt = frame(ID=["1"], SALARY=["139913.5"])
    assert not compare_dataframes(src, tgt, ["ID"], normalize=True).passed


def test_normalize_does_not_merge_leading_zero_codes():
    src = frame(ID=["1"], CODE=["007"])
    tgt = frame(ID=["1"], CODE=["7"])
    res = compare_dataframes(src, tgt, ["ID"], normalize=True)
    assert len(res.mismatches) == 1


def test_date_equals_midnight_timestamp_only():
    src = frame(ID=["1", "2"], D=["2020-04-18", "2020-04-18"])
    tgt = frame(ID=["1", "2"],
                D=["2020-04-18 00:00:00", "2020-04-18 09:00:00"])
    res = compare_dataframes(src, tgt, ["ID"], normalize=True)
    assert list(res.mismatches["key"]) == ["2"]


def make_target(tmp_path, rows):
    path = tmp_path / "target.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE customers "
                 "(id INTEGER, name TEXT, salary TEXT, age INTEGER)")
    conn.executemany("INSERT INTO customers VALUES (?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return f"sqlite:///{path.as_posix()}"


def make_csv(tmp_path):
    path = tmp_path / "source.csv"
    path.write_text("id,name,salary,age\n"
                    "1,Asha,139913.4,49\n"
                    "2,Ravi,61276.2,\n"
                    "3,Meera,500,30\n", encoding="utf-8")
    return str(path)


LOADED = [(1, "Asha", "139913.40", 49),
          (2, "Ravi", "61276.20", None),
          (3, "Meera", "500.00", 30)]


def test_file_vs_table_passes_despite_formatting(tmp_path):
    url = make_target(tmp_path, LOADED)
    res = compare_file_to_table(make_csv(tmp_path), url, "customers",
                                ["id"], normalize=True)
    assert res.passed          # blank in file == NULL in table


def test_exact_text_mode_shows_the_formatting_noise(tmp_path):
    url = make_target(tmp_path, LOADED)
    res = compare_file_to_table(make_csv(tmp_path), url, "customers",
                                ["id"], normalize=False)
    assert len(res.mismatches) == 3


def test_file_vs_table_finds_real_etl_defects(tmp_path):
    broken = [(1, "Asha", "139913.40", 49),
              (2, "Rav", "61276.20", None)]          # row 3 lost, name cut
    url = make_target(tmp_path, broken)
    res = compare_file_to_table(make_csv(tmp_path), url, "customers",
                                ["id"], normalize=True)
    assert list(res.only_in_source["ID"]) == ["3"]
    assert list(res.mismatches["column"]) == ["NAME"]


def test_cli_file_table_exit_codes(tmp_path, monkeypatch):
    monkeypatch.setenv("T_DB", make_target(tmp_path, LOADED))
    out = str(tmp_path / "ok")
    argv = ["file-table", make_csv(tmp_path), "env:T_DB", "customers",
            "--keys", "id", "--out", out]
    assert main(argv) == 0
    assert main(argv[:-2] + ["--exact-text", "--out", out]) == 1


def test_cli_file_table_reports_problems_clearly(tmp_path, capsys):
    url = make_target(tmp_path, LOADED)
    argv = ["file-table", str(tmp_path / "missing.csv"), url,
            "customers", "--keys", "id"]
    assert main(argv) == 2
    assert "STOPPED" in capsys.readouterr().out


def test_normalize_flag_on_files_command(tmp_path):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    a.write_text("id,salary\n1,10.5\n", encoding="utf-8")
    b.write_text("id,salary\n1,10.50\n", encoding="utf-8")
    base = ["files", str(a), str(b), "--keys", "id",
            "--out", str(tmp_path / "r")]
    assert main(base) == 1
    assert main(base + ["--normalize"]) == 0