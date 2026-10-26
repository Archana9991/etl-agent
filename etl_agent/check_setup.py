"""Check that every module has the functions the project expects.

Run from the project folder:   python check_setup.py

It reads the files WITHOUT importing them, so one broken file cannot
hide the others. For every problem it says which file to replace.
"""
import ast
import sys
from pathlib import Path

EXPECTED = {
    "readers": ["read_file", "read_csv", "read_excel", "read_xml"],
    "comparator": ["compare_dataframes", "normalize_value",
                   "CompareResult"],
    "db_compare": ["read_table", "check_connection", "table_schema",
                   "compare_tables", "compare_file_to_table"],
    "report": ["write_report"],
    "schema": ["infer_schema", "TableSchema", "ColumnSpec",
               "guess_primary_key", "infer_dtype"],
    "generators": ["generate_all", "write_file", "write_blank"],
    "etl_demo": ["demo_etl", "EtlRejected"],
    "agent": ["CommandRunner", "DemoRunner", "judge", "run_suite",
              "make_verifier", "load_code_map", "write_agent_report",
              "EtlUnavailable", "EtlOutcome", "KNOWN_CODES"],
    "sensitive": ["find_sensitive"],
    "llm_agent": ["ClaudeClient", "LLMUnavailable", "suggest_scenarios",
                  "explain_failures", "generate_ai_scenarios",
                  "parse_scenarios", "MUTATIONS"],
    "orchestrator": ["AgentConfig", "AgentResult", "ETLAgent",
                     "resolve_db_url"],
    "ui_helpers": ["split_names", "clean_path", "safe_name",
                   "save_upload", "schema_to_frame", "frame_to_schema",
                   "zip_folder"],
    "cli": ["main", "build_parser", "run_agent_command", "run_db_check"],
}


def find_package():
    for candidate in (Path("etl_agent"), Path("src") / "etl_agent"):
        if (candidate / "__init__.py").exists():
            return candidate
    return None


def top_level_names(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def check_modules(package):
    problems = []
    for module, wanted in EXPECTED.items():
        path = package / f"{module}.py"
        if not path.exists():
            print(f"MISSING FILE  {path.name}")
            problems.append(path.name)
            continue
        try:
            have = top_level_names(path)
        except SyntaxError as exc:
            print(f"SYNTAX ERROR  {path.name} line {exc.lineno}: {exc.msg}")
            problems.append(path.name)
            continue
        except UnicodeDecodeError:
            print(f"UNREADABLE    {path.name} (not UTF-8 text)")
            problems.append(path.name)
            continue
        missing = [n for n in wanted if n not in have]
        if missing:
            print(f"OUT OF DATE   {path.name}  missing: {', '.join(missing)}")
            problems.append(path.name)
        else:
            print(f"OK            {path.name}")
    return problems


def check_tests(package):
    print("\nTest files found:")
    found = sorted(p for p in Path(".").rglob("test_*.py")
                   if ".venv" not in