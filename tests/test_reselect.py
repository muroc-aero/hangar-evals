"""Re-scoring stored records under a changed selection policy.

The fixture DB is the same checkpointed paraboloid ``analysis.db`` the oracle
tests use. What varies here is the RECORD around it: which run the agent named,
whether its workspace survived, whether it was a harness error.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from hangar.evals.reselect import (
    POLICY,
    recover_report,
    reported_run_id,
    reselect_file,
    reselect_record,
)

FIXTURE = Path(__file__).parent / "fixtures" / "paraboloid_anchor_passed.db"
REFS_ANALYSIS = 39.0


def _run_ids(db: Path) -> list[str]:
    conn = sqlite3.connect(db)
    try:
        return [r[0][len("act-execute-"):] for r in conn.execute(
            "SELECT activity_id FROM activities WHERE activity_id LIKE "
            "'act-execute-%' ORDER BY started_at, activity_id")]
    finally:
        conn.close()


@pytest.fixture
def seed(tmp_path):
    """A stored seed: its own data_root copy of the fixture DB, plus a record."""
    data_root = tmp_path / "run_data" / "paraboloid_claude_s0"
    data_root.mkdir(parents=True)
    shutil.copy(FIXTURE, data_root / "analysis.db")
    workspace = tmp_path / "workspaces" / "paraboloid_claude_s0"
    workspace.mkdir(parents=True)
    record = {
        "case": "paraboloid", "harness": "claude", "model": "claude-opus-5",
        "seed": 0, "completed": True, "passed": False,
        "scores": [{"key": "analysis_f_xy", "lane_a": REFS_ANALYSIS,
                    "agent": None, "rel_err": None, "verdict": "FAIL"}],
        "reporting": {"parsed": True, "passed": True, "matches_effects": False},
        "oracle": {"n_runs": 2},
        "data_root": str(data_root), "workspace": str(workspace),
    }
    return record, data_root, workspace


def _write_events(workspace: Path, report: dict) -> None:
    """A minimal stream-json event stream ending in the agent's report."""
    text = "Done.\n\n```json\n" + json.dumps(report) + "\n```"
    workspace.joinpath("claude_events.jsonl").write_text(
        json.dumps({"type": "result", "subtype": "success", "result": text}) + "\n")


# --- recovering what the agent named ----------------------------------------


def test_run_id_comes_from_the_record_when_it_has_one(seed):
    record, _, _ = seed
    record["oracle"]["reported_run_id"] = "run-from-record"
    assert reported_run_id(record, {"run_id": "run-from-report"}) == "run-from-record"


def test_run_id_falls_back_to_the_stored_event_stream(seed):
    """Records written before 2026-09-12 carry no reported_run_id."""
    record, _, workspace = seed
    _write_events(workspace, {"run_id": "run-xyz", "metrics": {}})
    assert "reported_run_id" not in record["oracle"]
    assert reported_run_id(record, recover_report(record)) == "run-xyz"


def test_missing_workspace_recovers_nothing(seed):
    record, _, workspace = seed
    shutil.rmtree(workspace)
    assert recover_report(record) is None
    assert reported_run_id(record, None) is None


def test_unparseable_report_recovers_nothing(seed):
    record, _, workspace = seed
    workspace.joinpath("claude_events.jsonl").write_text(
        json.dumps({"type": "result", "result": "I could not finish."}) + "\n")
    assert recover_report(record) is None


# --- re-scoring --------------------------------------------------------------


def test_named_run_flips_the_verdict(seed):
    """The whole point: a seed graded FAIL on a later run passes on the run
    the agent named."""
    record, data_root, workspace = seed
    analysis_run = _run_ids(data_root / "analysis.db")[0]
    _write_events(workspace, {"run_id": analysis_run,
                              "metrics": {"analysis_f_xy": REFS_ANALYSIS}})
    outcome = reselect_record(record)
    assert outcome.status == "changed"
    assert outcome.verdict_flipped
    assert outcome.record["oracle"]["reported_run_id"] == analysis_run
    assert outcome.record["oracle"]["selection"] == "named"
    assert outcome.record["reselect"]["policy"] == POLICY


def test_harness_error_seeds_are_left_alone(seed):
    """Nothing was measured, so there is nothing to re-score."""
    record, _, _ = seed
    record["error"] = "harness exited 1 with no successful omd run"
    outcome = reselect_record(record)
    assert outcome.status == "skipped"
    assert outcome.record is record


