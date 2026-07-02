"""Effect-oracle tests — the ABC self-test (§4c) on a real fixture DB.

The fixture is a checkpointed ``analysis.db`` from a PASSED anchor run (our own
run output, not a reference answer — Lane A stays computed through the seam):
one analysis run (f_xy=39.0) and one optimize run (f_xy=-27.333…), both with a
completed execute activity and an assessment entity carrying the run mode.

References here are the ANALYTIC Lane A values, so these tests need no seam
subprocess: f=(x-3)^2 + x*y + (y+4)^2 - 3 → f(1,2)=39; the optimum is
x=20/3, y=-22/3, f=-82/3.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from hangar.evals.cases import CASES
from hangar.evals.oracle import (
    EffectRun,
    effect_values,
    oracle_ambiguity,
    read_effect_runs,
    report_matches_effects,
    select_run,
)
from hangar.evals.scoring import for_reporting, score_values

FIXTURE = Path(__file__).parent / "fixtures" / "paraboloid_anchor_passed.db"
METRICS = CASES["paraboloid"].metrics
REFS = {
    "analysis": {"f_xy": 39.0},
    "optimization": {"f_xy": -82 / 3, "x": 20 / 3, "y": -22 / 3},
}


def _run(run_id="r1", mode="optimize", ok=True, at="2026-01-01T00:00:00",
         values=None):
    return EffectRun(run_id=run_id, mode=mode, executed_ok=ok,
                     assess_status="completed", started_at=at,
                     final_values=values if values is not None else {})


# --- reading the fixture ---------------------------------------------------


def test_fixture_yields_both_runs_with_modes_and_values():
    runs = read_effect_runs(FIXTURE)
    assert len(runs) == 2
    assert [r.mode for r in runs] == ["analysis", "optimize"]  # started_at order
    assert all(r.executed_ok for r in runs)
    assert runs[0].final_values["f_xy"] == 39.0
    assert runs[1].final_values["x"]  # the optimize run carries the DVs


# --- A: known-good passes ---------------------------------------------------


def test_known_good_fixture_passes():
    runs = read_effect_runs(FIXTURE)
    effects = effect_values(METRICS, runs)
    assert all(v is not None for v in effects.values())
    score = score_values(METRICS, effects, REFS)
    assert score.passed
    # opt_x / opt_y are now REQUIRED for the effect grader and PASS from the DB.
    assert {s.verdict for s in score.scores} == {"PASS"}


# --- B: perturbed fails -----------------------------------------------------


def test_perturbed_fixture_fails(tmp_path):
    db = tmp_path / "analysis.db"
    shutil.copy(FIXTURE, db)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE run_cases SET data=replace(data, '39.0', '40.0') "
        "WHERE case_type='final'"
    )
    conn.commit()
    conn.close()

    effects = effect_values(METRICS, read_effect_runs(db))
    score = score_values(METRICS, effects, REFS)
    assert not score.passed
    verdicts = {s.key: s.verdict for s in score.scores}
    assert verdicts["analysis_f_xy"] == "FAIL"


# --- C: a no-op cannot pass -------------------------------------------------


def test_noop_empty_db_fails_every_required_metric(tmp_path):
    db = tmp_path / "analysis.db"
    db.touch()  # a DB omd never wrote to (no tables) == the agent did nothing
    runs = read_effect_runs(db)
    assert runs == []
    effects = effect_values(METRICS, runs)
    assert set(effects.values()) == {None}
    score = score_values(METRICS, effects, REFS)
    assert not score.passed
    assert all(s.verdict == "FAIL" for s in score.scores)  # incl. opt_x/opt_y


# --- selection policy: last successful run of the mode ----------------------


def test_last_successful_run_of_mode_wins_not_best():
    good = {"f_xy": -82 / 3, "x": 20 / 3, "y": -22 / 3}
    bad = {"f_xy": -5.0, "x": 0.0, "y": 0.0}
    runs = [
        _run("r1", at="2026-01-01T00:00:00", values=good),
        _run("r2", at="2026-01-01T00:01:00", values=bad),
    ]
    assert select_run(runs, "optimize").run_id == "r2"   # last, not best
    effects = effect_values(METRICS, runs)
    assert effects["opt_f_xy"] == -5.0                    # graded value is r2's
    # And the skipped candidate is surfaced, never silently resolved.
    assert oracle_ambiguity(METRICS, runs) == 1


def test_failed_and_modeless_runs_are_not_gradable():
    runs = [
        _run("r1", ok=False, values={"f_xy": -82 / 3}),   # execute failed
        _run("r2", mode=None, values={"f_xy": -82 / 3}),  # no assessment row
    ]
    assert select_run(runs, "optimize") is None
    assert effect_values(METRICS, runs)["opt_f_xy"] is None


# --- reporting fidelity ------------------------------------------------------


def test_report_matching_own_wrong_effects_is_honest_but_failing():
    # The agent ran a bad optimization and reported exactly what it got:
    # effect grade FAILs, but reporting fidelity is True (honest self-report).
    runs = [
        _run("a1", mode="analysis", values={"f_xy": 39.0}),
        _run("r1", values={"f_xy": -5.0, "x": 0.0, "y": 0.0}),
    ]
    effects = effect_values(METRICS, runs)
    assert not score_values(METRICS, effects, REFS).passed
    report = {"metrics": {"analysis_f_xy": 39.0, "opt_f_xy": -5.0,
                          "opt_x": 0.0, "opt_y": 0.0}}
    assert report_matches_effects(METRICS, report, effects) is True


def test_report_diverging_from_effects_is_flagged():
    runs = [_run("a1", mode="analysis", values={"f_xy": 39.0})]
    effects = effect_values(METRICS, runs)
    report = {"metrics": {"analysis_f_xy": 123.0}}   # not what it actually got
    assert report_matches_effects(METRICS, report, effects) is False


def test_report_fidelity_undefined_without_effects():
    effects = effect_values(METRICS, [])
    report = {"metrics": {"analysis_f_xy": 39.0}}
    assert report_matches_effects(METRICS, report, effects) is None


# --- reporting-required relaxation -------------------------------------------


def test_for_reporting_relaxes_dv_metrics_only():
    relaxed = {m.key: m.required for m in for_reporting(METRICS)}
    assert relaxed == {"analysis_f_xy": True, "opt_f_xy": True,
                       "opt_x": False, "opt_y": False}
    # ...while the effect grader keeps them required.
    assert all(m.required for m in METRICS)
