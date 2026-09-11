
"""Table rendering -- the live table and the final table share one renderer.

The columns report OUTCOMES. What these pin is the distinction that decides
re-runs: a graded FAIL is a result about the agent and stays; a seed the harness
lost was never measured, is not a failure, and must not be counted as one.
"""

from __future__ import annotations

from hangar.evals import report


def _summary(case="paraboloid", passed=2, seeds=3, harness=0, review=0):
    return {"case": case, "harness": "claude", "model": "claude-opus-5",
            "n_seeds": seeds, "n_completed": seeds - harness, "n_passed": passed,
            "n_harness_errors": harness, "n_needs_review": review,
            "turns": {"min": 38, "median": 40.5, "max": 43},
            "wall_clock_s": {"min": 208.0, "median": 229.5, "max": 251.0},
            "valid_call_rate": {"min": 1.0, "median": 1.0, "max": 1.0}}


def test_flags_are_empty_when_the_row_needs_no_action():
    assert report.flags(_summary()) == ""


def test_flags_name_the_actions():
    assert report.flags(_summary(harness=2, review=1)) == \
        "[2 harness errors, 1 to review]"


def test_a_lost_seed_is_not_counted_as_a_failure():
    # 3 seeds, 2 passed, 1 lost to the harness -> 0 failed, not 1. Counting it
    # as a failure reports harness fragility as agent incapability.
    cells = report.summary_cells(_summary(passed=2, seeds=3, harness=1))
    assert cells["passed"] == "2/3"
    assert cells["failed"] == "0"
    assert cells["harness"] == "1"


def test_a_graded_failure_is_counted_as_one():
    cells = report.summary_cells(_summary(passed=1, seeds=3, harness=0))
    assert cells["failed"] == "2"


def test_a_summary_predating_the_counts_shows_unknown_not_zero():
    # "--" and "0" mean different things: one is "not measured", the other
    # "measured, and clean". Rendering the first as the second would claim a
    # clean run nobody checked.
    bare = {"case": "x", "harness": "claude", "model": "m", "n_seeds": 1,
            "n_completed": 1, "n_passed": 1}
    cells = report.summary_cells(bare)
    assert cells["harness"] == "--" and cells["review"] == "--"
    assert cells["failed"] == "--"


def test_the_pass_rate_is_over_graded_seeds_not_all_seeds():
    out = report.render_terminal([_summary(passed=2, seeds=3, harness=1)])
    assert "2/2 graded seeds passed" in out


def test_harness_errors_say_the_table_is_not_final():
    out = report.render_terminal([_summary(passed=2, seeds=3, harness=1)])
    assert "NOT a result" in out and "not final" in out


def test_review_seeds_are_routed_to_a_person():
    out = report.render_terminal([_summary(review=2)])
    assert "need a human look" in out and "evals review" in out


def test_a_clean_cell_gets_no_warnings():
    out = report.render_terminal([_summary(passed=3)])
    assert "NOT a result" not in out and "human look" not in out


def test_there_is_no_ambiguity_column():
    # It measured a defect in the apparatus. Defects get fixed, not columned.
    assert "Ambig" not in report.render_markdown([_summary()])
    assert not any(key == "ambig" for key, _, _ in report.COLUMNS)


def test_markdown_has_one_row_per_cell_plus_header_and_rule():
    md = report.render_markdown([_summary(), _summary(case="pyc_turbojet")])
    lines = [ln for ln in md.splitlines() if ln.startswith("|")]
    assert len(lines) == 4
    assert lines[0].startswith("| Case | Model | Seeds | Passed | Failed | Lost |")


def test_empty_campaign_renders_without_raising():
    assert report.render_terminal([]) == "(no cells yet)"


class _FakeStream:
    """Records writes and flushes, so buffering behaviour is assertable."""

    def __init__(self):
        self.written, self.flushes = [], 0

    def write(self, data):
        self.written.append(data)
        return len(data)

    def flush(self):
        self.flushes += 1


def test_tee_flushes_the_terminal_on_every_line(tmp_path):
    """A piped stdout block-buffers, and every real launch is piped.

    The launch line is `op run --env-file=op.env -- scripts/evals run anchor`;
    `op` proxies the child's streams to conceal secrets, so stdout is a pipe.
    Without a per-line flush a whole arm's output sits in an 8 KB buffer and the
    runner is silent for hours — which is the exact failure it exists to fix.
    """
    from hangar.evals.campaign import Tee

    stream = _FakeStream()
    tee = Tee(stream, tmp_path / "campaign.log")
    tee.write("[ 1/11] paraboloid\n")
    assert stream.flushes == 1
    tee.write("no newline yet")
    assert stream.flushes == 1          # partial line: nothing to flush
    tee.close()


def test_tee_also_writes_every_line_to_the_log(tmp_path):
    from hangar.evals.campaign import Tee

    log = tmp_path / "campaign.log"
    tee = Tee(_FakeStream(), log)
    tee.write("[ 1/11] paraboloid\n")
    assert "paraboloid" in log.read_text()   # readable mid-run, not at exit
    tee.close()
