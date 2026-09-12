"""Offline re-derivation of cell summaries from stored records.

Two families, deliberately separated. ``extra_counts`` reports OUTCOMES that
belong in the results table: seeds the harness lost, and seeds a person has to
adjudicate. ``harness_health`` reports defects in the apparatus, which belong in
a fix and never in the table. These tests pin the counting, not any verdict --
re-grading must never silently change a pass or a fail.
"""

from __future__ import annotations

import json

from hangar.evals.regrade import (
    extra_counts,
    harness_health,
    load_records,
    regrade_file,
)


def _rec(seed, passed=True, ambiguity=0, reported=None, case="paraboloid",
         selection=None):
    rec = {
        "case": case, "harness": "claude", "model": "m", "seed": seed,
        "completed": True, "passed": passed, "scores": [],
        "reporting": {"parsed": reported is not None, "passed": reported,
                      "matches_effects": None, "scores": None},
        "oracle": {"n_runs": 1, "ambiguity": ambiguity, "runs": [],
                   **({"selection": selection} if selection else {})},
        "tool_use": {}, "tool_trace": [], "provenance": None,
        "telemetry": {"wall_clock_s": 1.0, "cost_usd": None, "num_turns": 1,
                      "tokens": None, "omd_transport": "http",
                      "sandbox": "container", "sandbox_image": None},
    }
    return rec


def test_a_contradicted_grade_is_routed_to_a_person():
    recs = [_rec(0, passed=False, ambiguity=2, reported=True),
            _rec(1, passed=True, ambiguity=0, reported=True)]
    assert extra_counts(recs) == {"n_harness_errors": 0, "n_needs_review": 1}


def test_an_agreeing_report_needs_no_review():
    # The observed ocp_oas_coupled shape: a same-mode run was skipped, but the
    # last one was the right one, so the grade and the report still agree.
    recs = [_rec(0, passed=True, ambiguity=3, reported=True)]
    assert extra_counts(recs)["n_needs_review"] == 0


def test_an_unparsed_report_cannot_contradict_anything():
    recs = [_rec(0, passed=False, ambiguity=0, reported=None)]
    assert extra_counts(recs)["n_needs_review"] == 0


def test_an_error_row_counts_as_a_harness_error():
    # The only failure that earns a re-run: nothing was measured.
    err = _rec(0, passed=False)
    err["error"] = {"type": "HarnessError", "message": "exited 1, no runs"}
    assert extra_counts([err, _rec(1)])["n_harness_errors"] == 1


def test_iterating_is_not_a_defect_when_the_agent_names_its_answer():
    # Three skipped same-mode runs used to count against the apparatus. Since
    # the agent names the run it is graded on, that is just iteration.
    recs = [_rec(0, passed=True, ambiguity=3, reported=True,
                 selection="named"), _rec(1)]
    assert harness_health(recs)["n_unnamed_selection"] == 0


def test_a_guessed_selection_is_the_defect():
    # No usable run_id AND more than one run: the policy chose for the agent.
    recs = [_rec(0, passed=True, ambiguity=3, reported=True,
                 selection="fallback_last"), _rec(1)]
    assert harness_health(recs)["n_unnamed_selection"] == 1
    assert "n_unnamed_selection" not in extra_counts(recs)


def test_a_fallback_with_nothing_to_choose_between_is_harmless():
    # One run, no report: nothing was guessed, so nothing to fix.
    recs = [_rec(0, passed=True, ambiguity=0, reported=None,
                 selection="fallback_last")]
    assert harness_health(recs)["n_unnamed_selection"] == 0


def test_records_predating_the_selection_change_fall_back_to_ambiguity():
    # No "selection" key at all -- those arms were all graded positionally.
    recs = [_rec(0, passed=True, ambiguity=2, reported=True)]
    assert "selection" not in recs[0]["oracle"]
    assert harness_health(recs)["n_unnamed_selection"] == 1


def test_harness_health_counts_a_graded_run_that_crashed_on_the_way():
    degraded = _rec(0, passed=True, reported=True)
    degraded["telemetry"]["exit_code"] = 1
    assert harness_health([degraded])["n_degraded"] == 1


def test_load_records_keeps_the_retry_not_the_superseded_row(tmp_path):
    p = tmp_path / "run.jsonl"
    superseded = _rec(0, passed=False)
    superseded["error"] = {"type": "RuntimeError", "message": "boom"}
    p.write_text("\n".join(json.dumps(r) for r in [superseded, _rec(0, passed=True)]))
    recs = load_records(p)
    assert len(recs) == 1 and recs[0]["passed"] is True


def test_regrade_file_summarises_per_cell_and_preserves_pass_count(tmp_path):
    p = tmp_path / "run.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in [
        _rec(0, passed=True, ambiguity=1, reported=True),
        _rec(1, passed=False, ambiguity=1, reported=True),
    ]))
    [summary] = regrade_file(p)
    assert summary["n_seeds"] == 2
    assert summary["n_passed"] == 1          # unchanged by re-grading
    assert summary["n_needs_review"] == 1
    assert summary["n_harness_errors"] == 0
    # The apparatus defect rides along for fixing, outside the result fields.
    assert summary["harness_health"]["n_unnamed_selection"] == 2
