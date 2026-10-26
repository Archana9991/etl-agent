"""Web UI for the ETL agent.   Run from the project folder:

    streamlit run app.py

This file is a thin layer, like cli.py: it only collects inputs,
calls the library, and shows the answer. No testing logic lives here.

How Streamlit works (the one idea you need):
  every click re-runs this whole file from top to bottom. Anything
  that must survive a re-run is kept in st.session_state.
"""
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from etl_agent.agent import KNOWN_CODES, load_code_map
from etl_agent.comparator import compare_dataframes
from etl_agent.db_compare import (check_connection, compare_file_to_table,
                                  compare_tables)
from etl_agent.generators import generate_all
from etl_agent.orchestrator import AgentConfig, ETLAgent, resolve_db_url
from etl_agent.readers import read_file
from etl_agent.report import write_report
from etl_agent.schema import infer_schema
from etl_agent.ui_helpers import (DTYPES, clean_path, frame_to_schema,
                                  save_upload, schema_to_frame,
                                  split_names, zip_folder)

FILE_TYPES = ["csv", "xlsx", "xls", "xml"]
MAX_SHOW = 1000      # rows shown on screen (the Excel report has all)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EXIT_TEXT = {
    0: ("success", "Every scenario passed."),
    1: ("error", "At least one scenario failed. That is a finding."),
    2: ("error", "Setup problem: the ETL could not be started."),
    3: ("warning", "Stopped: the data looks sensitive. Use a masked file."),
}

st.set_page_config(page_title="ETL Agent", layout="wide")


# ---- shared building blocks --------------------------------------------
def workdir():
    """One temp folder per browser session for uploads and outputs."""
    if "workdir" not in st.session_state:
        st.session_state["workdir"] = tempfile.mkdtemp(prefix="etl_agent_")
    return st.session_state["workdir"]


def new_folder():
    return tempfile.mkdtemp(dir=workdir())


def pick_file(label, key, types):
    """Upload a file OR type its path. Returns a path, or None.

    'Path on disk' is handy for big files: nothing is copied.
    """
    mode = st.radio(label, ["Upload", "Path on disk"],
                    key=f"{key}_mode", horizontal=True)
    if mode == "Upload":
        up = st.file_uploader(label, type=types, key=f"{key}_up",
                              label_visibility="collapsed")
        if up is None:
            return None
        return save_upload(up.name, up.getvalue(), Path(workdir()) / key)
    text = clean_path(st.text_input(
        "Full path", key=f"{key}_path",
        placeholder=r"C:\Users\you\Desktop\etl-agent\real_data\file.csv"))
    if not text:
        return None
    if not Path(text).is_file():
        st.warning(f"No file found at: {text}")
        return None
    return text


def run_compare(state_key, compare):
    """Run a comparison, write the Excel report, remember both."""
    try:
        with st.spinner("Comparing... big files can take a while."):
            res = compare()
            out = new_folder()
            write_report(res, out)
            report = (Path(out) / "comparison_report.xlsx").read_bytes()
    except Exception as exc:
        st.session_state.pop(state_key, None)
        st.error(f"STOPPED: {str(exc)[:400]}")
        return
    st.session_state[state_key] = (res, report)


def show_compare(state_key):
    saved = st.session_state.get(state_key)
    if not saved:
        return
    res, report = saved
    if res.passed:
        st.success("PASS: source and target match.")
    else:
        st.error("FAIL: differences found.")
    cols = st.columns(5)
    cols[0].metric("Rows in source", res.source_rows)
    cols[1].metric("Rows in target", res.target_rows)
    cols[2].metric("Only in source", len(res.only_in_source))
    cols[3].metric("Only in target", len(res.only_in_target))
    cols[4].metric("Value mismatches", len(res.mismatches))
    st.code(res.summary())
    parts = [("Only in source", res.only_in_source),
             ("Only in target", res.only_in_target),
             ("Value mismatches", res.mismatches)]
    for title, frame in parts:
        if not frame.empty:
            st.subheader(f"{title} ({len(frame)})")
            if len(frame) > MAX_SHOW:
                st.caption(f"First {MAX_SHOW} shown. The report has all.")
            st.dataframe(frame.head(MAX_SHOW))
    st.download_button("Download Excel report", report,
                       file_name="comparison_report.xlsx", mime=XLSX,
                       key=f"{state_key}_dl")


