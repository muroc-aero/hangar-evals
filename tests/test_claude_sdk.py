"""Contamination-policy guards for the Claude anchor driver.

These pin the *intent* of the tool restriction (block privileged-context /
external-knowledge vectors; leave benign tool discovery alone) so a future
edit to ``_DISALLOWED_TOOLS`` can't silently re-open a leak. The module is
importable without ``claude-agent-sdk`` (its import is lazy inside ``run``), so
these run in the base test env.
"""

from __future__ import annotations

from hangar.evals.drivers import claude_sdk


def test_skill_and_web_tools_are_blocked():
    # Skill = privileged procedural context; Web* = external knowledge. A
    # filesystem sandbox would not stop these, so they must stay blocklisted.
    for tool in ("Skill", "WebSearch", "WebFetch"):
        assert tool in claude_sdk._DISALLOWED_TOOLS


def test_toolsearch_is_not_blocked():
    # ToolSearch only discovers tools the agent may already use — no privileged
    # context leaks through it, so blocking it would only hurt without cause.
    assert "ToolSearch" not in claude_sdk._DISALLOWED_TOOLS


def test_interim_filesystem_tools_blocked_while_cwd_is_the_repo():
    # Blocked ONLY as an interim guard (cwd is the the-hangar repo today). This
    # test documents that coupling: it should be RELAXED in the same change that
    # introduces a clean sandboxed workspace.
    for tool in ("Bash", "Read", "Write", "Edit", "Glob", "Grep"):
        assert tool in claude_sdk._DISALLOWED_TOOLS
