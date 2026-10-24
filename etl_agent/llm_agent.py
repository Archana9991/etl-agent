"""The AI layer: Claude PROPOSES, your code DECIDES.

Two optional features (the core agent works without them):
  1. suggest_scenarios : Claude proposes extra test cases.
  2. explain_failures  : Claude explains why a test failed.

Safety design:
  * Claude never writes code that we run. It returns a small JSON
    description, we validate it against a fixed whitelist of changes,
    and OUR code applies them.
  * Only metadata is sent to the API (column names, types, scenario
    names, verdicts). File contents are never sent.
"""
import json
import os
import re
from pathlib import Path

import pandas as pd

from .agent import KNOWN_CODES
from .generators import write_file
from .readers import read_file
from .schema import TableSchema

DEFAULT_MODEL = "claude-sonnet-5-5"   # override: env var ETL_AGENT_MODEL


class LLMUnavailable(Exception):
    """No API key, package missing, or the API call failed.

    The core agent carries on without AI.
    """


class ClaudeClient:
    """Thin wrapper: the rest of the code only needs .complete().

    Tests replace this with a fake, so no API key is needed for tests.
    """

    def __init__(self, model=None):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise LLMUnavailable("ANTHROPIC_API_KEY is not set")
        try:
            import anthropic
        except ImportError as exc:
            raise LLMUnavailable("Run: pip install anthropic") from exc
        self._client = anthropic.Anthropic()   # reads key from environment
        default = os.environ.get("ETL_AGENT_MODEL", DEFAULT_MODEL)
        self.model = model or default

    def complete(self, system, user, max_tokens=1500):
        try:
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:
            raise LLMUnavailable(f"Claude API call failed: {exc}") from exc
        parts = [b.text for b in resp.content
                 if getattr(b, "type", "") == "text"]
        return "".join(parts)


# The whitelist: the ONLY ways an AI-suggested scenario can change a file.
MUTATIONS = {
    "blank": (lambda v, x: "", "set the cell to empty"),
    "whitespace_pad": (lambda v, x: f"  {v}  ",
                       "add leading and trailing spaces"),
    "special_chars": (lambda v, x: "Ab@#$%'\"<>;--",
                      "put special characters in the cell"),
    "very_long": (lambda v, x: "X" * 300, "300-character value"),
    "negative_number": (lambda v, x: "-1", "negative number"),
    "wrong_date_format": (lambda v, x: "05/01/2024",
                          "date written as DD/MM/YYYY"),
    "set_value": (lambda v, x: x,
                  "set the cell to the given short 'value' text"),
}


def _schema_for_prompt(schema: TableSchema) -> str:
    cols = [
        {"name": c.name, "type": c.dtype, "nullable": c.nullable,
         "max_length": c.max_length}
        for c in schema.columns
    ]
    return json.dumps({"primary_key": schema.primary_key,
                       "columns": cols}, indent=1)


def parse_scenarios(text, schema, max_n=10, taken=()):
    """Validate Claude's reply. Returns (valid, problems).

    Never trusts the model: every field is checked.
    """
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return [], ["reply contained no JSON list"]
    try:
        items = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return [], ["reply was not valid JSON"]
    columns = {c.name for c in schema.columns}
    allowed_expected = set(KNOWN_CODES) | {"LOAD_SUCCESS"}
    seen, valid, problems = set(taken), [], []
    if not isinstance(items, list):
        items = []
    for item in items:
        if not isinstance(item, dict):
            problems.append("item was not an object")
            continue
        raw_name = str(item.get("name", ""))
        name = re.sub(r"[^A-Za-z0-9]+", "_", raw_name).strip("_")[:30].lower()
        col = item.get("column")
        mut = item.get("mutation")
        exp = item.get("expected")
        val = item.get("value")
        if not name or name in seen:
            problems.append(f"bad or duplicate name {name!r}")
        elif col not in columns:
            problems.append(f"{name}: unknown column {col!r}")
        elif mut not in MUTATIONS:
            problems.append(f"{name}: mutation {mut!r} is not allowed")
        elif exp not in allowed_expected:
            problems.append(f"{name}: expected {exp!r} is not known")
        elif mut == "set_value" and not (
                isinstance(val, str) and len(val) <= 100):
            problems.append(f"{name}: set_value needs short text 'value'")
        else:
            seen.add(name)
            valid.append({
                "name": name, "column": col, "mutation": mut,
                "value": val, "expected": exp,
                "reason": str(item.get("reason", ""))[:200],
            })
        if len(valid) >= max_n:
            break
    return valid, problems


