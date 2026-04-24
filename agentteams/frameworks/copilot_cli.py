"""
copilot_cli.py — Framework adapter for GitHub Copilot CLI.

Agent files:  .github/copilot/<slug>.md  (plain Markdown system prompt)
Instructions: copilot-instructions.md
Format:       Plain Markdown — no YAML front matter, no metadata headers
Handoffs:     Inline handoffs removed from output; extracted handoffs can be
              preserved in references/runtime-handoffs.json by the build pipeline

GitHub Copilot CLI system prompt specification
----------------------------------------------
Source: https://docs.github.com/en/copilot/github-copilot-in-the-cli/about-github-copilot-in-the-cli

The Copilot CLI consumes agent files as plain Markdown system prompts.
No YAML front matter is defined or recognized by the CLI runtime.  All
VS Code Copilot metadata keys (name:, user-invokable:, tools:, model:,
agents:, handoffs:) must be stripped before delivery; only the prose body
is passed to the model.

VS Code Copilot YAML keys       → stripped (incompatible)
Handoff sections (## Handoff…)  → stripped from prompt body
Runtime handoff manifest        → emitted by build pipeline when handoffs exist
Body Markdown                   → preserved verbatim
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import FrameworkAdapter


class CopilotCLIAdapter(FrameworkAdapter):

    @property
    def framework_id(self) -> str:
        return "copilot-cli"

    def render_agent_file(self, content: str, agent_slug: str, manifest: dict[str, Any]) -> str:
        """Produce a plain-Markdown system prompt for the Copilot CLI.

        The CLI runtime does not recognise YAML front matter or handoff blocks.
        Both are stripped; the prose body is preserved verbatim and the output
        is normalised to a single trailing newline.
        """
        content = self._strip_yaml_front_matter(content)
        content = self._strip_handoffs_section(content)
        return content.strip() + "\n"

    def render_instructions_file(self, content: str, manifest: dict[str, Any]) -> str:
        return content

    def get_file_extension(self, file_type: str) -> str:
        return ".md"

    def supports_handoffs(self) -> bool:
        return False

    def handoff_delivery_mode(self) -> str:
        return "manifest"

    def get_agents_dir(self, project_path: Path) -> Path:
        return project_path / ".github" / "copilot"


