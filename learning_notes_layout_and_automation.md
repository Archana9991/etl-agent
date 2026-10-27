# Learning notes: why the tests broke and how they were fixed

## 1. ModuleNotFoundError: No module named 'etl_agent.automation'
`from .automation import x` means "import x from automation.py sitting next to
this file". If that file does not exist, Python raises ModuleNotFoundError.
cli.py imports it at the top, and every test imports cli.py, so ONE missing file
made all test files fail while pytest was still *collecting* them.
Lesson: a "collection error" is an import problem, not a failing assertion.

## 2. Why automation.py is a separate file (the "thin layers" idea)
cli.py (command line) and app.py (web page) must behave identically.
Both only gather input and call the same five functions in automation.py:
load_dataset, stop_if_sensitive, generate_sql_package, build_target, run_checks.
Fix a bug once there and both are fixed. The tests then check the CLI through
tests/test_automation.py and the web page through tests/test_ui.py.

Design details worth remembering:
- stop_if_sensitive(df, allow, log=print) takes a `log` function. The CLI passes
  print; the web page passes list.append. Same logic, different output.
- build_target loads the database BEFORE writing the CSV, so a failure leaves no
  half-finished output. generate_sql_package validates the dialect BEFORE
  writing any file. "Fail early, write nothing" keeps failures clean.
- SQL check rows are prefixed "sql:" so you can tell them from pandas checks.

## 3. Why test_ui.py failed with KeyError: 'build_btn'
Streamlit's AppTest runs a script with no browser, then lets a test click widgets
by key and read what was drawn. KeyError means "no widget with that key". The
tests were pointed at app.py, which held the older UI (keys f_go, t_go...), while
the tests expected the Phase 4 UI (keys sample_btn, build_btn...).
Lesson: a test is only as right as the file it points at.

## 4. The layout that makes everything line up
    src/etl_agent/   the importable package (no tests inside it)
    tests/           every test + conftest.py
    sample_data/     CSVs that test_ui.py and the "Load sample data" button read
    app.py           Phase 4 UI           app_legacy.py   Phase 1-3 UI
    my_etl.py        a standalone script (run with `python my_etl.py file`), so
                     it lives at the root, not inside the package
`pip install -e .` registers src/etl_agent so `python -m etl_agent.cli` works
from any folder. The [tool.pytest.ini_options] pythonpath makes pytest find it too.
