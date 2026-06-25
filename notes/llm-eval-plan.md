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
| **9** | **Multi-seed** — 3–5 seeds/cell, report pass-rate not single runs (§10) | `run.py` seed loop + aggregation | ⏭ **NEXT** |
| 10 | **Wire the Claude anchor live** — the "% of anchor" ceiling | live anchor run + leaderboard cell | todo |
| 11 | **Suite expansion** (T1–T4) + **OpenHands arm** | new cases, `drivers/openhands.py` | todo |

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

## 4. Step 2 spec — the seam (NEXT, for review)

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
- [ ] CLI-track sandboxing (Bash allowed) — container per run? OpenHands is
      container-based; OpenCode is not.
- [ ] Quantization policy: pin one quant per model (Q4_K_M / MLX-4bit) for fair
      comparison; record in `models.yaml`.
- [ ] Seeds/temperature per cell (default 3–5 @ low temp). **Evidence it's
      essential:** two identical paraboloid × opencode/qwen3:8b runs gave 1 turn /
      0 tool calls vs 13 turns / 46 tool calls — a single run is meaningless.

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
