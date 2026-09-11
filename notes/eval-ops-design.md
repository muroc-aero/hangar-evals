# Eval-Ops Design — secrets, agentic runs, and the-tower

**Status:** DRAFT of 2026-08-10, partially superseded — see §0.1.
**Scope:** Fix how we launch, credential, and watch Lane C eval batches so a
publication run is *one governed action* + *a live view*, not hand-rolled bash,
copy-pasted tokens, and `tail -f | grep`.

---

## 0. TL;DR

Three complaints, one root cause, and — the surprise — **almost everything is
already built.** The pain is that we have **two parallel run paths** and have
been driving the wrong one:

| Path | Files | What it is |
|---|---|---|
| **A — hand-rolled (the mess)** | `scripts/run_*.sh`, `scripts/_run_lib.sh`, `scripts/_done.py`, `configs/lane_c_*/*.json` | Per-case JSON configs looped by bash, skip-guard for idempotency, tail-grep monitoring. What I was driving. Doesn't scale — every arm is a new script + 11 configs. |
| **B — governed (the good one)** | `examples/lane_c_pub_{anchor,gemma,qwen}.yaml`, `src/hangar/evals/have_bridge.py`, `muroc.db` | One YAML manifest per arm → have-agent decomposes to a job DAG in `muroc.db` → pull-workers run cells through the *same* `run_matrix` → policy gates + report. Already written. Never wired to secrets or a live view. |

**The whole design below is: retire Path A, finish wiring Path B, and point the
existing dashboard at `muroc.db`.** Net new code is small.

The three fixes:

1. **Secrets** — stop exporting `CLAUDE_CODE_OAUTH_TOKEN` by hand. Store it in
   1Password (or Keychain), resolve it at worker-launch with `op run` / `direnv`.
   The token already crosses into containers safely via a bare `-e VAR`
   passthrough (`drivers/sandbox.py`) — the *value* never appears in argv. We're
   only fixing where the host process gets it from: a vault, not your fingers.
2. **Running** — the interface becomes (a) **now:** a Claude agent + a Skill that
   drives the have-agent lifecycle from one instruction; (b) **later:** a single
   `hangar-evals run <manifest>` console command that a GUI can also call.
   No per-case CLI, no generated scripts.
3. **Monitoring** — **the-tower** = the existing range-safety dashboard pointed at
   `muroc.db`, giving a live web view of every job's queued/running/done/failed
   state. `have status` / `have events --follow` is the terminal "tower-lite" we
   already have. No W&B, no bespoke dashboard, no tqdm-as-the-answer.

---

## 0.1 Status, 2026-09-11 — Path A retired, Path B's manifest kept

Axis A (secrets) shipped as designed: `op run --env-file=op.env` resolves the
token once per process tree, `op.env` holds references only, and the container
still receives it through a bare `-e` passthrough.

Axis B (running) did **not** ship as Option 1/2. What shipped is
`src/hangar/evals/campaign.py` + `scripts/evals` — an in-process executor that
reads **the same `examples/lane_c_pub_*.yaml` manifests** through the same
`overrides` mapping as the bridge (`have_bridge.config_from_overrides`). So the
central claim of this note holds — *the manifest is the unit; adding an arm is a
new YAML, not a new script* — while the control plane it assumed does not yet
exist here: `have` is not installed on the dev machine, and the approval gates
are a poor fit for the actual need, which was to start a five-hour arm and walk
away from it.

Path A is gone regardless: `scripts/_run_lib.sh`, `scripts/_done.py`, and
`scripts/run_qwen_chunk*.sh` are deleted, and `scripts/run_{anchor,gemma,qwen}.sh`
are one-line shims. `configs/lane_c_*/` survives for single-case work with the
direct runner (`--config`), and is no longer what an arm is made of.

Two things this note got right that the bash loop had been getting wrong, and
which the runner now enforces:

* **Preflight is not optional.** A stale token converted a bundle into error
  rows; the probe is now derived from the manifest and re-run between cases.
* **Idempotency needs the records, not the summary.** `_done.py` asked whether
  a cell had `n_seeds >= seeds`, and an error row counts as a seed — so seven
  seeds of the 2026-09-10 anchor arm were skipped rather than retried on every
  later run. `results_index.case_status` reads the records and distinguishes a
  graded FAIL (a result) from an error row (an absence).

