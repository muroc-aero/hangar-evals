"""Offline re-derivation of cell summaries from stored records.

The counts under test exist to stop an ORDER-DEPENDENT score from reading as a
clean fail: the oracle grades the last successful run of the matching mode, and
several Lane C prompts ask for a comparison run, so an agent that obeys can
leave a control run last. These tests pin the counting, not any verdict --
re-grading must never silently change a pass or a fail.
"""

from __future__ import annotations

import json

from hangar.evals.regrade import extra_counts, load_records, regrade_file


def _rec(seed, passed=True, ambiguity=0, reported=None, case="paraboloid"):
    rec = {
        "case": case, "harness": "claude", "model": "m", "seed": seed,
        "completed": True, "passed": passed, "scores": [],
        "reporting": {"parsed": reported is not None, "passed": reported,
                      "matches_effects": None, "scores": None},
        "oracle": {"n_runs": 1, "ambiguity": ambiguity, "runs": []},
        "tool_use": {}, "tool_trace": [], "provenance": None,
        "telemetry": {"wall_clock_s": 1.0, "cost_usd": None, "num_turns": 1,
                      "tokens": None, "omd_transport": "http",
                      "sandbox": "container", "sandbox_image": None},
    }
    return rec


def test_counts_ambiguous_seeds_without_touching_verdicts():
    recs = [_rec(0, passed=False, ambiguity=2, reported=True),
            _rec(1, passed=True, ambiguity=0, reported=True)]
    assert extra_counts(recs) == {"n_ambiguous": 1, "n_report_disagrees": 1}


def test_ambiguity_alone_is_not_a_disagreement():
    # The observed ocp_oas_coupled shape: the skipped run existed, but the
    # last one was the right one, so the grade and the report still agree.
    recs = [_rec(0, passed=True, ambiguity=3, reported=True)]
    assert extra_counts(recs) == {"n_ambiguous": 1, "n_report_disagrees": 0}


def test_unparsed_report_is_not_counted_as_disagreeing():
    recs = [_rec(0, passed=False, ambiguity=0, reported=None)]
    assert extra_counts(recs)["n_report_disagrees"] == 0


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
    assert summary["n_ambiguous"] == 2
    assert summary["n_report_disagrees"] == 1
