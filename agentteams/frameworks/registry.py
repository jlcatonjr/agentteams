"""
registry.py — single source of truth for framework adapter registration.

CH-05 (Single Source of Truth for Mappings, Critical): the framework-id →
adapter-class map lives here and ONLY here. ``build_team``, ``interop``, and
``convert`` import ``FRAMEWORKS`` from this module; adding a new target framework
is a one-line edit in this file instead of three.

No import cycle: this module imports adapter classes (which depend only on
``base``); the CLI/interop/convert consumers import this module. Adapters never
import the registry.
"""

from __future__ import annotations

from agentteams.frameworks.agents_md import AgentsMdAdapter
from agentteams.frameworks.base import FrameworkAdapter
from agentteams.frameworks.claude import ClaudeAdapter
from agentteams.frameworks.codex import CodexAdapter
from agentteams.frameworks.copilot_cli import CopilotCLIAdapter
from agentteams.frameworks.copilot_vscode import CopilotVSCodeAdapter
from agentteams.frameworks.goose import GooseAdapter

FRAMEWORKS: dict[str, type[FrameworkAdapter]] = {
    "copilot-vscode": CopilotVSCodeAdapter,
    "copilot-cli": CopilotCLIAdapter,
    "claude": ClaudeAdapter,
    "goose": GooseAdapter,
    "agents-md": AgentsMdAdapter,
    # F.4: thin prep-scoped Codex target (AGENTS.md rendering delegated to
    # agents_md; config.toml MCP emission is implemented separately — see
    # agentteams/codex_mcp_emit.py). The schema framework enums must carry
    # this id too — pinned by tests/test_framework_enum_consistency.py.
    "codex": CodexAdapter,
}

# Ordered framework-id list derived from FRAMEWORKS (insertion order).
# Schema enums (team-manifest.schema.json, agent-cai.schema.json) must match
# this exactly — pinned by tests/test_framework_enum_consistency.py so the
# recurring enum-staleness bug cannot return silently (CH-05 extension).
FRAMEWORK_IDS: tuple[str, ...] = tuple(FRAMEWORKS.keys())
