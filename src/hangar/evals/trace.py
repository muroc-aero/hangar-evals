"""Tool-use and workflow metrics from two complementary sources.

A run leaves two trails, and they capture different failures:

  * The **harness tool-call trace** — every MCP call the agent attempted, valid
    or not. This is the ONLY place schema-rejected calls (even ``num_y``,
    typo'd keys -> ``USER_INPUT_ERROR``) and hallucinated-tool calls show up:
    omd rejects or never sees them, so they never reach the provenance DB.
    Consumed as a harness-neutral ``list[ToolCall]`` (each driver emits these).

  * The omd **provenance DB** (``analysis.db``) — what the server persisted:
    domain entities (plans, decisions) and activities (decide/execute/replan/
    assess, each completed|failed). This is where domain-level success/recovery
    lives. NOTE: ``validate_plan`` records NO activity here, so "validated
    before executing" is read from the tool trace, not this DB.

``parse_tool_trace`` reads the first; ``read_provenance`` reads the second.
Together they answer §7's tool-use / workflow / robustness questions.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

# omd error envelope codes (see the MCP server instructions). A schema/usage
# rejection is USER_INPUT_ERROR; a call to a tool that does not exist never
# reaches omd, so the driver marks it with this synthetic code.
SCHEMA_ERROR_CODE = "USER_INPUT_ERROR"
HALLUCINATED_CODE = "TOOL_NOT_FOUND"

# omd tools that EXECUTE a plan vs VALIDATE it. "Validated before executing" (a
# §7 workflow/robustness signal) is computed from the harness trace, NOT the
# provenance DB: validate_plan records no activity row, so the DB literally
# cannot answer it. The execute set is every tool that runs a plan.
VALIDATE_TOOLS = frozenset({"validate_plan"})
EXECUTE_TOOLS = frozenset({"run_plan", "run_polar", "run_study"})

# The activity_type vocabulary omd ACTUALLY writes — verified against
# packages/omd/src/hangar/omd/*.py (2026-06-25): decide, execute, replan,
# assess. (db.py's docstring still lists a stale draft/revise/validate set; the
# code never writes those.) read_provenance stays vocabulary-agnostic, but this
# pins the ground truth so a future name-specific metric can't silently rot.
OMD_ACTIVITY_TYPES = frozenset({"decide", "execute", "replan", "assess"})


def parse_omd_error_code(content: str | None) -> str | None:
    """Pull ``error.code`` from an omd error-envelope JSON string, if present.

    omd tools return ``{"error": {"code": "USER_INPUT_ERROR", ...}, ...}`` on
    failure. Drivers use this to classify a failed tool result; returns None
    when the content isn't an omd envelope (e.g. a plain harness error string).
    """
    if not content:
        return None
    try:
        obj = json.loads(content)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    err = obj.get("error") if isinstance(obj, dict) else None
    if isinstance(err, dict) and isinstance(err.get("code"), str):
        return err["code"]
    return None


# ---------------------------------------------------------------------------
# Harness tool-call trace
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolCall:
    """One MCP tool call the agent attempted, as seen by the harness."""

    tool: str
    ok: bool
    error_code: str | None = None  # e.g. USER_INPUT_ERROR, TOOL_NOT_FOUND, None


@dataclass(frozen=True)
class ToolUseMetrics:
    total_calls: int
    valid_calls: int
    schema_errors: int
    hallucinated_calls: int
    recovered_errors: int          # failed calls later retried OK on the same tool
    failed_calls: int
    # Workflow-adherence signal read from the trace (NOT the provenance DB):
    # a validate_plan call precedes the first execute. None when nothing was
    # executed (the order question doesn't arise).
    validated_before_execute: bool | None

    @property
    def valid_call_rate(self) -> float:
        return self.valid_calls / self.total_calls if self.total_calls else 0.0

    @property
    def schema_error_rate(self) -> float:
        return self.schema_errors / self.total_calls if self.total_calls else 0.0

    @property
    def hallucinated_rate(self) -> float:
        return self.hallucinated_calls / self.total_calls if self.total_calls else 0.0

    @property
    def recovery_rate(self) -> float:
        return self.recovered_errors / self.failed_calls if self.failed_calls else 0.0


def parse_tool_trace(calls: list[ToolCall]) -> ToolUseMetrics:
    """Compute MCP-boundary metrics from a harness tool-call trace.

    Recovery = a failed call followed *later* in the trace by a successful call
    to the same tool (the agent read the error envelope and corrected itself).

    ``validated_before_execute`` = a ``validate_plan`` call (attempt; pass or
    fail) appears before the first execute tool. Read here, not from the
    provenance DB, because ``validate_plan`` leaves no activity row there.
    """
    valid = sum(c.ok for c in calls)
    schema = sum(c.error_code == SCHEMA_ERROR_CODE for c in calls)
    hallucinated = sum(c.error_code == HALLUCINATED_CODE for c in calls)
    failed = sum(not c.ok for c in calls)

    recovered = 0
    for i, c in enumerate(calls):
        if c.ok:
            continue
        if any(later.ok and later.tool == c.tool for later in calls[i + 1:]):
            recovered += 1

    exec_idx = next((i for i, c in enumerate(calls) if c.tool in EXECUTE_TOOLS), None)
    if exec_idx is None:
        validated_before_execute = None
    else:
        validated_before_execute = any(
            c.tool in VALIDATE_TOOLS for c in calls[:exec_idx]
        )

    return ToolUseMetrics(
        total_calls=len(calls),
        valid_calls=valid,
        schema_errors=schema,
        hallucinated_calls=hallucinated,
        recovered_errors=recovered,
        failed_calls=failed,
        validated_before_execute=validated_before_execute,
    )


# ---------------------------------------------------------------------------
# omd provenance DB
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProvenanceMetrics:
    activity_order: list[str]          # activity_type sequence, by started_at
    entity_types: list[str]            # entity_type set present
    n_activities: int
    n_failed: int
    recovered_activities: int          # failed type X later completed as type X
    has_decision: bool                 # a 'decision' entity => log_decision used

    @property
    def activity_success_rate(self) -> float:
        return (self.n_activities - self.n_failed) / self.n_activities if self.n_activities else 0.0


def read_provenance(db_path: Path) -> ProvenanceMetrics:
    """Derive workflow-adherence metrics from a run's analysis.db (read-only).

    Opens the SQLite file in read-only mode and queries the stable PROV schema
    (entities / activities). An empty or freshly-created DB yields zeroed
    metrics rather than an error.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"provenance DB not found: {db_path}")

    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        acts = conn.execute(
            "SELECT activity_type, status, started_at FROM activities "
            "ORDER BY started_at, activity_id"
        ).fetchall()
        ents = conn.execute("SELECT DISTINCT entity_type FROM entities").fetchall()
    finally:
        conn.close()

    order = [a["activity_type"] for a in acts]
    failed = [a["activity_type"] for a in acts if a["status"] == "failed"]

    # Recovery: a failed activity_type that later appears completed.
    completed_after: dict[str, bool] = {}
    recovered = 0
    seen_failed: list[str] = []
    for a in acts:
        if a["status"] == "failed":
            seen_failed.append(a["activity_type"])
        elif a["status"] == "completed" and a["activity_type"] in seen_failed:
            recovered += 1
            seen_failed = [t for t in seen_failed if t != a["activity_type"]]

    entity_types = [e["entity_type"] for e in ents]
    return ProvenanceMetrics(
        activity_order=order,
        entity_types=entity_types,
        n_activities=len(acts),
        n_failed=len(failed),
        recovered_activities=recovered,
        has_decision="decision" in entity_types,
    )
