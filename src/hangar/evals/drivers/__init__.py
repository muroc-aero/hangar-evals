"""Agent drivers: one uniform interface, many harnesses.

A driver runs an agent against a task and returns an ``AgentResult``. All
drivers point at the same MCP server (described by ``MCPServerSpec``); the
model and harness vary. ``ClaudeAgentSDKDriver`` is the frontier anchor every
local result is measured against.
"""

from hangar.evals.drivers.base import AgentDriver, AgentResult, MCPServerSpec

__all__ = ["AgentDriver", "AgentResult", "MCPServerSpec"]
