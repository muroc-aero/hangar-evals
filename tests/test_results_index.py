"""Whether a case is done -- answered from records, not from a summary.

The bug these pin cost three seeds of the 2026-09-10 anchor arm. The old check
asked whether the cell's summary had ``n_seeds >= seeds``; an error row counts
as a seed, so ``ocp_caravan_basic`` -- whose seed 0 raised at 19:07 -- looked
finished on every later run of the bundle and was never retried.

The distinction the tests hold: a graded FAIL is a RESULT and stays put; an
error row is an ABSENCE and comes back.
"""

from __future__ import annotations

import json

from hangar.evals.results_index import case_status
from hangar.evals.run import RunConfig


def _rec(seed, *, error=False, passed=True, case="paraboloid", model="claude-opus-5"):
    rec = {"case": case, "harness": "claude", "model": model, "seed": seed,
           "completed": not error, "passed": passed and not error, "scores": [],
           "reporting": {}, "oracle": {"ambiguity": 0}, "tool_use": {},
           "telemetry": {"wall_clock_s": 1.0, "num_turns": 1}}
    if error:
        rec["error"] = {"type": "RuntimeError", "message": "sandboxed run failed"}
    return rec


def _write(results, name, records):
    (results / f"{name}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n")
    (results / f"{name}_config.json").write_text(json.dumps({"stamp": "S"}))


def _config(tmp_path, seeds=3):
    return RunConfig(case="paraboloid", harnesses=("claude",),
                     model="claude-opus-5", seeds=seeds,
                     results_dir=str(tmp_path))


def test_no_records_is_not_started(tmp_path):
    assert case_status(_config(tmp_path)).state == "not_started"


def test_all_seeds_clean_is_graded(tmp_path):
    _write(tmp_path, "paraboloid_20260101T000000Z", [_rec(i) for i in range(3)])
    status = case_status(_config(tmp_path))
    assert status.state == "graded" and not status.should_run


def test_an_error_row_makes_the_case_resumable(tmp_path):
    # The exact 2026-09-10 shape: three seeds present, seed 0 an error row.
    _write(tmp_path, "paraboloid_20260101T000000Z",
           [_rec(0, error=True), _rec(1), _rec(2)])
    status = case_status(_config(tmp_path))
    assert status.state == "resumable"
    assert status.n_error_seeds == 1
    assert status.records.name == "paraboloid_20260101T000000Z.jsonl"


def test_a_graded_failure_is_a_result_not_an_absence(tmp_path):
    # Every seed ran and every seed failed the metrics -- nothing to retry.
    _write(tmp_path, "paraboloid_20260101T000000Z",
           [_rec(i, passed=False) for i in range(3)])
    status = case_status(_config(tmp_path))
    assert status.state == "graded" and status.n_passed == 0


def test_missing_seeds_are_resumable(tmp_path):
    _write(tmp_path, "paraboloid_20260101T000000Z", [_rec(0), _rec(1)])
    status = case_status(_config(tmp_path))
    assert status.state == "resumable" and "1 seed(s) never ran" in status.reason


def test_a_retry_supersedes_the_error_row_it_replaced(tmp_path):
    _write(tmp_path, "paraboloid_20260101T000000Z",
           [_rec(0, error=True), _rec(1), _rec(2), _rec(0)])
    assert case_status(_config(tmp_path)).state == "graded"


def test_another_models_records_do_not_answer_for_this_one(tmp_path):
    _write(tmp_path, "paraboloid_20260101T000000Z",
           [_rec(i, model="claude-opus-4-8") for i in range(3)])
    assert case_status(_config(tmp_path)).state == "not_started"


def test_the_newest_matching_file_wins(tmp_path):
    _write(tmp_path, "paraboloid_20260101T000000Z", [_rec(i) for i in range(3)])
    _write(tmp_path, "paraboloid_20260202T000000Z",
           [_rec(0, error=True), _rec(1), _rec(2)])
    status = case_status(_config(tmp_path))
    assert status.state == "resumable"
    assert status.records.name == "paraboloid_20260202T000000Z.jsonl"


def test_a_case_whose_name_prefixes_another_is_not_confused(tmp_path):
    # ocp_caravan_basic must never be answered by ocp_caravan_full's records.
    _write(tmp_path, "ocp_caravan_full_20260101T000000Z",
           [_rec(i, case="ocp_caravan_full") for i in range(3)])
    config = RunConfig(case="ocp_caravan_basic", harnesses=("claude",),
                       model="claude-opus-5", seeds=3, results_dir=str(tmp_path))
    assert case_status(config).state == "not_started"
