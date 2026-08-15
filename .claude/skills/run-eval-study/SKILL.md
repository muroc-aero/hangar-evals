---
name: run-eval-study
description: >
  How to run a Lane C agent-eval publication study (the anchor / gemma / qwen
  arms) as a governed have-agent job, end to end: preflight, submit, approve,
  launch a background worker under the 1Password token, watch it, and print the
  briefing. Use this skill whenever the user asks you to run, launch, kick off,
  or collect data for an eval arm (e.g. "run the anchor arm", "start gemma").
---

# run-eval-study

Runs one Lane C eval arm as a have-agent study: the manifest decomposes into an
ANALYSIS+CHECK+REPORT job DAG in `muroc.db`, a pull-worker executes each cell via
the `have_bridge` executor (writing the normal results triple so `paper/make_tables.py`
picks the rows up), and the worker publishes a briefing when the queue drains.

This replaces hand-assembling `$HAVE have --db muroc.db …` commands. One invocation
= one arm, run in the background so you keep working and come back for the report.

## Arms

| arm | manifest | harness / model | seeds | gate |
|---|---|---|---|---|
| `anchor` | `examples/lane_c_pub_anchor.yaml` | `claude` (Opus, no model override) | 3 | **human** — draws Max-plan usage; needs explicit approval |
| `gemma`  | `examples/lane_c_pub_gemma.yaml`  | `opencode` + `gemma4:26b-mlx` | 5 | auto — free, local |
| `qwen`   | `examples/lane_c_pub_qwen.yaml`   | `opencode` + `qwen3.6:35b-mlx` | 5 | auto — free, local |

All three are 11-case suites, sandboxed (`sandbox: container`, `omd_transport: http`),
each case capped at 900 s. Study id is `lane_c_pub_<arm>`.

## Prerequisites

Run from the repo root (`hangar-evals`). Two constants used throughout:

```bash
HAVE="uv run --project ../the-hangar --with ../have-agent --with-editable .[anchor]"
DB=muroc.db
```

The `HAVE=` prefix is **arm-invariant** — all three manifests document `.[anchor]`
(it only pulls in `claude-agent-sdk`; harmless for local arms). `have` reads the
DB from the `--db` flag placed **before** the subcommand.

> **zsh gotcha:** the default shell here is zsh, which does **not** word-split
> unquoted `$HAVE` — `$HAVE have …` runs the whole string as one command name and
> fails with "no such file or directory". Either inline the full
> `uv run --project ../the-hangar --with ../have-agent --with-editable ".[anchor]" have …`
> each time, or use `${=HAVE}` to force splitting. The examples below write `$HAVE`
> for brevity; expand it when you actually run.

Environment that must be up before launching:
- **container runtime** reachable (`docker info`) — runtime-agnostic. On macOS
  that runtime is colima (`colima start`); on native Linux it's the docker daemon
  (`sudo systemctl start docker`). The run path is identical either way (0.0.0.0
  omd bind + `--add-host=host.docker.internal:host-gateway`, portable across both).
- **sandbox images** built: `hangar-harness:anchor-*` (anchor) or `…:opencode-*`
  (local). Build with `./containers/build.sh` — same command on both OSes; build
  per-host (arm64 on the Mac, amd64 on spitfire) from the same Dockerfile.
- **anchor only** — the token in 1Password, resolved via `op run --env-file=op.env`.
  The `op`/`op.env`/`op://…` reference commands are byte-identical cross-OS; only the
  unlock backend differs: macOS = desktop app + Touch-ID; headless Ubuntu = a 1Password
  **service account** (`OP_SERVICE_ACCOUNT_TOKEN`, no desktop/biometric) — see design
  doc §8.2 / `secrets-1password-anchor-token`. Never export the OAuth token to a
  dotfile; never run token commands via the `!` prefix.
- **local arms only** — `ollama` up with the arm's model pulled (on Linux start it
  with `OLLAMA_HOST=0.0.0.0:11434`; MLX model tags are Apple-Silicon-only, so a Linux
  local arm needs GGUF/CUDA tags in its own manifest — keep local arms on the Mac).
