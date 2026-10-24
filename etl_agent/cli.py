"""Examples:
  python -m etl_agent.cli files src.csv tgt.xlsx --keys ID
  python -m etl_agent.cli tables sqlite:///dev.db sqlite:///qa.db CUSTOMERS --keys ID"""
import argparse
import sys
from .comparator import compare_dataframes
from .db_compare import compare_tables
from .readers import read_file
from .report import write_report


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

    a = p.parse_args(argv)
    if a.cmd == "files":
        res = compare_dataframes(read_file(a.source), read_file(a.target), a.keys, a.ignore)
    else:
        res = compare_tables(a.src_url, a.tgt_url, a.table, a.keys, ignore_columns=a.ignore)

    print(res.summary())
    print(f"Report written to: {write_report(res, a.out)}")
    return 0 if res.passed else 1   # non-zero exit lets CI fail the build


if __name__ == "__main__":
    sys.exit(main())