def suggest_scenarios(schema, existing, llm, max_n=5):
    system = (
        "You are a senior ETL test engineer. "
        "You design extra negative and edge-case tests for file loads. "
        "Reply with ONLY a JSON list, no prose."
    )
    mutations = "\n".join(f"- {k}: {v[1]}" for k, v in MUTATIONS.items())
    item_format = (
        '{"name": "short_snake_case", "column": "<exact column name>", '
        '"mutation": "<one of the list>", "value": "<only for set_value>", '
        '"expected": "<outcome>", "reason": "<one sentence>"}'
    )
    user = "\n".join([
        "Table schema:",
        _schema_for_prompt(schema),
        "",
        f"Scenarios that already exist (do not repeat): {existing}",
        "",
        f"Propose up to {max_n} NEW scenarios a real ETL might mishandle.",
        "Each item must look like:",
        item_format,
        "",
        "Allowed mutations:",
        mutations,
        "",
        f"Allowed expected outcomes: LOAD_SUCCESS or one of {KNOWN_CODES}",
    ])
    reply = llm.complete(system, user)
    return parse_scenarios(reply, schema, max_n, taken=existing)


def generate_ai_scenarios(source, scenarios, out_dir):
    """Apply validated scenarios to the good file.

    New rows are added to manifest.csv with origin = ai.
    """
    src, out = Path(source), Path(out_dir)
    df = read_file(source)
    manifest = pd.read_csv(out / "manifest.csv", dtype=str,
                           keep_default_na=False)
    if "origin" not in manifest.columns:
        manifest["origin"] = "rule"
    rows = []
    for s in scenarios:
        bad = df.copy()
        change = MUTATIONS[s["mutation"]][0]
        bad.loc[0, s["column"]] = change(df.loc[0, s["column"]], s["value"])
        name = f"{src.stem}__ai_{s['name']}{src.suffix.lower()}"
        write_file(bad, out / name)
        desc = (f"AI-suggested ({s['mutation']} on {s['column']}): "
                f"{s['reason']}")
        rows.append({
            "scenario": f"ai_{s['name']}",
            "file": name,
            "expected_failure": s["expected"],
            "description": desc,
            "origin": "ai",
        })
    manifest = pd.concat([manifest, pd.DataFrame(rows)], ignore_index=True)
    manifest.to_csv(out / "manifest.csv", index=False)
    return manifest


def explain_failures(results, schema, llm) -> str:
    """Plain-English diagnosis of failed scenarios.

    No failures means no API call (and no cost).
    """
    failed = results[results["verdict"] != "PASS"]
    if failed.empty:
        return ""
    lines = "\n".join(
        f"- {r.scenario} (origin: {r.origin}): expected {r.expected}, "
        f"actual {r.actual}, verdict {r.verdict}; "
        f"detail: {str(r.detail)[:200]}"
        for r in failed.itertuples()
    )
    system = (
        "You are a senior ETL test engineer explaining test results "
        "to a colleague. Be concrete and brief."
    )
    user = "\n".join([
        "Table schema:",
        _schema_for_prompt(schema),
        "",
        "Failed test scenarios:",
        lines,
        "",
        "For each failure give: (1) the most likely cause in the ETL,",
        "(2) one concrete fix.",
        "Meanings:",
        "- FAIL_BAD_DATA_ACCEPTED = ETL loaded a file it should reject",
        "- FAIL_WRONG_REASON = rejected by a different check than expected",
        "- FAIL_VALID_FILE_REJECTED = the good file was refused",
        "- FAIL_DATA_MISMATCH = loaded, but target differs from the file",
        "Scenarios with origin 'ai' have expectations guessed by an AI,",
        "so say when the expectation itself might be debatable.",
        "Use short plain paragraphs.",
    ])
    return llm.complete(system, user, max_tokens=1500).strip()