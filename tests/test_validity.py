"""Task-validity runner (Step 15): registry totality, parsing, one live proof.

The full 12-case sweep is a CLI activity (``python -m hangar.evals.validity
--all``), not a test: the coupled OCP/OAS cases run solver stacks for minutes.
The suite pins the cheap invariants plus ONE live end-to-end baseline
(paraboloid, slow) so the wire path — MCP HTTP client -> OmdHttpService ->
effect oracle — stays proven in-repo.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hangar.evals.cases import CASES
from hangar.evals.validity import BASELINES, _mission_config, _parse_result


def test_every_case_has_a_scripted_baseline_and_no_orphans():
    assert set(BASELINES) == set(CASES)


def test_mission_config_shape_matches_parity_tests():
    mission = {"num_nodes": 11, "range_nm": 300, "cruise_alt_ft": 10000}
    cfg = _mission_config(mission, slots={"drag": {"provider": "oas/vlm"}},
                          template="b738", architecture="twin_turbofan",
                          solver_settings={"solver_type": "nlbgs"},
                          extra={"propulsion_overrides": {"x": 1}})
    assert cfg["aircraft_template"] == "b738"
    assert cfg["architecture"] == "twin_turbofan"
    assert cfg["num_nodes"] == 11
    assert cfg["mission_params"] == {"range_nm": 300, "cruise_alt_ft": 10000}
    assert "num_nodes" not in cfg["mission_params"]
    assert cfg["slots"]["drag"]["provider"] == "oas/vlm"
    assert cfg["solver_settings"] == {"solver_type": "nlbgs"}
    assert cfg["propulsion_overrides"] == {"x": 1}


def test_parse_result_prefers_structured_content():
    res = SimpleNamespace(structuredContent={"valid": True}, content=[])
    assert _parse_result(res) == {"valid": True}
    # FastMCP's {"result": ...} wrapper for non-object returns is unwrapped
    # only when it is the sole key; a real "result" field passes through.
    res = SimpleNamespace(structuredContent={"result": {"a": 1}}, content=[])
    assert _parse_result(res) == {"a": 1}
    res = SimpleNamespace(structuredContent={"result": 1, "b": 2}, content=[])
    assert _parse_result(res) == {"result": 1, "b": 2}


def test_parse_result_falls_back_to_json_text():
    block = SimpleNamespace(text='{"plan_dir": "p"}')
    res = SimpleNamespace(structuredContent=None, content=[block])
    assert _parse_result(res) == {"plan_dir": "p"}
    res = SimpleNamespace(structuredContent=None,
                          content=[SimpleNamespace(text="not json")])
    assert _parse_result(res) == {}


# --- live proof (slow: boots the real omd server, runs both paraboloid plans)


@pytest.mark.slow
def test_paraboloid_scripted_baseline_is_valid(tmp_path):
    from hangar.evals.validity import check_case

    result = check_case(CASES["paraboloid"], tmp_path / "omd")
    assert result["error"] is None
    assert result["valid"], result
    assert {s["verdict"] for s in result["scores"]} == {"PASS"}
    # Both plans ran: one analysis + one optimize.
    assert result["n_runs"] == 2
