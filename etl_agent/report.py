from pathlib import Path
import pandas as pd


def write_report(result, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.txt").write_text(result.summary() + "\n")
    with pd.ExcelWriter(out / "comparison_report.xlsx") as xl:
        pd.DataFrame({"summary": result.summary().splitlines()}).to_excel(xl, sheet_name="Summary", index=False)
        result.only_in_source.to_excel(xl, sheet_name="Only_in_source", index=False)
        result.only_in_target.to_excel(xl, sheet_name="Only_in_target", index=False)
        result.mismatches.to_excel(xl, sheet_name="Value_mismatches", index=False)
    return out