- **caffeinate** (keep-awake) is a macOS launch-layer wrapper only; on Linux it's a
  no-op (servers don't sleep) — omit it, don't port it into the run path.

## Lifecycle — follow in order

### 1. Preflight (fail fast before touching the DB)

```bash
docker info >/dev/null && echo "runtime up"      # colima (macOS) or dockerd (Linux)
docker images | grep hangar-harness             # arm's image present?
# anchor only — verify the worker will SEE the token (prints length, never the value):
op run --env-file=op.env -- bash -c 'test -n "$CLAUDE_CODE_OAUTH_TOKEN" && echo "token OK len=${#CLAUDE_CODE_OAUTH_TOKEN}"'
# local only:
ollama list                                     # arm's model present?
```
If any check fails, stop and tell the user exactly what's missing — do not submit.

### 2. Submit

```bash
$HAVE have --db $DB submit examples/lane_c_pub_<arm>.yaml
```
Prints `study <study_id> proposed: 11 cases, 33 jobs {…}`. Capture the id:
```bash
STUDY=$($HAVE have --db $DB submit examples/lane_c_pub_<arm>.yaml | sed -n 's/^study \(\S*\) proposed.*/\1/p')
```
The study id is a generated **ULID** (e.g. `01KZPNN7SCAGAGB19YKZZAZPPC`), *not* the
manifest's `study: lane_c_pub_<arm>` name — capture it from the `submit` output as above.
Re-submitting creates a *new* study id; the manifest name is only the title. Already-done
cells are still skipped by the bridge's timestamped results, so re-runs don't redo work.

### 3. Approve (the gate)

- **anchor → HUMAN GATE.** Show the user the proposal and STOP for explicit approval:
  ```bash
  $HAVE have --db $DB review $STUDY      # prints plan, case count, inbox
  ```
  Do **not** run `approve` until the user says go. Then:
  ```bash
  $HAVE have --db $DB approve $STUDY     # → "study <id> approved (N jobs)"
  ```
- **gemma / qwen → AUTO.** Approve immediately (free, local):
  ```bash
  $HAVE have --db $DB approve $STUDY
  ```

### 4. Launch the worker — background, under the token

`--idle-exit 120` makes the worker self-terminate 120 s after the queue drains, so it
finishes on its own and you can come back for the report. Anchor wraps the launch in
`op run` so the token is injected into the worker process tree only; local arms don't.

```bash
mkdir -p logs
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
LOG=logs/worker-<arm>-$STAMP.log

# ANCHOR (token via op run):
nohup op run --env-file=op.env -- \
  $HAVE have --db $DB worker run --id worker:evals-1 --solvers evals \
    --executor hangar.evals.have_bridge:make_worker \
    --executor-opt results_dir=results \
    --idle-exit 120 > "$LOG" 2>&1 &
echo "worker PID $! → $LOG"

# LOCAL (no token needed) — same line without the `op run --env-file=op.env --` prefix.
```
Report the PID and log path to the user.

### 5. Watch (tower-lite — non-blocking)

```bash
$HAVE have --db $DB status $STUDY        # job tallies per type + worker heartbeat
$HAVE have --db $DB events --follow      # live event stream (Ctrl-C to stop)
tail -f "$LOG"                            # raw worker stdout
```
`status` prints e.g. `run  queued=3 running=1 done=7` and a `workers:` block with the
heartbeat. When every job is `done` and the worker has idle-exited, move to the report.
(P2 will point the range-safety dashboard at `muroc.db` for a graphical view.)

### 6. Report

The worker runs the REPORT job itself once ANALYSIS+CHECK drain, setting
`study.conclusion_ref`. Then:
```bash
$HAVE have --db $DB report $STUDY        # prints the briefing markdown
```
If it says `no report published`, the study hasn't finalized — check `status`; jobs may
still be running or the worker may not have reached the REPORT job yet.

Scores land in `results/` exactly like manual runs, so `paper/make_tables.py` merges
this arm into the comparison table regardless of when it ran.

## Resuming / stopping

- **Stop** the worker anytime: `kill <PID>` (or Ctrl-C if foreground). Cells checkpoint
  per seed — nothing is lost.
- **Resume**: re-run the same step-4 launch line. The worker claims only the remaining
  jobs; done cells are skipped.
- **Abort** a whole study: `$HAVE have --db $DB abort $STUDY`.

## Troubleshooting

- Need the id of a study you already submitted → `$HAVE have --db $DB status` lists all
  studies with their ULIDs and titles; match on the `lane_c_pub_<arm>` title.
- Worker exits immediately with no jobs done → check it's `--solvers evals` and the
  study is `approved` (not still `proposed`); `review $STUDY` shows the status.
- Anchor cells crash with auth errors → the token didn't reach the container; re-run the
  step-1 `op run` length check and confirm the launch used the `op run --env-file=op.env`
  prefix.
- A cell FAILs grading (not a crash) → that's a *successful* job with a failing CHECK, by
  design; it still records a result row. Only harness crashes fail the job.
