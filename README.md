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
  examples/               # StudyRequest YAMLs — the unit of an eval arm
  scripts/evals           # the one entry point: run / status / table
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

## Run an arm

One command runs a whole arm, prints every seed as it lands, keeps a table
current on disk, and re-renders the paper's tables when it finishes:

```bash
op run --env-file=op.env -- scripts/evals run anchor
```

`op` resolves the Claude Code token once and scopes it to that process tree, so
a five-hour bundle needs one unlock and no further prompts. The local arms need
no credential and no `op`:

```bash
scripts/evals run gemma            # on-device, ~14 h, free
scripts/evals run paper            # lanes + agent column + every arm
scripts/evals run anchor --dry-run # preflight and plan; no agent calls, no spend
scripts/evals run anchor --only paraboloid,pyc_turbojet
scripts/evals status               # the table from stored results; runs nothing
scripts/evals table                # regrade + re-render; runs nothing
scripts/evals review               # seeds awaiting a human look
```

An arm **is** its publication manifest — `examples/lane_c_pub_anchor.yaml` and
its siblings — read through the same `overrides` mapping the have-agent bridge
uses, so a manifest means one thing whichever front door drives it. Adding an
arm is a new YAML, not a new script.

What the runner guarantees:

- **Preflight before case one.** the-hangar resolves, the container runtime is
  up, the image exists, and one live turn proves the credential — derived from
  the manifest, so an on-device arm is never blocked on a token it does not use.
- **A live table.** `results/campaigns/<arm>_<stamp>/table.md` is re-rendered
  after every case. A crash at case 9 still leaves 8 cases tabulated.
- **Honest resume.** Re-running the same command skips cases that are *graded*
  and resumes ones the harness lost. That line decides re-runs: an agent that
  performs badly earns a graded FAIL, which is a RESULT and stays put, because
  re-running it would be sampling until the answer flatters. A harness that
  crashes, or loses its credential or network, produces nothing to grade, and
  re-running it is just finishing the measurement. Use `--force` to re-run
  everything once the harness is clean.
- **Post-run rendering.** Regrade plus `paper/make_tables.py`, on success,
  failure, and Ctrl-C alike.

Each run leaves `table.md`, `manifest.json` (what ran, at which SHA, with what
outcome), and `campaign.log` under `results/campaigns/<arm>_<stamp>/`.

## What gets graded

The grade is the run the agent **named** in its report's `run_id`, read from
the omd provenance DB — the effects of that run, never the numbers the report
claims for it. If the agent names no gradable run, the last successful run of
the right mode is used instead, and the seed is marked as having had its run
chosen for it.

Naming a run is not choosing an answer. A named run that failed, ran in the
wrong mode, or never happened grades nothing, so a report cannot conjure a
result its runs did not produce. Cherry-picking stays visible: every seed
records how many successful runs it made.

Before 2026-09-12 the policy was positional — the last successful run, full
stop — and it cost the 2026-09-11 anchor arm four seeds. Each had reported the
right answer and then kept working (a 500 NM sweep on a 250 NM task, a
surrogate wing model after the live one), and the exploration was what got
graded.

## Re-score an arm without re-running it

A verdict is a pure function of the provenance DB, the named run, and the
Lane A references — none of which involve the model. So a change in grading
policy can be applied to arms that already ran:

```bash
scripts/evals-reselect --campaign results/campaigns/anchor_<stamp>/manifest.json --dry-run
scripts/evals-reselect --campaign results/campaigns/anchor_<stamp>/manifest.json
python -m hangar.evals.regrade --results-dir results    # re-derive summaries
```

This is not a re-run and must not be reported as one: the agent's work is
untouched and no tokens are spent, only our reading of it changes. Every
rewritten record carries a `reselect` stamp saying which policy produced it.
It needs the seed's `data_root` and its workspace `claude_events.jsonl`; a
seed missing either keeps the grade it has and is reported as skipped.

## Run as a have-agent study

`src/hangar/evals/have_bridge.py` plugs the eval runner into the sibling
[have-agent](https://github.com/muroc-aero/have-agent) study substrate via its
`--executor pkg.module:factory` plugin seam: one have-agent job = one eval
cell (case x harness x model, N seeds), executed through the same
`run_matrix` the CLI uses. Jobs write the standard results triple into
`results/`, so `paper/make_tables.py` in the-hangar consumes study-produced
rows exactly like manual runs — have-agent adds leases, retries, policy
gates, and a briefing on top, never a second source of truth for scores.

This path and `scripts/evals` read the SAME manifests and call the same
`run_matrix`; they differ in what surrounds a cell. Use the campaign runner
for a publication arm you want to start and walk away from, and the study
substrate when you want leases, retries, and human approval gates. The
control plane needs the `have` CLI installed; the campaign runner needs
nothing beyond this repo.

```bash
# worker env = the-hangar project env (Lane A refs compute in-process)
#   + have-agent (the CLI) + this package with the [anchor] extra
HAVE="uv run --project ../the-hangar --with ../have-agent --with-editable .[anchor]"
$HAVE have --db muroc.db submit examples/lane_c_eval.yaml   # full 12-case suite
$HAVE have --db muroc.db approve <study_id>
$HAVE have --db muroc.db worker run --id worker:evals-1 --solvers evals \
    --executor hangar.evals.have_bridge:make_worker \
    --executor-opt results_dir=results
$HAVE have --db muroc.db report <study_id>
```

An eval that runs cleanly but fails its grade is a *successful* job with a
failing CHECK verdict (`min_pass_rate` in the study's `acceptance:` block);
only harness crashes and malformed payloads fail the job itself. See the
header of `examples/lane_c_eval.yaml` for prerequisites.

For the fully sandboxed run — every agent in a container, both arms —
submit `examples/lane_c_eval_sandboxed.yaml` instead: 24 cells, the 12-case
suite x {claude anchor under local Claude Code auth, `gemma4:26b-mlx` via
OpenCode/Ollama}, each with `omd_transport: http` + `sandbox: container`.
Its header lists the extra prerequisites (colima up, sandbox images built,
`CLAUDE_CODE_OAUTH_TOKEN` exported, gemma pulled).
