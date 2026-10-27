# Phase 4 learning notes: ETL automation

Read this alongside the code. Each section says WHAT the idea is, WHY it
matters in ETL testing, and WHERE it lives in the project.

---
## 1. The mapping is the single source of truth  (`mapping.py`)

In a real project the business analyst writes a **source-to-target
mapping**: "TARGET.FULL_NAME = FIRST_NAME + ' ' + LAST_NAME". Testers
usually read it and hand-write SQL. Here the mapping is *data* that every
other module reads, so the SQL, the expected data and the checks can
never drift apart.

**Python ideas used**
- `@dataclass`: a class that is mostly fields (`ColumnMap`, `Mapping`).
- `@property`: a value computed on demand (`mapping.primary_key`).
- Collecting ALL problems into a list, then raising once. Much friendlier
  than failing on the first error.

## 2. Whitelists beat free text  (`TRANSFORMATIONS`)

A mapping can only use the nine rules we implemented. It cannot contain
raw SQL. This is the same safety idea as the AI layer in Phase 3: *the
outside world proposes, our code decides.* Table and column names are also
checked with a regular expression before they ever reach a SQL string.

## 3. SQL is text, so build it from small pieces  (`sqlgen.py`)

`column_expression()` has one branch per rule and returns a small piece of
SQL. `select_expected_sql()` joins the pieces. Small pieces are easy to
test (see `test_each_transformation_as_sql`).

**Dialects.** Databases disagree on small things:

| Need | Oracle | SQL Server | Others |
|------|--------|------------|--------|
| join text | `\|\|` | `+` | `\|\|` |
| rows in A not in B | `MINUS` | `EXCEPT` | `EXCEPT` |
| bare SELECT | needs `FROM DUAL` | no | no |
| text length | `LENGTH` | `LEN` | `LENGTH` |

The `Dialect` object holds these differences so the generator code never
changes per database.

**SQL injection, again.** `sql_literal("O'Brien")` doubles the quote:
`'O''Brien'`. Without it, a mapping value such as `x'; DROP TABLE t; --`
would escape the string. `test_quotes_in_parameters_cannot_break_out...`
proves it does not.

## 4. The classic ETL checks  (`generate_sql_checks`)

| Check | Question it answers | Catches |
|-------|--------------------|---------|
| row count | same number of rows? | lost or duplicated rows |
| source MINUS target | anything expected that is missing or wrong? | wrong values, lost rows |
| target MINUS source | anything in the target we cannot explain? | extra rows, wrong values |
| duplicate keys | is the key unique? | double loads |
| NULL check | blanks in NOT NULL columns? | missing mandatory data |
| max length | values too long? | truncation rules |
| sum reconciliation | do totals match? | numeric corruption |

**Why count alone is not enough.** In `test_wrong_value_is_caught_by_the_
minus_checks` the row count PASSES while a city is wrong. Only the MINUS
queries notice. Always combine count-level and value-level checks.

**Why both directions of MINUS?** Source-minus-target finds what is
missing. Target-minus-source finds what should not be there. A wrong value
shows up in both: the correct row is missing, and the wrong row is extra.

**Set operators remove duplicates**, and need matching column types. If
the source stores `5` as text and the target as a number, EXCEPT may report
differences that are not real. That is why the test source table uses
properly typed columns.

## 5. Expected versus actual: the test oracle  (`target_builder.py`)

How do you know the ETL is right? You compute the answer a second,
independent way and compare. `build_expected_target()` applies the rules
in pandas; the ETL applies them in its own code. Any difference is a
finding.

`test_pandas_and_generated_sql_agree` checks that our two implementations
(pandas and generated SQL) agree. If they ever disagree, one has a bug,
and we have found it before the real ETL was even involved.

**Building the target.** `load_target()` creates the table from the mapping
(`CREATE TABLE` with NOT NULL and PRIMARY KEY) and inserts the expected
rows. Uses: a golden copy to compare against, or a target to test against
when the real ETL is not ready. Because the database enforces the
constraints, bad expected data is refused at load time.

**Python ideas used**
- pandas works on whole columns (`s.str.upper()`), no row loops.
- `engine.begin()` opens a transaction that commits on success and rolls
  back on error.
- `finally: engine.dispose()` always releases the connection.

## 6. Checks that explain themselves  (`etl_checks.py`)

Each check is one small function returning result rows
(`check | column | status | failing | detail`). The transformation check
reports **per column**, so a failure reads:

    transformation  CITY  FAIL  1  key 1: expected 'PUNE', got 'Pune'

That tells a developer exactly which mapping rule broke and on which row.
A good test result says *where* and *why*, not just "failed".

Statuses: PASS, FAIL, and SKIP (the check could not run). SKIP does not
fail the run, because the real cause is reported by another check
(`columns_present`).

`normalize=True` (from Phase 1) means `139913.4` equals `139913.40`.
Formatting noise is not a defect; leading zeros (`007` vs `7`) still are.

## 7. Small details that matter

- **A separator of one space.** The mapping loader does not strip the
  PARAMETER column, because `" "` is a legitimate CONCAT separator.
- **NULL || 'x' is NULL** in SQL. CONCAT wraps each part in `COALESCE`, and
  the pandas version treats NULL as blank, so both agree.
- **Colons in SQL.** SQLAlchemy's `text()` reads `:name` as a bind
  parameter, even inside quotes. `run_sql_checks` escapes colons as `\:`.
- **Exit codes** (0 ok, 1 finding, 2 setup problem, 3 sensitive data) let a
  CI pipeline tell "the ETL is wrong" apart from "the test could not run".
- **Sensitive-data guard.** Building an expected target copies real rows,
  so `stop_if_sensitive` runs first, exactly like Phase 3.

## 8. Pytest ideas used  (`tests/conftest.py`)

- **Fixtures** (`@pytest.fixture`): shared setup handed to tests by name
  (`mapping`, `source_df`, `db_url`). Each test gets a fresh `tmp_path`.
- **`@pytest.mark.parametrize`**: one test body, many inputs.
- **Planting a bug**: UPDATE or DELETE a row in a correct target, then
  assert the right check fails. This proves the checks can fail, which is
  as important as proving they can pass.

## 9. Exercises

1. Add a `REPLACE` rule (`PARAMETER` = `old=>new`). You must change
   `TRANSFORMATIONS`, `column_expression`, `apply_transformation` and add
   tests. `check_setup.py` and the parametrized test show where.
2. Support two source tables with a JOIN. Hint: add a `join` field to
   `Mapping` and extend `select_expected_sql`.
3. Add a check for "target rows with a key not in the source".
4. Add a `--references` option to `etl-check` for foreign-key checks
   (the function `check_referential_integrity` already exists).
5. Run the generated `oracle` or `mssql` script against a real database
   and note anything that needs a dialect tweak.
