"""UI tests: Streamlit's AppTest runs the app WITHOUT a browser, lets us set
widget values, click buttons, and read what was drawn."""
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "app.py")
SAMPLE = ROOT / "sample_data"


def fresh():
    return AppTest.from_file(APP, default_timeout=30)


def with_sample(at):
    at.session_state["mapping_path"] = str(SAMPLE / "customer_mapping.csv")
    at.session_state["source_table"] = "CUSTOMERS_RAW"
    at.session_state["target_table"] = "DIM_CUSTOMER"
    at.session_state["bld_source_path"] = str(SAMPLE / "customers_raw.csv")
    at.session_state["chk_source_path"] = str(SAMPLE / "customers_raw.csv")
    return at


def text(elements):
    return " ".join(str(e.value) for e in elements)


def test_without_a_mapping_the_app_asks_for_one():
    at = fresh().run()
    assert not at.exception
    assert "mapping" in text(at.info).lower()


def test_sample_button_fills_the_form():
    at = fresh().run()
    at.button(key="sample_btn").click().run()
    assert not at.exception
    assert at.session_state["source_table"] == "CUSTOMERS_RAW"
    assert "Mapping is valid" in text(at.success)


def test_bad_mapping_shows_the_problems_not_a_traceback(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("TARGET_COLUMN,TRANSFORMATION\nA,MAGIC\n")
    at = fresh()
    at.session_state["mapping_path"] = str(bad)
    at.session_state["source_table"], at.session_state["target_table"] = "S", "T"
    at.run()
    assert not at.exception
    assert "MAGIC" in " ".join(c.value for c in at.code)


def test_sql_tab_shows_dialect_specific_sql():
    at = with_sample(fresh())
    at.session_state["dialect"] = "oracle"
    at.run()
    assert not at.exception
    shown = " ".join(c.value for c in at.code)
    assert "MINUS" in shown and "VARCHAR2" in shown


def test_build_then_check_passes_then_catches_a_bug(tmp_path):
    at = with_sample(fresh()).run()
    at.button(key="build_btn").click().run()
    assert not at.exception
    built = at.session_state["built"]
    assert len(built["df"]) == 4

    # run the checks against the expected target: everything passes
    at.button(key="use_built_btn").click().run()
    at.button(key="checks_btn").click().run()
    assert not at.exception
    assert "All checks passed" in text(at.success)

    # plant a bug in the target file and run again
    bad = pd.read_csv(built["path"], dtype=str, keep_default_na=False)
    bad.loc[0, "CITY"] = "Pune"
    bad_path = tmp_path / "bad_target.csv"
    bad.to_csv(bad_path, index=False)
    at.session_state["chk_target_path"] = str(bad_path)
    at.button(key="checks_btn").click().run()
    assert not at.exception
    assert "failed" in text(at.error)


def test_sensitive_source_is_blocked(tmp_path):
    risky = pd.read_csv(SAMPLE / "customers_raw.csv", dtype=str, keep_default_na=False)
    risky["AADHAAR_NO"] = "x"
    path = tmp_path / "risky.csv"
    risky.to_csv(path, index=False)
    at = with_sample(fresh())
    at.session_state["bld_source_path"] = str(path)
    at.run()
    at.button(key="build_btn").click().run()
    assert "sensitive" in text(at.error).lower()
    assert "built" not in at.session_state


def test_missing_source_gives_a_friendly_error():
    at = with_sample(fresh())
    at.session_state["bld_source_path"] = "/no/such/file.csv"
    at.run()
    at.button(key="build_btn").click().run()
    assert not at.exception
    assert "Could not read" in text(at.error)


def test_database_mode_builds_and_loads(tmp_path):
    import sqlite3
    db = tmp_path / "src.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE CUSTOMERS_RAW (CUST_ID INTEGER, FIRST_NAME TEXT, "
                 "LAST_NAME TEXT, GENDER TEXT, CITY TEXT, SALARY REAL, "
                 "STATE TEXT, BRANCH_CODE TEXT)")
    conn.execute("INSERT INTO CUSTOMERS_RAW VALUES (1,'Asha','Rao','F','Pune',10.5,'MH','MH01')")
    conn.commit()
    conn.close()
    url = f"sqlite:///{db.as_posix()}"
    at = with_sample(fresh())
    at.session_state["bld_source_mode"] = "Database table"
    at.session_state["bld_source_url"] = url
    at.session_state["bld_load_db"] = True
    at.session_state["bld_db_url"] = url
    at.run()
    at.button(key="build_btn").click().run()
    assert not at.exception
    assert at.session_state["built"]["loaded"] == 1