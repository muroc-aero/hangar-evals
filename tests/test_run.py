"""Tests for the runner — run_cell with a fake driver (offline).

compute_refs runs the real paraboloid Lane A through the seam (needs the-hangar
in the interpreter), but no agent is launched: a fake driver returns a canned
report + tool trace, so the result-record assembly is deterministic.
"""

from __future__ import annotations

import json

import pytest

from hangar.evals import run as run_mod
from hangar.evals.cases import CASES, build_prompt
from hangar.evals.drivers.base import AgentResult
from hangar.evals.run import RunConfig, run_cell, run_matrix
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


# --- RunConfig: the scriptable/reproducible unit -------------------------------


def test_run_config_round_trips_through_json():
    cfg = RunConfig(case="paraboloid", harnesses=("opencode", "claude"),
                    model="qwen3.6:35b-mlx", seeds=3, max_turns=40)
    again = RunConfig.from_dict(json.loads(json.dumps(cfg.to_dict())))
    assert again == cfg
    assert isinstance(again.harnesses, tuple)   # JSON array -> tuple


def test_run_config_from_json_file(tmp_path):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"case": "paraboloid", "harnesses": ["opencode"],
                             "model": "qwen3.6:35b-mlx", "seeds": 5}))
    cfg = RunConfig.from_json_file(p)
    assert cfg.model == "qwen3.6:35b-mlx" and cfg.seeds == 5
    assert cfg.harnesses == ("opencode",)


def test_run_config_rejects_unknown_keys():
    with pytest.raises(ValueError, match="unknown keys"):
        RunConfig.from_dict({"case": "paraboloid", "temperature": 0.7})


# --- run_matrix: multi-seed wiring + manifest (run_cell faked) ------------------


def _fake_record(seed, *, passed):
    """A complete per-seed record (every field _print_summary/aggregate read)."""
    return {
        "case": "paraboloid", "harness": "fake", "model": "m0", "seed": seed,
        "completed": True, "passed": passed,
        "scores": [{"key": "analysis_f_xy", "lane_a": 39.0,
                    "agent": 39.0 if passed else 22.0, "rel_err": 0.0,
                    "verdict": "PASS" if passed else "FAIL"}],
        "tool_use": {"total_calls": 5, "valid_call_rate": 0.9, "schema_errors": 0,
                     "hallucinated_calls": 0, "recovered_errors": 0},
        "telemetry": {"num_turns": 10 + seed, "wall_clock_s": 100.0 + seed,
                      "cost_usd": 0.0},
    }


def test_run_matrix_writes_records_manifest_and_summary(monkeypatch, tmp_path):
    # seed 1 fails, seeds 0 and 2 pass -> 2/3 pass-rate, no driver/the-hangar.
    def fake_run_cell(case, driver, harness, model, seed, results_dir, max_turns):
        return _fake_record(seed, passed=(seed != 1))

    monkeypatch.setattr(run_mod, "run_cell", fake_run_cell)
    monkeypatch.setitem(run_mod.HARNESSES, "fake", (lambda: object(), "m0"))

    cfg = RunConfig(case="paraboloid", harnesses=("fake",), seeds=3,
                    results_dir=str(tmp_path))
    summaries = run_matrix(cfg, stamp="20260625T000000Z")

    assert len(summaries) == 1
    s = summaries[0]
    assert s.n_passed == 2 and s.n_seeds == 3 and s.completion_rate == 1.0

    base = tmp_path / "paraboloid_20260625T000000Z"
    # Per-seed records: one JSON line per seed.
    lines = base.with_suffix(".jsonl").read_text().strip().splitlines()
    assert len(lines) == 3
    # Manifest reproduces the run via --config.
    manifest = json.loads((tmp_path / "paraboloid_20260625T000000Z_config.json").read_text())
    assert manifest["config"] == cfg.to_dict()
    assert RunConfig.from_dict(manifest["config"]) == cfg
    # Summary persisted as JSON-shaped CellSummary list.
    summ = json.loads((tmp_path / "paraboloid_20260625T000000Z_summary.json").read_text())
    assert summ[0]["pass_rate"] == pytest.approx(2 / 3)
    assert summ[0]["per_metric_pass"] == {"analysis_f_xy": 2}


def test_run_matrix_rejects_unknown_harness(tmp_path):
    cfg = RunConfig(case="paraboloid", harnesses=("nope",), seeds=1,
                    results_dir=str(tmp_path))
    with pytest.raises(ValueError, match="unknown harness"):
        run_matrix(cfg, stamp="20260625T000000Z")
