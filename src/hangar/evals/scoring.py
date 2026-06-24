"""Numeric correctness scoring — self-reported metrics vs Lane A references.

The primary eval signal: parse the agent's fenced-JSON report, then compare
each reported metric against the trusted Lane A value (computed through the
Step-2 ``hangar_ref`` seam) within a per-metric relative tolerance. A faithful
port of ``eval_lane_c.py``'s scoring, refactored to RETURN structured results
instead of printing — presentation is ``report.py``'s job (Step 6).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from hangar.evals.hangar_ref import lane_a_reference


@dataclass(frozen=True)
class Metric:
    """One scored quantity: a key the agent reports, tied to a Lane A value."""

    key: str            # flat key under the report's "metrics" object
    lane_a_module: str  # <example>.lane_a.<module> whose run() holds the reference
    lane_a_key: str     # key in that run()'s return dict
    rtol: float
    required: bool = True  # False: a miss/over-tol is a WARN, not a FAIL


@dataclass(frozen=True)
class MetricScore:
    key: str
    lane_a: float
    agent: float | None
    rel_err: float | None
    verdict: str        # "PASS" | "FAIL" | "WARN"


@dataclass(frozen=True)
class ScoreResult:
    scores: list[MetricScore]
    passed: bool        # all required metrics PASS

    @property
    def n_pass(self) -> int:
        return sum(s.verdict == "PASS" for s in self.scores)


def extract_report(text: str) -> dict:
    """Return the last parseable fenced-JSON object in ``text``.

    The agent ends its run with a ```json block; harnesses sometimes emit more
    than one fenced block, so scan newest-first and take the first that parses.
    """
    blocks = re.findall(r"```(?:json)?\s*\n(\{.*?\})\s*\n```", text, re.DOTALL)
    for raw in reversed(blocks):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            continue
    raise ValueError(f"No parseable JSON report in agent output:\n{text[-2000:]}")


def compute_refs(example: str, metrics: list[Metric]) -> dict[str, dict]:
    """Compute Lane A references for every module the metrics reference.

    One subprocess per module via the seam (each example's ``shared.py``
    collides on ``sys.path``), keyed by module name.
    """
    modules = sorted({m.lane_a_module for m in metrics})
    return {mod: lane_a_reference(example, mod) for mod in modules}


def score_report(
    metrics: list[Metric], report: dict, refs: dict[str, dict]
) -> ScoreResult:
    """Score a parsed report against Lane A references.

    A required metric that is missing/non-numeric or outside ``rtol`` FAILs;
    the same on an optional metric is a WARN. Overall ``passed`` is true iff
    every required metric PASSes.
    """
    reported = report.get("metrics", {}) or {}
    scores: list[MetricScore] = []
    ok = True

    for m in metrics:
        ref = refs[m.lane_a_module][m.lane_a_key]
        got = reported.get(m.key)
        if not isinstance(got, (int, float)) or isinstance(got, bool):
            verdict = "FAIL" if m.required else "WARN"
            ok = ok and not m.required
            scores.append(MetricScore(m.key, ref, None, None, verdict))
            continue
        rel = abs(got - ref) / max(abs(ref), 1e-30)
        passed = rel <= m.rtol
        verdict = "PASS" if passed else ("FAIL" if m.required else "WARN")
        ok = ok and (passed or not m.required)
        scores.append(MetricScore(m.key, ref, float(got), rel, verdict))

    return ScoreResult(scores=scores, passed=ok)
