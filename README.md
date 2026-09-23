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
scripts/evals run gemma            # on-device, ~8.5 h, free
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

## What each harness shows the model

The anchor (Claude Code) receives omd's MCP `instructions` in its system
prompt and can list and read the server's resources; its first calls on
every case read `omd://reference` and `omd://plan-schema`. OpenCode 1.17.5
forwards neither (verified in the binary, 2026-09-21): the model sees omd's
tools and nothing else. So the OpenCode driver writes, before every run,
an `AGENTS.md` carrying the same instructions (OpenCode loads it into the
system prompt) and the two resources as `omd_reference.md` and
`omd_plan_schema.json` for its `read` tool -- inlined into `AGENTS.md` on the
unsandboxed track, which has no `read`. The texts are imported from
`hangar.omd` at run time (`drivers/omd_context.py`), never copied. Arms
before 2026-09-21 ran without this; the gemma arm of that date is the last
one that did.

## Ollama on the local arms

Ollama 0.30's MLX runner grows by about 0.5 GiB per request and never
shrinks: on 2026-09-22 `qwen3.6:35b-mlx` went from 21 GiB at load to 47 GiB
34 minutes later with no prompt above 33k tokens, the host swapped 20 GB,
and every seed after that ended in one turn with no tool call (the model's
opening sentence, then `stop`). Those look like graded FAILs but are an
infrastructure fault. The OpenCode driver therefore unloads the model after
every seed (`unload_model`, `keep_alive: 0`, a few seconds to reload), and
`unload_after_run=False` turns that off. If a local arm's seeds start
finishing in one turn, check `sysctl vm.swapusage` and Ollama's
`peak memory` log lines (`/opt/homebrew/var/log/ollama.log`) before
reading anything into the numbers. A long silent seed is not a second
face of the leak: read the GPU before calling it a hang (next section). Ollama 0.34.3 (MLX 0.32.1) has been in use since 2026-09-23
19:14 CEST; the campaign manifest records the version per arm.

## When a local seed hits the cap in silence

A timed-out seed is graded like any other (a run that happened before the
cap still counts), so a seed that hit the cap because something hung looks
exactly like one that hit it thinking. On the 2026-09-22 qwen arm three
seeds (oas_ocp_combined s0, pyc_turbojet s4, avy_three_tool s0) hit the cap
with the same signature: only `opencode run` in the container (no child
tool process), a new step and an empty reasoning part opened one second
after the last completed model request, no later `[GIN]` line for that
request, `ollama ps` showing the model `Stopping...`, the runner at ~70%
CPU, and `Request terminated: context canceled` the moment the client was
killed. That was read as the Ollama 0.30.10 MLX runner hanging (each
followed a `peak memory` of 30-32 GiB), the three seeds were marked lost,
Ollama was upgraded to 0.34.3, and the resume reproduced the signature on
the first seed at 25 GiB -- with the GPU at 95-99% -- and then ended it on
its own after 570 s: `step_finish reason=length`, 32 000 output tokens, no
tool call. A 32k-token step prints nothing on any side until it ends
(`Stopping...` only means the keep-alive expired under the in-flight
request), and at ~56 tok/s it takes ~10 min; the 0.30.10 seeds, slower
under the leak, ran into their caps first. The marks were undone
(`mark-lost --undo`); the three rows are the timed-out FAILs the arm
recorded. Ollama's access log lists only completed requests, so a request
in flight looks like an idle model -- read the runner lines, and check the
GPU (`ioreg -r -d 1 -c IOAccelerator | grep "Device Utilization"`) before
calling anything a hang.

The OpenCode driver leaves three things next to the events file:

- `opencode_stderr.txt` -- always.
- `opencode_timeout.json` -- on a timeout: the last printed event's time,
  the kill time, the gap (`silent_s`), and how to read them against
  `/opt/homebrew/var/log/ollama.log`.
- `opencode_state/` and `opencode_procs.txt` -- on a sandboxed timeout,
  `docker cp` of the container's `~/.local/share/opencode` (the SQLite
  session store: every message part, a pending tool call included) and
  its process list, taken BEFORE `docker kill`. A child process here with
  an incomplete tool part in the store would be a hung sandbox tool; none
  of the stalls so far had either.

A seed the evidence shows to be a harness loss (a hung tool, a dead
server, a rate limit) is not a result. Mark it so and the honest resume
retries exactly it, nothing else:

```bash
scripts/evals mark-lost pyc_turbojet --seeds 4 \
    --reason "omd server crashed at 03:52; seed ran 16 min against a dead socket"
scripts/evals run qwen --only pyc_turbojet       # resumes seed 4 only
scripts/evals mark-lost pyc_turbojet --seeds 4 --undo \
    --reason "the server was up; the seed was a 32k-token step"   # if the mark was wrong
```

`mark-lost` appends a superseding error row (type `HarnessLoss`, with the
reason and what it replaced) to the newest records file for the case; the
original row stays in the file. `--undo` appends the superseded row back,
over the mark and over any re-run rows a resume added meanwhile (a re-run
of a seed that was never a harness loss is a re-roll and does not count).
Never mark a seed lost for a verdict you disagree with -- that is what
`evals review` is for.

## When a seed exits nonzero

A nonzero `telemetry.exit_code` is reported by the harness-health banner and
never changes a verdict. The one seen repeatedly on the anchor is **not** a
harness defect and must not be "fixed":

```
API Error: Connection closed mid-response
terminal_reason: api_error
```

The upstream API drops the connection while the final message streams. The
agent's tool calls already happened and were recorded, so the run is gradable
from its provenance DB -- what is lost is the agent's own report, which costs
the reporting-fidelity score for that seed and nothing else. Three seeds of
`ocp_hybrid_twin` landed this way and still graded PASS.

The expensive part is the timeout: the CLI hangs for roughly 15 minutes per
drop before giving up. That is why `ocp_hybrid_twin` spent 78 of 91 wall-clock
minutes stalled on 13 minutes of actual agent work. Budget for it, or expect
an arm's wall clock to be dominated by drops rather than by analysis.

This is a condition to report, not a defect to repair. A seed the harness
genuinely lost -- crash, credential, network -- is a different thing: it
grades nothing, counts as `Lost`, and the arm needs re-running.

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
$HAVE have --db muroc.db submit examples/lane_c_eval.yaml   # full 14-case suite
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
submit `examples/lane_c_eval_sandboxed.yaml` instead: 28 cells, the 14-case
suite x {claude anchor under local Claude Code auth, `gemma4:26b-mlx` via
OpenCode/Ollama}, each with `omd_transport: http` + `sandbox: container`.
Its header lists the extra prerequisites (colima up, sandbox images built,
`CLAUDE_CODE_OAUTH_TOKEN` exported, gemma pulled).
