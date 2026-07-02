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
    lane_a_modules: list[str] = field(init=False, default_factory=list)

    def __post_init__(self):
        mods = sorted({m.lane_a_module for m in self.metrics})
        object.__setattr__(self, "lane_a_modules", mods)


def build_prompt(case: Case) -> str:
    """Assemble the full agent prompt for a case (read task via the seam)."""
    task = (examples_dir() / case.example / "lane_c" / case.prompt_file).read_text()
    metric_keys = ", ".join(f'"{m.key}": <number>' for m in case.metrics)
    return PREAMBLE + task + case.supplement + REPORT_FORMAT.format(metric_keys=metric_keys)


# T0 floor case: paraboloid analysis + optimization (the smoke/floor task).
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
}
