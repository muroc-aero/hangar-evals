"""Table rendering -- the live table and the final table share one renderer.

The property worth pinning is that Ambig and Rep-dis travel with Passed. A
pass-rate alone hides the two ways it misleads, and the whole point of the
column pair is that a reader cannot get the number without the caveat.
"""

from __future__ import annotations

from hangar.evals import report


def _summary(case="paraboloid", passed=2, seeds=3, ambig=0, repdis=0):
    return {"case": case, "harness": "claude", "model": "claude-opus-5",
            "n_seeds": seeds, "n_completed": seeds, "n_passed": passed,
            "n_ambiguous": ambig, "n_report_disagrees": repdis,
            "turns": {"min": 38, "median": 40.5, "max": 43},
            "wall_clock_s": {"min": 208.0, "median": 229.5, "max": 251.0},
            "valid_call_rate": {"min": 1.0, "median": 1.0, "max": 1.0}}


def test_flags_are_empty_when_the_pass_rate_reads_at_face_value():
    assert report.flags(_summary()) == ""


def test_flags_name_both_counts():
    assert report.flags(_summary(ambig=3, repdis=2)) == "[3 ambig, 2 rep-dis]"


def test_cells_carry_the_gating_counts_beside_the_pass_rate():
    cells = report.summary_cells(_summary(passed=0, ambig=3, repdis=3))
    assert cells["passed"] == "0/3"
    assert cells["ambig"] == "3" and cells["repdis"] == "3"


def test_a_summary_predating_regrade_shows_unknown_not_zero():
    # "--" and "0" mean different things: one is "not measured", the other is
    # "measured, and clean". Rendering the first as the second would license
    # exactly the face-value reading the columns exist to prevent.
    bare = {"case": "x", "harness": "claude", "model": "m", "n_seeds": 1,
            "n_completed": 1, "n_passed": 1}
    cells = report.summary_cells(bare)
    assert cells["ambig"] == "--" and cells["repdis"] == "--"


def test_terminal_table_totals_and_warns_when_counts_are_nonzero():
    out = report.render_terminal([_summary(passed=3), _summary(case="ocp_caravan_full",
                                                              passed=0, ambig=3, repdis=3)])
    assert "3/6 seeds passed across 2 cell(s)" in out
    assert "3 ambiguous, 3 report-disagree" in out
    assert "face value only where both are 0" in out


def test_terminal_table_stays_quiet_when_every_cell_is_clean():
    out = report.render_terminal([_summary(passed=3)])
    assert "face value" not in out


def test_markdown_has_one_row_per_cell_plus_header_and_rule():
    md = report.render_markdown([_summary(), _summary(case="pyc_turbojet")])
    lines = [ln for ln in md.splitlines() if ln.startswith("|")]
    assert len(lines) == 4
    assert lines[0].startswith("| Case | Model | Seeds |")


def test_empty_campaign_renders_without_raising():
    assert report.render_terminal([]) == "(no cells yet)"
