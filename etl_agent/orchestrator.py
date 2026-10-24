"""The ETL testing agent, as one reusable class.

    from etl_agent.orchestrator import ETLAgent, AgentConfig
    result = ETLAgent(AgentConfig(source="good.csv")).run()
    print(result.exit_code)

Exit codes:
  0  every scenario passed
  1  at least one scenario failed (a real finding)
  2  setup problem: the ETL could not be started, or bad settings
  3  stopped: the data looks sensitive (use a masked extract)
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .agent import (CommandRunner, DemoRunner, EtlUnavailable,
                    make_verifier, run_suite, write_agent_report)
from .generators import generate_all, write_file
from .llm_agent import (ClaudeClient, LLMUnavailable, explain_failures,
                        generate_ai_scenarios, suggest_scenarios)
from .readers import read_file
from .schema import TableSchema, infer_schema
from .sensitive import find_sensitive


@dataclass
class AgentConfig:
    source: str
    out_dir: str = "agent_run"
    schema_path: str | None = None
    primary_key: list | None = None
    etl_command: str | None = None     # None = use the built-in demo ETL
    db_url: str | None = None          # or "env:VARIABLE_NAME"
    table: str = "TARGET"
    sample_rows: int | None = 200      # None = use every row
    ai_scenarios: int = 0
    ai_explain: bool = False
    demo_disable: list = field(default_factory=list)
    code_map: dict = field(default_factory=dict)
    strict_reason: bool = True
    allow_sensitive: bool = False


@dataclass
class AgentResult:
    exit_code: int
    message: str = ""
    results: pd.DataFrame | None = None
    report: Path | None = None
    explanation: str = ""


def resolve_db_url(value):
    """'env:NAME' reads the URL from an environment variable, which keeps
    passwords out of command history and scripts."""
    if value and value.startswith("env:"):
        name = value[4:]
        url = os.environ.get(name)
        if not url:
            raise ValueError(f"environment variable {name} is not set")
        return url
    return value


class ETLAgent:
    def __init__(self, config: AgentConfig, llm=None, log=print):
        self.cfg, self.llm, self.log = config, llm, log

    # ---- helpers -------------------------------------------------------
    def _stop(self, code: int, message: str) -> AgentResult:
        self.log(message)
        return AgentResult(code, message)

    def _take_sample(self, df, out):
        """Real files can be huge. Build the bad files from a small sample."""
        n = self.cfg.sample_rows
        if n and len(df) > n:
            path = out / f"sample_source{Path(self.cfg.source).suffix.lower()}"
            df = df.head(n)
            write_file(df, path)
            self.log(f"Using the first {n} rows as the sample: {path}")
            return str(path), df
        return self.cfg.source, df

    def _connect_ai(self):
        cfg = self.cfg
        if not (cfg.ai_scenarios or cfg.ai_explain) or self.llm:
            return
        try:
            self.llm = ClaudeClient()
        except LLMUnavailable as exc:
            self.log(f"AI features skipped: {exc}")

    def _ai_scenarios(self, source, schema, manifest, test_dir):
        try:
            existing = list(manifest["scenario"])
            proposed, problems = suggest_scenarios(
                schema, existing, self.llm, self.cfg.ai_scenarios)
            if proposed:
                generate_ai_scenarios(source, proposed, test_dir)
            self.log(f"AI proposed {len(proposed)} extra scenarios "
                     f"({len(problems)} rejected as invalid).")
            for problem in problems:
                self.log(f"  rejected: {problem}")
        except LLMUnavailable as exc:
            self.log(f"AI scenario step skipped: {exc}")

    def _runner(self, schema, db_url):
        cfg = self.cfg
        if cfg.etl_command:
            return CommandRunner(cfg.etl_command, code_map=cfg.code_map)
        return DemoRunner(schema, db_url, cfg.table, cfg.demo_disable)

    # ---- the agent's plan ----------------------------------------------
    def run(self) -> AgentResult:
        cfg, log = self.cfg, self.log
        out = Path(cfg.out_dir)
        out.mkdir(parents=True, exist_ok=True)

        try:
            db_url = resolve_db_url(cfg.db_url)
            df = read_file(cfg.source)
        except (ValueError, OSError) as exc:
            return self._stop(2, f"STOPPED: {exc}")

        # 1. Safety check, before any file is copied
        if not cfg.allow_sensitive:
            hits = find_sensitive(df)
            if hits:
                log("STOPPED: the source looks like it holds sensitive data.")
                for column, reason in hits:
                    log(f"  column {column!r}: {reason}")
                log("Use a masked or synthetic extract instead.")
                log("If these columns are safe, add --allow-sensitive.")
                return AgentResult(3, "sensitive data suspected")

        # 2. Sample, schema, bad files
        source, df = self._take_sample(df, out)
        if cfg.schema_path:
            schema = TableSchema.load(cfg.schema_path)
        else:
            schema = infer_schema(df, cfg.primary_key)
        schema.save(out / "schema_used.json")
        test_dir = str(out / "test_data")
        try:
            manifest = generate_all(source, schema, test_dir)
        except ValueError as exc:
            return self._stop(2, f"STOPPED: {exc}")

        # 3. Optional AI layer
        self._connect_ai()
        if self.llm:
            log("Note: AI features send column names, types and test "
                "verdicts to the Anthropic API (never file contents).")
        if self.llm and cfg.ai_scenarios:
            self._ai_scenarios(source, schema, manifest, test_dir)

        # 4. Run every file through the ETL and judge each reaction
        if not db_url and not cfg.etl_command:
            db_url = f"sqlite:///{(out / 'target.db').as_posix()}"
        verify = None
        if db_url and schema.primary_key:
            verify = make_verifier(db_url, cfg.table, schema.primary_key)
        runner = self._runner(schema, db_url)
        try:
            results = run_suite(test_dir, runner, verify, cfg.strict_reason)
        except EtlUnavailable as exc:
            log("STOPPED: ETL command could not be started.")
            log("No files were tested.")
            log(f"Details: {exc}")
            log("Try running your ETL command by hand first.")
            return AgentResult(2, "ETL could not be started")

        # 5. Report
        report = write_agent_report(results, str(out))
        cols = ["scenario", "expected", "actual", "verdict"]
        log(results[cols].to_string(index=False))
        failed = results[results["verdict"] != "PASS"]
        passed_n = len(results) - len(failed)
        log(f"\n{passed_n} of {len(results)} passed. Report: {report}")
        for r in failed.itertuples():
            log(f"  FAILED {r.scenario}: {r.detail}")

        # 6. Optional AI explanation
        explanation = ""
        if self.llm and cfg.ai_explain and not failed.empty:
            try:
                explanation = explain_failures(results, schema, self.llm)
                (out / "ai_explanation.md").write_text(
                    explanation, encoding="utf-8")
                log("\n--- AI explanation ---")
                log(explanation)
            except LLMUnavailable as exc:
                log(f"AI explanation skipped: {exc}")

        code = 0 if failed.empty else 1
        return AgentResult(code, "", results, report, explanation)