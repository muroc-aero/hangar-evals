"""Case-table integrity (Step 15): the suite is well-formed and buildable.

``build_prompt`` reads each case's lane_c prompt through the seam, so these
tests also pin the Step-15 payload dependency: the resolved the-hangar
checkout must carry the ``*_open.prompt.md`` prompts (the lane-c-full-coverage
work). A missing prompt file fails HERE, at suite level, not mid-eval.
"""

from __future__ import annotations

from hangar.evals.cases import CASES, build_prompt
from hangar.evals.hangar_ref import examples_dir

EXPECTED_CASES = {
    "paraboloid", "oas_aero_rect", "oas_aerostruct_rect", "ocp_caravan_basic",
    "ocp_caravan_full", "ocp_hybrid_twin", "oas_ocp_combined",
    "ocp_oas_coupled", "ocp_oas_direct", "pyc_turbojet", "ocp_three_tool",
    "evt_native_sizing",
}


def test_suite_is_exactly_the_twelve_cases():
    assert set(CASES) == EXPECTED_CASES
    # ocp_pyc_coupled must stay out: its materializer path cannot match
    # Lane A through the tool surface (weight-slot OEW passthrough).
    assert "ocp_pyc_coupled" not in CASES


def test_case_names_match_keys_and_examples_exist():
    examples = examples_dir()
    for key, case in CASES.items():
        assert case.name == key
        assert (examples / case.example).is_dir(), case.example
        assert (examples / case.example / "lane_c" / case.prompt_file).is_file(), (
            f"{case.name}: missing lane_c/{case.prompt_file} — is the-hangar "
            f"checkout carrying the Lane-C open prompts?"
        )


def test_every_prompt_builds_with_its_metric_keys():
    for case in CASES.values():
        prompt = build_prompt(case)
        assert "--- TASK ---" in prompt and "--- REPORT FORMAT ---" in prompt
        for m in case.metrics:
            assert f'"{m.key}"' in prompt   # report skeleton names every metric


def test_metric_keys_unique_within_each_case():
    for case in CASES.values():
        keys = [m.key for m in case.metrics]
        assert len(keys) == len(set(keys)), case.name
