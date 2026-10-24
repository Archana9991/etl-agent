import sys

import pandas as pd

from etl_agent.agent import CommandRunner, DemoRunner, EtlOutcome, judge, make_verifier, run_suite
from etl_agent.cli import main
from etl_agent.generators import generate_all
from etl_agent.schema import infer_schema

GOOD = pd.DataFrame({"ID": ["1", "2", "3"], "NAME": ["Asha", "Ravi", "Meera"],
                     "JOINED": ["2024-01-05", "2024-02-10", "2024-03-15"], "NOTE": ["a", "", "c"]})


def build(tmp_path):
    src = tmp_path / "customers.csv"
    GOOD.to_csv(src, index=False)
    schema = infer_schema(GOOD)
    schema.columns[1].max_length = 10
    generate_all(str(src), schema, str(tmp_path / "td"))
    return schema, str(tmp_path / "td"), f"sqlite:///{(tmp_path / 't.db').as_posix()}"


def test_judge_all_verdicts():
    assert judge("LOAD_SUCCESS", EtlOutcome("LOADED"))[0] == "PASS"
    assert judge("LOAD_SUCCESS", EtlOutcome("REJECTED", "X"))[0] == "FAIL_VALID_FILE_REJECTED"
    assert judge("EMPTY_FILE", EtlOutcome("LOADED"))[0] == "FAIL_BAD_DATA_ACCEPTED"
    assert judge("EMPTY_FILE", EtlOutcome("REJECTED", "EMPTY_FILE"))[0] == "PASS"
    assert judge("EMPTY_FILE", EtlOutcome("REJECTED", "HEADER_MISMATCH"))[0] == "FAIL_WRONG_REASON"
    assert judge("EMPTY_FILE", EtlOutcome("ERROR", "CRASH"))[0] == "ERROR"


def test_healthy_demo_etl_passes_everything(tmp_path):
    schema, td, db = build(tmp_path)
    res = run_suite(td, DemoRunner(schema, db, "T"), make_verifier(db, "T", ["ID"]))
    assert len(res) >= 12 and (res["verdict"] == "PASS").all(), res[res.verdict != "PASS"]


def test_agent_catches_etl_that_skips_a_check(tmp_path):
    schema, td, db = build(tmp_path)
    res = run_suite(td, DemoRunner(schema, db, "T", ["DUPLICATE_PRIMARY_KEY"]))
    bad = res[res["verdict"] != "PASS"]
    assert list(bad["scenario"]) == ["duplicate_pk"]
    assert bad.iloc[0]["verdict"] == "FAIL_BAD_DATA_ACCEPTED"


def test_command_runner_reads_exit_code_and_error_code(tmp_path):
    script = tmp_path / "etl.py"
    script.write_text("import sys\nname = sys.argv[1]\n"
                      "if 'duplicate' in name:\n    print('DUPLICATE_PRIMARY_KEY found'); sys.exit(1)\n"
                      "if 'blank' in name:\n    print('something odd'); sys.exit(1)\n")
    runner = CommandRunner(f'"{sys.executable}" "{script}" {{file}}')
    _, td, _ = build(tmp_path)
    res = run_suite(td, runner).set_index("scenario")
    assert res.loc["valid_baseline", "verdict"] == "PASS"
    assert res.loc["duplicate_pk", "verdict"] == "PASS"
    assert res.loc["blank_file", "verdict"] == "FAIL_WRONG_REASON"      # UNKNOWN_REJECTION
    assert res.loc["invalid_header", "verdict"] == "FAIL_BAD_DATA_ACCEPTED"


def test_verifier_flags_unloaded_target(tmp_path):
    script = tmp_path / "noop.py"
    script.write_text("pass\n")
    _, td, db = build(tmp_path)
    res = run_suite(td, CommandRunner(f'"{sys.executable}" "{script}" {{file}}'), make_verifier(db, "MISSING", ["ID"]))
    assert res.set_index("scenario").loc["valid_baseline", "verdict"] == "FAIL_DATA_MISMATCH"


def test_cli_agent_exit_codes_and_report(tmp_path):
    src = tmp_path / "customers.csv"
    GOOD.to_csv(src, index=False)
    out = str(tmp_path / "run")
    assert main(["agent", str(src), "--pk", "ID", "--out", out]) == 0
    assert (tmp_path / "run" / "report.html").exists() and (tmp_path / "run" / "results.csv").exists()
    assert main(["agent", str(src), "--pk", "ID", "--out", out, "--demo-disable", "EMPTY_FILE"]) == 1