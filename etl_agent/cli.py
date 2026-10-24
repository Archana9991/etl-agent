"""Command line interface.

Examples:
  python -m etl_agent.cli files a.csv b.xlsx --keys ID
  python -m etl_agent.cli tables sqlite:///dev.db sqlite:///qa.db T --keys ID
  python -m etl_agent.cli infer-schema sample_data/source.csv --pk ID
  python -m etl_agent.cli generate sample_data/source.csv --pk ID
  python -m etl_agent.cli agent sample_data/source.csv --pk ID
  python -m etl_agent.cli agent sample_data/source.csv --pk ID --ai-explain
"""
import argparse
import sys
from pathlib import Path

from .agent import (CommandRunner, DemoRunner, EtlUnavailable,
                    make_verifier, run_suite, write_agent_report)
from .comparator import compare_dataframes
from .db_compare import compare_tables
from .generators import generate_all
from .llm_agent import (ClaudeClient, LLMUnavailable, explain_failures,
                        generate_ai_scenarios, suggest_scenarios)
from .readers import read_file
from .report import write_report
from .schema import TableSchema, infer_schema


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="etl-agent")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("files", help="compare two files (any mix of formats)")
    f.add_argument("source")
    f.add_argument("target")
    f.add_argument("--keys", nargs="+", required=True)
    f.add_argument("--ignore", nargs="*", default=[])
    f.add_argument("--out", default="reports")

    t = sub.add_parser("tables", help="compare a table across two databases")
    t.add_argument("src_url")
    t.add_argument("tgt_url")
    t.add_argument("table")
    t.add_argument("--keys", nargs="+", required=True)
    t.add_argument("--ignore", nargs="*", default=[])
    t.add_argument("--out", default="reports")

    i = sub.add_parser("infer-schema", help="infer a schema JSON from a file")
    i.add_argument("source")
    i.add_argument("--pk", nargs="*", default=None)
    i.add_argument("--out", default="schema.json")

    g = sub.add_parser("generate", help="generate invalid test files")
    g.add_argument("source")
    g.add_argument("--schema", default=None)
    g.add_argument("--pk", nargs="*", default=None)
    g.add_argument("--out", default="test_data")

    ag = sub.add_parser("agent", help="run bad files through an ETL, judge")
    ag.add_argument("source")
    ag.add_argument("--schema", default=None)
    ag.add_argument("--pk", nargs="*", default=None)
    ag.add_argument("--out", default="agent_run")
    ag.add_argument("--etl-command", default=None,
                    help='e.g. "python my_etl.py {file}" (omit: demo ETL)')
    ag.add_argument("--db-url", default=None,
                    help="target DB, used to verify loaded data")
    ag.add_argument("--table", default="TARGET")
    ag.add_argument("--ai-scenarios", type=int, default=0, metavar="N",
                    help="ask Claude for up to N extra scenarios")
    ag.add_argument("--ai-explain", action="store_true",
                    help="ask Claude to explain any failures")
    ag.add_argument("--demo-disable", nargs="*", default=[],
                    help="demo ETL: switch off checks to simulate bugs")
    return p


def main(argv=None, llm=None) -> int:
    a = build_parser().parse_args(argv)
    if a.cmd == "agent":
        return run_agent(a, llm)
    if a.cmd == "infer-schema":
        schema = infer_schema(read_file(a.source), a.pk)
        schema.save(a.out)
        pk = schema.primary_key or "none found"
        print(f"Schema saved to {a.out} (primary key: {pk}).")
        print("Review it before use.")
        return 0
    if a.cmd == "generate":
        if a.schema:
            schema = TableSchema.load(a.schema)
        else:
            schema = infer_schema(read_file(a.source), a.pk)
        manifest = generate_all(a.source, schema, a.out)
        cols = ["scenario", "file", "expected_failure"]
        print(manifest[cols].to_string(index=False))
        print(f"{len(manifest)} files written to {a.out}")
        return 0
    if a.cmd == "files":
        res = compare_dataframes(read_file(a.source), read_file(a.target),
                                 a.keys, a.ignore)
    else:
        res = compare_tables(a.src_url, a.tgt_url, a.table, a.keys,
                             ignore_columns=a.ignore)
    print(res.summary())
    print(f"Report written to: {write_report(res, a.out)}")
    return 0 if res.passed else 1   # non-zero exit lets CI fail the build


def run_agent(a, llm=None) -> int:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.schema:
        schema = TableSchema.load(a.schema)
    else:
        schema = infer_schema(read_file(a.source), a.pk)
    schema.save(out / "schema_used.json")
    manifest = generate_all(a.source, schema, str(out / "test_data"))

    # Optional AI layer. If unavailable, the core agent carries on.
    if (a.ai_scenarios or a.ai_explain) and llm is None:
        try:
            llm = ClaudeClient()
        except LLMUnavailable as exc:
            print(f"AI features skipped: {exc}")
    if llm and a.ai_scenarios:
        try:
            existing = list(manifest["scenario"])
            proposed, problems = suggest_scenarios(
                schema, existing, llm, a.ai_scenarios)
            if proposed:
                generate_ai_scenarios(a.source, proposed,
                                      str(out / "test_data"))
            print(f"AI proposed {len(proposed)} extra scenarios "
                  f"({len(problems)} rejected as invalid).")
            for pr in problems:
                print(f"  rejected: {pr}")
        except LLMUnavailable as exc:
            print(f"AI scenario step skipped: {exc}")

    db_url = a.db_url
    if not db_url and not a.etl_command:
        db_url = f"sqlite:///{(out / 'target.db').as_posix()}"
    if a.etl_command:
        runner = CommandRunner(a.etl_command)
    else:
        runner = DemoRunner(schema, db_url, a.table, a.demo_disable)
    verify = None
    if db_url and schema.primary_key:
        verify = make_verifier(db_url, a.table, schema.primary_key)

    try:
        results = run_suite(str(out / "test_data"), runner, verify)
    except EtlUnavailable as exc:
        print("STOPPED: ETL command could not be started.")
        print("No files were tested.")
        print(f"Details: {exc}")
        print("Try running your ETL command by hand first.")
        return 2
    report = write_agent_report(results, str(out))
    cols = ["scenario", "expected", "actual", "verdict"]
    print(results[cols].to_string(index=False))
    failed = results[results["verdict"] != "PASS"]
    passed_n = len(results) - len(failed)
    print(f"\n{passed_n} of {len(results)} passed. Report: {report}")
    for r in failed.itertuples():
        print(f"  FAILED {r.scenario}: {r.detail}")
    if llm and a.ai_explain and not failed.empty:
        try:
            text = explain_failures(results, schema, llm)
            (out / "ai_explanation.md").write_text(text, encoding="utf-8")
            print("\n--- AI explanation ---")
            print(text)
        except LLMUnavailable as exc:
            print(f"AI explanation skipped: {exc}")
    return 0 if failed.empty else 1


if __name__ == "__main__":
    sys.exit(main())