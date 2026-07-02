"""Tests for cell aggregation — pure over synthetic per-seed records.

Fully offline: no driver, no the-hangar. Each record matches the shape
``run_cell`` emits, only with the fields aggregation reads.
"""

from __future__ import annotations

import pytest

from hangar.evals.aggregate import Stat, aggregate_cell


def _record(seed, *, completed=True, passed=True, f_xy_pass=True,
            turns=10, wall=100.0, valid_rate=0.9):
    """A minimal per-seed record for cell (paraboloid, opencode, qwen)."""
    scores = None
    if completed:
        scores = [{"key": "analysis_f_xy", "lane_a": 39.0,
                   "agent": 39.0 if f_xy_pass else 22.0, "rel_err": 0.0,
                   "verdict": "PASS" if f_xy_pass else "FAIL"}]
    return {
        "case": "paraboloid", "harness": "opencode", "model": "qwen",
        "seed": seed, "completed": completed, "passed": passed,
        "scores": scores,
        "tool_use": {"valid_call_rate": valid_rate},
        "telemetry": {"num_turns": turns, "wall_clock_s": wall},
    }


def test_stat_of_skips_none_and_computes_spread():
    assert Stat.of([3, 1, 2]) == Stat(min=1, median=2.0, max=3)
    assert Stat.of([5, None, 1]) == Stat(min=1, median=3.0, max=5)
    assert Stat.of([None, None]) is None
    assert Stat.of([]) is None


def test_aggregate_pass_rate_and_spreads():
    records = [
        _record(0, passed=True, f_xy_pass=True, turns=8, wall=90.0, valid_rate=1.0),
        _record(1, passed=False, f_xy_pass=False, turns=13, wall=120.0, valid_rate=0.8),
        _record(2, passed=True, f_xy_pass=True, turns=10, wall=100.0, valid_rate=0.9),
    ]
    s = aggregate_cell(records)

    assert s.case == "paraboloid" and s.harness == "opencode" and s.model == "qwen"
    assert s.n_seeds == 3 and s.seeds == [0, 1, 2]
    assert s.n_passed == 2 and s.pass_rate == pytest.approx(2 / 3)
    assert s.n_completed == 3 and s.completion_rate == 1.0
    # 2 of 3 seeds got analysis_f_xy right.
    assert s.per_metric_pass == {"analysis_f_xy": 2}
    assert s.turns == Stat(min=8, median=10.0, max=13)
    assert s.wall_clock_s == Stat(min=90.0, median=100.0, max=120.0)
    assert s.valid_call_rate == Stat(min=0.8, median=0.9, max=1.0)


def test_aggregate_all_no_report_is_zero_pass_but_keeps_telemetry():
    # The gemma4 failure mode: ran tools, never emitted a report. pass/completion
    # are zero, no per-metric entries — but turns/wall still summarize.
    records = [
        _record(0, completed=False, passed=False, turns=4, wall=50.0, valid_rate=1.0),
        _record(1, completed=False, passed=False, turns=6, wall=60.0, valid_rate=1.0),
    ]
    s = aggregate_cell(records)
    assert s.n_completed == 0 and s.completion_rate == 0.0
    assert s.n_passed == 0 and s.pass_rate == 0.0
    assert s.per_metric_pass == {}
    assert s.turns == Stat(min=4, median=5.0, max=6)
    assert s.valid_call_rate == Stat(min=1.0, median=1.0, max=1.0)


def test_aggregate_counts_parsed_reports():
    records = [_record(0), _record(1), _record(2)]
    records[0]["reporting"] = {"parsed": True}
    records[1]["reporting"] = {"parsed": False}
    # records[2] has no reporting key (pre-Step-11 record) — tolerated as unparsed.
    s = aggregate_cell(records)
    assert s.n_report_parsed == 1


def test_aggregate_to_dict_is_json_shaped():
    s = aggregate_cell([_record(0)])
    d = s.to_dict()
    assert d["pass_rate"] == 1.0
    # nested Stat became a plain dict (JSON-serializable).
    assert d["turns"] == {"min": 10, "median": 10.0, "max": 10}


def test_aggregate_rejects_mixed_cells():
    a = _record(0)
    b = _record(0)
    b["model"] = "gemma"  # different cell
    with pytest.raises(ValueError, match="multiple cells"):
        aggregate_cell([a, b])


def test_aggregate_rejects_empty():
    with pytest.raises(ValueError, match="no records"):
        aggregate_cell([])
