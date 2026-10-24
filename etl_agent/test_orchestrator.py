import json
import sys

import pandas as pd
import pytest

from etl_agent.agent import CommandRunner, load_code_map
from etl_agent.orchestrator import AgentConfig, ETLAgent, resolve_db_url
from etl_agent.sensitive import find_sensitive

GOOD = pd.DataFrame({
    "ID": ["1", "2", "3"],
    "NAME": ["Asha", "Ravi", "Meera"],
    "CITY": ["Pune", "Delhi", "Chennai"],
})


def make_source(tmp_path, df=GOOD, name="customers.csv"):
    path = tmp_path / name
    df.to_csv(path, index=False)
    return str(path)


def quiet(lines):
    return lambda text: lines.append(str(text))


def etl_script(tmp_path, body):
    script = tmp_path / "etl.py"
    script.write_text(body)
    return f'"{sys.executable}" "{script}" {{file}}'


def test_agent_class_runs_as_a_library(tmp_path):
    cfg = AgentConfig(source=make_source(tmp_path),
                      out_dir=str(tmp_path / "run"), primary_key=["ID"])
    result = ETLAgent(cfg, log=quiet([])).run()
    assert result.exit_code == 0
    assert len(result.results) >= 12
    assert result.report.exists()


def test_failure_gives_exit_code_1(tmp_path):
    cfg = AgentConfig(source=make_source(tmp_path),
                      out_dir=str(tmp_path / "run"), primary_key=["ID"],
                      demo_disable=["EMPTY_FILE"])
    assert ETLAgent(cfg, log=quiet([])).run().exit_code == 1


def test_sample_rows_limits_the_test_files(tmp_path):
    big = pd.DataFrame({"ID": [str(i) for i in range(50)],
                        "NAME": [f"n{i}" for i in range(50)]})
    cfg = AgentConfig(source=make_source(tmp_path, big),
                      out_dir=str(tmp_path / "run"),
                      primary_key=["ID"], sample_rows=5)
    result = ETLAgent(cfg, log=quiet([])).run()
    sample = pd.read_csv(tmp_path / "run" / "sample_source.csv")
    assert len(sample) == 5
    assert result.exit_code == 0


def test_sensitive_column_name_stops_the_run(tmp_path):
    df = GOOD.assign(AADHAAR_NO=["a", "b", "c"])
    lines = []
    cfg = AgentConfig(source=make_source(tmp_path, df),
                      out_dir=str(tmp_path / "run"))
    result = ETLAgent(cfg, log=quiet(lines)).run()
    assert result.exit_code == 3
    assert not (tmp_path / "run" / "test_data").exists()
    assert "AADHAAR_NO" in "\n".join(lines)


def test_sensitive_value_pattern_stops_the_run_without_printing_it(tmp_path):
    df = GOOD.assign(REF=["ABCDE1234F", "x", "y"])
    lines = []
    cfg = AgentConfig(source=make_source(tmp_path, df),
                      out_dir=str(tmp_path / "run"))
    result = ETLAgent(cfg, log=quiet(lines)).run()
    assert result.exit_code == 3
    assert "ABCDE1234F" not in "\n".join(lines)


def test_allow_sensitive_overrides_the_guard(tmp_path):
    df = GOOD.assign(PASSPORT_NO=["a", "b", "c"])
    cfg = AgentConfig(source=make_source(tmp_path, df),
                      out_dir=str(tmp_path / "run"),
                      primary_key=["ID"], allow_sensitive=True)
    assert ETLAgent(cfg, log=quiet([])).run().exit_code == 0


def test_guard_has_no_false_positive_on_ordinary_names():
    df = pd.DataFrame({"COMPANY": ["a"], "SPAN_DAYS": ["1"],
                       "PANEL": ["x"], "CITY": ["Pune"], "ID": ["1"]})
    assert find_sensitive(df) == []


def test_code_map_translates_the_etls_own_wording(tmp_path):
    cmd = etl_script(tmp_path, (
        "import sys\n"
        "name = sys.argv[1]\n"
        "if 'duplicate' in name:\n"
        "    print('ORA-00001: unique constraint violated')\n"
        "    sys.exit(1)\n"))
    out = tmp_path / "td"
    cfg = AgentConfig(source=make_source(tmp_path),
                      out_dir=str(out), primary_key=["ID"])
    ETLAgent(cfg, log=quiet([])).run()      # builds the test files
    from etl_agent.agent import run_suite
    plain = run_suite(str(out / "test_data"), CommandRunner(cmd))
    mapped = run_suite(
        str(out / "test_data"),
        CommandRunner(cmd, code_map={"ORA-00001": "DUPLICATE_PRIMARY_KEY"}))
    pick = lambda df: df.set_index("scenario").loc["duplicate_pk", "verdict"]
    assert pick(plain) == "FAIL_WRONG_REASON"
    assert pick(mapped) == "PASS"


def test_reason_check_off_accepts_any_rejection(tmp_path):
    cmd = etl_script(tmp_path, (
        "import sys\n"
        "if 'valid_baseline' not in sys.argv[1]:\n"
        "    print('rejected, no code given')\n"
        "    sys.exit(1)\n"))
    base = dict(source=make_source(tmp_path), primary_key=["ID"],
                etl_command=cmd)
    strict = ETLAgent(AgentConfig(out_dir=str(tmp_path / "a"), **base),
                      log=quiet([])).run()
    relaxed = ETLAgent(AgentConfig(out_dir=str(tmp_path / "b"),
                                   strict_reason=False, **base),
                       log=quiet([])).run()
    assert strict.exit_code == 1
    assert relaxed.exit_code == 0


def test_missing_etl_gives_exit_code_2(tmp_path):
    cmd = f'"{sys.executable}" "{tmp_path / "nope.py"}" {{file}}'
    cfg = AgentConfig(source=make_source(tmp_path),
                      out_dir=str(tmp_path / "run"),
                      primary_key=["ID"], etl_command=cmd)
    assert ETLAgent(cfg, log=quiet([])).run().exit_code == 2


def test_db_url_can_come_from_environment(monkeypatch):
    monkeypatch.setenv("MY_TARGET_DB", "sqlite:///x.db")
    assert resolve_db_url("env:MY_TARGET_DB") == "sqlite:///x.db"
    assert resolve_db_url("sqlite:///plain.db") == "sqlite:///plain.db"
    monkeypatch.delenv("MY_TARGET_DB")
    with pytest.raises(ValueError):
        resolve_db_url("env:MY_TARGET_DB")


def test_code_map_file_is_validated(tmp_path):
    good = tmp_path / "ok.json"
    good.write_text(json.dumps({"ORA-00001": "DUPLICATE_PRIMARY_KEY"}))
    assert load_code_map(str(good)) == {"ORA-00001": "DUPLICATE_PRIMARY_KEY"}
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"ORA-00001": "MADE_UP"}))
    with pytest.raises(ValueError):
        load_code_map(str(bad))