"""Tests for numeric scoring — against REAL paraboloid Lane A refs via the seam.

``compute_refs`` runs the example's lane_a in a subprocess (needs the-hangar
importable in the current interpreter), exactly like the Step-2 seam tests.
"""

from __future__ import annotations

import pytest

from hangar.evals.scoring import (
    Metric,
    compute_refs,
    extract_report,
    score_report,
)

PARABOLOID_METRICS = [
    Metric("analysis_f_xy", "analysis", "f_xy", rtol=1e-6),
    Metric("opt_f_xy", "optimization", "f_xy", rtol=1e-4),
    Metric("opt_x", "optimization", "x", rtol=1e-3, required=False),
]


# --- extract_report -------------------------------------------------------


def test_extract_report_takes_last_parseable_block():
    text = (
        "first ```json\n{\"a\": 1}\n```\n"
        "final ```json\n{\"metrics\": {\"x\": 2}}\n```"
    )
    assert extract_report(text) == {"metrics": {"x": 2}}


def test_extract_report_no_block_raises():
    with pytest.raises(ValueError, match="No parseable JSON report"):
        extract_report("the agent rambled but emitted no fenced json")


# --- compute_refs (real Lane A) -------------------------------------------


@pytest.mark.requires_hangar
def test_compute_refs_paraboloid():
    refs = compute_refs("paraboloid", PARABOLOID_METRICS)
    assert refs["analysis"]["f_xy"] == 39.0
    assert "optimization" in refs


# --- score_report ---------------------------------------------------------


@pytest.mark.requires_hangar
def test_score_report_all_pass():
    refs = compute_refs("paraboloid", PARABOLOID_METRICS)
    report = {"metrics": {
        "analysis_f_xy": refs["analysis"]["f_xy"],
        "opt_f_xy": refs["optimization"]["f_xy"],
        "opt_x": refs["optimization"]["x"],
    }}
    res = score_report(PARABOLOID_METRICS, report, refs)
    assert res.passed
    assert {s.verdict for s in res.scores} == {"PASS"}
    assert res.n_pass == 3


@pytest.mark.requires_hangar
def test_score_report_required_metric_fails():
    refs = compute_refs("paraboloid", PARABOLOID_METRICS)
    report = {"metrics": {
        "analysis_f_xy": 999.0,                       # wrong -> FAIL
        "opt_f_xy": refs["optimization"]["f_xy"],     # right -> PASS
        # opt_x omitted -> optional, WARN
    }}
    res = score_report(PARABOLOID_METRICS, report, refs)
    assert not res.passed
    verdicts = {s.key: s.verdict for s in res.scores}
    assert verdicts["analysis_f_xy"] == "FAIL"
    assert verdicts["opt_f_xy"] == "PASS"
    assert verdicts["opt_x"] == "WARN"  # optional + missing


@pytest.mark.requires_hangar
def test_optional_missing_does_not_fail_overall():
    refs = compute_refs("paraboloid", PARABOLOID_METRICS)
    report = {"metrics": {
        "analysis_f_xy": refs["analysis"]["f_xy"],
        "opt_f_xy": refs["optimization"]["f_xy"],
    }}
    res = score_report(PARABOLOID_METRICS, report, refs)
    assert res.passed  # opt_x optional+missing -> WARN only


@pytest.mark.requires_hangar
def test_bool_is_not_a_valid_numeric_metric():
    refs = compute_refs("paraboloid", PARABOLOID_METRICS)
    report = {"metrics": {"analysis_f_xy": True, "opt_f_xy": refs["optimization"]["f_xy"]}}
    res = score_report(PARABOLOID_METRICS, report, refs)
    verdicts = {s.key: s.verdict for s in res.scores}
    assert verdicts["analysis_f_xy"] == "FAIL"  # bool rejected, required
