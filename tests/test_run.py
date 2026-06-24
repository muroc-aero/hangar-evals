"""Tests for the runner — run_cell with a fake driver (offline).

compute_refs runs the real paraboloid Lane A through the seam (needs the-hangar
in the interpreter), but no agent is launched: a fake driver returns a canned
report + tool trace, so the result-record assembly is deterministic.
"""

from __future__ import annotations

from hangar.evals.cases import CASES, build_prompt
from hangar.evals.drivers.base import AgentResult
from hangar.evals.run import run_cell
from hangar.evals.scoring import compute_refs
from hangar.evals.trace import ToolCall


class _FakeDriver:
    """Returns a fixed AgentResult; records the prompt it was handed."""

    def __init__(self, final_text, trace=None):
        self.final_text = final_text
        self.trace = trace or []
        self.seen_prompt = None

    def run(self, prompt, mcp, data_root, model=None, max_turns=80):
        self.seen_prompt = prompt
        return AgentResult(
            final_text=self.final_text,
            cost_usd=0.0,
            wall_clock_s=1.0,
            num_turns=3,
            tool_call_trace=self.trace,
        )


def _correct_report():
    case = CASES["paraboloid"]
    refs = compute_refs(case.example, case.metrics)
    return (
        'done\n```json\n{"metrics": {'
        f'"analysis_f_xy": {refs["analysis"]["f_xy"]}, '
        f'"opt_f_xy": {refs["optimization"]["f_xy"]}, '
        f'"opt_x": {refs["optimization"]["x"]}, '
        f'"opt_y": {refs["optimization"]["y"]}'
        '}}\n```'
    )


def test_run_cell_correct_report_passes(tmp_path):
    (tmp_path / "run_data").mkdir()
    trace = [ToolCall("start_session", ok=True), ToolCall("run_plan", ok=True)]
    driver = _FakeDriver(_correct_report(), trace)

    rec = run_cell(CASES["paraboloid"], driver, "fake", "m0", 0, tmp_path)

    assert rec["completed"] is True
    assert rec["passed"] is True
    assert {s["verdict"] for s in rec["scores"]} == {"PASS"}
    assert rec["tool_use"]["total_calls"] == 2
    assert rec["tool_use"]["valid_call_rate"] == 1.0
    # Per-call trace is recorded (which tools ran, not just counts).
    assert rec["tool_trace"] == [
        {"tool": "start_session", "ok": True, "error_code": None},
        {"tool": "run_plan", "ok": True, "error_code": None},
    ]
    assert rec["telemetry"]["num_turns"] == 3
    # No omd run happened (fake driver), so no provenance DB.
    assert rec["provenance"] is None
    # The agent got the real task prompt with the report format.
    assert "REPORT FORMAT" in driver.seen_prompt
    assert "Paraboloid" in driver.seen_prompt


def test_run_cell_no_report_is_incomplete(tmp_path):
    (tmp_path / "run_data").mkdir()
    driver = _FakeDriver("the model rambled but emitted no fenced json")

    rec = run_cell(CASES["paraboloid"], driver, "fake", "m0", 0, tmp_path)

    assert rec["completed"] is False
    assert rec["passed"] is False
    assert rec["scores"] is None


def test_run_cell_wrong_number_fails(tmp_path):
    (tmp_path / "run_data").mkdir()
    report = 'x\n```json\n{"metrics": {"analysis_f_xy": 999.0}}\n```'
    driver = _FakeDriver(report)

    rec = run_cell(CASES["paraboloid"], driver, "fake", "m0", 0, tmp_path)

    assert rec["completed"] is True
    assert rec["passed"] is False
    verdicts = {s["key"]: s["verdict"] for s in rec["scores"]}
    assert verdicts["analysis_f_xy"] == "FAIL"


def test_build_prompt_is_harness_neutral():
    prompt = build_prompt(CASES["paraboloid"])
    assert "mcp__omd__" not in prompt   # no Claude-specific tool namespace
    assert "omd_start_session" not in prompt
    assert "ONLY the omd tools" in prompt
