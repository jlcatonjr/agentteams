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

from agentteams.frameworks.base import FrameworkAdapter
from agentteams.frameworks.claude import ClaudeAdapter
from agentteams.frameworks.copilot_cli import CopilotCLIAdapter
from agentteams.frameworks.copilot_vscode import CopilotVSCodeAdapter

FRAMEWORKS: dict[str, type[FrameworkAdapter]] = {
    "copilot-vscode": CopilotVSCodeAdapter,
    "copilot-cli": CopilotCLIAdapter,
    "claude": ClaudeAdapter,
}
