"""Create two sample SQLite databases (DEV and QA) with planted differences.

Run from the project folder:  python sample_data/make_sample_dbs.py
The data is made up. *.db files are ignored by Git.
"""
import sqlite3
from pathlib import Path

N = 100_000
FIRST = ["Liam", "Emma", "Mia", "Noah", "Ava", "Jane"]
LAST = ["Williams", "Moore", "Jones", "Smith", "Taylor"]
CITY = ["Riverside", "Fairview", "Madison", "Ashland", "Oxford", "Dover"]

DDL = (
    "CREATE TABLE customers ("
    "id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, "
    "email TEXT, age INTEGER, city TEXT, salary TEXT, joined TEXT)"
)
INSERT = "INSERT INTO customers VALUES (?,?,?,?,?,?,?,?)"


def make_row(i):
    salary = 20000 + (i * 37 % 170000) + 0.25
    return (i, FIRST[i % 6], LAST[i % 5], f"user{i}@sample.net",
            18 + i % 60, CITY[i % 6], f"{salary:.2f}",
            f"2015-01-{1 + i % 28:02d}")


def main():
    folder = Path(__file__).parent
    paths = {"dev": folder / "etl_dev.db", "qa": folder / "etl_qa.db"}
    for path in paths.values():
        path.unlink(missing_ok=True)
    dev = sqlite3.connect(paths["dev"])
    qa = sqlite3.connect(paths["qa"])
    rows = [make_row(i) for i in range(1, N + 1)]
    for conn in (dev, qa):
        conn.execute(DDL)
        conn.executemany(INSERT, rows)

    # Differences planted in QA only
    qa.execute("DELETE FROM customers WHERE id IN (500, 77000, 99999)")
    qa.execute(INSERT, (100001, "Zoe", "Khan", "zoe.khan@sample.net",
                        29, "Pune", "55000.00", "2024-05-01"))
    qa.execute(INSERT, (100002, "Raj", "Mehta", "raj.mehta@sample.net",
                        41, "Delhi", "72000.50", "2023-11-12"))
    qa.execute("UPDATE customers SET city='Mumbai' WHERE id IN (10, 20)")
    qa.execute("UPDATE customers SET salary='21111.25' WHERE id = 30")
    qa.execute("UPDATE customers SET email=NULL WHERE id = 40")
    qa.execute("UPDATE customers SET age=NULL WHERE id = 50")
    # NULL in BOTH databases: must NOT be reported as a difference
    qa.execute("UPDATE customers SET age=NULL WHERE id = 70")
    dev.execute("UPDATE customers SET age=NULL WHERE id = 70")

    for conn in (dev, qa):
        conn.commit()
        conn.close()
    print("Created:")
    for path in paths.values():
        print(" ", path)


if __name__ == "__main__":
    main()