Axis C (monitoring) is unchanged and unbuilt. The stopgap is the live
`results/campaigns/<arm>_<stamp>/table.md` plus `scripts/evals status`; pointing
the range-safety dashboard at a control-plane DB remains the plan if and when
one exists.

---

## 1. What already exists (inventory)

Confirmed by reading the repos. This is why net-new code is small.

**Runner core — `hangar-evals/src/hangar/evals/run.py`**
- `RunConfig` (frozen) + `run_matrix(config, stamp) -> [CellSummary]`: runs
  {case} × harnesses × seeds. Python API, not just CLI.
- Drivers: `claude_sdk`, `claude_cli`, `opencode` (+ `sandbox`, `proc`).
- `sandbox="container"` requires `omd_transport="http"` (agent can't forge the
  provenance DB). Checkpoints per seed to `.jsonl`; `--resume` reruns only
  missing/error seeds.
- **Token passthrough:** `ContainerSandbox.wrap_argv` emits bare
  `-e CLAUDE_CODE_OAUTH_TOKEN` — docker reads the value from the client's own
  env, so the secret is never in the command line. Local-arm container passes
  `env_passthrough=()` (no token). *This part is already correct.*

**Control plane — `have-agent` + `muroc.db` (this repo's copy is the substrate)**
- `muroc.db` here (307 KB) is a have-agent control-plane DB: tables `study`,
  `job`, `job_dep`, `worker`, `verdict`, `event`. Append-only `event` log with
  update/delete triggers. One write surface; every change goes through
  `transition()`; humans touch only `proposed` and `review`.
- StudyRequest YAML → `submit` decomposes to ANALYSIS+CHECK jobs (+ a REPORT).
  Pull-workers claim runnable jobs (lease + reaper). Commands: `submit`,
  `approve`, `worker run`, `status`, `events --follow`, `review`, `report`.

**The bridge — `hangar-evals/src/hangar/evals/have_bridge.py`**
- `make_worker` factory → `(LaneCEvalExecutor, LaneCEvalCheckSuite)`. One
  have-agent case = one eval cell (case × harness × model) run for N seeds via
  `run_matrix`. Writes the normal results triple so `paper/make_tables.py` picks
  up study rows exactly like manual runs — have-agent never becomes a second
  source of truth for scores. Zero import of `have_agent` (duck-typed result).
- CHECK folds a cell summary into pass/warn/fail against `min_pass_rate`.

**The manifests — `examples/lane_c_pub_{anchor,gemma,qwen}.yaml`**
- The publication run is *already specified*: 11 cases × N seeds, sandboxed,
  arms as separate study files (anchor = Opus/3 seeds/deliberate window; local =
  free/overnight). Header even documents the launch recipe.

**Live view that already reads `muroc.db` — range-safety dashboard**
- `the-hangar/packages/range-safety/.../dashboard/app.py`: Starlette + React SPA,
  polls live. have-agent DECISIONS §29 verified
  `ReadModel(db_path="muroc.db").view_study(<id>)` renders a study directly.
  Launch: `uvicorn hangar.range_safety.dashboard.app:app --port 8011`.
- Terminal tower-lite already shipped: `have status`, `have events --follow`.

**"the-tower"** is not a repo — it's the *planned read-only view layer over the
append-only event log* (spec'd in `have-agent/have-agent-substrate-v0.md`:
"The tower is views over this table … No tower-owned storage, ever"). `have
status` is called "tower-lite, terminal" in that spec.

---

## 2. Axis A — Secrets (no copy-paste, no plaintext dotfiles)

**Decision:** token lives in **1Password**, resolved at worker launch. Nothing
on disk, nothing pasted, nothing in shell history. `colima` is irrelevant to
`op` (Touch-ID unlock via the desktop app, independent of the Docker engine).

**One-time setup**
```bash
op item create --category "API Credential" --title "claude-code" \
  --vault Dev 'token[password]=<paste once>'
# reference: op://Dev/claude-code/token
```

**Two ways it reaches the worker (pick one; both zero-per-run-copy-paste):**

- **`op run`** wrapping the launch — token exists only for that process tree:
  ```bash
  # op.env is committable: it holds a REFERENCE, not the secret
  # CLAUDE_CODE_OAUTH_TOKEN="op://Dev/claude-code/token"
  op run --env-file=op.env -- <the worker command>
  ```
- **`direnv`** so it's auto-present in the project shell only (nicer ergonomics):
  ```bash
  # .envrc (committable — reference, not value)
  export CLAUDE_CODE_OAUTH_TOKEN="$(op read 'op://Dev/claude-code/token')"
  ```

**How it crosses into the container:** unchanged — the existing bare
`-e CLAUDE_CODE_OAUTH_TOKEN` passthrough inherits it from the worker env. On a
solo machine the residual exposure (`docker inspect` of your own container) is
acceptable; if we ever want it gone, switch to a tmpfs-mounted secret file with a
one-line entrypoint (deferred, low value now).

**Fallback (drop the 1Password dependency):** macOS Keychain —
`security add-generic-password -U -w` to store,
`security find-generic-password -w` to read in `.envrc`. Same wiring, minus
stdout masking and shared rotation.

**Advanced (deferred):** `op-bridge` broker daemon so job containers never hold
the raw token and OAuth refreshes write back to 1Password. Overkill for now.

**Recommendation:** `op run` + `direnv` (`op read`). Adopt now; it's ~10 minutes.

---

## 3. Axis B — Running (agentic now, one-command later)

Per your call: **agent-driven now (Option 2), one CLI command later (Option 1)
to back a GUI.** Both sit on the *same* have-agent lifecycle.

### 3a. The lifecycle we're wrapping (Path B, already works)
```
submit  <manifest.yaml>   # decompose → proposed jobs in muroc.db
approve <study_id>        # human gate #1
worker run --executor hangar.evals.have_bridge:make_worker …   # runs cells
[review <study_id>]       # human gate #2 for warn/fail cells
report  <study_id>        # briefing artifact
```
This already replaces "generate 11 configs + a bash loop." The manifest is the
unit; adding an arm is a new YAML, not a new script.

### 3b. NOW — the agent + Skill (Option 2)
Create a **`run-eval-study` Skill** in this repo that encodes the lifecycle so you
say, in chat: *"run the publication anchor arm"* and the agent:
1. Preflight: colima up, sandbox images present, `op`/token resolvable (never
   prints the token), manifest valid.
2. `submit` the arm's manifest → capture `study_id`.
3. Surface the plan for your **approve** (gate #1 stays human — that's the point
   of governance; the agent proposes, you approve).
4. Launch the worker(s) under `op run`/`direnv` so the token is injected without
   copy-paste. Anchor = 1 worker (Opus budget/serial); local arms = can run
   overnight.
5. Watch via `have events --follow` / `have status` and report per-cell
   progress; route warn/fail cells to you for **review** (gate #2).
6. `report` when the study closes.

The Skill is the durable "how", so it's not re-derived each session and there's
no ad-hoc bash. The agent is the interface; governance gates stay human.

### 3c. LATER — one console command (Option 1, GUI-ready)
Add a `hangar-evals` console entry point (currently none in `pyproject.toml`):
```bash
hangar-evals run examples/lane_c_pub_anchor.yaml     # submit+approve+workers+wait
hangar-evals status <study_id>
```
It's a thin wrapper over the same have-agent calls the Skill makes, with
`--auto-approve` / budget flags for unattended local arms. A GUI (the-tower, or a
button in the dashboard) calls this exact command — so Option 1 and Option 2 are
the same engine with two front doors.

### 3d. Retire Path A
Once 3b works, delete `scripts/run_*.sh`, `scripts/_run_lib.sh`,
`scripts/_done.py`, and `configs/lane_c_*/` — the manifest + bridge + `run_keys`
idempotency supersede all of it. (Keep them until the first governed anchor run
succeeds end-to-end, as a rollback.)

---

## 4. Axis C — Monitoring / the-tower

**Decision:** the-tower = **existing range-safety dashboard pointed at `muroc.db`**,
plus the terminal `have status`/`events --follow` we already have. No new
storage (the spec forbids it — views only), no W&B, no bespoke server. tqdm-style
per-seed progress is a *nice-to-have inside a cell*, not the monitoring answer.

**Why this is the right reuse:** the dashboard is Starlette + React, polls live,
and DECISIONS §29 already proved it renders a have-agent study straight from
`muroc.db`. The event log is the single source of truth; the tower is a lens.

**the-tower MVP (smallest thing that gives "see what's running"):**
1. Launch the range-safety dashboard against this repo's `muroc.db`
   (`OMD_DB_PATH`/read-model path → `muroc.db`) on a fixed port.
2. Confirm the study/job/worker/event views render Lane C jobs (they're generic
   over the substrate, so expect yes; verify the CHECK verdict levels display).
3. If the generic study view is too aerospace-shaped, add **one** Lane-C view: a
   table of cells with completion% / pass-rate / seed status, sourced from the
   `verdict` + `event` tables (read-only). This is the only plausibly-new UI, and
   it's small.

**Does the-tower need standing up now?** For *this publication run* — **no**.
`have status` + `have events --follow` (tower-lite) plus the dashboard launched
read-only is enough to watch it. Building a richer the-tower is a **fast-follow**,
not a blocker. That's why the phasing below puts the run before the tower polish.

---

## 5. Phased plan

| Phase | Goal | Work | Blocker for a run? |
|---|---|---|---|
| **P0 — Secrets** ✅ DONE (2026-08-10) | Zero-copy-paste token | `op` item + `op.env`/`.envrc`; verify worker sees token; **no dotfile plaintext** | Yes (anchor) |
| **P1 — Agent Skill** ✅ WRITTEN (2026-08-10, pending first live run) | "Run arm X" from chat | Write `run-eval-study` Skill wrapping submit/approve/worker/report + preflight; token via `op run` | Yes |
| **P2 — Watch** | See what's running | Launch range-safety dashboard on `muroc.db`; confirm views; `have events --follow` for terminal | No (tower-lite suffices) |
| **P3 — Retire Path A** | One path only | Delete `scripts/` + `configs/lane_c_*/` after first clean governed anchor run | No |
| **P4 — One command** | GUI-ready front door | `hangar-evals run` console entry point over the same lifecycle | No (later) |
| **P5 — the-tower polish** | Rich live tower | Optional Lane-C cell view over `verdict`/`event`; the-tower as its own thin app if the dashboard proves too aerospace-shaped | No (fast-follow) |

**Critical path to collect publication data:** P0 → P1 → (watch with tower-lite).
Everything else is cleanup and ergonomics.

---

## 6. Decisions & open risks

**Decided (2026-08-10):**
1. **Secrets backend → 1Password** (`op run` + `direnv`, resolving `op read`).
   Keychain kept as documented fallback only.
2. **Approval gates → human-gate the anchor, auto-approve local.** Opus anchor
   requires `approve` (gate #1) + `review` of warn/fail cells (gate #2); free
   gemma/qwen arms launch with `--auto-approve` for overnight unattended runs.

**Still open (need your call):**

3. **Budget enforcement gap:** have-agent v0 declares `compute_budget` but does
   **not enforce** it — the only real spend-control for the Opus arm is
   idempotency (`run_keys`/skip) + serial single-worker + your `approve`. Fine for
   now; flag if you want a hard cap (small executor-side addition).
4. **the-tower home:** live as a launch-config of the range-safety dashboard
   (fastest), or eventually its own thin app in a new `the-tower` dir? Recommend:
   dashboard-config now, split out only if it earns it.
5. **Dashboard fit:** the range-safety dashboard's 5-state lifecycle is
   analysis-shaped; per-job queue status may want the P5 Lane-C view. Verify in P2
   before committing to build it.

---

## 7. End-state (what "fixed" looks like)

- You store the token once in 1Password and never touch it again.
- You say *"run the publication anchor arm"* to the agent (or later,
  `hangar-evals run …`). It preflights, submits, shows you the plan, you approve,
  it launches a sandboxed worker with the token injected from the vault, and
  streams per-cell progress.
- You watch a live web view of every cell (queued/running/done/pass/warn/fail) in
  a browser, backed by `muroc.db`, or `have events --follow` in a terminal.
- Warn/fail cells land in a review inbox for your verdict; the rest auto-accept.
- `report` emits the briefing; `paper/make_tables.py` merges the rows.
- No generated scripts, no per-case configs, no copy-pasted tokens, no tail-grep.

---

## 8. Portability & multi-OS (Linux + macOS, one codebase)

**Goal:** the framework runs unmodified on macOS/colima **and** native Linux
Docker. Runs split by convenience (local MLX arms on the Mac; anchor/API arms on
the Ubuntu box "spitfire", no GPU needed — Opus inference is remote), but there
is **one code path**. OS variance lives only in (a) per-arm config data and
(b) a thin install/bootstrap layer — never `if platform == "darwin"` in the run
path. Investigated 2026-08-11 (subagent recon); citations are current as of then.

### 8.1 The code changes (APPLIED + colima-validated 2026-08-13 — see §8.6)

Two edits, both **unconditional but harmless on macOS**, so no branch:

1. **`src/hangar/evals/omd_service.py:53`** — bind the omd HTTP service to
   `0.0.0.0` instead of `127.0.0.1` (default of the `host` param / env `OMD_HOST`).
   Keep the **readiness poll** (`:68`) on `127.0.0.1`. The FastMCP
   allowed-hosts guard (`:76-80`, `HANGAR_MCP_EXTRA_ALLOWED_HOSTS`) already admits
   the advertised `host.docker.internal` Host header, so widening the bind is safe.
   *Why:* on Linux the container reaches the host via the bridge gateway, and a
   loopback-only bind refuses that connection. On macOS, loopback clients still
   reach a `0.0.0.0` bind — no regression.

2. **`src/hangar/evals/drivers/sandbox.py:61`** (`ContainerSandbox.wrap_argv`) —
   add `--add-host=host.docker.internal:host-gateway` to the `docker run` argv,
   unconditionally. `host-gateway` is a Docker-standard special value (Engine
   20.10+) honored by Docker Desktop, colima, **and** native Linux — each maps it
   to the host. So `host.docker.internal` resolves identically everywhere and the
   in-container URL (`_containerize_url`, `opencode.py:143-150`;
   `advertise_host`, `run.py:206`) never changes.
   *Escape hatch (single seam, not a branch):* read `HANGAR_HOST_GATEWAY`
   (default `host-gateway`) so we can override the mapping if colima ever
   disagrees — **verify colima honors host-gateway with a one-shot test before
   committing** (if it already routes host.docker.internal to host loopback, the
   `--add-host` override must still land on something reachable, hence edit #1).

Ops-layer (no framework code), Linux-only, config/setup:

3. **Ollama** on a Linux box: start the daemon with `OLLAMA_HOST=0.0.0.0:11434`
   (`_containerize_url` already rewrites the URL — no code change). *Local arms
   only; anchor doesn't use ollama.*
4. **Model tags** are config data, not code: MLX tags (`gemma4:26b-mlx`,
   `qwen3.6:35b-mlx`) are **Apple-Silicon-only** (`run.py:21`). A Linux local arm
   needs GGUF/CUDA tags in *its own* manifest `model:` field. Anchor uses no model
   tag → OS-agnostic. **Recommended split: keep local arms on the Mac, anchor on
   spitfire** (avoids non-identical local results from a model swap).
5. **Images:** same `containers/{anchor,opencode}.Dockerfile` (pure
   `node:22-slim` + npm, nothing arch-specific). Rebuild per-arch on each host with
   the unchanged `docker build` command in each Dockerfile header. Add
   `containers/build.sh` (runs identically on both) to standardize tagging.

### 8.2 Secrets — 1Password on both OSes, only the unlock backend differs

Stay on 1Password everywhere: `op run --env-file=op.env` and the `op://…`
references (`op.env`, `.envrc`) are **byte-identical cross-OS**. Only the unlock
mechanism changes, and that's environment setup, not code or Skill divergence:

- **macOS:** desktop app + Touch-ID (done — see [[secrets-1password-anchor-token]]).
- **Ubuntu spitfire (headless):** 1Password **service account** — the purpose-built
  server/CI pattern. Install `op` from 1Password's apt repo, create a service
  account scoped **read-only** to the vault, set `OP_SERVICE_ACCOUNT_TOKEN` in the
  environment. Then the same `op run`/`op read` work with **no desktop app, no
  biometric**. Store the bootstrap token in a systemd `EnvironmentFile`
  (root-owned, `0600`) or the server keyring (`secret-tool`) — a scoped, revocable,
  single-vault-read credential, honoring the "no high-value plaintext token on
  disk" constraint better than the raw OAuth token would.
- `direnv` is on apt too → same `.envrc` ergonomics.
- Rejected alternatives: `pass` / `sops`+`age` / Vault (add a second secrets tool,
  fork the Skill); systemd `EnvironmentFile` alone for the *OAuth* token
  (reintroduces plaintext). Keeping `op` = exactly one secrets interface.

### 8.3 Install / bootstrap — OS detection allowed *here only*

One `scripts/bootstrap.sh` switching on `uname -s`: macOS → `brew` (docker/colima,
`op` + `1password` cask, direnv, ollama); Ubuntu → `apt` (docker-ce, `op` via
1Password apt repo, direnv; ollama optional). OS-branching in an installer is
inherent and fine — it is not the framework branching. Preflight already uses
`docker info` (runtime-agnostic); fix the "colima start" wording
(`scripts/_run_lib.sh:19`) to be generic.

### 8.4 Skill / launcher

Update `run-eval-study` preflight to check `docker info` (not `colima status`),
note the secrets unlock differs by host while `op run` is identical, and make the
`caffeinate` wrapper a launch-layer detail that no-ops on Linux (servers don't
sleep). Framework code untouched.

### 8.5 Confirms-single-path table

| Layer | macOS | Ubuntu (spitfire) | Divergent code? |
|---|---|---|---|
| Framework Python | 0.0.0.0 bind + host-gateway | same | **No** |
| Images | arm64 build | amd64 build (same Dockerfile) | No |
| Models | MLX tags in config | GGUF/CUDA tags in config | No (data) |
| Secrets | `op` + desktop/biometric | `op` + service-account token | No (same cmds) |
| Bootstrap | brew | apt | Only in installer |

### 8.6 Execution checklist

Framework code (DONE 2026-08-13 — validated on colima, no regression):

- [x] `omd_service.py` → separate `bind_host` (env `OMD_HOST`, default `0.0.0.0`)
      from the loopback `host` used for the readiness poll. Server binds wide;
      poll + `_free_port` stay on loopback.
- [x] `sandbox.py` `wrap_argv` → prepend `--add-host=host.docker.internal:host-gateway`
      (env `HANGAR_HOST_GATEWAY`, default `host-gateway`), unconditional.
- [x] Colima verify: `host-gateway` resolves to the colima gateway (`192.168.5.2`),
      and an in-container node client reached the omd service at
      `http://host.docker.internal:PORT/mcp` → **HTTP 406** (past FastMCP's 421
      DNS-rebinding guard = Host header admitted; 406 is content-negotiation, i.e.
      the request reached the MCP handler). NOTE: overriding `host.docker.internal`
      to the gateway IP means a loopback-only bind is now unreachable *even on
      colima* — the two edits are coupled, both required everywhere.
      *(Optional deeper check: run one local Mac cell end-to-end; agent code path
      is unchanged by these edits, so the isolated network test is sufficient.)*
- [x] Added `containers/build.sh` (both images, one command, OS-agnostic);
      fixed `_run_lib.sh` runtime-unreachable wording (macOS vs Linux).
- [x] Updated `run-eval-study` preflight → `docker info` (not `colima status`),
      documented secrets-unlock + caffeinate + ollama-host OS differences.

Spitfire setup (TODO — the anchor move; host setup, not framework code):

- [ ] Install docker-ce + `op` (1Password apt repo) + (optional) direnv.
- [ ] Create a 1Password **service account** scoped read-only to the vault;
      set `OP_SERVICE_ACCOUNT_TOKEN` via a root-owned `0600` systemd EnvironmentFile.
- [ ] `claude setup-token` once → store the OAuth token in the same 1Password item
      the `op://…` reference already points at (`op.env` is byte-identical).
- [ ] `./containers/build.sh anchor` (builds the amd64 anchor image).
- [ ] Canary `ocp_hybrid_twin` first with the workspace preserved (it reliably
      exit-1 crashes under the anchor — [[ocp-hybrid-twin-anchor-crash]]); read
      `claude_events.jsonl` for the real error before the full sweep.
- [ ] Run the `run-eval-study` Skill for the anchor arm.
