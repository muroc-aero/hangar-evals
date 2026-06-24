"""Tests for trace metrics — fixture analysis.db + synthetic tool-call traces.

Fully offline: the provenance test builds a tiny SQLite DB matching the omd
PROV schema; the tool-trace test feeds hand-built ToolCall lists.
"""

from __future__ import annotations

import sqlite3

import pytest

from hangar.evals.trace import (
    ToolCall,
    parse_omd_error_code,
    parse_tool_trace,
    read_provenance,
)


# --- parse_omd_error_code -------------------------------------------------


def test_parse_omd_error_code_from_envelope():
    assert parse_omd_error_code('{"error": {"code": "USER_INPUT_ERROR"}}') == "USER_INPUT_ERROR"


def test_parse_omd_error_code_non_envelope_is_none():
    assert parse_omd_error_code('{"session_id": "s1"}') is None
    assert parse_omd_error_code("not json at all") is None
    assert parse_omd_error_code(None) is None


# --- parse_tool_trace (harness boundary) ----------------------------------


def test_parse_tool_trace_metrics():
    calls = [
        ToolCall("start_session", ok=True),
        ToolCall("run_plan", ok=False, error_code="USER_INPUT_ERROR"),  # schema error
        ToolCall("run_plan", ok=True),                                  # recovery
        ToolCall("make_coffee", ok=False, error_code="TOOL_NOT_FOUND"), # hallucinated
    ]
    m = parse_tool_trace(calls)
    assert m.total_calls == 4
    assert m.valid_calls == 2
    assert m.failed_calls == 2
    assert m.schema_errors == 1
    assert m.hallucinated_calls == 1
    assert m.recovered_errors == 1
    assert m.valid_call_rate == 0.5
    assert m.schema_error_rate == 0.25
    assert m.hallucinated_rate == 0.25
    assert m.recovery_rate == 0.5


def test_parse_tool_trace_empty_is_zeroed():
    m = parse_tool_trace([])
    assert m.total_calls == 0
    assert m.valid_call_rate == 0.0
    assert m.recovery_rate == 0.0


def test_unrecovered_error_has_no_later_success():
    calls = [
        ToolCall("run_plan", ok=True),
        ToolCall("run_plan", ok=False, error_code="SOLVER_CONVERGENCE_ERROR"),
    ]
    m = parse_tool_trace(calls)
    assert m.failed_calls == 1
    assert m.recovered_errors == 0  # no later success on the same tool
    assert m.recovery_rate == 0.0


# --- read_provenance (omd DB) ---------------------------------------------


def _make_db(path):
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE entities (
            entity_id TEXT PRIMARY KEY, entity_type TEXT NOT NULL,
            created_at TEXT NOT NULL, created_by TEXT NOT NULL,
            plan_id TEXT, version INTEGER, content_hash TEXT,
            storage_ref TEXT, user TEXT
        );
        CREATE TABLE activities (
            activity_id TEXT PRIMARY KEY, activity_type TEXT NOT NULL,
            started_at TEXT, completed_at TEXT, agent TEXT NOT NULL, status TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO activities (activity_id, activity_type, started_at, agent, status) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            ("a1", "draft", "2026-01-01T00:00:01", "omd", "completed"),
            ("a2", "validate", "2026-01-01T00:00:02", "omd", "failed"),
            ("a3", "validate", "2026-01-01T00:00:03", "omd", "completed"),
            ("a4", "execute", "2026-01-01T00:00:04", "omd", "completed"),
        ],
    )
    conn.executemany(
        "INSERT INTO entities (entity_id, entity_type, created_at, created_by) "
        "VALUES (?, ?, ?, ?)",
        [
            ("e1", "plan", "2026-01-01T00:00:01", "omd"),
            ("e2", "decision", "2026-01-01T00:00:02", "omd"),
            ("e3", "run_record", "2026-01-01T00:00:04", "omd"),
        ],
    )
    conn.commit()
    conn.close()


def test_read_provenance_metrics(tmp_path):
    db = tmp_path / "analysis.db"
    _make_db(db)
    m = read_provenance(db)
    assert m.activity_order == ["draft", "validate", "validate", "execute"]
    assert m.n_activities == 4
    assert m.n_failed == 1
    assert m.recovered_activities == 1            # validate failed then completed
    assert m.validated_before_execute is True
    assert m.has_decision is True
    assert m.activity_success_rate == 0.75


def test_read_provenance_missing_db_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_provenance(tmp_path / "nope.db")
