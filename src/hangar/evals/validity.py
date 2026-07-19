"""Task-validity baselines (Step 15) — a scripted tool sequence per case.

Before a model is blamed for failing a case, the case itself must be proven
achievable: a SCRIPTED (non-LLM) client executes the intended tool sequence
against a real ``OmdHttpService`` over MCP streamable HTTP — the same server,
transport, and ``data_root`` a real seed uses — and the outcome is graded by
the SAME effect oracle + scoring path as an agent run. A case whose baseline
grades PASS is a valid task AND a gradable one: the tool surface reproduces
Lane A, and the oracle can read the evidence from the provenance DB.

The sequences are ports of the-hangar's in-process parity tests
(``packages/omd/examples/tests/test_parity_lane_c.py``) onto the MCP wire.
They intentionally skip the provenance niceties an agent is asked for
(start_session, log_decision, record_conclusion): validity is about the
minimal achieving path, and grading must not depend on etiquette.

    python -m hangar.evals.validity --case oas_aero_rect
    python -m hangar.evals.validity --all

Each invocation writes ``results/validity/validity_<stamp>.json``; per-case
omd state lands under ``results/validity/run_data`` for post-mortems.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable

from hangar.evals.cases import CASES, Case
from hangar.evals.hangar_ref import shared_constants
from hangar.evals.omd_service import OmdHttpService
from hangar.evals.oracle import effect_values, read_effect_runs
from hangar.evals.scoring import compute_refs, score_values

# One tool call may legitimately run a solver stack for minutes.
_CALL_TIMEOUT = timedelta(seconds=3600)

# The Newton settings the OCP Lane B plans embed in their component config.
_OCP_NEWTON = {
    "solver_type": "newton", "maxiter": 20, "atol": 1.0e-10, "rtol": 1.0e-10,
}

ToolCaller = Callable[..., Awaitable[dict]]


def _mission_config(
    mission: dict,
    slots: dict | None = None,
    *,
    template: str = "caravan",
    architecture: str = "turboprop",
    solver_settings: dict | None = None,
    extra: dict | None = None,
) -> dict:
    """Build an ocp mission component config from a shared ``MISSION`` dict."""
    config = {
        "aircraft_template": template,
        "architecture": architecture,
        "num_nodes": mission["num_nodes"],
        "mission_params": {k: v for k, v in mission.items() if k != "num_nodes"},
    }
    if solver_settings:
        config["solver_settings"] = solver_settings
    if slots:
        config["slots"] = slots
    if extra:
        config.update(extra)
    return config


async def _init(call: ToolCaller, plan_dir: str, name: str) -> str:
    await call("plan_init", plan_dir=plan_dir, plan_id=plan_dir, name=name)
    return plan_dir


async def _newton_solver(call: ToolCaller, plan_dir: str) -> None:
    await call("plan_set_solver", plan_dir=plan_dir,
               nonlinear="NewtonSolver", linear="DirectSolver",
               nonlinear_options={"maxiter": 20, "atol": 1.0e-10, "rtol": 1.0e-10})


async def _assemble_and_run(call: ToolCaller, plan_dir: str,
                            mode: str = "analysis") -> dict:
    assembled = await call("assemble_plan", plan_dir=plan_dir)
    if assembled.get("errors"):
        raise RuntimeError(f"assemble_plan errors: {assembled['errors']}")
    plan_yaml = assembled["output_path"]
    check = await call("validate_plan", plan_path=plan_yaml)
    if not check.get("valid"):
        raise RuntimeError(f"validate_plan errors: {check.get('errors')}")
    env = await call("run_plan", plan_path=plan_yaml, mode=mode)
    if "error" in env:
        raise RuntimeError(f"run_plan error: {env['error']}")
    status = (env.get("results") or {}).get("status")
    if status not in ("completed", "converged"):
        raise RuntimeError(f"run_plan status {status!r}")
    return env


# --- the scripted sequences, one per case -----------------------------------


async def _paraboloid(call: ToolCaller, sh: dict) -> None:
    d = await _init(call, "validity-para-analysis", "Paraboloid analysis")
    await call("plan_add_component", plan_dir=d, comp_id="paraboloid",
               comp_type="paraboloid/Paraboloid", config={})
    await call("plan_set_operating_point", plan_dir=d,
               fields={"x": sh["ANALYSIS_X"], "y": sh["ANALYSIS_Y"]})
    await _assemble_and_run(call, d, mode="analysis")

    d = await _init(call, "validity-para-opt", "Paraboloid optimization")
    await call("plan_add_component", plan_dir=d, comp_id="paraboloid",
               comp_type="paraboloid/Paraboloid", config={})
    await call("plan_set_operating_point", plan_dir=d, fields={"x": 0.0, "y": 0.0})
    await call("plan_add_dv", plan_dir=d, name="x",
               lower=sh["OPT_X_LOWER"], upper=sh["OPT_X_UPPER"])
    await call("plan_add_dv", plan_dir=d, name="y",
               lower=sh["OPT_Y_LOWER"], upper=sh["OPT_Y_UPPER"])
    await call("plan_set_objective", plan_dir=d, name="f_xy")
    await _assemble_and_run(call, d, mode="optimize")


async def _oas_aero_rect(call: ToolCaller, sh: dict) -> None:
    d = await _init(call, "validity-oas-aero", "Rect wing VLM analysis")
    await call("plan_add_component", plan_dir=d, comp_id="wing",
               comp_type="oas/AeroPoint", config={"surfaces": [sh["WING"]]})
    await call("plan_set_operating_point", plan_dir=d, fields=sh["FLIGHT"])
    await _assemble_and_run(call, d)


async def _oas_aerostruct_rect(call: ToolCaller, sh: dict) -> None:
    d = await _init(call, "validity-oas-aerostruct", "Rect wing aerostruct")
    await call("plan_add_component", plan_dir=d, comp_id="wing",
               comp_type="oas/AerostructPoint", config={"surfaces": [sh["WING"]]})
    # Match the Lane B solvers.yaml (not shared.SOLVERS, which Lane A applies
    # to its own hand-built coupled group).
    await call("plan_set_solver", plan_dir=d,
               nonlinear="NewtonSolver", linear="DirectSolver",
               nonlinear_options={"maxiter": 20, "atol": 1.0e-6})
    await call("plan_set_operating_point", plan_dir=d, fields=sh["FLIGHT"])
    await _assemble_and_run(call, d)


async def _ocp_caravan_basic(call: ToolCaller, sh: dict) -> None:
    d = await _init(call, "validity-caravan-basic", "Caravan basic mission")
    await call("plan_add_component", plan_dir=d, comp_id="caravan-mission",
               comp_type="ocp/BasicMission", config=_mission_config(sh["MISSION"]))
    await _newton_solver(call, d)
    await _assemble_and_run(call, d)


async def _ocp_caravan_full(call: ToolCaller, sh: dict) -> None:
    d = await _init(call, "validity-caravan-full", "Caravan full mission")
    await call("plan_add_component", plan_dir=d, comp_id="caravan-mission",
               comp_type="ocp/FullMission",
               config=_mission_config(sh["MISSION"],
                                      solver_settings=dict(_OCP_NEWTON)))
    await _assemble_and_run(call, d)


async def _ocp_hybrid_twin(call: ToolCaller, sh: dict) -> None:
    d = await _init(call, "validity-hybrid-twin", "King Air series-hybrid mission")
    await call(
        "plan_add_component", plan_dir=d, comp_id="hybrid-mission",
        comp_type="ocp/FullMission",
        config=_mission_config(
            sh["MISSION"], template="kingair",
            architecture=sh["PROPULSION"]["architecture"],
            solver_settings=dict(_OCP_NEWTON),
            extra={"propulsion_overrides": {
                "battery_specific_energy":
                    sh["PROPULSION"]["battery_specific_energy"],
            }},
        ),
    )
    await _assemble_and_run(call, d)


async def _oas_ocp_combined(call: ToolCaller, sh: dict) -> None:
    d = await _init(call, "validity-oas-ocp-combined", "Wing + Caravan composite")
    await call("plan_add_component", plan_dir=d, comp_id="wing",
               comp_type="oas/AeroPoint", config={"surfaces": [sh["WING"]]})
    await call("plan_add_component", plan_dir=d, comp_id="mission",
               comp_type="ocp/BasicMission",
               config=_mission_config(sh["MISSION"],
                                      solver_settings=dict(_OCP_NEWTON)))
    await call("plan_set_composition_policy", plan_dir=d, policy="explicit")
    await call("plan_set_operating_point", plan_dir=d, fields=sh["FLIGHT"])
    await _assemble_and_run(call, d)


async def _ocp_oas_coupled(call: ToolCaller, sh: dict) -> None:
    d = await _init(call, "validity-ocp-oas-coupled", "Caravan + VLM drag slot")
    slots = {"drag": {"provider": "oas/vlm", "config": sh["VLM_CONFIG"]}}
    await call("plan_add_component", plan_dir=d, comp_id="mission",
               comp_type="ocp/BasicMission",
               config=_mission_config(sh["MISSION"], slots=slots))
    await _newton_solver(call, d)
    await _assemble_and_run(call, d)


async def _ocp_oas_direct(call: ToolCaller, sh: dict) -> None:
    d = await _init(call, "validity-ocp-oas-direct", "Caravan + direct VLM drag")
    slots = {"drag": {"provider": "oas/vlm-direct", "config": sh["VLM_CONFIG"]}}
    await call(
        "plan_add_component", plan_dir=d, comp_id="mission",
        comp_type="ocp/BasicMission",
        config=_mission_config(
            sh["MISSION"], slots=slots,
            solver_settings={"solver_type": "newton", "maxiter": 30,
                             "atol": 1.0e-8, "rtol": 1.0e-8},
        ),
    )
    await _assemble_and_run(call, d)


async def _pyc_turbojet(call: ToolCaller, sh: dict) -> None:
    d = await _init(call, "validity-pyc-turbojet", "Turbojet design point")
    await call("plan_add_component", plan_dir=d, comp_id="turbojet",
               comp_type="pyc/TurbojetDesign", config=sh["ENGINE_PARAMS"])
    await call("plan_set_operating_point", plan_dir=d, fields=sh["DESIGN_POINT"])
    await _assemble_and_run(call, d)


async def _ocp_three_tool(call: ToolCaller, sh: dict) -> None:
    d = await _init(call, "validity-ocp-three-tool", "B738 three-tool mission")
    slots = {
        "drag": {"provider": "oas/vlm", "config": sh["VLM_CONFIG"]},
        "propulsion": {"provider": "pyc/surrogate",
                       "config": sh["PYC_SURR_CONFIG"]},
    }
    await call(
        "plan_add_component", plan_dir=d, comp_id="mission",
        comp_type="ocp/BasicMission",
        config=_mission_config(
            sh["MISSION"], slots=slots,
            template="b738", architecture="twin_turbofan",
            # NLBGS: dual-surrogate coupling leaves Newton ill-conditioned.
            solver_settings={"solver_type": "nlbgs", "maxiter": 200,
                             "atol": 1.0e-8, "rtol": 1.0e-8},
        ),
    )
    await _assemble_and_run(call, d)


async def _evt_native_sizing(call: ToolCaller, sh: dict) -> None:
    # The blind-agent path: the vendored archer_midnight template, not the
    # repo-relative config file Lane A loads (identical contents by design —
    # and the server runs cwd=data_root, where relative paths don't resolve).
    d = await _init(call, "validity-evt-native", "Native eVTOL sizing")
    await call("plan_add_component", plan_dir=d, comp_id="evtol",
               comp_type="evt/Sizing",
               config={"template": "archer_midnight", "solver": "newton"})
    await _assemble_and_run(call, d)


@dataclass(frozen=True)
class Baseline:
    """A case's scripted proof: which shared constants it needs, and the
    tool sequence that should reproduce Lane A through the MCP surface."""

    shared: tuple[str, ...]
    steps: Callable[[ToolCaller, dict], Awaitable[None]]


BASELINES: dict[str, Baseline] = {
    "paraboloid": Baseline(
        ("ANALYSIS_X", "ANALYSIS_Y", "OPT_X_LOWER", "OPT_X_UPPER",
         "OPT_Y_LOWER", "OPT_Y_UPPER"), _paraboloid),
    "oas_aero_rect": Baseline(("WING", "FLIGHT"), _oas_aero_rect),
    "oas_aerostruct_rect": Baseline(("WING", "FLIGHT"), _oas_aerostruct_rect),
    "ocp_caravan_basic": Baseline(("MISSION",), _ocp_caravan_basic),
    "ocp_caravan_full": Baseline(("MISSION",), _ocp_caravan_full),
    "ocp_hybrid_twin": Baseline(("MISSION", "PROPULSION"), _ocp_hybrid_twin),
    "oas_ocp_combined": Baseline(("WING", "FLIGHT", "MISSION"), _oas_ocp_combined),
    "ocp_oas_coupled": Baseline(("MISSION", "VLM_CONFIG"), _ocp_oas_coupled),
    "ocp_oas_direct": Baseline(("MISSION", "VLM_CONFIG"), _ocp_oas_direct),
    "pyc_turbojet": Baseline(("ENGINE_PARAMS", "DESIGN_POINT"), _pyc_turbojet),
    "ocp_three_tool": Baseline(
        ("MISSION", "VLM_CONFIG", "PYC_SURR_CONFIG"), _ocp_three_tool),
    "evt_native_sizing": Baseline((), _evt_native_sizing),
}


# --- MCP wire + grading ------------------------------------------------------


def _parse_result(res) -> dict:
    """Unwrap a CallToolResult into the tool's dict return."""
    sc = res.structuredContent
    if isinstance(sc, dict):
        # FastMCP wraps non-object returns as {"result": ...}; omd tools
        # return dicts, which pass through unwrapped.
        return sc.get("result", sc) if set(sc) == {"result"} else sc
    for block in res.content or []:
        text = getattr(block, "text", None)
        if text:
            try:
                obj = json.loads(text)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue
    return {}


