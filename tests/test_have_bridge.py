"""Tests for the have-agent bridge — no agent, no have-agent install needed.

``run_matrix`` is monkeypatched with a fake that records the RunConfig it was
handed and writes whatever result files a test injects; the CheckSuite tests
write summary files directly. The seam itself (plugin loading, worker wiring)
is covered on the have-agent side (its tests/test_plugins.py); here we cover
the hangar-evals half: payload -> RunConfig mapping, job success semantics,
verdict folding, and the factory's --executor-opt parsing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from hangar.evals import have_bridge
from hangar.evals.cases import CASES
from hangar.evals.have_bridge import (
    LaneCEvalCheckSuite,
    LaneCEvalExecutor,
    make_worker,
)
from hangar.evals.run import HARNESSES

EXAMPLE_YAML = Path(__file__).parent.parent / "examples" / "lane_c_eval.yaml"


def _summary_obj(**kw) -> SimpleNamespace:
    base = dict(
        case="paraboloid", harness="claude", model="claude-opus-4-8",
        n_seeds=1, n_completed=1, n_passed=1,
        completion_rate=1.0, pass_rate=1.0,
        per_metric_pass={"f_xy": 1},
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def fake_matrix(monkeypatch, tmp_path):
    """Patch run_matrix; returns a recorder with .config/.stamp/.summary."""
    rec = SimpleNamespace(config=None, stamp=None, summary=_summary_obj(),
                          error_rows=[], calls=0)

    def _fake(config, stamp, resume_records=None):
        rec.calls += 1
        rec.config = config
        rec.stamp = stamp
        results_dir = Path(config.results_dir)
        results_dir.mkdir(parents=True, exist_ok=True)
        base = f"{config.case}_{stamp}"
        with (results_dir / f"{base}.jsonl").open("w") as fh:
            for row in rec.error_rows:
                fh.write(json.dumps(row) + "\n")
        (results_dir / f"{base}_summary.json").write_text("[{}]")
        return [rec.summary]

    monkeypatch.setattr(have_bridge, "run_matrix", _fake)
    return rec


# --- executor: payload -> RunConfig ------------------------------------------

def test_execute_maps_overrides_onto_defaults(fake_matrix, tmp_path):
    ex = LaneCEvalExecutor(str(tmp_path), seeds=3, model="m-default",
                           omd_transport="http", sandbox="container")
    res = ex.execute(
        {"overrides": {"case": "pyc_turbojet", "harness": "opencode",
                       "seeds": 2, "model": "qwen3:8b", "max_turns": 40,
                       "timeout_s": 120}},
        study_id="study:abc", job_id="job:0123", attempt=1,
    )
    assert res.ok
    c = fake_matrix.config
    assert (c.case, c.harnesses, c.model) == ("pyc_turbojet", ("opencode",), "qwen3:8b")
    assert (c.seeds, c.max_turns, c.timeout_s) == (2, 40, 120.0)
    # untouched keys fall back to executor defaults
    assert (c.omd_transport, c.sandbox) == ("http", "container")
    assert c.results_dir == str(tmp_path)


def test_execute_defaults_when_overrides_minimal(fake_matrix, tmp_path):
    ex = LaneCEvalExecutor(str(tmp_path))
    res = ex.execute(
        {"overrides": {"case": "paraboloid", "harness": "claude"}},
        study_id="s", job_id="job:xyz", attempt=1,
    )
    assert res.ok
    c = fake_matrix.config
    assert (c.seeds, c.model, c.max_turns, c.timeout_s) == (1, None, None, None)
    assert (c.omd_transport, c.sandbox) == ("stdio", "none")


def test_stamp_is_timestamped_and_job_scoped(fake_matrix, tmp_path):
    # make_tables keeps the lexicographically-last summary per cell, so the
    # stamp MUST lead with a UTC timestamp like manual runs do.
    ex = LaneCEvalExecutor(str(tmp_path))
    ex.execute({"overrides": {"case": "paraboloid", "harness": "claude"}},
               study_id="s", job_id="job:01HXYZ99", attempt=2)
    assert re.fullmatch(r"\d{8}T\d{6}Z_have_01HXYZ99_a2", fake_matrix.stamp)


def test_execute_success_payload(fake_matrix, tmp_path):
    fake_matrix.summary = _summary_obj(n_seeds=3, n_passed=2, pass_rate=2 / 3)
    ex = LaneCEvalExecutor(str(tmp_path))
    res = ex.execute({"overrides": {"case": "paraboloid", "harness": "claude"}},
                     study_id="s", job_id="j", attempt=1)
    assert res.ok and res.error is None and not res.permanent
    assert res.run_ref.endswith("_summary.json")
    assert Path(res.run_ref).exists()
    assert len(res.artifacts) == 3
    assert res.result["pass_rate"] == pytest.approx(2 / 3)
    assert res.result["per_metric_pass"] == {"f_xy": 1}
    assert res.result["stamp"] == fake_matrix.stamp


def test_execute_fails_job_on_harness_crash_rows(fake_matrix, tmp_path):
    # a graded FAIL is a successful job; an error ROW (harness crash) is not
    fake_matrix.error_rows = [
        {"seed": 0, "error": {"type": "TimeoutError", "message": "boom"}},
        {"seed": 1, "error": {"type": "RuntimeError", "message": "bang"}},
    ]
    fake_matrix.summary = _summary_obj(n_seeds=2, n_passed=0, pass_rate=0.0)
    ex = LaneCEvalExecutor(str(tmp_path))
    res = ex.execute({"overrides": {"case": "paraboloid", "harness": "claude"}},
                     study_id="s", job_id="j", attempt=1)
    assert not res.ok and not res.permanent  # retryable
    assert "2/2 seed(s) crashed" in res.error
    assert "RuntimeError" in res.error and "TimeoutError" in res.error
    assert res.run_ref.endswith("_summary.json")  # evidence still linked


@pytest.mark.parametrize("overrides, fragment", [
    ({"harness": "claude"}, "missing required key 'case'"),
    ({"case": "paraboloid"}, "missing required key 'harness'"),
    ({"case": "nope", "harness": "claude"}, "unknown eval case"),
    ({"case": "paraboloid", "harness": "nope"}, "unknown harness"),
    ({"case": "paraboloid", "harness": "claude", "sedes": 3}, "unknown override key"),
    # RunConfig's own validation propagates (container requires http)
    ({"case": "paraboloid", "harness": "claude", "sandbox": "container"},
     "requires omd_transport='http'"),
])
def test_execute_rejects_bad_payload_permanently(
    fake_matrix, tmp_path, overrides, fragment
):
    ex = LaneCEvalExecutor(str(tmp_path))
    res = ex.execute({"overrides": overrides}, study_id="s", job_id="j", attempt=1)
    assert not res.ok and res.permanent
    assert fragment in res.error
    assert fake_matrix.calls == 0  # failed fast, no agent run


# --- check suite --------------------------------------------------------------

def _write_summary(tmp_path, **kw) -> str:
    d = dict(case="paraboloid", harness="claude", model="m", n_seeds=3,
             n_completed=3, n_passed=3, completion_rate=1.0, pass_rate=1.0,
             per_metric_pass={"f_xy": 3})
    d.update(kw)
    path = tmp_path / "cell_summary.json"
    path.write_text(json.dumps([d]))
    return str(path)


def test_check_pass_when_all_seeds_pass(tmp_path):
    level, checks = LaneCEvalCheckSuite().run(
        "paraboloid_claude", _write_summary(tmp_path), {})
    assert level == "pass"
    assert {c["check"] for c in checks} == {"completion", "pass_rate", "per_metric"}


def test_check_warn_on_partial_pass(tmp_path):
    ref = _write_summary(tmp_path, n_passed=1, pass_rate=1 / 3)
    level, _ = LaneCEvalCheckSuite().run("c", ref, {})
    assert level == "warn"


def test_check_min_pass_rate_from_acceptance(tmp_path):
    ref = _write_summary(tmp_path, n_passed=2, pass_rate=2 / 3)
    level, _ = LaneCEvalCheckSuite().run("c", ref, {"min_pass_rate": 0.5})
    assert level == "pass"


def test_check_fail_when_ran_but_never_passed(tmp_path):
    ref = _write_summary(tmp_path, n_passed=0, pass_rate=0.0)
    level, checks = LaneCEvalCheckSuite().run("c", ref, {})
    assert level == "fail"
    assert next(c for c in checks if c["check"] == "completion")["level"] == "pass"


def test_check_fail_dominates_when_no_run_at_all(tmp_path):
    ref = _write_summary(tmp_path, n_completed=0, n_passed=0,
                         completion_rate=0.0, pass_rate=0.0)
    level, checks = LaneCEvalCheckSuite().run("c", ref, {})
    assert level == "fail"
    assert next(c for c in checks if c["check"] == "completion")["level"] == "fail"


def test_check_error_without_evidence(tmp_path):
    suite = LaneCEvalCheckSuite()
    assert suite.run("c", None, {})[0] == "error"
    assert suite.run("c", str(tmp_path / "missing.json"), {})[0] == "error"


# --- factory ------------------------------------------------------------------

def test_make_worker_defaults():
    executor, suite = make_worker(SimpleNamespace(executor_opts={}))
    assert isinstance(executor, LaneCEvalExecutor)
    assert isinstance(suite, LaneCEvalCheckSuite)
    assert (executor.results_dir, executor.seeds) == ("results", 1)
    assert (executor.omd_transport, executor.sandbox) == ("stdio", "none")


def test_make_worker_coerces_opt_strings():
    executor, _ = make_worker(SimpleNamespace(executor_opts={
        "results_dir": "/data/results", "seeds": "3", "model": "qwen3:8b",
        "max_turns": "40", "timeout_s": "120",
        "omd_transport": "http", "sandbox": "container",
    }))
    assert executor.results_dir == "/data/results"
    assert (executor.seeds, executor.max_turns, executor.timeout_s) == (3, 40, 120.0)
    assert (executor.model, executor.sandbox) == ("qwen3:8b", "container")


def test_make_worker_rejects_unknown_opt():
    with pytest.raises(ValueError, match="unknown --executor-opt"):
        make_worker(SimpleNamespace(executor_opts={"result_dir": "x"}))


# --- example StudyRequest ------------------------------------------------------

def test_example_study_covers_the_full_suite():
    spec = yaml.safe_load(EXAMPLE_YAML.read_text())
    assert spec["baseline"]["template"].startswith("evals/")
    cases = spec["cases"]
    ids = [c["case_id"] for c in cases]
    assert len(ids) == len(set(ids))
    eval_cases = {c["overrides"]["case"] for c in cases}
    assert eval_cases == set(CASES)  # every suite case, nothing else
    for c in cases:
        ov = c["overrides"]
        assert ov["harness"] in HARNESSES
        assert set(ov) <= have_bridge._CELL_KEYS
