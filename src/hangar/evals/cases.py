"""Eval cases — task prompts + the metrics that score them.

A ``Case`` binds a the-hangar example (its lane_c task prompt, read through the
Step-2 seam) to the ``Metric``s that grade the agent's reported numbers against
Lane A. ``build_prompt`` wraps the task in a harness-neutral preamble and a
strict report format, a port of ``eval_lane_c.py`` generalized so the SAME
prompt is fair to every harness (Claude sees ``mcp__omd__*`` tools, OpenCode
sees ``omd_*`` — the preamble names neither).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hangar.evals.hangar_ref import examples_dir
from hangar.evals.scoring import Metric

# Harness-neutral preamble: MCP-only task, no tool-namespace assumptions.
PREAMBLE = """\
You are an engineering analysis agent evaluating the omd MCP server.
Complete the task below using ONLY the omd tools exposed to you.

HARD RULES:
- You have no filesystem, shell, or web access; author and run the plan
  entirely through the tool workspace (relative paths resolve server-side).
- References to `omd-cli` or skill files in the task text do not apply:
  use the equivalent omd tools instead, and skip deliverables that only
  make sense for a filesystem client.
- If a tool call fails, adapt and retry with corrected inputs.

WORKFLOW (the server's required order):
start_session -> author plan (plan_init, plan_add_component, ...) ->
log_decision -> validate_plan -> review_plan -> run_plan ->
get_results / get_run_summary -> generate_plots -> record_conclusion ->
get_provenance -> export_session_graph.

--- TASK ---
"""

REPORT_FORMAT = """\

--- REPORT FORMAT ---
End your final message with exactly one fenced JSON block:

```json
{{
  "plan_id": "...",
  "run_id": "...",
  "status": "...",
  "metrics": {{{metric_keys}}},
  "friction": ["each tool error, confusing parameter, or workaround"]
}}
```

Report every metric at full precision (all digits the tools give you).
If a metric is not retrievable through the tools, set it to null and
explain in "friction". Do not round, do not omit keys.
"""


@dataclass(frozen=True)
class Case:
    name: str               # cell/case identifier
    example: str            # directory under packages/omd/examples/
    prompt_file: str        # file under <example>/lane_c/
    metrics: list[Metric]
    supplement: str = ""    # extra task detail a blind MCP agent can't read
    # Per-case budgets (Step 18). The runner enforces timeout_s as a hard
    # wall-clock cap around the whole agent run; both observed SDK failures
    # (a crash and a deterministic hang) came AFTER the physics finished, so
    # expiry costs nothing — effects are still graded from the provenance DB.
    max_turns: int = 80
    timeout_s: float = 900.0    # 15 min default; override per case
    lane_a_modules: list[str] = field(init=False, default_factory=list)

    def __post_init__(self):
        mods = sorted({m.lane_a_module for m in self.metrics})
        object.__setattr__(self, "lane_a_modules", mods)


def build_prompt(case: Case) -> str:
    """Assemble the full agent prompt for a case (read task via the seam)."""
    task = (examples_dir() / case.example / "lane_c" / case.prompt_file).read_text()
    metric_keys = ", ".join(f'"{m.key}": <number>' for m in case.metrics)
    return PREAMBLE + task + case.supplement + REPORT_FORMAT.format(metric_keys=metric_keys)


def _ocp_metrics(module: str) -> list[Metric]:
    """The standard OCP mission trio, graded against ``<example>.lane_a.<module>``."""
    return [
        Metric("fuel_burn_kg", module, "fuel_burn_kg", rtol=1e-3),
        Metric("OEW_kg", module, "OEW_kg", rtol=1e-3),
        Metric("MTOW_kg", module, "MTOW_kg", rtol=1e-6),
    ]


# The Lane-C suite (Step 15). T0 = paraboloid (hinted prompt, the smoke/floor
# task); everything else is T1/T4 on the example's *_open.prompt.md — the open
# prompts state the engineering goal and physical inputs but name no factory,
# slot provider, or tool sequence, so the agent must discover the workflow from
# the server's own affordances. Metrics, prompt files, and tolerances mirror
# the-hangar's eval_lane_c.py; each case's tool-surface achievability is proven
# by the scripted baseline in validity.py (and the-hangar's
# test_parity_lane_c.py) BEFORE any model is blamed for failing it.
#
# ocp_pyc_coupled is deliberately absent: its tool-surface path shares the
# Lane B materializer, whose weight-slot precedence forces an OEW passthrough
# (~8% OEW / ~4% fuel gap vs Lane A — see the example's TODO.md), so no agent
# can match the reference through the tools.
CASES: dict[str, Case] = {
    "paraboloid": Case(
        name="paraboloid",
        example="paraboloid",
        prompt_file="all.prompt.md",
        metrics=[
            Metric("analysis_f_xy", "analysis", "f_xy", rtol=1e-6),
            Metric("opt_f_xy", "optimization", "f_xy", rtol=1e-4),
            # x/y are REQUIRED for the effect grader (run_cases stores them
            # directly), but DV retrieval through the TOOL surface is a known
            # gap, so the self-report keeps them WARN-only (Step 11, §4c risk 2).
            Metric("opt_x", "optimization", "x", rtol=1e-3, report_required=False),
            Metric("opt_y", "optimization", "y", rtol=1e-3, report_required=False),
        ],
    ),
    "oas_aero_rect": Case(
        name="oas_aero_rect",
        example="oas_aero_rect",
        prompt_file="aero_analysis_open.prompt.md",
        metrics=[
            Metric("CL", "aero_analysis", "CL", rtol=1e-6),
            Metric("CD", "aero_analysis", "CD", rtol=1e-6),
        ],
    ),
    # 1e-4 leaves headroom for the agent's coupled-solver tolerance choice; a
    # wrong mesh or condition still misses by orders of magnitude.
    "oas_aerostruct_rect": Case(
        name="oas_aerostruct_rect",
        example="oas_aerostruct_rect",
        prompt_file="aerostruct_analysis_open.prompt.md",
        metrics=[
            Metric("CL", "aerostruct_analysis", "CL", rtol=1e-4),
            Metric("CD", "aerostruct_analysis", "CD", rtol=1e-4),
        ],
    ),
    "ocp_caravan_basic": Case(
        name="ocp_caravan_basic",
        example="ocp_caravan_basic",
        prompt_file="basic_mission_open.prompt.md",
        metrics=_ocp_metrics("basic_mission"),
    ),
    "ocp_caravan_full": Case(
        name="ocp_caravan_full",
        example="ocp_caravan_full",
        prompt_file="full_mission_open.prompt.md",
        metrics=_ocp_metrics("full_mission"),
    ),
    "ocp_hybrid_twin": Case(
        name="ocp_hybrid_twin",
        example="ocp_hybrid_twin",
        prompt_file="hybrid_mission_open.prompt.md",
        metrics=_ocp_metrics("hybrid_mission"),
    ),
    # Composite plan: the effect values live under per-component summaries
    # flattened to "<comp_id>.<key>"; the agent picks the comp ids, so the
    # oracle finds them by unique dotted-suffix match on effect_key.
    "oas_ocp_combined": Case(
        name="oas_ocp_combined",
        example="oas_ocp_combined",
        prompt_file="wing_mission_open.prompt.md",
        metrics=[
            Metric("wing_CL", "wing_mission", "wing_CL", rtol=1e-6,
                   effect_key="CL"),
            Metric("wing_CD", "wing_mission", "wing_CD", rtol=1e-6,
                   effect_key="CD"),
            *_ocp_metrics("wing_mission"),
        ],
    ),
    "ocp_oas_coupled": Case(
        name="ocp_oas_coupled",
        example="ocp_oas_coupled",
        prompt_file="coupled_mission_open.prompt.md",
        metrics=_ocp_metrics("coupled_mission"),
    ),
    "ocp_oas_direct": Case(
        name="ocp_oas_direct",
        example="ocp_oas_direct",
        prompt_file="direct_coupled_mission_open.prompt.md",
        metrics=_ocp_metrics("direct_coupled_mission"),
    ),
    "pyc_turbojet": Case(
        name="pyc_turbojet",
        example="pyc_turbojet",
        prompt_file="turbojet_design_open.prompt.md",
        metrics=[
            Metric("Fn", "design_analysis", "Fn", rtol=1e-4),
            Metric("TSFC", "design_analysis", "TSFC", rtol=1e-4),
            Metric("OPR", "design_analysis", "OPR", rtol=1e-4),
        ],
    ),
    # Three-tool coupled mission: every run_plan invokes OAS + OCP + pyCycle
    # together, so honest attempts legitimately take far longer than the suite
    # default — 45 min before the runner calls it a hang.
    "ocp_three_tool": Case(
        name="ocp_three_tool",
        example="ocp_three_tool",
        prompt_file="coupled_mission_open.prompt.md",
        metrics=_ocp_metrics("coupled_mission"),
        timeout_s=2700.0,
    ),
    # Lane A loads the archer-midnight vehicle from its JSON config file; the
    # built-in template is vendored from that same file, so the template-built
    # result a blind agent can reach matches the file-based reference to
    # round-off.
    "evt_native_sizing": Case(
        name="evt_native_sizing",
        example="evt_native_sizing",
        prompt_file="sizing_open.prompt.md",
        metrics=[
            Metric("sized_mtow_kg", "sizing", "sized_mtow_kg", rtol=1e-3),
            Metric("total_mission_energy_kw_hr", "sizing",
                   "total_mission_energy_kw_hr", rtol=1e-3),
            Metric("peak_power_kw", "sizing", "peak_power_kw", rtol=1e-3),
        ],
    ),
}