async def _drive(url: str, case: Case, sh: dict) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            async def call(tool: str, **arguments) -> dict:
                res = await session.call_tool(
                    tool, arguments, read_timeout_seconds=_CALL_TIMEOUT)
                if res.isError:
                    text = ""
                    for block in res.content or []:
                        text = getattr(block, "text", "") or text
                    raise RuntimeError(f"{tool} failed: {text[:800]}")
                return _parse_result(res)

            await BASELINES[case.name].steps(call, sh)


def check_case(case: Case, data_root: Path) -> dict:
    """Run one case's scripted baseline and grade it like an agent run."""
    baseline = BASELINES[case.name]
    t0 = time.monotonic()
    error = None
    try:
        sh = (shared_constants(case.example, baseline.shared)
              if baseline.shared else {})
        with OmdHttpService(data_root) as spec:
            asyncio.run(_drive(spec.url, case, sh))
    except Exception as exc:  # graded below: no runs -> required metrics FAIL
        error = f"{type(exc).__name__}: {exc}"

    refs = compute_refs(case.example, case.metrics)
    db = data_root / "analysis.db"
    runs = read_effect_runs(db) if db.exists() else []
    effects = effect_values(case.metrics, runs)
    score = score_values(case.metrics, effects, refs)
    return {
        "case": case.name,
        "valid": score.passed and error is None,
        "error": error,
        "scores": [
            {"key": s.key, "lane_a": s.lane_a, "agent": s.agent,
             "rel_err": s.rel_err, "verdict": s.verdict}
            for s in score.scores
        ],
        "n_runs": len(runs),
        "wall_clock_s": round(time.monotonic() - t0, 1),
        "data_root": str(data_root),
    }


