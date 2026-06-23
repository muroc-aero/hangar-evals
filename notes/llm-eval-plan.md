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

Each step is **one reviewable commit** in this repo. Steps 2–6 each get their own
full spec (the six points above) when we reach them.

| # | Step | Reviewable artifact | Status |
|---|------|--------------------|--------|
| **1** | **Repo skeleton** — installable, no logic | `pyproject.toml`, `src/hangar/evals/__init__.py`, `README.md`, `.gitignore` | ✅ **DONE** (see §3) |
| **2** | **The seam** — resolve `HANGAR_REPO`, compute a Lane A reference | `src/hangar/evals/hangar_ref.py` + `tests/` proving `paraboloid → f_xy == 39.0` | ⏭ **NEXT** (spec in §4) |
| 3 | **Driver interface + Claude anchor** — port `eval_lane_c.py`'s agent behind `AgentDriver` | `drivers/base.py`, `drivers/claude_sdk.py` + test | todo |
| 4 | **OpenCode driver** — the local-model arm | `drivers/opencode.py` + the config it writes + test | todo |
| 5 | **Scoring + trace** — numeric scoring (port) + provenance-DB tool-use metrics | `scoring.py`, `trace.py` + test | todo |
| 6 | **Runner + one cell** — run `paraboloid × {claude, opencode} × T0` | `run.py` + a results file | todo |

We can re-order or split any of these. After Step 6 the harness exists end-to-end
on one case; suite expansion (T1–T4, CLI track) and the full model×harness×seed
matrix follow.

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
  ├── OpenCodeDriver         # OpenAI-compat endpoint + MCP config (tools: omd_<tool>)
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

- **Smoke:** Qwen3-8B (~5 GB) — the T0 floor model (pulled). ✅
- **Tier-1:** Qwen3-Coder-30B (A3B MoE, ~18 GB) — primary result-bearing model.
- **Stretch:** Qwen3-Coder-Next (80B-A3B, ~46 GB-class; ~70.6% SWE-bench
  Verified on a 46 GB machine) — upper bound of the local arm. Confirm exact
  ollama tag/quant before pulling.
- **Cross-family:** Devstral-Small-24B (tuned for OpenHands), Gemma 4, a
  local-feasible GLM — breadth guard against tuning to one family.
  (GLM-5.1=754B / Kimi / DeepSeek / MiniMax frontier MoEs are too big for 48 GB.)
- **Anchor:** frontier hosted Claude via the existing Agent-SDK driver.

⚠️ Knowledge cutoff Jan 2026; it's now mid-2026. Trust the *families*; confirm
exact current tags before pulling. Serve via MLX for best Apple-Silicon
throughput; Ollama easier and now has workable tool-calling.

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
      variable in the matrix.
- [ ] OpenCode `--format json` event schema — where do tool-call/text parts
      appear? Needed for programmatic scoring (Step 5). Until resolved, score
      tool-use from the omd provenance DB.
- [ ] How do OpenHands/OpenCode expose a per-run tool-call trace? If thin, lean
      harder on the omd provenance DB.
- [ ] CLI-track sandboxing (Bash allowed) — container per run? OpenHands is
      container-based; OpenCode is not.
- [ ] Quantization policy: pin one quant per model (Q4_K_M / MLX-4bit) for fair
      comparison; record in `models.yaml`.
- [ ] Seeds/temperature per cell (default 3–5 @ low temp).

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
