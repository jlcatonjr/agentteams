"""
copilot_cli.py — Framework adapter for GitHub Copilot CLI.

Agent files:  .github/copilot/<slug>.md  (plain Markdown system prompt)
Instructions: copilot-instructions.md
Format:       Plain Markdown — no YAML front matter, no metadata headers
Handoffs:     Inline handoffs removed from output; extracted handoffs can be
              preserved in references/runtime-handoffs.json by the build pipeline

GitHub Copilot CLI system prompt specification
----------------------------------------------
Source: https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-custom-agents-for-cli
(the former about-github-copilot-in-the-cli URL now 301s to a generic
responsible-use page — verified 2026-08-15).

KNOWN UPSTREAM DIVERGENCE (P1, 2026-08-15): current docs give the CLI the SAME
custom-agent surface as VS Code — `.github/agents/<slug>.agent.md` WITH YAML
front matter; no upstream doc mentions `.github/copilot/`. This adapter's
plain-Markdown emission predates that convergence and is not read by the modern
CLI. Redesign is tracked in references/copilot-cli-agent-infrastructure-expert.md
and references/agentteams-remediation-log.csv; until it lands, the shipped
behavior below remains the tested contract.

The Copilot CLI consumes agent files as plain Markdown system prompts.
No YAML front matter is defined or recognized by the CLI runtime.  All
VS Code Copilot metadata keys (name:, user-invokable:, tools:, model:,
agents:, handoffs:) must be stripped before delivery; only the prose body
is passed to the model.

VS Code Copilot YAML keys       → stripped (incompatible)
Handoff sections (exact titles: ## Handoffs / ## Handoff Instructions) → stripped from prompt body
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

    def required_front_matter_keys(self) -> tuple[str, ...]:
        """None: a Copilot CLI agent is a prompt body with no YAML header.

        Measured on this module's own team: 29 of 30 emitted files carry no front matter. The
        exception is `team-builder.md`, which comes from `render_builder_file` — a different
        path with a different shape — and does not make a header contract true of the agents.
        A contract declared here would be asserted against all 29.

        Stated rather than inherited so that "inspected, and there is none" is distinguishable
        from "not yet inspected", which is what the empty default meant when per-framework
        contracts were first added for copilot-vscode and claude only.
        """
        return ()

    def supports_handoffs(self) -> bool:
        return False

    def handoff_delivery_mode(self) -> str:
        return "manifest"

    def get_agents_dir(self, project_path: Path) -> Path:
        return project_path / ".github" / "copilot"


