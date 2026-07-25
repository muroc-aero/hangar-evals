"""have-agent bridge — run Lane C eval cells as jobs in a governed study.

have-agent (sibling repo) is a deterministic study substrate: a StudyRequest
YAML decomposes into ANALYSIS+CHECK job DAGs in SQLite, pull workers execute
them, and policy gates + a REPORT briefing sit on top. Its worker takes a
pluggable executor: ``have worker run --executor hangar.evals.have_bridge:make_worker``
imports this module and calls ``make_worker(args)`` for an
``(executor, check_suite)`` pair (have-agent DECISIONS #32).

The mapping: one have-agent case = one eval CELL (case × harness × model),
run for N seeds through ``run_matrix`` — the same entry point the CLI uses.
Each job therefore writes the standard results triple
(``<case>_<stamp>.jsonl`` + ``_config.json`` + ``_summary.json``) into the
normal results dir, so ``paper/make_tables.py`` picks study-produced rows up
exactly like manual runs; have-agent never becomes a second source of truth
for scores. Stamps are prefixed with a UTC timestamp because make_tables
keeps the lexicographically-last summary per cell — a non-timestamp prefix
would permanently shadow later manual runs.

Grading vs execution stay distinct: an agent that ran cleanly but failed the
eval is a *successful* job with a failing CHECK verdict; only harness crashes
(error rows) and malformed payloads fail the ANALYSIS job itself.

This module never imports have_agent — ``BridgeResult`` duck-types
have_agent.executor.ExecResult (the worker only reads attributes), so
hangar-evals keeps zero dependency on the substrate. The worker environment
has have_agent installed by definition (it IS the CLI being run).

StudyRequest overrides accepted per case (see ``examples/lane_c_eval.yaml``):
``case`` (required, a CASES key), ``harness`` (required, a HARNESSES key),
``model``, ``seeds``, ``max_turns``, ``timeout_s``, ``omd_transport``,
``sandbox``. Executor-level defaults for the optional ones come from
``--executor-opt KEY=VALUE`` flags on the worker.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hangar.evals.cases import CASES
from hangar.evals.run import HARNESSES, RunConfig, run_matrix

# per-case override keys we accept; anything else is a typo -> permanent fail
_CELL_KEYS = frozenset(
    {"case", "harness", "model", "seeds", "max_turns", "timeout_s",
     "omd_transport", "sandbox"}
)
_LEVEL_ORDER = {"pass": 0, "warn": 1, "fail": 2, "error": 3}


@dataclass
class BridgeResult:
    """Duck-types have_agent.executor.ExecResult (attribute-compatible)."""

    ok: bool
    run_ref: str | None = None
    artifacts: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    permanent: bool = False


def _job_suffix(job_id: str) -> str:
    """Filesystem-safe tail of the job id, for unique result stamps."""
    return re.sub(r"[^A-Za-z0-9]", "", job_id)[-8:] or "job"


class LaneCEvalExecutor:
    """have-agent Executor: one ANALYSIS job = one eval cell, N seeds.

    Constructor args are the executor-level defaults (from --executor-opt);
    per-case StudyRequest overrides win.
    """

    def __init__(
        self,
        results_dir: str = "results",
        *,
        seeds: int = 1,
        model: str | None = None,
        max_turns: int | None = None,
        timeout_s: float | None = None,
        omd_transport: str = "stdio",
        sandbox: str = "none",
    ):
        self.results_dir = results_dir
        self.seeds = seeds
        self.model = model
        self.max_turns = max_turns
        self.timeout_s = timeout_s
        self.omd_transport = omd_transport
        self.sandbox = sandbox

    def _config(self, ov: dict[str, Any]) -> RunConfig:
        unknown = set(ov) - _CELL_KEYS
        if unknown:
            raise ValueError(
                f"unknown override key(s) {sorted(unknown)}; "
                f"choose from {sorted(_CELL_KEYS)}"
            )
        for key in ("case", "harness"):
            if key not in ov:
                raise ValueError(f"overrides missing required key {key!r}")
        if ov["case"] not in CASES:
            raise ValueError(
                f"unknown eval case {ov['case']!r}; choose from {list(CASES)}"
            )
        if ov["harness"] not in HARNESSES:
            raise ValueError(
                f"unknown harness {ov['harness']!r}; choose from {list(HARNESSES)}"
            )
        max_turns = ov.get("max_turns", self.max_turns)
        timeout_s = ov.get("timeout_s", self.timeout_s)
        return RunConfig(
            case=ov["case"],
            harnesses=(ov["harness"],),
            model=ov.get("model", self.model),
            seeds=int(ov.get("seeds", self.seeds)),
            max_turns=None if max_turns is None else int(max_turns),
            timeout_s=None if timeout_s is None else float(timeout_s),
            results_dir=self.results_dir,
            omd_transport=ov.get("omd_transport", self.omd_transport),
            sandbox=ov.get("sandbox", self.sandbox),
        )

    def execute(
        self, payload: dict[str, Any], *, study_id: str, job_id: str, attempt: int
    ) -> BridgeResult:
        try:
            config = self._config(payload.get("overrides") or {})
        except (ValueError, TypeError) as exc:
            # malformed payload: identical inputs can never succeed
            return BridgeResult(ok=False, error=str(exc)[:500], permanent=True)

        # Timestamp prefix so make_tables' lexicographic last-wins keeps
        # working; job suffix + attempt make concurrent cells collision-free.
        stamp = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + f"_have_{_job_suffix(job_id)}_a{attempt}"
        )
        summaries = run_matrix(config, stamp)
        s = summaries[0]

        results_dir = Path(config.results_dir).resolve()
        base = f"{config.case}_{stamp}"
        records_path = results_dir / f"{base}.jsonl"
        summary_path = results_dir / f"{base}_summary.json"
        artifacts = [
            str(records_path),
            str(results_dir / f"{base}_config.json"),
            str(summary_path),
        ]

        # Harness crashes land as retryable error rows in the records file
        # (run_matrix Step 18); any of them makes the JOB a failure so the
        # substrate's retry policy applies. A graded FAIL is not an error.
        error_rows = [
            r for r in _read_jsonl(records_path) if r.get("error")
        ]
        if error_rows:
            kinds = sorted({r["error"]["type"] for r in error_rows})
            return BridgeResult(
                ok=False,
                run_ref=str(summary_path),
                artifacts=artifacts,
                error=f"{len(error_rows)}/{s.n_seeds} seed(s) crashed: "
                      + ", ".join(kinds),
            )
        return BridgeResult(
            ok=True,
            run_ref=str(summary_path),
            artifacts=artifacts,
            result={
                "stamp": stamp,
                "case": s.case,
                "harness": s.harness,
                "model": s.model,
                "n_seeds": s.n_seeds,
                "n_completed": s.n_completed,
                "n_passed": s.n_passed,
                "completion_rate": s.completion_rate,
                "pass_rate": s.pass_rate,
                "per_metric_pass": s.per_metric_pass,
            },
        )


class LaneCEvalCheckSuite:
    """have-agent CheckSuite: fold a cell summary into pass/warn/fail.

    Reads the ``_summary.json`` the executor returned as run_ref (files, not
    shared memory — CHECK may run on another worker). Acceptance knobs:
    ``min_pass_rate`` (default 1.0 — every seed must pass for a "pass").
    Verdicts: pass_rate >= min_pass_rate -> pass; any seed passed -> warn;
    ran but none passed -> fail; nothing to grade -> error.
    """

    def run(
        self,
        case_id: str,
        run_ref: str | None,
        acceptance: dict[str, Any],
        *,
        overrides: dict[str, Any] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        if run_ref is None:
            return "error", [
                {"check": "summary_present", "level": "error",
                 "detail": "no upstream run_ref"}
            ]
        path = Path(run_ref)
        if not path.exists():
            return "error", [
                {"check": "summary_present", "level": "error",
                 "detail": f"summary not found: {path}"}
            ]
        s = json.loads(path.read_text())[0]  # one cell per job
        min_pass = float(acceptance.get("min_pass_rate", 1.0))

        completion_level = "pass" if s["n_completed"] > 0 else "fail"
        if s["pass_rate"] >= min_pass:
            pass_level = "pass"
        elif s["n_passed"] > 0:
            pass_level = "warn"
        else:
            pass_level = "fail"
        checks = [
            {"check": "completion", "level": completion_level,
             "detail": f"{s['n_completed']}/{s['n_seeds']} seed(s) produced "
                       "a successful omd run"},
            {"check": "pass_rate", "level": pass_level,
             "detail": f"effect-graded {s['n_passed']}/{s['n_seeds']} "
                       f"({s['pass_rate']:.0%}) vs min_pass_rate {min_pass:.0%}"},
            {"check": "per_metric", "level": pass_level,
             "detail": ", ".join(
                 f"{k} {v}/{s['n_seeds']}"
                 for k, v in (s.get("per_metric_pass") or {}).items()
             ) or "no metrics scored"},
        ]
        level = max((c["level"] for c in checks), key=_LEVEL_ORDER.__getitem__)
        return level, checks


def make_worker(args: Any) -> tuple[LaneCEvalExecutor, LaneCEvalCheckSuite]:
    """Plugin factory for ``have worker run --executor hangar.evals.have_bridge:make_worker``.

    ``--executor-opt KEY=VALUE`` flags (all optional) set executor defaults:
    results_dir, seeds, model, max_turns, timeout_s, omd_transport, sandbox.
    """
    opts = dict(getattr(args, "executor_opts", None) or {})
    known = {"results_dir", "seeds", "model", "max_turns", "timeout_s",
             "omd_transport", "sandbox"}
    unknown = set(opts) - known
    if unknown:
        raise ValueError(
            f"have_bridge: unknown --executor-opt key(s) {sorted(unknown)}; "
            f"choose from {sorted(known)}"
        )
    executor = LaneCEvalExecutor(
        results_dir=opts.get("results_dir", "results"),
        seeds=int(opts.get("seeds", 1)),
        model=opts.get("model"),
        max_turns=int(opts["max_turns"]) if "max_turns" in opts else None,
        timeout_s=float(opts["timeout_s"]) if "timeout_s" in opts else None,
        omd_transport=opts.get("omd_transport", "stdio"),
        sandbox=opts.get("sandbox", "none"),
    )
    return executor, LaneCEvalCheckSuite()


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]
