# hangar-evals

Offline benchmark of **local LLMs** (running on Apple-Silicon) driven by **agentic
coding harnesses** (OpenCode, OpenHands) against the
[the-hangar](https://github.com/muroc-aero/hangar) MCP servers and CLI tools.

It measures how well a given **model x harness** can:

1. **reproduce correct engineering analyses** — numbers within tolerance of a
   trusted reference, and
2. **correctly use the Hangar tools** — valid tool calls, prescribed workflow
   order, recovery from errors.

Results are scored against a frontier hosted model (Claude) as the ceiling/anchor.

> Status: scaffolding. This repo currently contains only the package skeleton;
> logic lands one reviewable step at a time. See the step ladder and full
> context in [`notes/llm-eval-plan.md`](notes/llm-eval-plan.md).

## The seam to the-hangar

hangar-evals does **not** vendor the-hangar. It locates an installed copy at
runtime via the `HANGAR_REPO` environment variable (default: sibling
`../the-hangar`), reusing the convention the-hangar's deployment scripts already
use. From there it:

- reads Lane A reference values + per-example tolerances (`shared.py`), and
- launches the installed `hangar.*` MCP servers (e.g. `python -m hangar.omd.server`).

This means hangar-evals works whether the-hangar sits beside it as a sibling or
is wired in as a git submodule -- there are no hardcoded in-tree paths.

```
~/Developer/muroc-aero/
  the-hangar/        # the tools under test (set HANGAR_REPO here if not the sibling default)
  hangar-evals/      # this repo
```

## Layout

```
hangar-evals/
  pyproject.toml          # dist: hangar-evals; PEP 420 hangar.evals namespace pkg
  src/hangar/evals/       # package source (leaf __init__ only)
  tests/                  # pytest suite (added alongside each step)
  examples/               # StudyRequest YAMLs for the have-agent bridge
  results/                # gitignored eval run outputs (never committed)
```

The `hangar.*` namespace and hatchling layout mirror the-hangar's packages, so
this reads as a sibling rather than a foreign tree.

## Develop

```bash
# from this directory, in a Python 3.11 environment
uv pip install -e ".[dev]"
python -c "import hangar.evals; print(hangar.evals.__version__)"
```

## Run as a have-agent study

`src/hangar/evals/have_bridge.py` plugs the eval runner into the sibling
[have-agent](https://github.com/muroc-aero/have-agent) study substrate via its
`--executor pkg.module:factory` plugin seam: one have-agent job = one eval
cell (case x harness x model, N seeds), executed through the same
`run_matrix` the CLI uses. Jobs write the standard results triple into
`results/`, so `paper/make_tables.py` in the-hangar consumes study-produced
rows exactly like manual runs — have-agent adds leases, retries, policy
gates, and a briefing on top, never a second source of truth for scores.

```bash
HAVE="uv run --project ../have-agent --with -e ."
$HAVE have submit examples/lane_c_eval.yaml     # full 12-case suite, claude arm
$HAVE have approve <study_id>
$HAVE have worker run --id worker:evals-1 --solvers evals \
    --executor hangar.evals.have_bridge:make_worker \
    --executor-opt results_dir=results
$HAVE have report <study_id>
```

An eval that runs cleanly but fails its grade is a *successful* job with a
failing CHECK verdict (`min_pass_rate` in the study's `acceptance:` block);
only harness crashes and malformed payloads fail the job itself. See the
header of `examples/lane_c_eval.yaml` for prerequisites and the sandboxed
(`omd_transport=http`, `sandbox=container`) variant.