def _print_result(r: dict) -> None:
    verdict = "VALID" if r["valid"] else "INVALID"
    print(f"  {r['case']}: {verdict} "
          f"(runs={r['n_runs']}, wall={r['wall_clock_s']}s)")
    if r["error"]:
        print(f"    error: {r['error']}")
    for s in r["scores"]:
        got = "null" if s["agent"] is None else f"{s['agent']:.6g}"
        print(f"    {s['key']:<26s} ref={s['lane_a']:.6g} got={got} "
              f"-> {s['verdict']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--case", choices=list(CASES))
    group.add_argument("--all", action="store_true")
    parser.add_argument("--results-dir", default="results")
    args = parser.parse_args(argv)

    names = list(CASES) if args.all else [args.case]
    out_dir = Path(args.results_dir).resolve() / "validity"
    (out_dir / "run_data").mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    results = []
    for name in names:
        case = CASES[name]
        data_root = Path(tempfile.mkdtemp(
            prefix=f"{name}_", dir=str(out_dir / "run_data"))).resolve()
        print(f"validity: {name}")
        r = check_case(case, data_root)
        _print_result(r)
        results.append(r)

    out_path = out_dir / f"validity_{stamp}.json"
    out_path.write_text(json.dumps(results, indent=2))
    n_valid = sum(r["valid"] for r in results)
    print(f"\n{n_valid}/{len(results)} cases valid. Wrote {out_path}")
    return 0 if n_valid == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