def test_missing_db_is_skipped_not_failed(seed):
    record, data_root, _ = seed
    (data_root / "analysis.db").unlink()
    outcome = reselect_record(record)
    assert outcome.status == "skipped"
    assert "no provenance DB" in outcome.reason


def test_unknown_case_is_skipped(seed):
    record, _, _ = seed
    record["case"] = "case_that_was_retired"
    assert reselect_record(record).status == "skipped"


# --- file handling -----------------------------------------------------------


def test_dry_run_writes_nothing(seed, tmp_path):
    record, data_root, workspace = seed
    _write_events(workspace, {"run_id": _run_ids(data_root / "analysis.db")[0],
                              "metrics": {}})
    path = tmp_path / "paraboloid_20260911T000000Z.jsonl"
    before = json.dumps(record) + "\n"
    path.write_text(before)
    outcomes = reselect_file(path, dry_run=True)
    assert any(o.status == "changed" for o in outcomes)
    assert path.read_text() == before


def test_rewrite_preserves_line_order_and_row_count(seed, tmp_path):
    """A resumed file holds a superseded row before its retry; both must stay
    put so ``regrade`` de-duplicates them the same way afterwards."""
    record, data_root, workspace = seed
    _write_events(workspace, {"run_id": _run_ids(data_root / "analysis.db")[0],
                              "metrics": {}})
    superseded = dict(record, error="transient", seed=0)
    path = tmp_path / "paraboloid_20260911T000000Z.jsonl"
    path.write_text(json.dumps(superseded) + "\n" + json.dumps(record) + "\n")
    reselect_file(path)
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    assert len(rows) == 2
    assert rows[0].get("error") == "transient"      # untouched, still first
    assert rows[1]["reselect"]["policy"] == POLICY


def test_rescoring_twice_is_idempotent(seed, tmp_path):
    record, data_root, workspace = seed
    _write_events(workspace, {"run_id": _run_ids(data_root / "analysis.db")[0],
                              "metrics": {}})
    path = tmp_path / "paraboloid_20260911T000000Z.jsonl"
    path.write_text(json.dumps(record) + "\n")
    reselect_file(path)
    first = json.loads(path.read_text())
    outcomes = reselect_file(path)
    assert all(o.status == "unchanged" for o in outcomes)
    assert json.loads(path.read_text())["scores"] == first["scores"]


# --- scoping to one arm ------------------------------------------------------


def test_campaign_scope_picks_the_newest_file_per_case(tmp_path):
    """Several arms' files for one case sit side by side; only the newest is
    the campaign's."""
    import os

    from hangar.evals.reselect import campaign_records

    results = tmp_path / "results"
    results.mkdir()
    old = results / "paraboloid_20260901T000000Z.jsonl"
    newest = results / "paraboloid_20260911T160000Z.jsonl"
    other_case = results / "pyc_turbojet_20260911T160000Z.jsonl"
    for i, f in enumerate((old, newest, other_case)):
        f.write_text("{}\n")
        os.utime(f, (1_000 + i, 1_000 + i))

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"cases": [{"case": "paraboloid"}]}))
    assert campaign_records(manifest, results) == [newest]


def test_campaign_scope_survives_the_rewrite_it_causes(tmp_path):
    """Re-scoring touches the file's mtime. Scoping must not then lose it --
    otherwise a second pass silently covers less than the first."""
    import os

    from hangar.evals.reselect import campaign_records

    results = tmp_path / "results"
    results.mkdir()
    # The --force case: the arm appended to a file named for an EARLIER arm.
    appended = results / "paraboloid_20260910T194825Z.jsonl"
    stale = results / "paraboloid_20260719T092051Z.jsonl"
    for f, t in ((stale, 1_000), (appended, 2_000)):
        f.write_text("{}\n")
        os.utime(f, (t, t))

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"cases": [{"case": "paraboloid"}]}))
    assert campaign_records(manifest, results) == [appended]

    appended.write_text('{"rescored": true}\n')       # as a re-score would
    assert campaign_records(manifest, results) == [appended]


def test_campaign_scope_ignores_cases_with_no_records(tmp_path):
    from hangar.evals.reselect import campaign_records

    results = tmp_path / "results"
    results.mkdir()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"cases": [{"case": "paraboloid"}]}))
    assert campaign_records(manifest, results) == []
