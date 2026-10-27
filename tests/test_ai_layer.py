import json

import pandas as pd
import pytest

from etl_agent.cli import main
from etl_agent.generators import generate_all
from etl_agent.llm_agent import (
    ClaudeClient, LLMUnavailable, explain_failures,
    generate_ai_scenarios, parse_scenarios, suggest_scenarios,
)
from etl_agent.schema import infer_schema

GOOD = pd.DataFrame({
    "ID": ["1", "2", "3"],
    "NAME": ["Asha", "Ravi", "Meera"],
    "JOINED": ["2024-01-05", "2024-02-10", "2024-03-15"],
    "NOTE": ["a", "", "c"],
})
SCHEMA = infer_schema(GOOD)


def scn(name, column, mutation, expected, **extra):
    item = {"name": name, "column": column,
            "mutation": mutation, "expected": expected, "reason": "x"}
    item.update(extra)
    return item


PROPOSAL = json.dumps([
    scn("Padded name", "NAME", "whitespace_pad", "LOAD_SUCCESS"),
    scn("slash date", "JOINED", "wrong_date_format", "INVALID_DATA_TYPE"),
    scn("evil", "NAME", "run_shell", "LOAD_SUCCESS"),
    scn("ghost", "NOPE", "blank", "LOAD_SUCCESS"),
    scn("weird", "NAME", "blank", "MADE_UP_CODE"),
    scn("no value", "NAME", "set_value", "LOAD_SUCCESS"),
])


class FakeLLM:
    def __init__(self, reply):
        self.reply, self.calls, self.prompts = reply, 0, []

    def complete(self, system, user, max_tokens=1500):
        self.calls += 1
        self.prompts.append(user)
        return self.reply


def read(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def test_parse_keeps_only_valid_scenarios():
    text = "Sure!\n```json\n" + PROPOSAL + "\n```"
    valid, problems = parse_scenarios(text, SCHEMA)
    assert [v["name"] for v in valid] == ["padded_name", "slash_date"]
    # evil mutation, unknown column, bad outcome, missing value
    assert len(problems) == 4


def test_parse_handles_garbage_and_caps_count():
    assert parse_scenarios("no json here", SCHEMA)[0] == []
    assert parse_scenarios("[not valid json", SCHEMA)[0] == []
    many = json.dumps(
        [scn(f"s{i}", "NAME", "blank", "LOAD_SUCCESS") for i in range(20)])
    assert len(parse_scenarios(many, SCHEMA, max_n=3)[0]) == 3


def test_parse_skips_names_that_already_exist():
    reply = json.dumps(
        [scn("duplicate_pk", "NAME", "blank", "LOAD_SUCCESS")])
    valid, _ = parse_scenarios(reply, SCHEMA, taken=["duplicate_pk"])
    assert valid == []


def test_ai_scenarios_are_applied_by_our_code(tmp_path):
    src = tmp_path / "customers.csv"
    GOOD.to_csv(src, index=False)
    td = tmp_path / "td"
    generate_all(str(src), SCHEMA, str(td))
    proposed, _ = suggest_scenarios(
        SCHEMA, ["valid_baseline"], FakeLLM(PROPOSAL))
    manifest = generate_ai_scenarios(str(src), proposed, str(td))
    ai = manifest[manifest["origin"] == "ai"]
    rule = manifest[manifest["origin"] == "rule"]
    assert len(ai) == 2 and rule.shape[0] >= 10
    padded = read(td / "customers__ai_padded_name.csv")
    assert padded.loc[0, "NAME"] == "  Asha  "
    dated = read(td / "customers__ai_slash_date.csv")
    assert dated.loc[0, "JOINED"] == "05/01/2024"


def test_explain_skips_api_when_nothing_failed():
    llm = FakeLLM("should not be used")
    ok = pd.DataFrame([{
        "scenario": "a", "origin": "rule", "expected": "X",
        "actual": "Y", "verdict": "PASS", "detail": "",
    }])
    assert explain_failures(ok, SCHEMA, llm) == ""
    assert llm.calls == 0


def test_prompts_never_contain_file_contents():
    llm = FakeLLM("[]")
    suggest_scenarios(SCHEMA, ["valid_baseline"], llm)
    bad = pd.DataFrame([{
        "scenario": "duplicate_pk", "origin": "rule",
        "expected": "DUPLICATE_PRIMARY_KEY", "actual": "LOADED",
        "verdict": "FAIL_BAD_DATA_ACCEPTED", "detail": "ETL loaded it",
    }])
    llm.reply = "Likely no unique constraint."
    assert explain_failures(bad, SCHEMA, llm) == llm.reply
    for prompt in llm.prompts:
        for secret in ("Asha", "Ravi", "Meera"):
            assert secret not in prompt
    assert "duplicate_pk" in llm.prompts[1]
    assert "NAME" in llm.prompts[0]


def test_client_needs_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMUnavailable):
        ClaudeClient()


def test_cli_agent_with_ai_features(tmp_path):
    src = tmp_path / "customers.csv"
    GOOD.to_csv(src, index=False)
    out = tmp_path / "run"
    argv = ["agent", str(src), "--pk", "ID", "--out", str(out),
            "--ai-scenarios", "3", "--ai-explain",
            "--demo-disable", "DUPLICATE_PRIMARY_KEY"]
    code = main(argv, llm=FakeLLM(PROPOSAL))
    assert code == 1          # the planted bug is still caught
    res = read(out / "results.csv")
    ai_names = set(res[res["origin"] == "ai"]["scenario"])
    assert ai_names == {"ai_padded_name", "ai_slash_date"}
    assert (out / "ai_explanation.md").exists()


def test_cli_without_key_still_runs_core_agent(
        tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    src = tmp_path / "customers.csv"
    GOOD.to_csv(src, index=False)
    argv = ["agent", str(src), "--pk", "ID", "--out", str(tmp_path / "r"),
            "--ai-scenarios", "3", "--ai-explain"]
    code = main(argv)
    assert code == 0
    assert "AI features skipped" in capsys.readouterr().out