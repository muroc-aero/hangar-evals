"""What omd tells an agent about itself, delivered as workspace files.

Claude Code hands the model an MCP server's ``instructions`` (from the
initialize handshake) in the system prompt and exposes ``resources/list`` and
``resources/read`` as callable tools. The anchor uses both: its first calls
on every case are ``ListMcpResourcesTool`` then reads of ``omd://reference``
and ``omd://plan-schema``, and only then does it author a plan.

OpenCode 1.17.5 forwards neither. It never calls the client's
``getInstructions()``, and resources exist only for the TUI's ``@`` picker
(a person attaches one; ``opencode run`` cannot). So a local model sees omd's
tools and nothing else, and guesses component types. Verified in the binary
on 2026-09-21; see hangar-evals PR #27.

This module delivers the same three texts the way OpenCode CAN receive them:
``AGENTS.md`` in the project dir goes into its system prompt, and the two
resources become files next to it that the ``read`` tool can open. Sandboxed
runs have ``read``; the unsandboxed MCP-only track does not, so there the
resources are inlined into ``AGENTS.md`` instead.

The texts are imported from ``hangar.omd`` itself (never copied), so they
cannot drift from what the anchor reads over MCP.
"""

from __future__ import annotations

import asyncio

AGENTS_FILE = "AGENTS.md"
REFERENCE_FILE = "omd_reference.md"
SCHEMA_FILE = "omd_plan_schema.json"

_POINTER = f"""

## Reading the omd resources here

This harness cannot fetch MCP resources by URI. The two omd resources are
files in this directory instead; read them with your `read` tool:

- `omd://reference`   -> `./{REFERENCE_FILE}`  (component types and the config keys each accepts)
- `omd://plan-schema` -> `./{SCHEMA_FILE}`  (JSON Schema for plan YAML)
"""


def omd_texts() -> tuple[str, str, str]:
    """``(instructions, reference, plan_schema)`` straight from ``hangar.omd``."""
    from hangar.omd.instructions import INSTRUCTIONS
    from hangar.omd.tools.resources import plan_schema_resource, reference_guide

    async def _both() -> tuple[str, str]:
        return await reference_guide(), await plan_schema_resource()

    reference, schema = asyncio.run(_both())
    return INSTRUCTIONS, reference, schema


def omd_agent_context(*, files_readable: bool) -> dict[str, str]:
    """Workspace files to write before ``opencode run``: ``{name: text}``.

    ``files_readable`` — the agent has a ``read`` tool (sandboxed runs). If
    not, the resources are inlined into ``AGENTS.md``.
    """
    instructions, reference, schema = omd_texts()
    agents = "# omd MCP server\n\n" + instructions.strip() + "\n"
    if files_readable:
        agents += _POINTER
    else:
        agents += ("\n\n## omd://reference\n\n" + reference.strip()
                   + "\n\n## omd://plan-schema\n\n```json\n" + schema.strip() + "\n```\n")
    return {AGENTS_FILE: agents, REFERENCE_FILE: reference, SCHEMA_FILE: schema}
