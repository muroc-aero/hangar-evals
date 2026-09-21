"""The campaign runner, driven by the publication manifests.

An arm is its ``examples/lane_c_pub_*.yaml``, read through the same
``overrides`` mapping the have-agent bridge uses. These pin the two things that
buys: a manifest means one thing whichever front door drives it, and what gets
checked before a run is DERIVED from the cases rather than declared beside them,
so the checks cannot drift from the arm they guard.
"""

from __future__ import annotations

import json

import pytest

from hangar.evals.campaign import (
    COMPOSITES,
    HANGAR_STEPS,
    derive_preflight,
    load_manifest,
    manifest_path,
    plan_rows,
    print_plan,
)
from hangar.evals.run import RunConfig

yaml = pytest.importorskip("yaml")


def _manifest(tmp_path, cases, name="arm"):
    path = tmp_path / f"lane_c_pub_{name}.yaml"
    path.write_text(yaml.safe_dump({"study": name, "title": f"{name} arm",
                                    "cases": cases}))
    return path


def _case(case, harness="claude", **over):
    return {"case_id": f"{case}_{harness}",
            "overrides": {"case": case, "harness": harness, "seeds": 3,
                          "omd_transport": "http", "sandbox": "container",
                          **over}}


def test_an_arm_name_resolves_to_its_publication_manifest():
    assert manifest_path("anchor").name == "lane_c_pub_anchor.yaml"


def test_an_unknown_arm_lists_what_exists():
    with pytest.raises(SystemExit, match="anchor"):
        manifest_path("nope")


def test_manifest_cases_become_runconfigs_in_declared_order(tmp_path):
    path = _manifest(tmp_path, [_case("pyc_turbojet"), _case("paraboloid")])
    title, cells = load_manifest(path, tmp_path)
    assert title == "arm arm"
    assert [c.case for _, c in cells] == ["pyc_turbojet", "paraboloid"]
    assert all(isinstance(c, RunConfig) for _, c in cells)


def test_overrides_reach_the_runconfig(tmp_path):
    path = _manifest(tmp_path, [_case("paraboloid", harness="opencode",
                                      model="gemma4:26b-mlx", seeds=5,
                                      timeout_s=900)])
    _, [(case_id, config)] = load_manifest(path, tmp_path)
    assert case_id == "paraboloid_opencode"
    assert config.model == "gemma4:26b-mlx" and config.seeds == 5
    assert config.timeout_s == 900.0 and config.sandbox == "container"


def test_the_real_anchor_manifest_is_an_all_claude_container_arm(tmp_path):
    _, cells = load_manifest(manifest_path("anchor"), tmp_path)
    assert len(cells) == 11
    assert {h for _, c in cells for h in c.harnesses} == {"claude"}
    assert {c.sandbox for _, c in cells} == {"container"}


def test_a_claude_arm_is_gated_on_the_credential(tmp_path):
    _, cells = load_manifest(_manifest(tmp_path, [_case("paraboloid")]), tmp_path)
    checks, needs_anchor = derive_preflight(cells)
    assert needs_anchor
    assert checks == ["hangar_refs", "container_runtime",
                      "anchor_image", "anchor_auth"]


def test_an_on_device_arm_is_never_blocked_on_a_credential_it_does_not_use(tmp_path):
    # The gemma arm is free and runs overnight; probing an anchor token before
    # each of its cases would stall it on a check it can neither need nor pass.
    _, cells = load_manifest(
        _manifest(tmp_path, [_case("paraboloid", harness="opencode",
                                   model="gemma4:26b-mlx")]), tmp_path)
    checks, needs_anchor = derive_preflight(cells)
    assert not needs_anchor and "anchor_auth" not in checks


def test_cheap_checks_come_before_the_one_that_needs_a_credential(tmp_path):
    # Stop-at-first-failure means a missing token must not hide a broken repo.
    _, cells = load_manifest(_manifest(tmp_path, [_case("paraboloid")]), tmp_path)
    checks, _ = derive_preflight(cells)
    assert checks.index("hangar_refs") < checks.index("anchor_auth")


def test_an_unsandboxed_arm_does_not_require_a_container_runtime(tmp_path):
    _, cells = load_manifest(
        _manifest(tmp_path, [_case("paraboloid", harness="opencode",
                                   sandbox="none", omd_transport="stdio")]),
        tmp_path)
    assert "container_runtime" not in derive_preflight(cells)[0]


def test_a_bad_override_is_rejected_at_load_not_at_hour_three(tmp_path):
    path = _manifest(tmp_path, [_case("paraboloid", harness="not_a_harness")])
    with pytest.raises(ValueError, match="not_a_harness"):
        load_manifest(path, tmp_path)


def test_plan_reports_a_status_per_cell_without_running_anything(tmp_path):
    _, cells = load_manifest(_manifest(tmp_path, [_case("paraboloid")]), tmp_path)
    [row] = plan_rows(cells, tmp_path)
    assert row["status"].state == "not_started"
    assert row["estimate_s"] is None       # no prior data to estimate from


def test_the_plan_shows_force_as_a_re_run_not_a_skip(tmp_path, capsys):
    """Seen 2026-09-21: `run gemma --force --dry-run` printed all 11 graded
    cells as `skip` and "2 to run" while the run loop re-ran all 13."""
    from hangar.evals.results_index import CaseStatus
    _, cells = load_manifest(_manifest(tmp_path, [_case("paraboloid")]), tmp_path)
    rows = plan_rows(cells, tmp_path)
    rows[0]["status"] = CaseStatus(state="graded", records=tmp_path / "x.jsonl",
                                   n_seeds_found=5, n_seeds_wanted=5,
                                   n_error_seeds=0, n_passed=5, reason="5/5 passed")
    print_plan("gemma", rows)
    assert "1 case(s), 0 to run, 1 already graded" in capsys.readouterr().out
    print_plan("gemma", rows, force=True)
    out = capsys.readouterr().out
    assert "1 to run" in out and "FORCE " in out and "skip" not in out


def test_the_estimate_uses_the_newest_prior_run_of_the_case(tmp_path):
    (tmp_path / "paraboloid_20260101T000000Z_summary.json").write_text(json.dumps(
        [{"case": "paraboloid", "wall_clock_s": {"median": 100.0}}]))
    _, cells = load_manifest(_manifest(tmp_path, [_case("paraboloid")]), tmp_path)
    assert plan_rows(cells, tmp_path)[0]["estimate_s"] == 300.0


def test_the_paper_composite_covers_both_tables():
    paper = COMPOSITES["paper"]
    assert paper["arms"] == ["anchor", "gemma"]
    assert all(step in HANGAR_STEPS for step in paper["hangar_steps"])
