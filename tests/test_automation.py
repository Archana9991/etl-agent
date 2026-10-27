import pandas as pd

from etl_agent.cli import main
from etl_agent.db_compare import read_table
from etl_agent.sqlgen import create_table_sql, get_dialect, select_expected_sql
from conftest import execute


def common(mapping_file):
    return [mapping_file, "--source-table", "CUSTOMERS_RAW",
            "--target-table", "DIM_CUSTOMER"]


def test_mapping_sql_writes_three_files(tmp_path, mapping_file, capsys):
    out = tmp_path / "sql"
    assert main(["mapping-sql", *common(mapping_file), "--dialect", "oracle",
                 "--out", str(out)]) == 0
    assert (out / "create_target.sql").read_text().startswith("CREATE TABLE")
    assert "MINUS" in (out / "checks.sql").read_text()
    assert "checks generated" in capsys.readouterr().out


def test_bad_mapping_stops_with_exit_code_2(tmp_path, capsys):
    bad = tmp_path / "bad.csv"
    bad.write_text("TARGET_COLUMN,TRANSFORMATION\nA,MAGIC\n")
    assert main(["mapping-sql", str(bad), "--source-table", "S",
                 "--target-table", "T"]) == 2
    assert "MAGIC" in capsys.readouterr().out


def test_unknown_dialect_stops_with_exit_code_2(tmp_path, mapping_file):
    assert main(["mapping-sql", *common(mapping_file), "--dialect", "db2",
                 "--out", str(tmp_path / "o")]) == 2


def test_build_target_writes_file_and_table(tmp_path, mapping_file, source_csv, db_url):
    out = tmp_path / "bt"
    code = main(["build-target", *common(mapping_file), "--source", source_csv,
                 "--db-url", db_url, "--out", str(out)])
    assert code == 0
    csv = pd.read_csv(out / "expected_target.csv", dtype=str, keep_default_na=False)
    assert list(csv["FULL_NAME"])[0] == "Asha Rao"
    assert len(read_table(db_url, "DIM_CUSTOMER")) == 4


def test_build_target_can_read_the_source_from_a_database(tmp_path, mapping_file, db_url):
    code = main(["build-target", *common(mapping_file), "--source-url", db_url,
                 "--out", str(tmp_path / "bt")])
    assert code == 0


def test_sensitive_source_stops_everything(tmp_path, mapping_file, source_df, capsys):
    risky = source_df.assign(AADHAAR_NO=["a", "b", "c", "d"])
    path = tmp_path / "risky.csv"
    risky.to_csv(path, index=False)
    out = tmp_path / "bt"
    code = main(["build-target", *common(mapping_file), "--source", str(path),
                 "--out", str(out)])
    assert code == 3
    assert not out.exists()                          # nothing was written
    assert "AADHAAR_NO" in capsys.readouterr().out
    assert main(["build-target", *common(mapping_file), "--source", str(path),
                 "--out", str(out), "--allow-sensitive"]) == 0


def test_etl_check_passes_then_fails_when_the_etl_is_wrong(
        tmp_path, mapping_file, source_csv, db_url, capsys):
    main(["build-target", *common(mapping_file), "--source", source_csv,
          "--db-url", db_url, "--out", str(tmp_path / "bt")])
    argv = ["etl-check", *common(mapping_file), "--source", source_csv,
            "--target-url", db_url, "--run-sql", "--out", str(tmp_path / "chk")]
    assert main(argv) == 0
    assert (tmp_path / "chk" / "etl_checks.html").exists()

    execute(db_url, "UPDATE DIM_CUSTOMER SET CITY = 'pune' WHERE ID = 1")
    assert main(argv) == 1
    out = capsys.readouterr().out
    assert "sql:source_minus_target" in out and "FAIL" in out


def test_etl_check_against_a_target_file(tmp_path, mapping_file, source_csv):
    main(["build-target", *common(mapping_file), "--source", source_csv,
          "--out", str(tmp_path / "bt")])
    argv = ["etl-check", *common(mapping_file), "--source", source_csv,
            "--target", str(tmp_path / "bt" / "expected_target.csv"),
            "--out", str(tmp_path / "chk")]
    assert main(argv) == 0


def test_run_sql_without_a_target_url_is_a_setup_error(tmp_path, mapping_file, source_csv):
    argv = ["etl-check", *common(mapping_file), "--source", source_csv,
            "--target", source_csv, "--run-sql", "--out", str(tmp_path / "c")]
    assert main(argv) == 2


def test_missing_source_file_is_a_setup_error(tmp_path, mapping_file, capsys):
    argv = ["etl-check", *common(mapping_file), "--source", str(tmp_path / "no.csv"),
            "--target", str(tmp_path / "no.csv")]
    assert main(argv) == 2
    assert "STOPPED" in capsys.readouterr().out