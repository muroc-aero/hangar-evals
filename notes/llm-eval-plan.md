# Local-LLM × Coding-Harness Eval Plan

> **This is the plan of record for the `hangar-evals` repo.** It moved here from
> the-hangar's gitignored `notes/` once the repo was scaffolded (Step 1).
> Started 2026-06-21. Hardware: **Apple M5 Pro, 48 GB unified memory**.

---

## 0. HOW WE WORK — read this first

Work **one step at a time, with the user in the loop on every step.** This is a
hard requirement from the user, not a style preference.

- Do **NOT** run ad-hoc smoke tests / one-off commands to "prove feasibility."
  That produces nothing reviewable and is not useful.
- Before building each step, write a short **spec** nailing down six things:
  1. **Purpose** — why the step exists.
  2. **Artifact** — the concrete files/dirs it produces.
  3. **Where / setup** — where the code lives on disk.
  4. **Git** — how it gets committed (and the user commits it, in their repo).
  5. **Organization** — how it's laid out and why.
  6. **Review** — exactly how the user verifies it.
- Get sign-off on the spec → build only that piece → user reviews → user commits
  → next step. The user runs the commits themselves.

**Why:** the user needs to understand and own this system, not be handed a
working black box. Reviewability and clear organization beat speed.

---

## 1. Goal

Benchmark how well **local LLMs** (on a 48 GB Apple-Silicon Mac) driven by
**agentic coding harnesses** (OpenCode, OpenHands) can:

1. **Reproduce correct engineering analyses** — numbers within tolerance of a
   trusted reference, and
2. **Correctly use the Hangar MCP servers and CLI tools** — valid tool calls,
   prescribed workflow order, recovery from errors.

Compare across **model × harness × task**, with a frontier hosted model (Claude,
via the existing Agent-SDK driver) as the ceiling/anchor. Every local result is
reported as **"% of the Claude anchor."**

---

## 2. Step ladder — the execution plan

Each step is **one reviewable commit** in this repo. The original ladder (Steps
1–6) built the harness end-to-end on one case; it is now **complete**. Step 7 and
beyond are the post-ladder refinements.

| # | Step | Reviewable artifact | Status |
|---|------|--------------------|--------|
| **1** | **Repo skeleton** — installable, no logic | `pyproject.toml`, `src/hangar/evals/__init__.py`, `README.md`, `.gitignore` | ✅ **DONE** (see §3) |
| **2** | **The seam** — resolve `HANGAR_REPO`, compute a Lane A reference | `src/hangar/evals/hangar_ref.py` + `tests/` proving `paraboloid → f_xy == 39.0` | ✅ **DONE** |
| **3** | **Driver interface + Claude anchor** — port `eval_lane_c.py`'s agent behind `AgentDriver` | `drivers/base.py`, `drivers/claude_sdk.py` + test | ✅ **DONE** |
| **4** | **OpenCode driver** — the local-model arm | `drivers/opencode.py` + the config it writes + test | ✅ **DONE** |
| **5** | **Scoring + trace** — numeric scoring (port) + tool-use + provenance metrics | `scoring.py`, `trace.py` + test | ✅ **DONE** |
| **6** | **Runner + one cell** — run `paraboloid × {claude, opencode} × T0` | `run.py` + a results file | ✅ **DONE** |
| **7** | **OpenCode MCP-only restriction** — disable built-ins, persist raw traces | `tools` disable map + `opencode_events.jsonl` | ✅ **DONE** |

**Harness is end-to-end** on paraboloid T0, validated by the first MLX live runs
(qwen3:8b / gemma4:26b-mlx / qwen3.6:35b-mlx). Post-ladder work, in priority order:

