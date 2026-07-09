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

Tests that need a the-hangar checkout are marked `requires_hangar` and skip
automatically when `$HANGAR_REPO` doesn't resolve — that's what CI runs
(`.github/workflows/ci.yml`). With the-hangar installed alongside, `pytest`
runs the full suite.
