"""Tests for environment capture — shape, real-checkout SHAs, never-fatal.

The capture is best-effort by contract: the never-fatal test breaks EVERY
subprocess call and asserts the block still comes back whole, because a
missing CLI must degrade a manifest field, never kill a run.
"""

from __future__ import annotations

import string
import subprocess

from hangar.evals.environment import UNAVAILABLE, capture_environment

EXPECTED_KEYS = {
    "python", "platform", "hangar_evals", "the_hangar",
    "claude_agent_sdk", "opencode", "ollama",
}


def _looks_like_sha(s: str) -> bool:
    return len(s) == 40 and set(s) <= set(string.hexdigits.lower())


def test_capture_shape_and_git_shas():
    env = capture_environment()
    assert set(env) == EXPECTED_KEYS
    assert env["python"].count(".") == 2          # e.g. "3.12.4"
    # Running from checkouts (this repo + the-hangar sibling): SHAs are real.
    for repo_key in ("hangar_evals", "the_hangar"):
        info = env[repo_key]
        assert isinstance(info, dict), f"{repo_key} not captured: {info}"
        assert _looks_like_sha(info["sha"])
        assert isinstance(info["dirty"], bool)
    # Versions are strings either way ("x.y.z" or "unavailable"), never None.
    for tool_key in ("claude_agent_sdk", "opencode", "ollama"):
        assert isinstance(env[tool_key], str) and env[tool_key]


def test_capture_never_fatal_when_subprocess_breaks(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("no such binary")

    monkeypatch.setattr(subprocess, "run", boom)
    env = capture_environment()          # must not raise
    assert set(env) == EXPECTED_KEYS
    assert env["hangar_evals"] == UNAVAILABLE
    assert env["the_hangar"] == UNAVAILABLE
    assert env["opencode"] == UNAVAILABLE
    assert env["ollama"] == UNAVAILABLE
    # importlib.metadata path is subprocess-free — still a plain string.
    assert isinstance(env["claude_agent_sdk"], str)
