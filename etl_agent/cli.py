"""Command line interface (a thin layer over the library).

Examples:
  python -m etl_agent.cli files a.csv b.xlsx --keys ID
  python -m etl_agent.cli tables sqlite:///dev.db sqlite:///qa.db T --keys ID
  python -m etl_agent.cli infer-schema sample_data/source.csv --pk ID
  python -m etl_agent.cli generate sample_data/source.csv --pk ID
  python -m etl_agent.cli agent sample_data/source.csv --pk ID
"""
import argparse
import sys

from .agent import load_code_map
from .comparator import compare_dataframes
from .db_compare import (check_connection, compare_file_to_table,
                         compare_tables)
from .generators import generate_all
from .orchestrator import AgentConfig, ETLAgent, resolve_db_url
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
    f.add_argument("--normalize", action="store_true",
                   help="treat 1.5 == 1.50 and a date == its midnight time")
    f.add_argument("--out", default="reports")

    t = sub.add_parser("tables", help="compare two database tables")
    t.add_argument("src_url", help="database URL, or env:VARIABLE_NAME")
    t.add_argument("tgt_url", help="database URL, or env:VARIABLE_NAME")
    t.add_argument("table", help="source table, e.g. dbo.customers_src")
    t.add_argument("--tgt-table", default=None,
                   help="target table if its name differs from the source")
    t.add_argument("--keys", nargs="+", required=True)
    t.add_argument("--ignore", nargs="*", default=[])
    t.add_argument("--normalize", action="store_true",
                   help="treat 1.5 == 1.50 and a date == its midnight time")
    t.add_argument("--out", default="reports")

    ft = sub.add_parser("file-table",
                        help="compare a source file with a database table")
    ft.add_argument("file", help="source file: csv, xlsx or xml")
    ft.add_argument("url", help="database URL, or env:VARIABLE_NAME")
    ft.add_argument("table", help="target table, e.g. dbo.customers")
    ft.add_argument("--keys", nargs="+", required=True)
    ft.add_argument("--ignore", nargs="*", default=[])
    ft.add_argument("--exact-text", action="store_true",
                    help="compare as exact text (no number/date tolerance)")
    ft.add_argument("--out", default="reports")

    d = sub.add_parser("db-check", help="test a database connection")
    d.add_argument("url", help="database URL, or env:VARIABLE_NAME")
    d.add_argument("--schema", default=None, help="e.g. dbo")

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
                    help="target DB URL, or env:VARIABLE_NAME")
    ag.add_argument("--table", default="TARGET")
    ag.add_argument("--sample-rows", type=int, default=200,
                    help="build test files from the first N rows (0 = all)")
    ag.add_argument("--code-map", default=None,
                    help="JSON file: your ETL's wording -> our error codes")
    ag.add_argument("--reason-check", choices=["strict", "off"],
                    default="strict",
                    help="off: any rejection of a bad file counts as a pass")
    ag.add_argument("--allow-sensitive", action="store_true",
                    help="skip the sensitive-data guard (use with care)")
    ag.add_argument("--ai-scenarios", type=int, default=0, metavar="N",
                    help="ask Claude for up to N extra scenarios")
    ag.add_argument("--ai-explain", action="store_true",
                    help="ask Claude to explain any failures")
    ag.add_argument("--demo-disable", nargs="*", default=[],
                    help="demo ETL: switch off checks to simulate bugs")
    return p


def run_agent_command(a, llm=None) -> int:
    try:
        code_map = load_code_map(a.code_map) if a.code_map else {}
    except (OSError, ValueError) as exc:
        print(f"STOPPED: bad --code-map file: {exc}")
        return 2
    config = AgentConfig(
        source=a.source,
        out_dir=a.out,
        schema_path=a.schema,
        primary_key=a.pk,
        etl_command=a.etl_command,
        db_url=a.db_url,
        table=a.table,
        sample_rows=a.sample_rows or None,
        ai_scenarios=a.ai_scenarios,
        ai_explain=a.ai_explain,
        demo_disable=a.demo_disable,
        code_map=code_map,
        strict_reason=(a.reason_check == "strict"),
        allow_sensitive=a.allow_sensitive,
    )
    return ETLAgent(config, llm=llm).run().exit_code


def run_db_check(a) -> int:
    try:
        dialect, names = check_connection(resolve_db_url(a.url), a.schema)
    except Exception as exc:
        print("FAILED: could not connect.")
        print(f"Details: {str(exc)[:600]}")
        return 2
    print(f"Connected OK (database type: {dialect}).")
    print(f"Tables found: {len(names)}")
    for name in names[:50]:
        print(f"  {name}")
    return 0


def main(argv=None, llm=None) -> int:
    a = build_parser().parse_args(argv)
    if a.cmd == "agent":
        return run_agent_command(a, llm)
    if a.cmd == "db-check":
        return run_db_check(a)
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
                                 a.keys, a.ignore, normalize=a.normalize)
    elif a.cmd == "file-table":
        try:
            res = compare_file_to_table(
                a.file, resolve_db_url(a.url), a.table, a.keys,
                ignore_columns=a.ignore, normalize=not a.exact_text)
        except Exception as exc:
            print("STOPPED: could not compare the file and the table.")
            print(f"Details: {str(exc)[:400]}")
            return 2
    else:
        try:
            res = compare_tables(
                resolve_db_url(a.src_url), resolve_db_url(a.tgt_url),
                a.table, a.keys, tgt_table=a.tgt_table,
                ignore_columns=a.ignore, normalize=a.normalize)
        except Exception as exc:
            print("STOPPED: could not compare the tables.")
            print(f"Details: {str(exc)[:400]}")
            print("Tip: run the db-check command on each URL first.")
            return 2
    print(res.summary())
    print(f"Report written to: {write_report(res, a.out)}")
    return 0 if res.passed else 1   # non-zero exit lets CI fail the build


if __name__ == "__main__":
    sys.exit(main())