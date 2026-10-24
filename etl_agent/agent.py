"""The ETL testing agent: run every generated file through an ETL and judge the reaction.

Plug in ANY ETL by supplying a 'runner': a function that takes a file path and returns
an EtlOutcome. Two runners are provided:
  DemoRunner     - the built-in demo ETL (for demos and tests)
  CommandRunner  - runs any command line, e.g.  "python my_etl.py {file}"
"""
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from html import escape
from pathlib import Path

import pandas as pd

from .comparator import compare_dataframes
from .db_compare import read_table
from .etl_demo import EtlRejected, demo_etl
from .readers import read_file

KNOWN_CODES = ["INVALID_FILE_NAME", "EMPTY_FILE", "COLUMN_COUNT_MISMATCH", "HEADER_MISMATCH",
               "NO_DATA_ROWS", "NULL_IN_NOT_NULL_COLUMN", "INVALID_DATA_TYPE",
               "VALUE_TOO_LONG", "DUPLICATE_PRIMARY_KEY"]


@dataclass
class EtlOutcome:
    status: str          # LOADED | REJECTED | ERROR
    code: str = ""
    message: str = ""


class DemoRunner:
    def __init__(self, schema, db_url, table, disabled_checks=()):
        self.schema, self.db_url, self.table = schema, db_url, table
        self.disabled = tuple(disabled_checks)

    def __call__(self, file: str) -> EtlOutcome:
        try:
            n = demo_etl(file, self.schema, self.db_url, self.table, self.disabled)
            return EtlOutcome("LOADED", message=f"{n} rows loaded")
        except EtlRejected as exc:
            return EtlOutcome("REJECTED", exc.code, exc.message)
        except Exception as exc:  # the ETL itself crashed
            return EtlOutcome("ERROR", "CRASH", str(exc))


class CommandRunner:
    """Exit code 0 = file loaded. Non-zero = rejected; the reason is the first known
    error code found in the command's output (otherwise UNKNOWN_REJECTION)."""

    def __init__(self, template: str, timeout: int = 300):
        self.template, self.timeout = template, timeout

    def __call__(self, file: str) -> EtlOutcome:
        tokens = [t.strip('"') for t in shlex.split(self.template, posix=(os.name != "nt"))]
        args = [t.replace("{file}", file) for t in tokens]    # no shell=True: safer
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            return EtlOutcome("ERROR", "TIMEOUT", f"No result after {self.timeout}s")
        except OSError as exc:
            return EtlOutcome("ERROR", "CANNOT_RUN", str(exc))
        text = proc.stdout + proc.stderr
        if proc.returncode == 0:
            return EtlOutcome("LOADED", message=text.strip()[:200])
        code = next((c for c in KNOWN_CODES if c in text), "UNKNOWN_REJECTION")
        return EtlOutcome("REJECTED", code, text.strip()[:200])


def judge(expected: str, o: EtlOutcome):
    """Compare what the ETL did with what it should have done."""
    if o.status == "ERROR":
        return "ERROR", f"{o.code}: {o.message}"
    if expected == "LOAD_SUCCESS":
        if o.status == "LOADED":
            return "PASS", "Valid file loaded"
        return "FAIL_VALID_FILE_REJECTED", f"Valid file rejected with {o.code}: {o.message}"
    if o.status == "LOADED":
        return "FAIL_BAD_DATA_ACCEPTED", f"ETL loaded a file that should fail with {expected}"
    if o.code == expected:
        return "PASS", f"Rejected with {o.code}"
    return "FAIL_WRONG_REASON", f"Expected {expected}, got {o.code}"


def make_verifier(db_url: str, table: str, keys: list):
    """After a valid file loads, reuse Phase 1 to check the target table equals the file."""
    def verify(file: str):
        try:
            res = compare_dataframes(read_file(file), read_table(db_url, table), keys)
        except Exception as exc:
            return False, f"Could not verify target table: {exc}"
        return res.passed, res.summary().replace("\n", " | ")
    return verify


def run_suite(test_dir: str, runner, verify=None) -> pd.DataFrame:
    manifest = pd.read_csv(Path(test_dir) / "manifest.csv", dtype=str, keep_default_na=False)
    rows = []
    for _, m in manifest.iterrows():
        start = time.perf_counter()
        outcome = runner(str(Path(test_dir) / m["file"]))
        verdict, detail = judge(m["expected_failure"], outcome)
        if verdict == "PASS" and m["expected_failure"] == "LOAD_SUCCESS" and verify:
            ok, msg = verify(str(Path(test_dir) / m["file"]))
            if not ok:
                verdict, detail = "FAIL_DATA_MISMATCH", msg
        rows.append({"scenario": m["scenario"], "file": m["file"], "expected": m["expected_failure"],
                     "actual": f"{outcome.status} {outcome.code}".strip(), "verdict": verdict,
                     "detail": detail, "seconds": round(time.perf_counter() - start, 3)})
    return pd.DataFrame(rows)


def write_agent_report(results: pd.DataFrame, out_dir: str) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    results.to_csv(out / "results.csv", index=False)
    passed = int((results["verdict"] == "PASS").sum())
    rows = "".join(
        f"<tr class='{'ok' if r.verdict == 'PASS' else 'bad'}'>"
        + "".join(f"<td>{escape(str(v))}</td>" for v in r)
        + "</tr>"
        for r in results.itertuples(index=False))
    head = "".join(f"<th>{escape(c)}</th>" for c in results.columns)
    (out / "report.html").write_text(
        "<html><head><meta charset='utf-8'><title>ETL Agent Report</title><style>"
        "body{font-family:sans-serif;margin:2em}table{border-collapse:collapse}"
        "td,th{border:1px solid #ccc;padding:6px 10px;text-align:left}"
        ".ok{background:#e6f4ea}.bad{background:#fce8e6}</style></head><body>"
        f"<h1>ETL Agent Report</h1><p><b>{passed} of {len(results)} scenarios passed.</b></p>"
        f"<table><tr>{head}</tr>{rows}</table></body></html>", encoding="utf-8")
    return out / "report.html"