def colour_rows(row):
    ok = row["verdict"] == "PASS"
    colour = "#e6f4ea" if ok else "#fce8e6"
    return [f"background-color: {colour}; color: black"] * len(row)


# ---- Phase 1: compare --------------------------------------------------
def tab_files():
    st.write("Compare two files. They can be different formats.")
    src = pick_file("Source file", "f_src", FILE_TYPES)
    tgt = pick_file("Target file", "f_tgt", FILE_TYPES)
    keys = split_names(st.text_input("Key column(s)", "ID", key="f_keys"))
    ignore = split_names(st.text_input("Ignore columns (optional)",
                                       key="f_ignore"))
    norm = st.checkbox("Treat 1.5 == 1.50 and a date == its midnight time",
                       key="f_norm")
    if st.button("Compare files", key="f_go"):
        if not (src and tgt and keys):
            st.warning("Choose both files and at least one key column.")
        else:
            run_compare("f_res", lambda: compare_dataframes(
                read_file(src), read_file(tgt), keys, ignore,
                normalize=norm))
    show_compare("f_res")


def tab_tables():
    st.write("Compare a table in two databases, or two tables.")
    st.caption("Use env:VARIABLE_NAME instead of a URL with a password.")
    src_url = st.text_input("Source database URL", key="t_src",
                            placeholder="sqlite:///dev.db")
    tgt_url = st.text_input("Target database URL", key="t_tgt",
                            placeholder="sqlite:///qa.db")
    table = st.text_input("Source table", key="t_table")
    tgt_table = st.text_input("Target table (only if the name differs)",
                              key="t_tgt_table")
    keys = split_names(st.text_input("Key column(s)", "ID", key="t_keys"))
    ignore = split_names(st.text_input("Ignore columns (optional)",
                                       key="t_ignore"))
    norm = st.checkbox("Treat 1.5 == 1.50 and a date == its midnight time",
                       key="t_norm")
    if st.button("Compare tables", key="t_go"):
        if not (src_url and tgt_url and table and keys):
            st.warning("Fill in both URLs, the table and the key.")
        else:
            run_compare("t_res", lambda: compare_tables(
                resolve_db_url(src_url), resolve_db_url(tgt_url), table,
                keys, tgt_table=tgt_table or None, ignore_columns=ignore,
                normalize=norm))
    show_compare("t_res")


def tab_file_table():
    st.write("Source = a file. Target = a database table.")
    src = pick_file("Source file", "ft_src", FILE_TYPES)
    url = st.text_input("Database URL (or env:NAME)", key="ft_url",
                        placeholder="sqlite:///target.db")
    table = st.text_input("Target table", key="ft_table")
    keys = split_names(st.text_input("Key column(s)", "ID", key="ft_keys"))
    ignore = split_names(st.text_input("Ignore columns (optional)",
                                       key="ft_ignore"))
    exact = st.checkbox("Exact text (no number or date tolerance)",
                        key="ft_exact")
    if st.button("Compare file with table", key="ft_go"):
        if not (src and url and table and keys):
            st.warning("Choose a file, a URL, a table and a key.")
        else:
            run_compare("ft_res", lambda: compare_file_to_table(
                src, resolve_db_url(url), table, keys,
                ignore_columns=ignore, normalize=not exact))
    show_compare("ft_res")


