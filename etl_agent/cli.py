"""Command line interface.

Examples:
  python -m etl_agent.cli files  src.csv tgt.xlsx --keys ID
  python -m etl_agent.cli tables sqlite:///dev.db sqlite:///qa.db CUSTOMERS --keys ID
  python -m etl_agent.cli infer-schema sample_data/source.csv --pk ID
  python -m etl_agent.cli generate sample_data/source.csv --pk ID --out test_data
  python -m etl_agent.cli agent sample_data/source.csv --pk ID
"""
import argparse
import sys
from pathlib import Path

from .agent import CommandRunner, DemoRunner, make_verifier, run_suite, write_agent_report
from .comparator import compare_dataframes
from .db_compare import compare_tables
from .generators import generate_all
from .readers import read_file
from .report import write_report
from .schema import TableSchema, infer_schema


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="etl-agent")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("files", help="compare two files (csv/xlsx/xml, any mix)")
    f.add_argument("source"); f.add_argument("target")
    f.add_argument("--keys", nargs="+", required=True)
    f.add_argument("--ignore", nargs="*", default=[])
    f.add_argument("--out", default="reports")

    t = sub.add_parser("tables", help="compare a table across two DB environments")
    t.add_argument("src_url"); t.add_argument("tgt_url"); t.add_argument("table")
    t.add_argument("--keys", nargs="+", required=True)
    t.add_argument("--ignore", nargs="*", default=[])
    t.add_argument("--out", default="reports")

    i = sub.add_parser("infer-schema", help="infer a schema JSON from a good sample file")
    i.add_argument("source"); i.add_argument("--pk", nargs="*", default=None)
    i.add_argument("--out", default="schema.json")

    g = sub.add_parser("generate", help="generate invalid test files from a good sample file")
    g.add_argument("source"); g.add_argument("--schema", default=None)
    g.add_argument("--pk", nargs="*", default=None)
    g.add_argument("--out", default="test_data")

    ag = sub.add_parser("agent", help="generate bad files, run them through an ETL, judge the results")
    ag.add_argument("source"); ag.add_argument("--schema", default=None)
    ag.add_argument("--pk", nargs="*", default=None)
    ag.add_argument("--out", default="agent_run")
    ag.add_argument("--etl-command", default=None, help='e.g. "python my_etl.py {file}"; omit to use the demo ETL')
    ag.add_argument("--db-url", default=None, help="target DB, used to verify loaded data")
    ag.add_argument("--table", default="TARGET")
    ag.add_argument("--demo-disable", nargs="*", default=[], help="demo ETL: switch off checks to simulate bugs")

    a = p.parse_args(argv)
    if a.cmd == "agent":
        return run_agent(a)
    if a.cmd == "infer-schema":
        schema = infer_schema(read_file(a.source), a.pk)
        schema.save(a.out)
        print(f"Schema saved to {a.out} (primary key: {schema.primary_key or 'none found'}). Review it before use.")
        return 0
    if a.cmd == "generate":
        schema = TableSchema.load(a.schema) if a.schema else infer_schema(read_file(a.source), a.pk)
        manifest = generate_all(a.source, schema, a.out)
        print(manifest[["scenario", "file", "expected_failure"]].to_string(index=False))
        print(f"{len(manifest)} files written to {a.out}")
        return 0
    if a.cmd == "files":
        res = compare_dataframes(read_file(a.source), read_file(a.target), a.keys, a.ignore)
    else:
        res = compare_tables(a.src_url, a.tgt_url, a.table, a.keys, ignore_columns=a.ignore)

    print(res.summary())
    print(f"Report written to: {write_report(res, a.out)}")
    return 0 if res.passed else 1   # non-zero exit lets CI pipelines fail the build


def run_agent(a) -> int:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    schema = TableSchema.load(a.schema) if a.schema else infer_schema(read_file(a.source), a.pk)
    schema.save(out / "schema_used.json")
    generate_all(a.source, schema, str(out / "test_data"))

    db_url = a.db_url or (None if a.etl_command else f"sqlite:///{(out / 'target.db').as_posix()}")
    if a.etl_command:
        runner = CommandRunner(a.etl_command)
    else:
        runner = DemoRunner(schema, db_url, a.table, a.demo_disable)
    verify = make_verifier(db_url, a.table, schema.primary_key) if db_url and schema.primary_key else None

    results = run_suite(str(out / "test_data"), runner, verify)
    report = write_agent_report(results, str(out))
    print(results[["scenario", "expected", "actual", "verdict"]].to_string(index=False))
    failed = results[results["verdict"] != "PASS"]
    print(f"\n{len(results) - len(failed)} of {len(results)} passed. Report: {report}")
    for r in failed.itertuples():
        print(f"  FAILED {r.scenario}: {r.detail}")
    return 0 if failed.empty else 1


if __name__ == "__main__":
    sys.exit(main())