| # | Step | Reviewable artifact | Status |
|---|------|--------------------|--------|
| **8** | **Fix the dead `validated_before_execute` metric** (§12) — recompute from the tool trace; pin the activity vocabulary | `trace.py` + real-DB test | ✅ **DONE** |
| **9** | **Multi-seed + scriptable runs** — N seeds/cell → pass-rate `CellSummary`; `RunConfig` (JSON config + manifest) | `aggregate.py`, `run.py` (`RunConfig`/`run_matrix`), `configs/` | ✅ **DONE** |
| **10** | **Wire the Claude anchor live** — the "% of anchor" ceiling; contamination guard on the anchor | `[anchor]` install, `configs/paraboloid_claude.json`, tightened `_DISALLOWED_TOOLS` + `setting_sources=[]`, `test_claude_sdk.py` | ✅ **DONE** |
| **11** | **Effect-based grading + oracle self-test** — grade omd side effects (`run_cases`), demote the fenced-JSON self-report | `oracle.py` + `run.py` rewiring + ABC tests | ✅ **DONE** (spec §4c, PR #11) |
| **12** | **Reporting rigor** — pass@1/pass@k/**pass^k** in the aggregate; pin environment versions in the manifest (incl. explicit anchor model); surface token counts | `aggregate.py`, `run.py` manifest, `environment.py` | ✅ **DONE** (spec §4d, PR #12) |
| 13 | **omd-over-HTTP decoupling** — omd as a host-side HTTP service; parity test stdio↔HTTP (extracted from the old sandbox Task 1) | `MCPServerSpec` HTTP variant + `OmdHttpService` launcher + parity test | ✅ **DONE** (spec §4e, PR #13) |
| 14 | **Filesystem sandbox — container-per-run (colima)** — clean workspace OUTSIDE this repo; relax the interim tool blocklists (two commits: **14a anchor under local Claude Code auth**, **14b OpenCode/local-LLM arm**) | container image + `drivers/sandbox.py` + `drivers/claude_cli.py` + isolation test | **14a ✅ DONE** (spec §4f, PR #14; live sandboxed effect-graded PASS under local Claude Code auth); 14b after (spec §4b) |
| 15 | **Suite expansion** (T1 + T3 first) + per-case **task-validity** check (a scripted tool sequence reproduces Lane A) | new cases + scripted-baseline proofs | ⏭ **T1+T4 IMPLEMENTED 2026-07-18, awaiting review** (spec §4g — every effect-gradable example landed in one shape; live proof: **12/12 scripted baselines VALID, all metrics exact PASS**); **T3 trap/recovery cases split to 15b** (different grading design) |
| 16 | **OpenHands arm + deconfounding cells** — same local model through both harnesses; a Claude-via-OpenCode cell to split model vs harness ceiling | `drivers/openhands.py` + configs | todo |
| 17 | **Live run progress** — watch seeds/turns/tokens during a run (§12) | streaming driver + driver-agnostic progress callback | todo |

> **Ladder reordered 2026-07-02** after an external review against τ-bench /
> SWE-bench / BikeBench / Terminal-Bench practice (full findings in §12b):
> effect-based grading jumped ahead of the sandbox because the interim tool
> blocklist already guards contamination *today*, while the self-report grader
> corrupts every result collected until it flips — and every case added before
> the flip inherits the format-brittleness. The sandbox goes straight to
> containers (old Option B): the user runs colima, OpenHands (Step 16) forces
> containers anyway, and `sandbox-exec` is a quasi-deprecated detour. The
> omd-over-HTTP work is the shared enabler either way, so it becomes its own
> step (13).
>
> **Reordered again 2026-07-17**: execution order is now **12 → 14 → 15**
> (rows above are in execution order; step numbers are stable IDs, kept so the
> §4b/§4c/§4d spec headings and §12b cross-references stay valid). Sandboxing
> moved ahead of suite expansion because "sandboxed runs" is a standing
> requirement of the eval design, and every case added post-sandbox is born
> with the relaxed-blocklist semantics rather than migrated to them. Step 13
> (omd-over-HTTP) is deferred until a step concretely needs it — if the Step 14
> container can't run omd in-container over stdio, 13 folds back in as its
> prerequisite; otherwise OpenHands (16) decides.
>
> **Determination 2026-07-17 (Step-14 spec pass): it can't — 13 folds back in.**
> Not for plumbing reasons but grading integrity: inside the container the
> agent and a stdio-child omd share one privilege domain, so once Bash/Write
> are relaxed, every file omd can write — including `analysis.db`, the
> provenance DB that is the PRIMARY grading evidence since Step 11 — is
> agent-writable, reopening the forged-evidence class the effect grader
> exists to close. (It would also require the-hangar + the whole solver stack
> inside the image, violating threat (a).) Execution order is therefore
> **12 → 13 → 14 → 15**; Step 13 spec drafted at §4e, §4b re-amended.

---

## 3. What's DONE / verified (don't redo this)

### Step 1 — repo skeleton (committed or about-to-be)
- Repo created by the user at **`~/Developer/muroc-aero/hangar-evals`**, remote
  **`github.com/muroc-aero/hangar-evals`**, branch **`main`**. GitHub seeded a
  Python `.gitignore` + `LICENSE` in the initial commit.
- Skeleton written:
  - `pyproject.toml` — dist `hangar-evals`, py≥3.11, **hatchling**,
    **zero runtime deps** (deps added by the step that first uses them),
    `[tool.hatch.build.targets.wheel] packages = ["src/hangar"]`, pytest config.
  - `src/hangar/evals/__init__.py` — **leaf init only**. There is intentionally
    **NO `src/hangar/__init__.py`** (PEP 420 namespace rule, same as the-hangar).
  - `README.md` — repo purpose + the `HANGAR_REPO` seam + layout + dev install.
  - `.gitignore` — appended `results/` + `.DS_Store` to the GitHub template.
- **Verified:** editable-installed into the-hangar's `.venv` via
  `uv pip install -e` (NOT `uv sync` → the-hangar's pyproject/lockfile untouched).
  `hangar.__path__` lists `hangar-evals/src/hangar` alongside all the-hangar
  packages; `import hangar.evals` (0.1.0) and `import hangar.omd` both succeed.
  PEP 420 namespace composes. ✅

> **Dev-env decision pending:** Step 1 shares the-hangar's `.venv` for the
> coexistence check. For real dev, decide whether hangar-evals gets its **own**
> venv (with the-hangar installed into it) or keeps sharing. Either works with
> the `HANGAR_REPO` seam; see Open Questions.

### Verified environment (the smoke work — its only lasting value)
The earlier ad-hoc smoke commands produced no reviewable artifact (the mistake
that prompted the cadence rule), but they did establish these facts:

- ✅ **Ollama 0.30.10** (brew), daemon up. Client ≥0.19 ⇒ **MLX backend** active
  on Apple Silicon (32 GB+ requirement met by 48 GB).
- ✅ **OpenCode 1.17.5** (brew). Headless driver shape:
  `opencode run -m ollama/<model> --dir <ws> "<prompt>"`.
- ✅ **`qwen3:8b`** pulled (~5.2 GB).
- ✅ OpenCode → Ollama provider wired **and the omd MCP server connects**
  (`opencode mcp list` → `✓ omd connected`).
- ✅ End-to-end function calling works: `qwen3:8b` through OpenCode called
  `omd_start_session` and got back a session id.
- ✅ Lane A ground truth computes: paraboloid analysis `f_xy = 39.0` for
  x=1.0, y=2.0; `f = (x-3)^2 + x*y + (y+4)^2 - 3`.

**Key OpenCode gotchas (carry forward):**
- OpenCode exposes MCP tools as **`<server>_<tool>`** (e.g. `omd_start_session`),
  **NOT** the `mcp__omd__<tool>` form the Claude Agent SDK uses in
  `eval_lane_c.py`. Drivers must account for this naming difference.
- OpenCode did **not** auto-detect Ollama; the `ollama` provider was
  hand-authored in `opencode.json` via `@ai-sdk/openai-compatible` + baseURL.
- **Default output format showed tool calls reliably; `--format json` only
  emitted `step_start`/`step_finish`** in testing (text/tool parts not visible to
  a naive parser). The JSON event schema needs investigation before it can back a
  programmatic scorer (Step 5). Until then, lean on the omd provenance DB.

**Verified OpenCode config** (lived at the-hangar `notes/phase0/ws/opencode.json`,
gitignored scratch — captured here so it isn't lost; Step 4 productionizes it):
```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama (local)",
      "options": { "baseURL": "http://localhost:11434/v1" },
      "models": {
        "qwen3:8b": { "name": "Qwen3 8B (smoke)", "tools": true },
        "qwen3-coder:30b": { "name": "Qwen3-Coder 30B A3B", "tools": true }
      }
    }
  },
  "mcp": {
    "omd": {
      "type": "local",
      "enabled": true,
      "command": ["<the-hangar>/.venv/bin/python", "-m", "hangar.omd.server"],
      "environment": {
        "OMD_DATA_ROOT": "<ws>/omd_data",
        "OMD_DB_PATH": "<ws>/analysis.db",
        "OMD_PLAN_STORE": "<ws>/plans",
        "OMD_RECORDINGS_DIR": "<ws>/recordings"
      }
    }
  }
}
```

### Not yet started
- ⏸ **OpenHands arm** — Docker installed (29.6.0) but daemon was DOWN; start
  Docker Desktop (or use OpenHands' local runtime) before that arm. (Step 4+ is
  OpenCode-first; OpenHands is the second harness.)
- ⏸ Larger models: `qwen3-coder:30b` (A3B, ~18 GB) and the stretch
  `qwen3-coder-next` (80B-A3B, ~46 GB-class) — pull when their step needs them.

---

## 4. Step 2 spec — the seam (DONE — kept for the record)

**Purpose.** First runnable, reviewable logic: a single module that resolves the
path to the-hangar from `HANGAR_REPO` and computes a Lane A reference value by
running the example's `lane_a` in a subprocess (the same trick `eval_lane_c.py`
uses). Proves the contract that every later step depends on, on the cheapest case.

**Artifact.**
```
src/hangar/evals/hangar_ref.py   # resolve_hangar_repo(); lane_a_reference(example, ...)
tests/test_hangar_ref.py         # asserts paraboloid f_xy == 39.0 (within tol)
```

**Where / setup.** In this repo. Reads from `$HANGAR_REPO`
(default sibling `../the-hangar`); no hardcoded in-tree paths.

**Git.** One commit, `feat: hangar_ref seam — resolve HANGAR_REPO + Lane A refs`.
User commits.

**Organization.** `hangar_ref.py` is THE seam — the only module that knows where
the-hangar is. Everything downstream imports refs/tolerances through it.

**Review.** Read the two short files; `HANGAR_REPO=../the-hangar pytest tests/`
goes green; flip `HANGAR_REPO` to a bad path and see a clear error.

*(Open the spec for Steps 3–6 when we reach them.)*

---

## 4b. Step 14 spec — filesystem sandbox, container-per-run (amended 2026-07-02, re-amended 2026-07-17)

> **Amendments (2026-07-02 review):** (1) mechanism **DECIDED** — container-per-
> run on colima (old Option B); `sandbox-exec` (old Option A) dropped. (2) The
> omd HTTP decoupling ("Task 1") is **extracted to its own Step 13** — it is the
> shared enabler and independently testable. (3) Threat model gains item (e):
> the per-run workspace itself must move OUT of this repo. (4) Renumbered
> Step 11 → 14 after effect-based grading (§4c) was promoted ahead of it.
>
> **Amendments 2 (2026-07-17 spec pass — sign-off pending):**
> (1) **In-container stdio omd rejected; Step 13 is a hard prerequisite** —
> see the determination note in §2: a stdio-child omd shares the agent's
> privilege domain, so relaxed Bash/Write makes the provenance DB (the
> PRIMARY grading evidence, §4c) forgeable from inside the sandbox. omd runs
> host-side over HTTP; its state lives under a host-only `data_root` that is
> **never mounted** — the agent sees only a URL.
> (2) **Workspace ≠ data_root.** The mounted per-run scratch workspace
> (agent-writable, outside both repos) splits from `data_root` (host-only omd
> state). Today they are the same directory (`run_cell` hands `data_root` to
> both the driver and the oracle); after this step the driver gets
> `(workspace, omd_url)` and the oracle keeps `data_root`. Threat (e) closes
> structurally for the containerized arm.
> (3) **Delivery in two commits.** **14a** — container infra + the OpenCode
> arm: `containers/harness.Dockerfile` (node + pinned opencode), `sandbox.py`
> (external workspace + `docker run` wrapper), OpenCode `tools` map relaxed
> to workspace-scoped read/write/bash, isolation tests. **14b** — the Claude
> anchor in-container: auth (macOS keychain creds don't exist in a linux
> container — `claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN` env, verify)
> plus the in-container runner mechanism (thin standalone SDK runner script
> with NO `hangar.evals` in the image, vs headless `claude -p
> --output-format json`), and THEN `_INTERIM_FILESYSTEM_TOOLS` is relaxed —
> that blocklist is the anchor's, so it falls in 14b, matching the coupling
> documented in `test_interim_filesystem_tools_blocked_while_cwd_is_the_repo`.
> (4) **Recon 2026-07-17:** colima 0.10.3 installed (not running — start it
> before live tests); omd `--transport http` + auth-off-by-default confirmed
> (§4e enablers). Container→host reachability is the top open risk: both
> Ollama (default bind 127.0.0.1:11434) and the per-run omd service must be
> reachable from the colima VM (`host.docker.internal` /
> `--add-host=host.docker.internal:host-gateway`, fallback the lima gateway
> IP; may force `OLLAMA_HOST`/`OMD_HOST` to a non-loopback bind — if so,
> note the unauthenticated-on-LAN exposure and keep ports ephemeral).
> (5) **14a detailed spec: §4f (2026-07-18).** The recon-item-4 networking
> risk is RETIRED — live probes showed `host.docker.internal` forwards to the
> host loopback on the installed colima; loopback binds stay, no LAN exposure.
> (6) **Goal alignment 2026-07-18 — ARM ORDER SWAPPED (user direction):** the
> first version must run the Lane C examples sandboxed under LOCAL CLAUDE CODE
> AUTH, local LLMs after. So **14a = anchor in-container** (auth via
> `claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN`; mechanism DECIDED:
> headless `claude -p` stream-json, see §4f), **14b = OpenCode arm** (tools
> map + Ollama-over-`host.docker.internal`). `_INTERIM_FILESYSTEM_TOOLS`
> relaxes in 14a now (it is the anchor's list); OpenCode's map in 14b.

**Purpose.** Give each run a clean, isolated filesystem so the agent can be handed
rich Bash/Read/Write tools *without* test-set contamination — then **relax the
interim `_INTERIM_FILESYSTEM_TOOLS` blocklist** added in Step 10. This is the
end state the contamination reframe (§10) pointed at: control *reachability*, not
the tool list. It is also the isolation the OpenHands/CLI track (Step 16) needs,
so we build it once, here.

**Threat model (what the sandbox must make unreachable).** From inside a run, the
agent must NOT be able to read: (a) the-hangar source (solvers, `eval_lane_c.py`,
the scoring engine), (b) this repo's `hangar.evals` scoring code, (c) the Lane-A
**reference answers**, (d) the user's `~/.claude` memory / project `CLAUDE.md`
(the anchor's memory vector is already closed via `setting_sources=[]`; the local
arm needs the same starvation), and **(e) this repo itself via the workspace**:
today `run_cell` creates `data_root` under `results/run_data/` INSIDE
hangar-evals, and OpenCode runs with `--dir <data_root>` — once filesystem tools
are re-allowed, `../..` from the agent's cwd is the scoring code and a
`results/` directory full of prior answers. The per-run workspace must live
OUTSIDE this repo (temp root), whatever the mechanism; the container mount
closes it structurally. It MAY freely read/write a per-run scratch
workspace, reach the model endpoint (Ollama on host), and drive omd via its tools.

**Core architectural change — decouple omd from the agent's filesystem.** Today
each driver spawns omd as a *child* (`sys.executable -m hangar.omd.server`, stdio)
inheriting the agent's cwd — and that cwd is the-hangar repo. Under a sandbox a
child omd would inherit the filesystem deny and couldn't reach the-hangar. So omd
must run as a **host-side sibling service the agent connects to over a channel**,
never as a child in the agent's FS. **Enabler (verified):** omd is a FastMCP
server with an **HTTP transport** (`server.py:80`). So:
  * **omd runs on the host** with `HANGAR_REPO` set, serving HTTP on
    `127.0.0.1:<port>` (one instance per run, state rooted at the run's
    `data_root`, as `MCPServerSpec.omd` already arranges via `OMD_*`).
  * the **agent connects to that URL** — it gets the omd tools but no filesystem
    path to the-hangar. `MCPServerSpec` grows an HTTP/remote variant alongside the
    stdio one; the drivers render it (Claude SDK: `{"type":"http","url":...}`;
    OpenCode: the remote-server form in `opencode.json`).
  * **Task 1 (de-risk first — EXTRACTED to Step 13):** confirm the exact omd HTTP launch incantation
    (transport flag / env, host/port, whether auth/OIDC can be disabled for a
    localhost loopback) and that a run scores identically over HTTP vs stdio.

**Agent isolation mechanism — the decision for sign-off.** Two options, same omd
decoupling underneath:
  * **(A) macOS `sandbox-exec` (Seatbelt) profile** — wrap the harness subprocess
    in a profile that denies file reads outside the per-run scratch workspace
    (allow: workspace, the harness's own binaries/libs, loopback network for
    Ollama + omd). *Pro:* lightest, host-native, no colima, lands in one commit;
    works for both OpenCode and the Claude CLI. *Con:* `sandbox-exec` is
    quasi-deprecated (still ships on macOS 15); profiles are fiddly; covers the
    local Mac only.
  * **(B) container-per-run (colima/Docker)** — run the harness in a container
    with only a scratch volume mounted; reach Ollama + omd on the host via
    loopback. *Pro:* strongest, reproducible, **shared with the OpenHands arm**
    (Step 16 is container-based anyway); portable to CI. *Con:* heaviest —
    host-networking to Ollama, image build, and the omd-over-HTTP bridge all land
    at once; colima specifics (user runs colima, not Docker Desktop).
  * **DECIDED (2026-07-02): (B) container-per-run on colima.** Pay the container
    cost once: the user already runs colima, the OpenHands arm (Step 16) forces
    containers regardless, and `sandbox-exec` is a quasi-deprecated detour we'd
    maintain briefly then discard. With the omd-over-HTTP work extracted to
    Step 13, B's remaining scope is: image + scratch-volume mount + reaching
    host Ollama/omd from the container (colima supports `host.docker.internal`
    on recent versions; verify on the installed one, fallback the VM gateway
    IP). Containers also close threat (e) structurally — there is nothing to
    traverse up to.

**Artifact (Option B path — amended 2026-07-02).**
```
containers/harness.Dockerfile         # OpenCode (+ pinned version) image the run executes in
src/hangar/evals/drivers/sandbox.py   # per-run scratch workspace (OUTSIDE the repo) +
                                      #   container-run wrapper (mounts, host networking)
  (+ small wiring in claude_sdk.py / opencode.py: workspace cwd + the omd URL from Step 13)
tests/test_sandbox.py                 # the isolation proof (below)
```
*(The `MCPServerSpec` HTTP variant + host-side omd launcher land in Step 13.)*

**Acceptance test (the isolation proof + ABC oracle self-test).**
  1. **Isolation:** from inside the sandbox, an attempt to read a known the-hangar
     path (`packages/omd/examples/agent_eval/eval_lane_c.py`) AND the Lane-A
     reference value/answer file FAILS (no such file / permission denied).
  2. **Channel intact:** the omd tools still work over HTTP (a trivial
     `start_session`/`plan_init` round-trips).
  3. **Task unbroken:** a paraboloid run still completes and the anchor still
     **PASSES** end-to-end (re-run the Step-10 smoke, now sandboxed).
  4. **Blocklist relaxed:** with `_INTERIM_FILESYSTEM_TOOLS` removed, the agent
     can read/write inside its scratch workspace but step (1) still FAILS.
  5. **Oracle self-test (carry-over to effect-grading):** a no-op run cannot
     "pass by doing nothing."

**Non-goals (keep the commit reviewable).** No effect-based grading (Step 11,
§4c — lands before this); no omd HTTP work (Step 13 — lands before this); no
OpenHands arm (Step 16); no new cases; no CI containerization.
Just: clean external workspace + container isolation proven + blocklist relaxed.

**Risks.** (1) omd HTTP transport auth/OIDC on loopback — now Step 13's risk,
retired before this step starts. (2) Container→host networking on colima
(`host.docker.internal` support varies by colima/docker version) — verify
early; fallback is the VM gateway IP. (3) OpenCode's remote-MCP config shape
needs verifying (we only proved the stdio form in Step 4). (4) Per-run host omd
process lifecycle (port allocation, teardown on crash) — reuse the `data_root`
temp-dir discipline. (5) Image drift vs the brew-installed OpenCode — pin the
OpenCode version in the Dockerfile and record it in the run manifest (Step 12).

**Git.** One commit, `feat: per-run container sandbox — workspace isolation (Step 14)`.
User reviews/merges.

**Open decisions for sign-off.**
  * ~~Mechanism~~ — **RESOLVED (2026-07-02): (B) container-per-run on colima.**
  * ~~omd transport~~ — **RESOLVED (2026-07-17): host-side over HTTP; Step 13
    is a hard prerequisite** (see Amendments 2 and the §2 determination note).
  * ~~Blocklist scope~~ — **RESOLVED (2026-07-17): per-arm, in the commit that
    sandboxes that arm** — OpenCode `tools` map in 14a, the anchor's
    `_INTERIM_FILESYSTEM_TOOLS` in 14b (relaxing a blocklist before its arm
    is contained would open the very hole the sandbox exists to close).
  * **14a/14b split** (Amendment 2.3): two reviewable commits (recommended)
    vs one big one?
  * **14b anchor mechanism:** standalone SDK runner script inside the image
    (keeps the current driver/telemetry shape; NO `hangar.evals` in the
    image) vs headless `claude -p --output-format json` (no python in the
    image, but a driver rewrite). Decide at 14b sign-off — 14a doesn't
    depend on it.

---

## 4c. Step 11 spec — effect-based grading + oracle self-test (✅ DONE, PR #11)

**Purpose.** Flip the PRIMARY grader from the agent's fenced-JSON self-report to
the **side effects of what the agent actually ran** — the omd run outputs
persisted in the run's own `data_root` — compared against Lane A. This is the
direct fix for the seed-0 injustice (§12: a qwen3.6 run computed the correct
optimum but emitted prose → scored NO REPORT on format alone), and it aligns
the eval with every final-state benchmark in the §12 survey (SWE-bench,
τ-bench, AppWorld, Terminal-Bench, BikeBench). It must land BEFORE suite
expansion (Step 15) so every new case is born effect-graded, and before any
result we intend to keep.

**Ground truth located (recon 2026-07-02, on the passed anchor run
`paraboloid_claude_s0_8lylywo8`).** The oracle does NOT need to parse
`omd_data` HTML/YAML — the provenance DB's **`run_cases` table already stores
the numbers**:
  * `run_cases(case_type='final')` per `run_id` holds the output dict —
    analysis run: `{"paraboloid.f_xy": 39.0, "x": 1.0, "y": 2.0, "f_xy": 39.0}`;
    optimize run (row `iteration`=8): `{"paraboloid.f_xy": -27.3333…,
    "x": 6.66662…, "y": -7.33331…, "f_xy": -27.3333…}`.
  * `activities` rows `act-execute-<run_id>` give per-run execute status;
    `entities` has a `run_record` per run (`storage_ref` → `recordings/*.sql`).
  * Optimizer runs additionally have `case_type='driver'` iteration rows — a
    candidate mode discriminator.

**Task 1 (small de-risk, inside this step).** Pick + pin the run-**mode**
discriminator (analysis vs optimize vs polar/study). Candidates: presence of
`driver` rows; the `prov_edges` linkage run → plan + the plan YAML's mode; the
run-summary entity. Must be robust to agents naming plans arbitrarily (we
cannot key on `paraboloid-optimize`). Also confirm the WAL-checkpoint story for
read-only opens of a committed fixture DB.

**Grading policy (the decision for sign-off).** Per metric:
  1. Map `Metric.lane_a_module` (`analysis`/`optimization`) → the omd run
     **mode**; find the agent's runs of that mode in `run_cases`/`activities`.
  2. Grade the **last successful** run of the matching mode — the agent's
     final answer-by-action. Deliberately NOT best-of-all-runs: max-over-runs
     would reward spray-and-pray (τ-bench grades final state for the same
     reason).
  3. **No successful run of the required mode → that metric FAILs.** This is
     the τ-bench "pass by doing nothing" guard made structural: a no-op run
     cannot pass, with or without a forged report.
  4. The fenced-JSON self-report is DEMOTED to a secondary **reporting
     fidelity** signal: `parsed` (bool), `passed` (the old numeric compare,
     kept), and `matches_effects` (the agent's reported numbers within tol of
     what its own runs produced — honest self-reporting is a deployment-
     relevant trait in its own right).
  5. Record shape: top-level `passed` now means **effect-passed**;
     `reporting: {parsed, passed, matches_effects}` is the new sub-record;
     `completed` is redefined as "≥1 successful execute activity" (was:
     "emitted parseable JSON").

**Artifact.**
```
src/hangar/evals/oracle.py       # effect oracle: find the agent's runs in data_root,
                                 #   select per policy, extract metric values
src/hangar/evals/cases.py        # Metric: add the effect-source key mapping
                                 #   (e.g. opt_x -> run_cases final key "x")
src/hangar/evals/run.py          # run_cell: passed = effect score; reporting
                                 #   sub-record (aggregate.py field names follow)
tests/test_oracle.py             # ABC triple + policy tests (see Review)
tests/fixtures/                  # a checkpointed analysis.db from a real passed run
```
The fixture is our own run OUTPUT, not a reference answer — Lane A stays
computed-on-demand through the seam; nothing privileged lands in-tree that the
Step-14 sandbox wouldn't already make unreachable.

**Where / setup.** All in this repo. The oracle reads ONLY the run's
`data_root` (already captured per seed) — no new the-hangar surface. Existing
`results/run_data/*` dirs serve as dev fixtures; one minimal checkpointed DB
gets committed under `tests/fixtures/`.

**Git.** One commit, `feat: effect-based grading — grade omd side effects,
demote the self-report (Step 11)`. User reviews/merges.

**Organization.** `oracle.py` sits beside `scoring.py` with a clean split:
`scoring.py` stays the pure comparator (Metric, tolerances, verdicts — reused
unchanged by BOTH graders), `oracle.py` owns "what did the agent actually
produce", and `run_cell` reconciles the two into the record. Zero driver
changes — the step is 100% harness-neutral and benefits every current and
future arm.

**Review (acceptance = the ABC oracle self-test from §12).**
  1. **Known-good passes:** the committed fixture (a real passed anchor run)
     scores PASS through the effect oracle.
  2. **Perturbed fails:** the same fixture with one `run_cases` final value
     nudged outside rtol scores FAIL.
  3. **No-op cannot pass:** a fresh/empty `data_root` (no successful execute)
     FAILs every required metric — even with a forged "correct" fenced-JSON
     report attached to the agent text.
  4. **The seed-0 case now scores correctly:** replay an archived
     correct-but-prose run (or a synthetic equivalent): effect `passed=True`,
     `reporting.parsed=False`.
  5. **Live smoke:** re-run the Step-10 anchor smoke — still PASSES, now
     effect-graded, with `reporting.matches_effects=True`.

**Non-goals.** No pass^k / manifest version-pinning (Step 12), no sandbox
(Step 14), no new cases (Step 15), no prompt changes, no changes to the
tool-trace or provenance metrics.

**Risks.** (1) The mode discriminator may be ambiguous under exotic agent
behavior (multiple plans, replans, study runs) — Task 1 pins it; residual
ambiguity gets LOGGED into the record (`oracle_ambiguity` count), never
silently resolved. (2) `opt_x`/`opt_y` were `required=False` because DV
retrieval through the TOOL surface is unreliable — but `run_cases` stores x/y
directly, so the effect oracle likely makes them reliably gradable. Decide at
review whether they flip to required (recommend: required for the effect
grader, WARN-only for reporting fidelity). (3) `run_cases` key naming may vary
across omd examples (`paraboloid.f_xy` and `f_xy` are both present) — the
Metric mapping names ONE canonical key per case and the fixture test guards it.

---

## 4d. Step 12 spec — reporting rigor: pass^k, environment pinning, tokens (IMPLEMENTED 2026-07-17, awaiting review/merge)

**Purpose.** Make the numbers citeable. Three §12b findings, one step, no new
capability under test — this is all about how results are *reported*:
  1. **pass^k missing.** `aggregate.py` reports mean pass-rate only; the §12
     survey's own guidance is pass@1, pass@k AND pass^k together (τ-bench:
     GPT-4o retail pass^1 ≫ pass^8 ≈ 25% — reliability, not luck, is the
     production metric). Must land before any multi-seed sweep we intend to
     quote.
  2. **The manifest doesn't pin the environment.** The live anchor run recorded
     `claude-opus-4-8` arriving as the SDK *default* — it drifts silently when
     the SDK updates, and nothing records the-hangar SHA, omd/OpenCode/Ollama
     versions, or quant. SWE-bench-style scaffold disclosure starts with
     pinning our own environment.
  3. **Token counts are parsed then dropped.** OpenCode `step_finish` carries
     them; the Claude `ResultMessage.usage` is never read — and `num_turns`
     comes back `None` on anchor runs today (visible in the Step-11 smoke:
     `turns=None`).

**Current state (recon, 2026-07-02 — all confirmed in code).**
  * `aggregate.py` — `pass_rate = n_passed/n` only; no tokens Stat.
  * `run.py` — manifest is `{"stamp", "config"}`; **latent bug:** the module
    docstring promises "re-run via `--config <manifest>`" but
    `RunConfig.from_dict` rejects the wrapper keys (`stamp`, `config`) as
    unknown — `--config` works only on bare config files today.
  * `opencode.py` `parse_opencode_events` — reads `step_finish` `cost`,
    ignores the sibling token fields.
  * `claude_sdk.py` — `ResultMessage.num_turns` and `.usage` never captured;
    `AgentResult.num_turns` left `None`.
  * `HARNESSES["claude"]` default model is `None` → SDK default (the drift).

**Decisions (for sign-off).**
  1. **Estimators** (per cell: n seeds, c effect-passes; pure functions in
     `aggregate.py`):
       * pass@1 = c/n (unchanged headline `pass_rate`).
       * pass@k = 1 − C(n−c, k)/C(n, k) — unbiased "≥1 of k passes"
         (Chen et al. 2021).
       * pass^k = C(c, k)/C(n, k) — unbiased "all k pass" (τ-bench).
     Stored as curves `{k: value}` for k = 1..n in `CellSummary` (JSON keys are
     strings — accepted). n is small (3–5), so the curves are coarse; raw
     `n_passed/n_seeds` stays the headline and the curves are the trend signal.
  2. **Pin the anchor model literally.** `HARNESSES["claude"]` default flips
     `None` → `"claude-opus-4-8"`; `configs/paraboloid_claude.json` states it
     explicitly. A model is now ALWAYS a string in records/manifests — "SDK
     default" ceases to be a reachable state.
  3. **Environment block** in the manifest, captured by a new
     `environment.py` — **best-effort, never fatal** (a missing CLI records
     `"unavailable"`, never crashes a run): hangar-evals git SHA + dirty flag,
     the-hangar SHA + dirty (via `HANGAR_REPO`), python version, platform,
     `claude-agent-sdk` version (importlib.metadata), `opencode --version`,
     `ollama --version`. Written under `"environment"` in the manifest; NOT
     part of `RunConfig` (it's observed, not configured — reproduction compares
     it, doesn't replay it).
  4. **Tokens.** `AgentResult` gains `tokens: dict | None`, normalized to
     `{"input": int, "output": int, ...}` (extra provider keys pass through:
     `reasoning`, `cache_read`, …). OpenCode: sum `step_finish` token fields
     across steps. Claude: read `ResultMessage.usage` — and capture
     `ResultMessage.num_turns` while there (fixes `turns=None`). Record:
     `telemetry.tokens`; aggregate adds a `Stat` over output tokens (the
     model-effort proxy least confounded by prompt caching).
  5. **Manifest fix.** `--config` accepts BOTH shapes: a bare `RunConfig` JSON
     and a run manifest (descend into `"config"` when present, ignore
     `stamp`/`environment`) — making the docstring's promise true.

**Artifact.**
```
src/hangar/evals/environment.py   # capture_environment() -> dict (best-effort)
src/hangar/evals/aggregate.py     # pass_at_k / pass_pow_k curves + tokens Stat
src/hangar/evals/run.py           # manifest env block; --config manifest fix;
                                  #   telemetry.tokens; anchor model pin
src/hangar/evals/drivers/base.py  # AgentResult.tokens
src/hangar/evals/drivers/opencode.py   # sum step_finish tokens
src/hangar/evals/drivers/claude_sdk.py # usage + num_turns from ResultMessage
tests/test_aggregate.py           # estimator math + tokens Stat + back-compat
tests/test_environment.py         # capture shape + never-fatal guarantee
tests/test_opencode.py / test_claude_sdk.py / test_run.py  # token plumbing,
                                  #   manifest round-trip via --config
```

**Where / setup.** All in this repo. Zero new dependencies —
`importlib.metadata` + `subprocess` for git/CLI versions; `math.comb` for the
estimators.

**Git.** One commit, `feat: pass^k reporting, environment pinning, token
telemetry (Step 12)`. User reviews/merges.

**Organization.** Estimators are pure functions in `aggregate.py` beside
`Stat` (testable without records, reusable by the report layer).
`environment.py` is its own module so `run.py` stays orchestration and the
capture is testable offline. Driver changes are additive fields — no interface
break; `run_cell` reads `result.tokens` with a `None` fallback, so third-party
drivers that never set it still work.

**Review (acceptance).**
  1. **Estimator math is exact:** unit tests against hand-computed
     combinatorics — n=3, c=2 → pass@2 = 1.0, pass^2 = 1/3; c=0 → all zeros;
     c=n → all ones; k > n rejected. Property: pass^k ≤ pass@1 ≤ pass@k.
  2. **Manifest carries the environment** on a fake-driver `run_matrix`: git
     SHAs present (running in this checkout), and `--config <manifest>`
     reproduces the run (the round-trip test that today would fail).
  3. **Tokens flow end-to-end:** OpenCode fixture JSONL → summed counts;
     Claude driver captures `usage` + `num_turns` (mocked messages);
     `telemetry.tokens` lands in the record.
  4. **Backward compatible:** pre-Step-12 records (no `tokens`, no curves)
     still aggregate cleanly (same tolerance pattern as `n_report_parsed`).
  5. **Live smoke:** re-run the anchor cell — record shows
     `model="claude-opus-4-8"` (explicit, not default), `turns` non-None,
     tokens populated; manifest has the environment block.

**Non-goals.** No new cases (Step 15), no sandbox (14), no omd-HTTP (13), no
live token *streaming* (tokens land in the record; the live display is
Step 17), no random-seed reproducibility (still out of scope), no cost
modeling for local models (cost stays 0.0 there).

**Risks.** (1) pass^k at n=3 is a coarse estimate — mitigated by keeping c/n
as the headline and reporting curves as trend, not truth; bump seeds when a
number matters. (2) CLI version-output formats drift (`opencode --version`,
`ollama --version`) — capture is best-effort with `"unavailable"`, asserted
never-fatal. (3) Local-model token semantics vary by provider (reasoning
tokens, zeros vs missing) — normalize key names only, never invent values;
`None` ≠ 0 in the record. (4) SDK `usage` field names may change across
claude-agent-sdk versions — defensive `.get`, and the env block records the
SDK version so any drift is diagnosable after the fact.

---

## 4e. Step 13 spec — omd-over-HTTP decoupling (IMPLEMENTED 2026-07-17, awaiting review/merge)

> **Implemented 2026-07-17** (user green-lit the recommendations, so both
> open decisions resolved as recommended: parity arm = anchor, one extended
> `MCPServerSpec`). Live parity smoke: `paraboloid · claude/claude-opus-4-8 ·
> seed 0` over HTTP → **PASS effect-graded, all 4 metrics**, matching the
> Step-12 stdio baseline's verdicts (turns=46, wall=171.6s, cost=$1.372,
> tokens=9712/12021, `omd=http` in the summary line; results at
> `results/paraboloid_20260717T203443Z*`, manifest round-trips with
> `omd_transport: "http"`). 91 tests pass incl. a real two-concurrent-server
> lifecycle test. Risk (2) resolved: FastMCP serves **streamable HTTP** at
> `/mcp` (`mcp.streamable_http_app()`), which is what the SDK's
> `{"type": "http"}` speaks. Risk (1) (OpenCode remote-MCP shape) is
> rendered per the documented `{"type": "remote", "url": ...}` form and
> unit-tested, but not yet proven against a live OpenCode run — tracked as a
> 14a verify item, exactly as the risk note planned.

**Why now.** Pulled back from deferred by the Step-14 determination (§2 note,
§4b Amendments 2): the sandbox container cannot host omd as a stdio child
without putting the provenance DB and the solver stack inside the agent's
privilege domain. Host-side omd over HTTP is therefore a hard prerequisite for
14 — this step builds and proves the channel while everything else stays as it
is today (no container, no workspace move, no blocklist change). It is also
independently useful: it removes the last reason any driver's cwd must be
the-hangar.

**Enablers (verified by 2026-07-17 recon).**
  * The shared `run_server_main` (the-hangar `packages/sdk/.../server_main.py`)
    already takes `--transport {stdio,http}`, `--host`, `--port` (omd default
    8003) and serves MCP at `http://<host>:<port>/mcp`. No the-hangar changes
    needed.
  * Auth is **off unless** `OIDC_ISSUER_URL`/`KEYCLOAK_ISSUER_URL` is set
    (`build_auth_settings()` returns `None`) — unauthenticated loopback works
    out of the box; the loud stderr warning it prints is expected. The old
    "auth/OIDC on loopback" risk is retired.
  * Gotcha found: the server autostarts a range-safety dashboard thread on a
    FIXED port 7655 (`_maybe_start_rs_dashboard`) — two concurrent per-run
    servers would collide. The launcher must set `RS_DASHBOARD_AUTOSTART=off`.

**Artifact.**
```
src/hangar/evals/drivers/base.py     # MCPServerSpec grows transport + url; MCPServerSpec.omd_http()
src/hangar/evals/omd_service.py      # OmdHttpService: free port, launch, readiness wait, teardown
src/hangar/evals/drivers/claude_sdk.py  # render http spec as {"type": "http", "url": ...}
src/hangar/evals/drivers/opencode.py    # render http spec as OpenCode's remote-mcp entry (shape TBV)
src/hangar/evals/run.py              # RunConfig.omd_transport ("stdio" default | "http"); run_cell branches
tests/test_omd_service.py            # lifecycle tests + the stdio↔HTTP parity smoke (slow)
```

**Design.**
  * `MCPServerSpec` stays one frozen dataclass: add `transport: str = "stdio"`
    and `url: str | None = None`; `MCPServerSpec.omd_http(url)` carries no
    command/args/env (the client side needs only the URL — that IS the
    contamination property: no filesystem paths cross the channel). Drivers
    branch on `transport`.
  * `OmdHttpService(data_root)` (context manager): allocate a free port
    (bind-0), launch `sys.executable -m hangar.omd.server --transport http
    --host 127.0.0.1 --port N` with the same `OMD_*` env `MCPServerSpec.omd`
    sets today plus `RS_DASHBOARD_AUTOSTART=off`, poll `/mcp` until ready
    (bounded), yield the spec, and tear down SIGTERM→kill on exit — including
    the failure path. `host` is a parameter so Step 14 can bind an interface
    the colima VM reaches.
  * `run_cell` keeps stdio as the default path; `omd_transport: "http"` in
    `RunConfig` flips a cell, and the choice is recorded in the per-record
    telemetry + manifest so parity runs are self-describing.

**Acceptance.**
  1. **Lifecycle (fast, no model):** service starts; `/mcp` responds; the
     provenance DB initializes under `data_root`; teardown leaves no orphan;
     two concurrent services get distinct ports and don't collide.
  2. **Parity smoke (slow, live):** the paraboloid **anchor** cell over HTTP
     is effect-graded PASS with the same per-metric verdicts as the Step-12
     stdio smoke (`results/paraboloid_20260717T171527Z*`), and the record
     names `omd_transport: "http"`.
  3. **Contamination:** the rendered agent-side config contains the URL and
     no `OMD_*` path, no `sys.executable`, no the-hangar path.

**Non-goals.** No container (14a), no workspace move (14a), no blocklist
change (14a/14b), no new cases (15). Stdio stays the default transport until
Step 14 flips the containerized arm.

**Risks.** (1) OpenCode's remote-MCP config shape is unverified (only the
stdio form was proven, Step 4) — verify first; if OpenCode can't consume it
cleanly, the anchor still proves the channel and the OpenCode side moves to
14a. (2) FastMCP HTTP flavor (streamable-http vs SSE) vs what each harness
speaks — pin at implementation. (3) Orphaned server if the runner dies hard —
context-manager + best-effort `atexit`. (4) Readiness polling must bound
startup (the solver-stack import is slow, tens of seconds first run).

**Git.** One commit: `feat: omd as host-side HTTP service + stdio↔HTTP parity
(Step 13)`. User reviews/merges.

**Open decisions for sign-off.**
  * **Parity arm:** anchor (recommended — a proven-PASS baseline exists from
    the Step-12 smoke; one seed ≈ $1.35) vs OpenCode local (free, but
    qwen3:8b passes are flaky, so "same verdicts" is a weaker signal).
  * **MCPServerSpec shape:** extend the existing frozen dataclass
    (recommended — one type flows through every seam) vs a separate
    remote-spec class.

---

## 4f. Step 14a spec — container sandbox: infra + the Claude-anchor arm, local Claude Code auth (DONE 2026-07-18; live smoke PASS after the 421 allowlist fix)

> **Implemented results (2026-07-18).** 107/107 tests pass, including the live
> proofs: **isolation** (the-hangar + this repo's `results/` unreachable
> in-container; workspace mount real and writable), **channel** (container →
> advertised omd `/mcp` URL answers via node fetch), **image** (in-container
> CLI reports the 2.1.212 pin; warm container start ≈ 0.1–0.2 s on colima/vz,
> negligible vs a multi-minute agent run). A field lesson landed in the tests
> themselves: the isolation test first used pytest's `tmp_path` (=
> `/var/folders/...`) and hit the silent-empty-mount failure mode exactly as
> recon predicted — live tests now use `make_workspace()`.
>
> **Live smoke (2026-07-18, after the token arrived): acceptance item 3
> PASSED.** First attempt failed NO-RUN with a clean diagnosis: every
> in-container request 421'd on FastMCP's DNS-rebinding Host allowlist (spec
> item 5's amendment — auth, sandbox, parsing, and the agent itself all
> behaved; the server just never accepted the advertised name). After the
> two-sided allowlist fix: **effect-graded PASS**, all four metrics exact
> (`analysis_f_xy` 39, `opt_f_xy` −27.3333, `opt_x` 6.66667, `opt_y`
> −7.33333) — the same verdicts as the stdio (Step 12) and http (Step 13)
> baselines, i.e. §4b acceptance items 3+4 in one run. 47 turns, 201 s wall,
> 46 tool calls at 100% valid / 0 hallucinated, report `matches_effects=True`.
> Risk 1 (virtiofs file ownership under `USER node`) and risk 2 (headless
> OAuth in a clean container) are both retired by the same run.

> **Re-scope 2026-07-18 (goal alignment; supersedes the same-day OpenCode-first
> draft).** The first-version target is **the-hangar's Lane C examples running
> sandboxed under LOCAL CLAUDE CODE AUTH** (the user's subscription OAuth, not
> an API key), extending to local LLMs after. So the arm order **swaps**:
> **14a = container infra + the Claude anchor in-container** (was 14b);
> **14b = the OpenCode / local-LLM arm** (its tools-map relaxation moves there
> with it). The §4b open decision "14b anchor mechanism" is decided here:
> **headless `claude -p --output-format stream-json`** — node-only image, no
> python inside, and stream-json is exactly the protocol the claude-agent-sdk
> wraps, so the driver keeps the same trace/telemetry shape as the SDK driver.
> Verified on the host CLI **2.1.212** (the image pin): `--mcp-config` +
> `--strict-mcp-config`, `--setting-sources`, `--disallowed-tools`,
> `--permission-mode`, `--output-format stream-json` (requires `--verbose`),
> and `--max-turns` (absent from `--help` on 2.1.212 but parsed — it is what
> the SDK passes). Lane-C payload for Step 15 after this lands: the-hangar has
> **13 examples** under `packages/omd/examples/` (`paraboloid`,
> `pyc_turbojet`, `oas_*`, `ocp_*`, `evt_*`), each with
> `lane_c/*_open.prompt.md`; `cases.py` already keys on example names, so the
> suite extends without touching the sandbox.

> **Recon 2026-07-18 (live: colima 0.10.3 / vz / virtiofs / docker client
> 29.6.0) — §4b's top risk is RETIRED.** From inside a container,
> `host.docker.internal` resolves with NO extra flags (`--add-host` not
> needed) and forwards to the host **loopback**: a `127.0.0.1`-bound test
> listener AND the real Ollama (`127.0.0.1:11434`) both answered 200. So omd
> and Ollama keep their loopback binds — no `OLLAMA_HOST` change, no
> unauthenticated-on-LAN exposure, ports stay ephemeral. Two new facts with
> teeth: **(1) bind-mounts only see paths under colima's VM mounts** (default
> config `mounts: []` = `$HOME` + `/tmp/colima`) — a dir under
> `/var/folders/...` (python's default `mkdtemp`) mounts **silently empty**,
> so the workspace root MUST live under `$HOME`. **(2) opencode 1.17.5
> (matches brew) is a self-contained binary** — no runtime npm provider
> install — but it caches the models.dev registry
> (`~/.cache/opencode/models.json`); pre-warm that at image build so
> containers don't fetch it (or hang offline) at run start.

**Purpose.** First half of the §4b delivery, anchor-first: the container
infrastructure plus the **Claude anchor running IN the container under the
user's local Claude Code (subscription) auth**, with the anchor's
`_INTERIM_FILESYSTEM_TOOLS` finally relaxed — inside the sandbox those tools
only reach the scratch workspace, which is the payoff the interim blocklist
was always waiting on. The OpenCode/local-LLM arm is untouched (14b).

**Design.**
  1. **Workspace ≠ data_root** (Amendment 2.2 — closes threat (e) for this
     arm). When sandboxed, `run_cell` creates a per-seed **workspace** via
     `mkdtemp` under `~/.cache/hangar-evals/workspaces/` (mountable, outside
     both repos) and hands THAT to the driver; `data_root` stays under
     `results/run_data/` (omd state + oracle evidence, host-only, **never
     mounted**). The record gains a `"workspace"` field; driver debug
     artifacts (`mcp_config.json`, `claude_events.jsonl`) land in the
     workspace, retained like `run_data`.
  2. **`drivers/sandbox.py`** — `make_workspace()` (root policy: MUST be
     under `$HOME`, see recon fact 1 — unit-tested) +
     `ContainerSandbox(image)` rendering `docker run --rm
     -e CLAUDE_CODE_OAUTH_TOKEN -v <ws>:/workspace -w /workspace <image>
     <inner argv>`. Only the workspace is mounted; the token crosses as an
     env var whose VALUE never appears in argv; `--rm` per run.
  3. **`containers/anchor.Dockerfile`** — `node:22-slim` + `npm install -g
     @anthropic-ai/claude-code@2.1.212` (pinned == host CLI), `USER node`
     (Claude Code refuses permission-bypass as root, and workspace files
     shouldn't come back root-owned), tagged
     `hangar-harness:anchor-2.1.212`. No python, no hangar code, no
     `~/.claude` state — threat (d) closes structurally.
  4. **`drivers/claude_cli.py` — `ClaudeCliDriver`** (new; the SDK driver
     stays for unsandboxed host runs). Runs `claude -p <prompt>
     --output-format stream-json --verbose --model <m> --max-turns <n>
     --permission-mode bypassPermissions --setting-sources ""
     --mcp-config /workspace/mcp_config.json --strict-mcp-config
     --disallowed-tools Skill WebSearch WebFetch` inside the sandbox, parses
     the stream-json events (assistant text / tool_use, user tool_result,
     result telemetry) into the SAME `AgentResult` shape as the SDK driver —
     reusing its `_classify_tool_result` / `_normalize_usage` /
     `_normalize_tool_name`. Auth: fails fast with `claude setup-token`
     guidance when `CLAUDE_CODE_OAUTH_TOKEN` is unset (macOS-keychain creds
     don't exist in a linux container).
  5. **`OmdHttpService` grows `advertise_host`** — binds and readiness-polls
     `127.0.0.1` as today; the returned spec's URL uses the advertised host
     (`host.docker.internal` when sandboxed). ~~Loopback recon says no other
     change is needed.~~ **Falsified by the first live smoke (2026-07-18):**
     reachability is not acceptance. FastMCP freezes a localhost-only
     DNS-rebinding allowlist at construction, so every in-container request
     421'd on its `Host: host.docker.internal` header and the MCP connect
     hung at "pending" (the anchor burned 17 turns ToolSearch-ing for tools
     that never appeared). Fix is two-sided: the-hangar's `server_main` gains
     `HANGAR_MCP_EXTRA_ALLOWED_HOSTS` (comma-separated Host values, applied
     before the session manager freezes; guard stays ON otherwise), and
     `OmdHttpService` sets it to `{advertise_host}:*` for its child exactly
     when advertising a non-bind name. Pinned host-side in the lifecycle test
     (advertised name admitted on the advertising service, still 421 on the
     non-advertising one) and in the tightened container channel test
     (status != 421, not merely "answered").
  6. **Blocklist relaxation, per-arm as decided (§4b):** the sandboxed CLI
     driver passes ONLY `_CONTAMINATION_TOOLS` (Skill/WebSearch/WebFetch — a
     filesystem sandbox can't stop those vectors); Bash/Read/Write/Edit/
     Glob/Grep/NotebookEdit/Task are AVAILABLE, scoped to the container. The
     host SDK driver keeps the full interim blocklist (its cwd is still the
     the-hangar repo). OpenCode's all-False tools map is untouched until 14b.
  7. **`RunConfig.sandbox: "none" | "container"`** (default `"none"`).
     Validation: `"container"` requires `omd_transport == "http"` (the §2
     determination — stdio omd in-container makes the grading evidence
     forgeable) and, until 14b, `harnesses ⊆ {"claude"}` — opencode ×
     container is a hard error. Records carry `sandbox` + the image tag;
     `configs/paraboloid_claude_sandbox.json` scripts the smoke.

**Acceptance (§4b's proof, scoped to this arm).**
  1. **Isolation (live):** from inside the container, reading a known
     the-hangar path (`packages/omd/examples/agent_eval/eval_lane_c.py`) and
     this repo's `results/` FAILS (paths absent — nothing to traverse to);
     a probe file in the workspace is readable/writable (also proves the
     mount isn't silently empty, recon fact 1).
  2. **Channels intact (live):** container → advertised omd `/mcp` URL
     answers; the CLI inside the image reports its pinned version.
  3. **Task unbroken (live smoke, needs the token):** sandboxed `paraboloid ×
     claude/claude-opus-4-8 × 1 seed` is **effect-graded PASS** with the same
     per-metric verdicts as the stdio (Step 12) and http (Step 13) baselines
     — §4b acceptance items 3 + 4 in one run, since this run already has the
     filesystem tools relaxed.
  4. **Relaxed safely:** unit tests pin BOTH blocklists — the sandboxed CLI
     argv blocks only Skill/Web* (Bash et al. absent), the host SDK path
     still blocks the interim set — so neither can drift silently.
  5. **Config contamination:** the rendered `mcp_config.json` + argv carry
     container paths and `host.docker.internal` URLs only — no host
     filesystem path, no `OMD_*`, no token value.

**Non-goals.** No OpenCode/local-LLM sandboxing (14b — its image, tools-map
relaxation, and Ollama-over-`host.docker.internal` wiring); no OpenHands (16);
no new cases (15 — but nothing here blocks the Lane-C payload); no CI image
publishing; no network isolation beyond the tool blocklist (the container
legitimately needs omd; the anchor needs api.anthropic.com).

**Risks.** (1) File ownership on virtiofs mounts (`USER node`, uid 1000, vs
host uid) — verify at live test; fallback `--user` mapping. (2) Headless CLI
auth wrinkles with an OAuth token in a clean container (no `~/.claude.json`
onboarding state) — verify with a trivial `-p` call before the full smoke.
(3) The silent-empty-mount class — workspace-root policy unit test + the
live probe-file assert. (4) Image drift vs the host CLI — version pinned in
the Dockerfile, recorded per-record. (5) stream-json event-shape drift
across CLI versions — parser is tolerant (unknown event types skipped), pin
recorded. (6) `--max-turns` is undocumented on 2.1.212 — verified parsed
today; re-verify on version bumps.

**Git.** One commit: `feat: container sandbox — external workspace + Claude
anchor arm, local auth (Step 14a)`. User reviews/merges. (Prereqs: Steps
12 + 13 merged.)

**Open decisions.**
  * ~~Anchor mechanism~~ — **RESOLVED (2026-07-18): headless CLI
    stream-json** (node-only image; same protocol the SDK wraps; no python or
    hangar code in the image). The SDK-runner-script option is dropped.
  * ~~Arm order~~ — **RESOLVED (2026-07-18): anchor first** (user goal:
    first version = Lane C examples, sandboxed, local Claude Code auth;
    local LLMs extend after in 14b).
  * Workspace root `~/.cache/hangar-evals/workspaces/`, knob shape
    `"none"|"container"`, and the 14a/14b split carry over from the morning
    draft as recommended.
  * **User action required for acceptance item 3:** mint the token once with
    `claude setup-token` and export `CLAUDE_CODE_OAUTH_TOKEN` (the live smoke
    and the token-gated tests skip cleanly without it).

---

## 4g. Step 15 spec — Lane-C suite expansion + task-validity baselines (T1+T4 IMPLEMENTED 2026-07-18, awaiting review/merge)

**Purpose.** Convert the proven single-case capability (paraboloid, sandboxed,
local Claude Code auth) into the actual v1 goal: **the Lane-C examples,
plural**. Every the-hangar example that is effect-gradable lands as a case,
and every case ships with a **task-validity baseline** — a scripted (non-LLM)
tool sequence that reproduces Lane A through the SAME channel an agent uses —
so a model failure can never be confused with a task defect (§12b finding:
τ-bench-style task-validity checks; GAIA's "solvable by construction").

**What landed (one commit + one upstream PR).**

  1. **12-case suite** (`cases.py`): paraboloid (T0, hinted prompt) + 11 new
     cases on their `lane_c/*_open.prompt.md` open prompts — `oas_aero_rect`,
     `oas_aerostruct_rect`, `ocp_caravan_basic`, `ocp_caravan_full`,
     `ocp_hybrid_twin`, `oas_ocp_combined`, `ocp_oas_coupled`,
     `ocp_oas_direct`, `pyc_turbojet`, `ocp_three_tool` (T4 multi-tool),
     `evt_native_sizing`. Metrics/tolerances mirror the-hangar's
     `eval_lane_c.py` (lane-c-full-coverage). **`ocp_pyc_coupled` is
     excluded by design**: its materializer weight-slot precedence forces an
     OEW passthrough (~8% OEW gap vs Lane A), so the task is not achievable
     through the tools — exactly what the validity check exists to catch.
     T3 trap/recovery cases are NOT here (15b): they need new prompts and a
     recovery-grading design, not just case-table rows.

  2. **Oracle evidence layers** (`oracle.py`). Raw `run_cases` final rows
     hold OpenMDAO variable names, but suite metrics are SUMMARY quantities
     (`fuel_burn_kg` integrates phases; `CL` reads a solver point) that never
     appear as recorder variables. omd snapshots summary scalars into the
     `assessment-<run_id>` metadata — but only through an OAS+OCP allowlist
     that silently dropped pyCycle's `Fn`/`TSFC`/`OPR` and evt's
     `sized_mtow_kg`/... → **the-hangar PR #101** widens the snapshot to
     every scalar summary key. The oracle now reads both layers: assessment
     scalars (+ composite per-component summaries flattened to
     `<comp_id>.<key>`) overlaying raw final data. Metric lookup: exact key
     in assessment values → exact key in raw recorder data (paraboloid's
     promoted `x`/`y`/`f_xy`) → **unique dotted-suffix match** among
     flattened component keys (composites — the agent names the components,
     so `wing_CL` grades via `effect_key="CL"` whatever the comp id is; an
     ambiguous suffix grades None, never a guess). `MODE_BY_MODULE` is now
     total over the suite (all new modules → `"analysis"`), and a test keeps
     it total so an unmapped module can't grade silently wrong.

  3. **Task-validity runner** (`validity.py`). Per case, a scripted MCP
     client (ported from the-hangar's in-process `test_parity_lane_c.py`)
     drives the REAL `OmdHttpService` over streamable HTTP — same server,
     transport, and `data_root` as a live seed — then the outcome is graded
     by the SAME oracle+scoring path as an agent run. VALID means both
     halves hold: the tool surface reproduces Lane A, AND the oracle can
     read the evidence from the provenance DB. Physical inputs come from
     each example's `shared.py` via a new seam helper
     (`hangar_ref.shared_constants`, same subprocess-per-example pattern as
     `lane_a_reference`). CLI: `python -m hangar.evals.validity --case X` /
     `--all`; writes `results/validity/validity_<stamp>.json`. evt uses the
     vendored `archer_midnight` template (the blind-agent path; the
     repo-relative `CONFIG_DIR` Lane A loads doesn't resolve under the
     service's `cwd=data_root` — and matches by design anyway).

**Dependencies.** the-hangar `lane-c-full-coverage` (the open prompts +
parity tests) must be present in the resolved checkout — `test_cases.py`
fails loudly at suite level if not — and the-hangar **PR #101**
(assessment-scalar widening) must be live for pyc/evt effect grading.

**Non-goals.** No T3 trap/recovery cases (15b); no T2 no-hint/workflow
grading variants; no anchor/local-model runs across the suite (that's just
`--case <name>` now — budget the anchor sweep deliberately, ~$1.7/case-seed);
no polar/study run modes in the oracle.

> **Live proof (2026-07-18, `validity_20260718T160242Z.json`): 12/12 cases
> VALID, every metric an exact PASS against its Lane A reference.**
> Highlights: `oas_ocp_combined` graded `wing_CL`/`wing_CD` through the
> unique-suffix path (composite metrics survive agent-chosen component ids),
> and `pyc_turbojet` + `evt_native_sizing` graded through the widened
> assessment snapshot (the-hangar PR #101) — both evidence layers proven on
> the wire, not just in unit tests. Wall clock: 1.5–6.3 s per case, except
> `ocp_three_tool` at 2513 s active compute (monotonic; end-to-end elapsed
> was ~4 h including machine sleep). Its server log shows the pyCycle HBTF
> surrogate deck generated **three times** in one baseline (one per factory
> instantiation across assemble/validate/run) plus a **fourth** in the
> Lane A reference subprocess — `deck_path=None` means no disk cache
> (`pyc/surrogate.py`). Until the-hangar caches decks (the `deck_path`
> option exists; nothing sets it — candidate upstream follow-up),
> `ocp_three_tool` is impractical for multi-seed sweeps; keep it in the
> suite for validity/anchor use, but budget it separately.

---

## 5. Repo scaffolding decision + the seam

**Separate repo `hangar-evals`** (importable as `hangar.evals` via PEP 420),
NOT a monorepo package, NOT part of range-safety. Why separate:
- Heavy/unusual deps (OpenHands, OpenCode, MLX/Ollama clients) must not infect
  the-hangar's lockfile, the deployed MCP-server Docker images, or tool CI.
- range-safety = operational/runtime V&V; this = offline agent benchmarking.
- Standalone benchmark = citeable artifact alongside the AIAA case studies.

**The seam (one contract, reuse the existing convention):**
- Resolve the-hangar via **`HANGAR_REPO`** env var (default sibling
  `../the-hangar`), exactly like
  `lakesideai-infra/scripts/package-case-study.sh`.
- Import the installed `hangar.*` MCP servers (`python -m hangar.omd.server`, …).
- Read Lane A refs + `shared.py` tolerances from
  `$HANGAR_REPO/packages/*/examples`.
- Compute references by subprocess (same trick as `eval_lane_c.py`).
- No hardcoded in-tree paths → works as siblings OR submoduled.

**Dev layout:** siblings by default. Add `hangar-evals` as a submodule at
`the-hangar/evals/` only when a single working tree or pinned CI is wanted.

**Target structure (aspirational — built incrementally via the step ladder):**
```
hangar-evals/
  pyproject.toml
  src/hangar/evals/
    __init__.py
    hangar_ref.py     # THE SEAM (Step 2)
    cases.py          # Case/Metric defs (lifted from eval_lane_c.py + expanded)
    drivers/
      base.py         # AgentDriver interface (Step 3)
      claude_sdk.py    # anchor — port of eval_lane_c.run_agent (Step 3)
      opencode.py     # local-model arm (Step 4)
      openhands.py    # second harness (later)
    scoring.py        # numeric scoring (port) + provenance-trace scoring (Step 5)
    trace.py          # parse analysis.db / session graph → tool-use metrics (Step 5)
    serving.py        # local model endpoint mgmt (ollama / mlx_lm.server)
    report.py         # leaderboard / per-capability tables
    run.py            # CLI: run model×harness×task matrix, N seeds (Step 6)
  configs/
    models.yaml       # model registry (tag, quant, endpoint, ctx)
    matrix.yaml       # which cells to run
  tests/
  results/            # gitignored run outputs
  README.md
  notes/llm-eval-plan.md   # this file
```

---

## 6. Architecture: hold scoring constant, vary model × harness × task

Refactor the driver out of `eval_lane_c.py` behind an interface. All drivers
point at the **same omd MCP stdio server**; model serving stays constant via one
**OpenAI-compatible endpoint** (Ollama now; native MLX later).

```
AgentDriver (abstract)
  ├── ClaudeAgentSDKDriver   # exists today → frontier ANCHOR / ceiling
  ├── OpenCodeDriver         # OpenAI-compat endpoint + MCP config (tools: omd_<tool>);
  │                          #   built-ins (write/bash/…) DISABLED via `tools` -> MCP-only,
  │                          #   matching the anchor's disallowed_tools (Step 7)
  └── OpenHandsDriver        # OpenAI-compat endpoint + ~/.openhands/mcp.json
```

Driver contract (minimal):
```python
class AgentDriver(Protocol):
    def run(self, prompt: str, mcp: MCPServerSpec, data_root: Path,
            model: str, max_turns: int) -> AgentResult:
        # returns: final_text, tool_call_trace?, tokens, wall_clock, (cost?)
```

### Two task surfaces
1. **MCP-only track (FIRST — decided).** `eval_lane_c.py` shape: agent gets only
   the omd MCP tools, authors/runs a plan, reports metrics. Tests pure tool-use +
   analysis correctness.
2. **CLI track (later).** Agent gets Bash, drives `oas-cli`/`omd-cli`. Graded by
   `evals.json` assertions + output correctness vs Lane A.

---

## 7. What to measure (don't trust the self-report — read the provenance DB)

| Dimension | Metric | Source |
|---|---|---|
| **Analysis correctness** (primary) | per-metric pass within rtol/atol | self-report JSON ↔ Lane A (coded) |
| **Tool-use validity** | valid-call rate, schema-error rate, hallucinated-tool rate | provenance trace |
| **Workflow adherence** | followed required order (start_session→…→export) | session graph |
| **Error recovery** | recovered after a tool error (error envelope → corrected retry) | trace |
| **Robustness traps** | validated before optimizing; caught even-num_y / unknown-DV / typo'd key | trace + result |
| **Efficiency** | turns, tokens, wall-clock, tok/s | harness telemetry |
| **Completion** | produced a parseable report at all | harness |

- Repeat each cell **3–5 seeds** (local models are stochastic); report
  **pass-rate**, not a single run.
- Numeric tolerances are the backbone; an optional frontier LLM-judge can grade
  open-ended "did it interpret correctly," kept secondary.

---

## 8. Task suite (all from existing ground truth in the-hangar)

- **T0 Smoke/floor** — paraboloid analysis + optimization.
- **T1 Single-tool correctness** — OAS aero α=5° + drag polar + twist-opt; OCP
  caravan basic mission; pyCycle turbojet design point; evt mission-energy + MTOW
  sizing (**4076.0876 kg / 37-iter golden**, rtol 1e-5).
- **T2 Workflow adherence** — same tasks, graded on provenance order.
- **T3 Robustness/recovery** — documented squawks: even `num_y`, unknown DV name
  (OAS silent-ignore trap), unknown evt config key (typo recovery), fake
  1–2-iter convergence. Score validation + recovery.
- **T4 Multi-tool composition** — `ocp_oas_coupled` (already a case), hard ceiling.

Existing cases in `eval_lane_c.py` to lift: `paraboloid`, `ocp_caravan_basic`,
`ocp_oas_coupled`, `evt_open_sizing`.

---

## 9. Target model set — 48 GB unified memory

Budget ~10–12 GB for macOS + harness → plan for **~32–36 GB weights+KV**. Sweet
spot: **24–32B dense @ 4-bit** or a **30B-A3B MoE**. **Tool-calling reliability
is the gate.**

- **Smoke:** Qwen3-8B (~5 GB) — the T0 floor model (pulled). ✅ Note: **not**
  MLX-accelerated (see below) — it runs on the llama.cpp Metal path. Fine as the
  floor; just slower than the MLX picks.
- **Tier-1 (MLX, primary):** a **Qwen3.5 / Qwen3.6 coder** tag — these ARE
  MLX-accelerated on Ollama (Qwen3.5-35B-A3B class fits at 4-bit). Qwen3.6 is the
  newer pick with stronger agentic coding. This **replaces** the earlier
  Qwen3-Coder-30B pick for the result-bearing model: Qwen3-Coder-30B GGUF runs
  but falls back to llama.cpp Metal, NOT MLX.
- **Cross-family (MLX):** **Gemma 4** — also MLX-accelerated; breadth guard
  against tuning to one family.
- **Stretch:** an 80B-A3B-class tag (e.g. Qwen3-Coder-Next) — upper bound of the
  local arm; confirm exact ollama tag/quant + that it fits 48 GB before pulling.
- **Non-MLX breadth (llama.cpp Metal):** Devstral-Small-24B, a local-feasible
  GLM — usable but unaccelerated; keep secondary.
  (GLM-5.1=754B / Kimi / DeepSeek / MiniMax frontier MoEs are too big for 48 GB.)
- **Anchor:** frontier hosted Claude via the existing Agent-SDK driver.

### MLX-acceleration reality (verified 2026-06-24, via direct search + claude.ai)
Ollama's MLX backend (0.19+, Mar 2026; ~2× tok/s, needs **32 GB+** unified mem —
48 GB ✓) accelerates **only specific architectures: Qwen3.5, Qwen3.6, Gemma 4**.
Everything else (Qwen3, Qwen3-Coder, Llama 4, Mistral, Phi) silently falls back
to **llama.cpp Metal**. So for an MLX-accelerated primary, pick a Qwen3.5/3.6
tag, not the older Qwen3-Coder-30B. Sources: ollama.com/blog/mlx,
ollama.com/library/{qwen3.5,qwen3.6,qwen3-coder}, gingter.org/2026/04/23.

**Native-MLX serving (the later serving variable):** the stock
`mlx_lm.server` OpenAI tool-call support is a **stub** (enabling PR unmerged) —
do NOT wire the harness to it for tool calling. Use **`mlx-openai-server`**,
which has per-model `--tool-call-parser` flags (`qwen3_coder`, `qwen3_next`, …).

⚠️ Knowledge cutoff Jan 2026; it's now mid-2026. Trust the *families*; confirm
exact current tags before pulling.

**Refresh-the-picks search prompt:**
> "As of mid-2026, list the best open-weight LLMs for *agentic coding with
> reliable function/tool calling* that fit ~32 GB weights+KV on a 48 GB
> Apple-Silicon Mac. For each: param count, MoE vs dense, recommended quant +
> approx VRAM, native context, SWE-bench Verified, a tool-calling benchmark
> (Terminal-Bench, BFCL), license, and whether OpenHands/OpenCode officially
> recommend it. Prefer Qwen3-Coder, Devstral, GLM-4.x, Qwen2.5-Coder, gpt-oss."

---

## 10. Open questions / decisions pending

- [ ] **Dev venv:** own venv for hangar-evals (with the-hangar installed in) vs
      sharing the-hangar's `.venv` (current). Both work with the seam.
- [ ] Serving runtime of record: Ollama (now) → native MLX (later) as a serving
      variable in the matrix. **Resolved facts (§9):** Ollama MLX accelerates only
      Qwen3.5/3.6/Gemma 4; native-MLX tool calling needs `mlx-openai-server`, not
      `mlx_lm.server`. Open part: which exact tags + when to add the MLX arm.
- [x] OpenCode `--format json` event schema — **RESOLVED** (live spike,
      2026-06-24). JSONL, one event/line: `step_start`, `tool_use`
      (`part.tool`, `part.state.status`, `part.state.output`=omd result/error
      envelope), `text` (`part.text` = the report), `step_finish` (tokens,
      cost). One json run yields BOTH the report and the tool trace — the
      earlier "only step_start/step_finish" finding was an early-exit artifact.
      Also found: `opencode run` BLOCKS on open stdin headless — must close it.
- [x] OpenCode per-run tool-call trace — **RESOLVED**: `tool_use` events give a
      clean trace. omd schema errors arrive as tool OUTPUT (status stays
      "completed"), so classify on the error envelope, not the status.
      (OpenHands trace exposure still TBD when that arm lands.)
- [~] **Contamination model — reframed (2026-06-26).** The eval's real threat is
      *test-set contamination*, NOT tool fairness: **different tool surfaces
      across arms are fine** (that's the "grade the whole model+harness system"
      premise) — what must be prevented is the agent reaching **privileged
      context**: the-hangar source, the eval scoring code, the Lane-A reference
      answers, or hangar/omd-specific **skills/memory**. Implication: the right
      end state is a **filesystem sandbox** (reachability control) where rich
      Bash/Read/Write are *allowed* — there's simply nothing privileged to find —
      **plus** a small harness-level guard for vectors a filesystem sandbox can't
      stop. **Inspect AI rejected** for this (its standardized-scaffold model
      drops the "harness under test" premise; we keep native OpenCode/SDK).
      What a filesystem sandbox does NOT close (must stay harness-level):
        * **Memory / CLAUDE.md** — harness-*injected* into the system prompt, not
          file-read. Fixed on the anchor via `setting_sources=[]` (Step 10).
        * **Skills** — harness-injected; `Skill` tool blocklisted (Step 10).
        * **Network** — the LLM API needs it, so WebSearch/WebFetch are denied at
          the *tool* level, not the sandbox boundary.
      Architecture constraint for the sandbox step: **omd MCP server must run
      OUTSIDE the agent sandbox** (it needs `HANGAR_REPO`), exposed only as the
      stdio/socket channel — else the agent reads the-hangar through omd's own
      files. Also audit: `ListMcpResourcesTool`/`ReadMcpResourceTool` are benign
      *only while* omd exposes no reference answers as MCP resources.
- [~] **Filesystem sandbox (→ Step 14, spec §4b amended — container-per-run
      DECIDED 2026-07-02).** Give each run a clean scratch
      workspace (no repo/answers reachable) and **relax** the interim filesystem
      blocklist in `_DISALLOWED_TOOLS` (`_INTERIM_FILESYSTEM_TOOLS`) so the agent
      may use Bash/Read/Write to help drive omd. Until then those tools stay
      blocked because the anchor's cwd *is* the-hangar repo (the answers).
- [x] CLI-track sandboxing (Bash allowed) — **RESOLVED 2026-07-02:
      container-per-run on colima (Step 14)**; OpenHands (Step 16) shares it.
- [ ] Quantization policy: pin one quant per model (Q4_K_M / MLX-4bit) for fair
      comparison; record in `models.yaml`.
- [~] Seeds/temperature per cell. **Multi-seed landed (Step 9):** default **3**
      seeds/cell, reduced to a pass-rate `CellSummary` (`aggregate.py`).
      **Evidence it's essential:** two identical paraboloid × opencode/qwen3:8b
      runs gave 1 turn / 0 tool calls vs 13 turns / 46 tool calls. **Still open:**
      the random seed is NOT yet reproducible (Step 9 uses plain repetition +
      natural stochasticity); the *matrix* is reproducible via the `RunConfig`
      manifest (`--config <case>_<stamp>_config.json`). Deterministic per-seed
      sampling (thread an Ollama seed) is a deferred follow-up.

---

## 12. Known bugs / follow-ups (found during the first MLX live runs, 2026-06-24/25)

The first real multi-model runs (qwen3:8b floor, gemma4:26b-mlx, qwen3.6:35b-mlx
on paraboloid T0) surfaced these. None block the harness; record before fixing.

- [x] **`validated_before_execute` was permanently `False` (dead metric).**
      RESOLVED (Step 8). `trace.py:read_provenance` derived it by looking for an
      activity_type literally named `"validate"` preceding the first
      `"execute"`. But omd **never records a `validate` activity** —
      `validate_plan` writes no activity row at all. Verified: the only
      activity_types omd actually writes are **`decide`, `execute`, `replan`,
      `assess`** (`grep activity_type= packages/omd/src/hangar/omd/*.py`). So the
      lookup was always `None` and the metric could only ever report `False`,
      even when the agent validated heavily — qwen3.6 called `validate_plan`
      **8×** and still scored `False`. **Fix:** moved off the provenance DB onto
      the **harness tool-call trace** — now `ToolUseMetrics.validated_before_execute`
      = a `validate_plan` call before the first execute tool
      (`run_plan`/`run_polar`/`run_study`), the only place those calls appear.

- [x] **Provenance metric vocabulary is unverified against the real schema.**
      RESOLVED (Step 8). Root cause of the bug above. The real, code-confirmed
      activity set is `decide/execute/replan/assess`; it is now pinned as
      `trace.OMD_ACTIVITY_TYPES` and guarded by a test asserting the fixture
      stays within it (so a future omd rename fails loudly, not silently). The
      test fixture previously used the *fake* `draft`/`validate` names — exactly
      why the dead metric slipped through — and now uses the real vocabulary.

- [ ] **Upstream (the-hangar) note, not ours:** `omd/db.py:215` docstring lists
      the activity vocabulary as "draft, revise, validate, execute, assess,
      replan" — stale vs the code, which writes `decide` (never `draft`,
      `revise`, or `validate`). Flag to the-hangar; do not fix from this repo.

- [ ] **Single-seed results are not trustworthy (already-known, reconfirmed).**
      qwen3:8b gave 1-turn vs 13-turn runs on identical cells; gemma4 stalled at
      4 turns once. The first MLX leaderboard (qwen3.6 PASS-analysis/FAIL-opt,
      gemma4 no-report) is **indicative only** until multi-seed lands (§10).

- [x] **Effect-based grading — PROMOTED to Step 11, spec written (§4c,
      2026-07-02; was ranked #2 after the sandbox).** The seed-0 finding: a
      qwen3.6 run **computed the correct optimum** (x=6.667, f=−27.33) but emitted
      prose instead of the required ```json block → scored NO REPORT on format
      alone. A *correct analysis got zero credit*. Per the §12 prior-art survey
      (SWE-bench/τ-bench/BikeBench all grade final state), the primary grader
      should read **what the agent actually ran** — the omd run results /
      provenance DB (`get_results`, the `analysis.db`) — and compare to the Lane-A
      reference, with the fenced-JSON demoted to a *secondary* signal. **Scope
      sketch:** add an oracle that pulls the agent's final/pinned omd run outputs
      from `data_root/analysis.db` (already captured per run) and scores those
      against `compute_refs`; keep `extract_report` as a secondary "did it also
      self-report correctly" metric; reconcile the two in `run_cell`. **Oracle
      self-test (ABC checklist):** a known-good run passes, a perturbed run fails,
      and a no-op / "did nothing" run cannot pass (τ-bench's pass-by-doing-nothing
      trap). Touches `scoring.py` + `run.py`; harness-neutral, benefits every arm.
      This is the smaller, self-contained alternative to the sandbox if priorities
      shift. **Spec it before building.**
- [ ] **Live run progress / watchability (Step 17; was 13 pre-reorder).**
      Today a run is BLIND
      mid-seed: `OpenCodeDriver` uses a blocking `subprocess.run(capture_output)`,
      so nothing prints until a seed finishes — and a qwen3.6 seed is ~13 min. We
      want to watch seeds complete + the in-flight seed's turns / tool-calls /
      cumulative tokens. **Approach:** stream instead of buffer — `Popen` +
      read `--format json` JSONL line-by-line (we already parse those events),
      teeing to `opencode_events.jsonl` as today; update a live status line from
      `step_finish` token counts + `tool_use` events. Expose it as a
      **driver-agnostic progress callback** (`on_event(seed, turns, tool_calls,
      tokens)`) so the Claude SDK driver (async message stream) reports through
      the same interface. A coarse `tqdm` bar over the seed/cell matrix can sit
      on top. **No-overhead requirements:** (1) no model/runtime cost — it's just
      parsing a stream we already capture; (2) **TTY-gated** — auto-silence when
      stdout isn't a terminal (background runs pipe to a log; a progress bar
      there is noise — which is exactly the `run_q36.log` case today); (3) keep
      `tqdm` an OPTIONAL extra (base stays dep-free) or use a stdlib
      carriage-return status line; throttle redraws. Verify it doesn't change the
      hang-avoidance contract (`stdin=DEVNULL`).

      **Also part of Step 17 — results viewing + a USER-DRIVEN run handoff.**
      Two gaps the agent-run-it model hides:
        * **View results, not raw JSON.** Today the output is three
          `results/*.json[l]` files the user has to read by hand. Need an
          ergonomic way to see a finished run: a small viewer/`report.py` (cell
          summary + per-seed pass/fail + a pointer into each seed's
          `opencode_events.jsonl` trace), or at minimum documented one-liners.
          Pairs with the (later) leaderboard.
        * **Hand the run to the user, with step-by-step instructions.** So far
          the *agent* launches every run, so the user has never experienced the
          live UX firsthand. Step 17 should produce a short runbook (start a run,
          watch progress, open the results, find a seed's trace) and have the
          **user drive a run themselves** and give feedback on what's missing.
          The agent-run path masks exactly the rough edges (blind waits, raw
          JSON, finding traces) this step exists to fix — a human-in-the-loop
          pass is the acceptance test for the progress/viewer work.

- [ ] **Study prior-art OSS eval tooling/benchmarks and adopt what fits.**
      Before hardening our scoring, completion, and reliability metrics, see how
      the best agent/LLM benchmarks solve the exact problems we just hit.
      *(Deep-research pass done 2026-06-26 — findings below. Versions/IDs churn
      fast; verify before formal citation.)*

      **Two grading philosophies — pick effect-based.** Trajectory/AST grading
      (BFCL v1/v2, agentevals strict-match, DeepEval ToolCorrectness) matches the
      *sequence* of tool calls — deterministic but brittle: many valid tool paths
      reach the same correct artifact, so it yields false negatives. Effect/
      final-state grading (SWE-bench, τ-bench, AppWorld, Terminal-Bench,
      BikeBench, SimulCost) compares the *final artifact/world-state* to a
      reference. **Effect-based is the correct primary grader for parity** — this
      is the direct fix for seed-0's "correct-but-prose got 0 credit": grade the
      **omd run results / provenance DB** (what the agent actually ran), keep the
      fenced-JSON self-report as a *secondary* signal only.

      **How the references handle our four criteria:**
        * **SWE-bench / Verified** — execution-based (apply patch in Docker, run
          hidden FAIL_TO_PASS + PASS_TO_PASS tests); pass@1 (some pass@3);
          harness-agnostic, *grades the whole model+scaffold and requires scaffold
          disclosure*; "Verified" = 500 tasks human-filtered by 93 devs.
        * **τ-bench / tau2-bench (Sierra)** — final DB/world-state vs hand-
          annotated goal state; **introduced pass^k** (all k trials pass, ≈ p^k).
          Key lesson: GPT-4o pass^8 ~25% in retail vs much higher pass^1 —
          *reliability collapses under repetition*. This is our 0/3 finding
          formalized. Known flaw to guard against: **"pass by doing nothing"**
          when a task doesn't change state.
        * **Terminal-Bench (1.0/2.0)** — per-task container + human reference
          solution + verification tests; runs **≥5 repeats**, variance-aware; 2.0
          *standardizes the harness* to isolate harness-vs-model effects (the
          harness materially moves scores). Feeds §10 CLI sandboxing + OpenHands.
        * **BFCL (v1–v4)** — v1/v2 AST match, v3 state-based, v4 holistic agentic;
          separate ast_checker / executable_checker. Reference for grading
          `trace.py` tool-use more rigorously than valid/schema-error counts —
          but as a *secondary* check, not the parity grader.
        * **BikeBench** — closest published engineering analog: simulator/
          analytical evaluators over ~10 objectives + ~40 constraints, grades the
          **final design artifact** not a trajectory. Finding: LLMs underperform
          optimization/hybrid methods. Closest in spirit to our Lane-A oracle.
        * Also noted: **AppWorld** (gold standard for reproducible state isolation
          — versioned DB, exact resets, hash-diffing, catches collateral edits);
          **MLE-bench** (pass@1 *and* pass@k; o1-preview 16.9%→34.1% at pass@8).

      **Directly-analogous physics-solver benchmarks (2025–26) — track these:**
        * **PETScAgent-Bench** — near-exact architectural analog: A2A between
          evaluator and model-under-test, MCP for compile/execute tools.
        * **PDEAgent-Bench** — PDE→solver gen; case-level pass rate with
          sub-metrics (executability, numerical accuracy, efficiency, quality).
        * **SimulCost** — success rate + **cost-efficiency** on solver param tuning.
        * **PhysCodeBench / AInsteinBench** — executability + physical accuracy
          (residuals, conservation/invariant checks). Caveat we must heed:
          *"acceptable conservation violation is highly scenario-dependent" — set
          per-quantity tolerances, never one global epsilon.*

      **Reliability metric guidance:** report **pass@1, pass@k, AND pass^k**
      together. pass^k (all k runs pass) is the production-relevant one — an agent
      that hits parity 1-in-3 is not usable. Treat solver non-determinism
      (threading, BLAS, RNG) explicitly: fix seeds where possible; widen tolerance
      only with physical justification, never to mask flakiness.

      **Recommended stack (from the research):**
        1. **Inspect AI (UK AISI)** as harness/grader backbone — harness-agnostic
           Task/Solver/Scorer, native **MCP tool support**, sandboxed Docker/K8s,
           and (the key fit) a custom `@scorer` can **read the sandbox after a run**
           (`await sandbox().read_file()/.exec()`) to do deterministic physics-
           tolerance grading. `epochs=Epochs(k, reducers)` computes pass_at(k)/
           at_least(k) natively; a community **pass^k reducer** exists and is
           trivial to add. MIT; used by METR/Apollo/Anthropic/DeepMind. **This is
           the recommended path to evaluate vs. continuing to hand-roll `run.py`.**
        2. A standalone **`is_parity(candidate, reference, tol)`** predicate module
           with **per-quantity absolute + relative tolerances** — importable into
           an Inspect/MLflow/OpenAI-style grader so it's portable across harnesses.
        3. **Opik or Phoenix** (OSS, self-host) for trace observability of *failed*
           parities — debugging aid, NOT the authoritative grader.
        4. DeepEval / agentevals for *secondary* tool-call/trajectory checks in CI.
      Other frameworks weighed: MLflow GenAI evaluate (Trace-based `@scorer`, good
      if we want experiment tracking too); Promptfoo (CI/YAML, `--repeat N`);
      HELM (leaderboards, not custom execution grading). **Avoid** building on the
      hosted OpenAI Evals/Graders platform — being deprecated (read-only 2026-10-31,
      shutdown 2026-11-30); copy the open-source grader *pattern* only.

      **ABC checklist** (arXiv:2507.02825, "Best Practices for Building Rigorous
      Agentic Benchmarks") — adopt as our acceptance criteria:
        * **Validate the oracle:** a known-good run passes; a perturbed run fails;
          "doing nothing" / returning the initial state *cannot* pass.
        * **Reproducible episodes:** pin solver versions in a sandbox; AppWorld-
          style state reset/isolation between cases.
        * **Per-quantity absolute + relative tolerances** with physical
          justification — not one global epsilon.
        * **Pin a reference harness** but keep a stable task API and require
          scaffold disclosure for external submissions.
        * ABC found flaws in most popular benchmarks that mis-estimate performance
          by up to 100% relative — task-validity + outcome-validity checks matter.

      **Net design implications for us:** (1) flip the primary grader to side
      effects (omd results/provenance), demote the fenced-JSON; (2) add pass^k to
      the aggregate alongside the mean; (3) build/borrow `is_parity()` with
      per-quantity tolerances; (4) add an oracle self-test (good passes, perturbed
      fails, no-op fails); (5) **seriously evaluate adopting Inspect AI** rather
      than growing `run.py` further — it already provides MCP + sandbox-reading
      scorers + native pass@k. Open question for a future step: migrate to Inspect
      AI now (before more harness code accretes) vs. after the anchor lands (§10).
      **RESOLVED 2026-07-02: stay hand-rolled; adopt the patterns (see §12b).**

---

## 12b. External review findings (2026-07-02) — gaps vs prior-art practice

A full-repo review (code + plan) against τ-bench / SWE-bench / BikeBench /
Terminal-Bench / BFCL practice. The two big items already moved the ladder
(grader flip → Step 11 §4c; container sandbox → Step 14 §4b). The rest, with
dispositions:

- [ ] **Harness × model are confounded in the matrix.** The anchor is (Claude
      SDK × Opus) and the local arm is (OpenCode × Qwen) — "% of anchor" cannot
      decompose model gap vs harness gap, and quietly credits the SDK's
      scaffolding quality to the model. Terminal-Bench 2.0 standardized its
      harness for exactly this reason; our premise (harness = treatment
      variable) is fine, but then the matrix needs isolating cells. Fix
      (→ Step 16): run the SAME local model through OpenCode AND OpenHands, and
      add a **Claude-via-OpenCode** cell (OpenCode has a native Anthropic
      provider) to split "frontier model ceiling" from "Claude-SDK harness
      ceiling."
- [ ] **pass^k missing from the aggregate** (→ Step 12, spec §4d). `aggregate.py` reports
      mean pass-rate only. Report pass@1, pass@k AND pass^k together, per the
      §12 survey's own recommendation (τ-bench: GPT-4o retail pass^1 much
      higher than pass^8 ≈ 25% — reliability is the production metric).
- [ ] **The run manifest doesn't pin the environment** (→ Step 12, spec §4d). `RunConfig`
      records the matrix but not: the-hangar git SHA, omd version, OpenCode /
      Ollama versions, model quant, or the anchor model — the live anchor run
      recorded `claude-opus-4-8` arriving as the SDK **default**, which drifts
      silently when the SDK updates. Pin the anchor model explicitly in
      `configs/`; write versions into the manifest. SWE-bench-style scaffold
      disclosure starts with pinning our own environment.
- [ ] **Token counts are parsed then dropped** (→ Step 12, spec §4d; live display folds into 17).
      OpenCode `step_finish` carries tokens; `AgentResult`/telemetry never
      surfaces them, though §7 lists tokens as an efficiency metric.
- [ ] **The prompt gives away the workflow.** `cases.PREAMBLE` spells out the
      required tool order (and the omd server's MCP `instructions` repeat it),
      so "workflow adherence" currently measures instruction-following, not
      discovery. Fine for T0; T2 (→ Step 15) should add a **no-hint variant**
      to measure discoverability, and each driver should record whether its
      harness surfaces MCP server `instructions` to the model at all — a
      hidden harness variable.
- [x] **Per-run workspace lives inside this repo** (`results/run_data/`, and
      OpenCode's cwd is `--dir <data_root>`) — folded into the Step-14 threat
      model as item (e); the container mount closes it structurally.
- [~] **`recovered_errors` over-counts** — a later successful call to the same
      tool may serve a different purpose than the failed one. Accepted as a
      coarse SECONDARY signal; don't headline it in reports.
- [x] **Inspect AI migration — RESOLVED: stay hand-rolled.** Its standardized-
      scaffold model conflicts with the "harness under test" premise (already
      rejected in §10 for driving agents); what remains attractive (pass^k
      reducers, oracle self-tests, tolerance predicates) we adopt as
      *patterns*, not a dependency. Revisit only if the matrix outgrows a
      serial overnight run — local Ollama serializes model execution anyway,
      so parallel cells buy little today.
- **Framing note (for the eventual writeup).** With <10 tasks this is a
      **diagnostic suite / case-study eval**, not a leaderboard benchmark —
      frame it that way alongside the AIAA case studies. The novel, citeable
      core is **T3**: trap/recovery tasks over real documented solver squawks
      (silent-ignore DVs, even `num_y`, typo'd keys, fake convergence) — no
      τ-bench/BikeBench analog tests failure-mode vigilance, and the closest
      physics-solver benchmarks (§12) don't either. The `friction` field
      doubles as a tool-ergonomics instrument for the-hangar itself — worth
      reporting as its own output, not just eval overhead.

---

## 11. Reference paths IN THE-HANGAR (resolved via `HANGAR_REPO`)

These live in the-hangar, reached through the seam — NOT copied into this repo:
- Scoring engine to generalize: `packages/omd/examples/agent_eval/eval_lane_c.py`
- Lane A refs + tolerances:
  `packages/{oas,ocp,pyc,evt}/examples/*/{lane_a,shared.py}`,
  `packages/omd/examples/*/`
- CLI evals: `packages/<pkg>/skills/<tool>-cli-guide/evals/evals.json`
- Failure modes: `.claude/CLAUDE.md` (Known OAS failure modes),
  `skills/oas-known-squawks`
- Provenance: `packages/omd/src/hangar/omd/db.py`, `export_session_graph` tool
- `HANGAR_REPO` convention: `.claude/CLAUDE.md` Deployment section
