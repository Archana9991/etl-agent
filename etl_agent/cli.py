"""Examples:
  python -m etl_agent.cli files src.csv tgt.xlsx --keys ID
  python -m etl_agent.cli tables sqlite:///dev.db sqlite:///qa.db CUSTOMERS --keys ID
  python -m etl_agent.cli infer-schema sample_data/source.csv --pk ID
  python -m etl_agent.cli generate sample_data/source.csv --pk ID --out test_data"""
import argparse
import sys

from .comparator import compare_dataframes
from .db_compare import compare_tables
from .generators import generate_all
from .readers import read_file
from .report import write_report
from .schema import TableSchema, infer_schema


def main(argv=None):
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

    a = p.parse_args(argv)
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
    return 0 if res.passed else 1


if __name__ == "__main__":
    sys.exit(main())