def tab_db_check():
    st.write("Test a connection before you compare. No data is read.")
    url = st.text_input("Database URL (or env:NAME)", key="d_url",
                        placeholder="sqlite:///target.db")
    schema = st.text_input("Schema (optional, e.g. dbo)", key="d_schema")
    if st.button("Test connection", key="d_go"):
        if not url:
            st.warning("Enter a database URL.")
            return
        try:
            dialect, names = check_connection(
                resolve_db_url(url), schema or None)
        except Exception as exc:
            st.error("FAILED: could not connect.")
            st.code(str(exc)[:600])
            return
        st.success(f"Connected OK (database type: {dialect}).")
        st.write(f"Tables found: {len(names)}")
        st.dataframe(pd.DataFrame({"table": names[:200]}))


# ---- Phase 2: generate bad files ---------------------------------------
def tab_generate():
    st.write("Give one GOOD file. Get one bad file per failure type.")
    src = pick_file("Good source file", "g_src", FILE_TYPES)
    pk_text = st.text_input("Primary key column(s) (blank = guess)",
                            key="g_pk")
    if st.button("1. Infer schema", key="g_infer"):
        if not src:
            st.warning("Choose a file first.")
        else:
            try:
                schema = infer_schema(read_file(src),
                                      split_names(pk_text) or None)
            except Exception as exc:
                st.error(f"STOPPED: {str(exc)[:400]}")
            else:
                st.session_state["g_schema"] = {
                    "frame": schema_to_frame(schema),
                    "pk": schema.primary_key, "source": src,
                    "version": st.session_state.get("g_ver", 0) + 1}
                st.session_state["g_ver"] = st.session_state["g_schema"][
                    "version"]
                st.session_state.pop("g_out", None)
    saved = st.session_state.get("g_schema")
    if not saved:
        return
    st.subheader("2. Review and edit the schema")
    st.caption("Tick or untick 'nullable', set a type, add a max_length. "
               f"Primary key: {saved['pk'] or 'none found'}.")
    edited = st.data_editor(
        saved["frame"], key=f"g_editor_{saved['version']}",
        disabled=["column"],
        column_config={
            "type": st.column_config.SelectboxColumn(
                "type", options=DTYPES, required=True),
            "nullable": st.column_config.CheckboxColumn("nullable"),
            "max_length": st.column_config.NumberColumn(
                "max_length", min_value=1, step=1),
        })
    if st.button("3. Generate test files", key="g_go"):
        try:
            schema = frame_to_schema(edited, saved["pk"])
            out = new_folder()
            manifest = generate_all(saved["source"], schema, out)
            data = zip_folder(out)
        except Exception as exc:
            st.error(f"STOPPED: {str(exc)[:400]}")
            return
        st.session_state["g_out"] = (manifest, data)
    result = st.session_state.get("g_out")
    if result:
        manifest, data = result
        st.success(f"{len(manifest)} files generated.")
        st.dataframe(manifest[["scenario", "file", "expected_failure"]])
        st.download_button("Download all files (.zip)", data,
                           file_name="test_data.zip",
                           mime="application/zip", key="g_dl")


# ---- Phase 3: the agent ------------------------------------------------
def build_config(src, out, opts):
    """Turn the screen's answers into an AgentConfig."""
    code_map = load_code_map(opts["code_map"]) if opts["code_map"] else {}
    return AgentConfig(
        source=src, out_dir=out, schema_path=opts["schema"],
        primary_key=split_names(opts["pk"]) or None,
        etl_command=opts["command"] or None,
        db_url=opts["db_url"] or None, table=opts["table"] or "TARGET",
        sample_rows=int(opts["rows"]) or None,
        ai_scenarios=int(opts["ai_n"]), ai_explain=opts["ai_explain"],
        demo_disable=opts["disable"], code_map=code_map,
        strict_reason=opts["strict"],
        allow_sensitive=opts["allow_sensitive"])


