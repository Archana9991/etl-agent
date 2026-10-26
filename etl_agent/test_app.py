"""Drive the real Streamlit screen without a browser (AppTest)."""
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parent.parent / "app.py")
GOOD = pd.DataFrame({"ID": ["1", "2", "3"],
                     "NAME": ["Asha", "Ravi", "Meera"],
                     "CITY": ["Pune", "Delhi", "Chennai"]})


def start():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception
    return at


def use_path(at, key, path):
    at.radio(key=f"{key}_mode").set_value("Path on disk").run()
    at.text_input(key=f"{key}_path").set_value(str(path)).run()


def make_csv(tmp_path, name, df=GOOD):
    path = tmp_path / name
    df.to_csv(path, index=False)
    return path


def test_page_loads_with_six_tabs():
    at = start()
    assert len(at.tabs) == 6


def test_compare_files_pass_and_fail(tmp_path):
    a = make_csv(tmp_path, "a.csv")
    changed = GOOD.copy()
    changed.loc[0, "CITY"] = "Goa"
    b = make_csv(tmp_path, "b.csv", changed)
    at = start()
    use_path(at, "f_src", a)
    use_path(at, "f_tgt", a)
    at.button(key="f_go").click().run()
    assert not at.exception and not at.error
    assert at.session_state["f_res"][0].passed
    use_path(at, "f_tgt", b)
    at.button(key="f_go").click().run()
    res = at.session_state["f_res"][0]
    assert not res.passed and len(res.mismatches) == 1
    assert at.session_state["f_res"][1][:2] == b"PK"   # a real xlsx


def test_compare_needs_inputs():
    at = start()
    at.button(key="f_go").click().run()
    assert at.warning


def test_db_check_and_bad_connection(tmp_path):
    db = tmp_path / "x.db"
    sqlite3.connect(db).execute("CREATE TABLE t (id INTEGER)").close()
    at = start()
    at.text_input(key="d_url").set_value(f"sqlite:///{db.as_posix()}")
    at.button(key="d_go").click().run()
    assert at.success and not at.error
    at.text_input(key="d_url").set_value("sqlite:////nope_xyz/none.db")
    at.button(key="d_go").click().run()
    assert at.error


def test_file_vs_table(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    GOOD.to_sql("customers", conn, index=False)
    conn.close()
    src = make_csv(tmp_path, "s.csv")
    at = start()
    use_path(at, "ft_src", src)
    at.text_input(key="ft_url").set_value(f"sqlite:///{db.as_posix()}")
    at.text_input(key="ft_table").set_value("customers")
    at.button(key="ft_go").click().run()
    assert at.session_state["ft_res"][0].passed


def test_generate_flow_with_schema_editing(tmp_path):
    src = make_csv(tmp_path, "customers.csv")
    at = start()
    use_path(at, "g_src", src)
    at.text_input(key="g_pk").set_value("ID").run()
    at.button(key="g_infer").click().run()
    assert not at.exception
    at.button(key="g_go").click().run()
    assert not at.exception and not at.error
    manifest, data = at.session_state["g_out"]
    assert "invalid_header" in set(manifest["scenario"])
    assert data[:2] == b"PK"


def test_agent_demo_etl_passes_and_catches_a_planted_bug(tmp_path):
    src = make_csv(tmp_path, "customers.csv")
    at = start()
    use_path(at, "a_src", src)
    at.text_input(key="a_pk").set_value("ID").run()
    at.button(key="a_go").click().run()
    assert not at.exception and not at.error
    assert at.session_state["a_res"][0].exit_code == 0
    at.multiselect(key="a_disable").set_value(["EMPTY_FILE"]).run()
    at.button(key="a_go").click().run()
    assert at.session_state["a_res"][0].exit_code == 1


def test_agent_stops_on_sensitive_columns(tmp_path):
    src = make_csv(tmp_path, "customers.csv",
                   GOOD.assign(AADHAAR_NO=["a", "b", "c"]))
    at = start()
    use_path(at, "a_src", src)
    at.button(key="a_go").click().run()
    assert at.session_state["a_res"][0].exit_code == 3
    assert at.warning


def test_agent_runs_a_real_command(tmp_path):
    src = make_csv(tmp_path, "customers.csv")
    etl = Path(__file__).resolve().parent.parent / "my_etl.py"
    cmd = f'"{sys.executable}" "{etl}" {{file}}'
    at = start()
    use_path(at, "a_src", src)
    at.text_input(key="a_pk").set_value("ID").run()
    at.text_input(key="a_cmd").set_value(cmd).run()
    at.button(key="a_go").click().run()
    assert not at.exception
    assert at.session_state["a_res"][0].exit_code == 0