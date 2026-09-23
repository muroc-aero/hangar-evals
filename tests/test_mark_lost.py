"""`evals mark-lost`: a graded seed that was really a harness loss becomes an
error row the honest resume retries -- without touching the other seeds."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hangar.evals.mark_lost import ERROR_TYPE, mark_lost, restore_marked
from hangar.evals.regrade import load_records
from hangar.evals.results_index import case_status
from hangar.evals.run import load_resume_records


def _record(case, seed, *, passed, harness="opencode", model="qwen3.6:35b-mlx",
            timed_out=False):
    return {
        "case": case, "harness": harness, "model": model, "seed": seed,
        "completed": True, "passed": passed, "scores": {"CL": passed},
        "reporting": {"parsed": True, "passed": passed, "matches_effects": True,
                      "scores": {}},
        "oracle": {"run_id": "r1"}, "tool_use": {}, "tool_trace": [],
        "provenance": {}, "telemetry": {"wall_clock_s": 1100.0 if timed_out else 300.0,
                                        "timed_out": timed_out, "num_turns": 5},
    }


def _write(path: Path, records) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


@pytest.fixture
def results(tmp_path):
    d = tmp_path / "results"
    d.mkdir()
    _write(d / "pyc_turbojet_20260901T000000Z.jsonl",
           [_record("pyc_turbojet", s, passed=False) for s in range(5)])  # older arm
    _write(d / "pyc_turbojet_20260923T015151Z.jsonl", [
        _record("pyc_turbojet", 0, passed=False),
        _record("pyc_turbojet", 1, passed=False),
        _record("pyc_turbojet", 2, passed=True),
        _record("pyc_turbojet", 3, passed=False),
        _record("pyc_turbojet", 4, passed=False, timed_out=True),
    ])
    return d


def test_mark_lost_appends_a_superseding_error_row(results):
    out = mark_lost("pyc_turbojet", [4], "sandbox stalled: model idle 16 min", results)
    assert out["file"].endswith("pyc_turbojet_20260923T015151Z.jsonl")
    assert out["marked"] == [("opencode", "qwen3.6:35b-mlx", 4)]

    lines = Path(out["file"]).read_text().splitlines()
    assert len(lines) == 6                       # original rows kept, one appended
    row = json.loads(lines[-1])
    assert row["seed"] == 4 and row["error"] == {
        "type": ERROR_TYPE, "message": "sandbox stalled: model idle 16 min"}
    assert row["passed"] is False and row["scores"] is None
    assert row["marked_lost"]["superseded"] == {
        "passed": False, "completed": True, "timed_out": True, "wall_clock_s": 1100.0}

    # last row per seed wins for every reader
    latest = {r["seed"]: r for r in load_records(Path(out["file"]))}
    assert latest[4].get("error") and latest[2]["passed"] is True
    assert [r["seed"] for r in load_resume_records(Path(out["file"]))] == [0, 1, 2, 3]


def test_marked_cell_is_resumable_for_exactly_those_seeds(results):
    from types import SimpleNamespace

    config = SimpleNamespace(case="pyc_turbojet", harnesses=["opencode"],
                             model="qwen3.6:35b-mlx", seeds=5, results_dir=results)
    assert case_status(config).state == "graded"
    mark_lost("pyc_turbojet", [0, 4], "stalled", results)
    status = case_status(config)
    assert status.state == "resumable" and status.n_error_seeds == 2
    assert status.records.name == "pyc_turbojet_20260923T015151Z.jsonl"


def test_mark_lost_is_idempotent_and_needs_a_reason(results):
    mark_lost("pyc_turbojet", [4], "stalled", results)
    again = mark_lost("pyc_turbojet", [4], "stalled again", results)
    assert again["marked"] == []                 # already an error row
    with pytest.raises(ValueError, match="reason"):
        mark_lost("pyc_turbojet", [1], "   ", results)
    with pytest.raises(ValueError, match="no records file"):
        mark_lost("pyc_turbojet", [9], "stalled", results)
    with pytest.raises(ValueError, match="no records file"):
        mark_lost("pyc_turbojet", [1], "stalled", results, model="other")


def test_cli_mark_lost(results, capsys):
    from hangar.evals.campaign import main

    rc = main(["mark-lost", "pyc_turbojet", "--seeds", "4", "--reason", "stalled",
               "--results-dir", str(results)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "marked lost  pyc_turbojet · opencode/qwen3.6:35b-mlx · seed 4" in out


def test_restore_marked_puts_the_original_grade_back_over_a_rerun(results):
    mark_lost("pyc_turbojet", [4], "thought it hung", results)
    path = results / "pyc_turbojet_20260923T015151Z.jsonl"
    with path.open("a") as fh:                          # a resume re-ran the seed
        fh.write(json.dumps(_record("pyc_turbojet", 4, passed=True)) + "\n")
    assert {r["seed"]: r["passed"] for r in load_records(path)}[4] is True

    out = restore_marked("pyc_turbojet", [4, 2], "it was a 32k-token step, not a hang",
                         results)
    assert out["restored"] == [("opencode", "qwen3.6:35b-mlx", 4)]
    assert out["unmarked"] == [2]                       # never marked: untouched

    lines = path.read_text().splitlines()
    assert len(lines) == 8                              # 5 + mark + rerun + restore
    row = json.loads(lines[-1])
    assert row["passed"] is False and row["telemetry"]["timed_out"] is True
    assert "error" not in row and "marked_lost" not in row
    assert [s["kind"] for s in row["restored_from_lost"]["supersedes"]] == [
        "marked_lost", "rerun"]
    latest = {r["seed"]: r for r in load_records(path)}
    assert latest[4]["passed"] is False and latest[4]["telemetry"]["wall_clock_s"] == 1100.0
    assert [r["seed"] for r in load_resume_records(path)] == [0, 1, 2, 3, 4]
    with pytest.raises(ValueError, match="reason"):
        restore_marked("pyc_turbojet", [4], "", results)


def test_cli_mark_lost_undo(results, capsys):
    from hangar.evals.campaign import main

    mark_lost("pyc_turbojet", [4], "stalled", results)
    rc = main(["mark-lost", "pyc_turbojet", "--seeds", "4", "--undo",
               "--reason", "not a hang", "--results-dir", str(results)])
    assert rc == 0
    assert "restored     pyc_turbojet · opencode/qwen3.6:35b-mlx · seed 4" in capsys.readouterr().out