def run_agent_ui(src, opts):
    lines = []
    try:
        out = new_folder()
        cfg = build_config(src, out, opts)
        with st.spinner("Running every test file through the ETL..."):
            result = ETLAgent(cfg, log=lines.append).run()
        report = csv = None
        if result.report is not None:
            report = Path(result.report).read_bytes()
            csv = (Path(out) / "results.csv").read_bytes()
    except Exception as exc:
        st.session_state.pop("a_res", None)
        st.error(f"STOPPED: {str(exc)[:400]}")
        return
    st.session_state["a_res"] = (result, lines, report, csv)


def show_agent():
    saved = st.session_state.get("a_res")
    if not saved:
        return
    result, lines, report, csv = saved
    kind, text = EXIT_TEXT.get(result.exit_code, ("error", "Unknown."))
    getattr(st, kind)(f"Exit code {result.exit_code}. {text}")
    if result.results is not None:
        total = len(result.results)
        passed = int((result.results["verdict"] == "PASS").sum())
        cols = st.columns(3)
        cols[0].metric("Scenarios", total)
        cols[1].metric("Passed", passed)
        cols[2].metric("Failed", total - passed)
        st.dataframe(result.results.style.apply(colour_rows, axis=1))
        st.download_button("Download report.html", report,
                           file_name="report.html", mime="text/html",
                           key="a_dl_html")
        st.download_button("Download results.csv", csv,
                           file_name="results.csv", mime="text/csv",
                           key="a_dl_csv")
    if result.explanation:
        st.subheader("AI explanation")
        st.write(result.explanation)
    with st.expander("Agent log"):
        st.code("\n".join(lines) or "(empty)")


def tab_agent():
    st.write("Run every bad file through an ETL and judge the reaction.")
    src = pick_file("Good source file", "a_src", FILE_TYPES)
    pk = st.text_input("Primary key column(s) (blank = guess)", key="a_pk")
    command = st.text_input(
        "ETL command (blank = built-in demo ETL)", key="a_cmd",
        placeholder="python my_etl.py {file}")
    st.caption("Write {file} where the file name goes.")
    disable = st.multiselect(
        "Demo ETL only: switch checks OFF to simulate bugs",
        KNOWN_CODES, key="a_disable")
    with st.expander("Advanced options"):
        rows = st.number_input("Build test files from the first N rows "
                               "(0 = all rows)", 0, 1000000, 200, key="a_rows")
        strict = st.checkbox("Strict: the rejection reason must match",
                             value=True, key="a_strict")
        db_url = st.text_input("Target database URL (or env:NAME)",
                               key="a_db")
        table = st.text_input("Target table", "TARGET", key="a_table")
        st.write("Optional schema JSON (from the Generate tab idea):")
        schema = pick_file("Schema JSON", "a_schema", ["json"])
        st.write("Optional code map JSON (your ETL's wording):")
        code_map = pick_file("Code map JSON", "a_map", ["json"])
        allow = st.checkbox("Skip the sensitive-data guard (use with care)",
                            key="a_allow")
        st.write("Optional AI layer (needs ANTHROPIC_API_KEY):")
        ai_n = st.number_input("Extra AI scenarios", 0, 10, 0, key="a_ai_n")
        ai_explain = st.checkbox("AI explains failures", key="a_ai_x")
    if st.button("Run the agent", key="a_go", type="primary"):
        if not src:
            st.warning("Choose a source file first.")
        else:
            run_agent_ui(src, {
                "pk": pk, "command": command, "disable": disable,
                "rows": rows, "strict": strict, "db_url": db_url,
                "table": table, "schema": schema, "code_map": code_map,
                "allow_sensitive": allow, "ai_n": ai_n,
                "ai_explain": ai_explain})
    show_agent()


# ---- page layout -------------------------------------------------------
st.title("ETL Agent")
st.caption("Phase 1 compare - Phase 2 generate bad files - "
           "Phase 3 run the agent")
tabs = st.tabs(["Compare files", "Compare tables", "File vs table",
                "DB check", "Generate bad files", "Run the agent"])
for tab, draw in zip(tabs, [tab_files, tab_tables, tab_file_table,
                            tab_db_check, tab_generate, tab_agent]):
    with tab:
        draw()