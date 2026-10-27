import pytest

from etl_agent.mapping import ColumnMap
from etl_agent.sqlgen import (SqlCheck, column_expression, create_table_sql,
                              generate_sql_checks, generate_sql_script,
                              get_dialect, run_sql_checks,
                              select_expected_sql, sql_literal)
from conftest import execute

ANSI = get_dialect("ansi")


def expr(transformation, sources=("C",), parameter="", dialect=ANSI):
    return column_expression(
        ColumnMap("T", list(sources), transformation, parameter), dialect)


@pytest.mark.parametrize("transformation, sources, parameter, expected", [
    ("DIRECT", ("C",), "", "C"),
    ("TRIM", ("C",), "", "TRIM(C)"),
    ("UPPER", ("C",), "", "UPPER(C)"),
    ("LOWER", ("C",), "", "LOWER(C)"),
    ("CONSTANT", (), "IN", "'IN'"),
    ("SUBSTRING", ("C",), "2:3", "SUBSTR(C, 2, 3)"),
    ("CONCAT", ("A", "B"), " ", "COALESCE(A, '') || ' ' || COALESCE(B, '')"),
    ("DEFAULT", ("C",), "NA",
     "CASE WHEN C IS NULL OR TRIM(C) = '' THEN 'NA' ELSE C END"),
    ("LOOKUP", ("C",), "M=Male;F=Female",
     "CASE C WHEN 'M' THEN 'Male' WHEN 'F' THEN 'Female' ELSE C END"),
])
def test_each_transformation_as_sql(transformation, sources, parameter, expected):
    assert expr(transformation, sources, parameter) == expected


def test_quotes_in_parameters_cannot_break_out_of_the_string():
    assert sql_literal("O'Brien") == "'O''Brien'"
    assert expr("CONSTANT", (), "x'; DROP TABLE t; --") == "'x''; DROP TABLE t; --'"


def test_dialects_differ_where_databases_differ():
    oracle, mssql = get_dialect("oracle"), get_dialect("mssql")
    assert expr("CONCAT", ("A", "B"), "-", mssql) == \
        "COALESCE(A, '') + '-' + COALESCE(B, '')"
    assert expr("SUBSTRING", ("C",), "1:2", mssql) == "SUBSTRING(C, 1, 2)"
    assert expr("CONCAT", ("A", "B"), "-", oracle).count("||") == 2


def test_oracle_uses_minus_and_dual(mapping):
    sql = {c.name: c.sql for c in generate_sql_checks(mapping, get_dialect("oracle"))}
    assert "\nMINUS\n" in sql["source_minus_target"]
    assert "EXCEPT" not in sql["source_minus_target"]
    assert sql["row_count"].endswith("FROM DUAL")
    assert "EXCEPT" in {c.name: c.sql for c in generate_sql_checks(
        mapping, get_dialect("mssql"))}["source_minus_target"]


def test_unknown_dialect_is_a_clear_error():
    with pytest.raises(ValueError, match="Unknown dialect"):
        get_dialect("db2")


def test_create_table_follows_the_mapping(mapping):
    ddl = create_table_sql(mapping, ANSI)
    assert ddl.startswith("CREATE TABLE DIM_CUSTOMER (")
    assert "ID INTEGER NOT NULL" in ddl
    assert "FULL_NAME VARCHAR(50) NOT NULL" in ddl
    assert "SALARY DECIMAL(18,4)\n" in ddl or "SALARY DECIMAL(18,4)," in ddl
    assert "PRIMARY KEY (ID)" in ddl
    assert "NUMBER(38)" in create_table_sql(mapping, get_dialect("oracle"))


def test_the_expected_select_aliases_every_target_column(mapping):
    sql = select_expected_sql(mapping, ANSI)
    assert sql.endswith("FROM CUSTOMERS_RAW")
    assert "UPPER(CITY) AS CITY" in sql and "'IN' AS COUNTRY" in sql


def test_check_list_covers_the_standard_etl_checks(mapping):
    names = [c.name for c in generate_sql_checks(mapping, ANSI)]
    for expected in ("row_count", "source_minus_target", "target_minus_source",
                     "duplicate_keys", "null_ID", "null_FULL_NAME",
                     "too_long_CITY", "sum_SALARY"):
        assert expected in names
    assert "null_CITY" not in names          # CITY is nullable


def test_script_has_a_comment_per_check(mapping):
    script = generate_sql_script(mapping, ANSI)
    assert "-- row_count:" in script and "-- PASS when:" in script
    assert script.count(";") >= len(generate_sql_checks(mapping, ANSI))


# ---- run the generated SQL for real, in SQLite ------------------------------
def load_correct_target(db_url, mapping):
    """Play the part of a CORRECT ETL: the generated SELECT, inserted."""
    execute(db_url, create_table_sql(mapping, ANSI),
            f"INSERT INTO DIM_CUSTOMER ({', '.join(mapping.target_columns)})\n"
            + select_expected_sql(mapping, ANSI))


def verdicts(db_url, mapping):
    res = run_sql_checks(db_url, generate_sql_checks(mapping, ANSI))
    return dict(zip(res["check"], res["status"]))


def test_a_correct_load_passes_every_generated_check(db_url, mapping):
    load_correct_target(db_url, mapping)
    v = verdicts(db_url, mapping)
    assert set(v.values()) == {"PASS"}, v


def test_wrong_value_is_caught_by_the_minus_checks(db_url, mapping):
    load_correct_target(db_url, mapping)
    execute(db_url, "UPDATE DIM_CUSTOMER SET CITY = 'pune' WHERE ID = 1")
    v = verdicts(db_url, mapping)
    assert v["source_minus_target"] == "FAIL"
    assert v["target_minus_source"] == "FAIL"
    assert v["row_count"] == "PASS"          # counts alone would MISS this


def test_lost_row_and_wrong_total_are_caught(db_url, mapping):
    load_correct_target(db_url, mapping)
    execute(db_url, "DELETE FROM DIM_CUSTOMER WHERE ID = 1")
    v = verdicts(db_url, mapping)
    assert v["row_count"] == "FAIL"
    assert v["sum_SALARY"] == "FAIL"
    assert v["source_minus_target"] == "FAIL"


def test_too_long_value_is_caught(db_url, mapping):
    load_correct_target(db_url, mapping)
    execute(db_url, "UPDATE DIM_CUSTOMER SET CITY = '" + "X" * 31 + "' WHERE ID = 2")
    assert verdicts(db_url, mapping)["too_long_CITY"] == "FAIL"


def test_a_broken_query_is_reported_as_error_not_a_crash(db_url):
    res = run_sql_checks(db_url, [SqlCheck("oops", "d", "SELECT * FROM NO_SUCH", "no_rows")])
    assert res.loc[0, "status"] == "ERROR"


def test_a_colon_in_a_constant_does_not_confuse_sqlalchemy(db_url):
    sql = "SELECT 'a:b' AS x WHERE 'a:b' <> 'a:b'"
    res = run_sql_checks(db_url, [SqlCheck("colon", "d", sql, "no_rows")])
    assert res.loc[0, "status"] == "PASS"