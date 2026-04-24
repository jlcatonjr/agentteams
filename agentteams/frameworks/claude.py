"""
claude.py — Framework adapter for Claude Code sub-agents.

Agent files:  .claude/agents/<slug>.md  (Claude Code sub-agent format)
Instructions: CLAUDE.md (merged with agents for smaller teams)
Format:       Claude Code front matter (name, description, allowed-tools) + Markdown body
Handoffs:     Inline handoffs removed from prompt body; extracted handoffs can
              be preserved in references/runtime-handoffs.json by the build pipeline

Claude Code sub-agent front matter specification
-------------------------------------------------
Source: https://docs.anthropic.com/en/docs/claude-code/sub-agents

Recognised front matter keys (all optional, but name + description are strongly recommended):
  name:          Display name shown in the agent picker
  description:   When/how to invoke this agent (used for automatic routing)
  allowed-tools: Comma-separated list of Claude tool names the agent may use
                 (Bash, Read, Write, Edit, MultiEdit, Glob, Grep, LS,
                  WebFetch, WebSearch, TodoRead, TodoWrite)
  model:         Claude model variant (e.g. claude-opus-4-5, claude-sonnet-4-5)

VS Code Copilot keys (name:, user-invokable:, tools:, agents:, model:) are NOT
recognised by Claude Code and must NOT be passed through.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .base import FrameworkAdapter


# ---------------------------------------------------------------------------
# Claude Code front matter constants
# ---------------------------------------------------------------------------

# Default tool list mapped to Claude Code tool names.
# VS Code tools: read→Read, edit→Edit, search→(Grep/Glob), execute→Bash
_CLAUDE_DEFAULT_ALLOWED_TOOLS = "Bash, Read, Write, Edit"

# Required keys for a well-formed Claude Code sub-agent front matter block
_CLAUDE_REQUIRED_KEYS = {"name", "description"}


class ClaudeAdapter(FrameworkAdapter):

    @property
    def framework_id(self) -> str:
        return "claude"

    def render_agent_file(self, content: str, agent_slug: str, manifest: dict[str, Any]) -> str:
        """Produce a Claude Code sub-agent file from VS Code Copilot template content.

        Transformation steps:
        1. Extract name and description from the VS Code YAML front matter (if present).
        2. Strip the VS Code YAML front matter entirely (keys are incompatible).
        3. Strip handoffs sections (Claude Code does not support them).
        4. Prepend a Claude Code-compatible front matter block.
        """
        name, description = _extract_name_description(content, agent_slug, manifest)
        content = self._strip_yaml_front_matter(content)
        content = self._strip_handoffs_section(content)
        content = _inject_claude_front_matter(content, name, description)
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
        return project_path / ".claude" / "agents"

    def finalize_output_path(self, rel_path: str, file_type: str) -> str:
        """Map generic planned paths to Claude-native file names/locations."""
        if file_type == "instructions" and rel_path.endswith("copilot-instructions.md"):
            return "../CLAUDE.md"
        return super().finalize_output_path(rel_path, file_type)


# ---------------------------------------------------------------------------
# Claude Code front matter helpers
# ---------------------------------------------------------------------------

# Captures the raw YAML body between the opening and closing --- delimiters
_YAML_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# Matches a single-line YAML scalar: key: value (with optional surrounding quotes)
_YAML_SCALAR_RE = re.compile(r'^([a-zA-Z][a-zA-Z0-9_-]*)\s*:\s*"?([^"\n]+)"?\s*$', re.MULTILINE)


def _extract_name_description(
    content: str,
    agent_slug: str,
    manifest: dict[str, Any],
) -> tuple[str, str]:
    """Return (name, description) sourced from VS Code YAML front matter.

    Falls back to a slug-derived name when no YAML is present or the name key
    is absent.  Description defaults to empty string when absent.
    """
    name = ""
    description = ""

    match = _YAML_FRONT_MATTER_RE.match(content)
    if match:
        yaml_body = match.group(1)
        for key_match in _YAML_SCALAR_RE.finditer(yaml_body):
            key = key_match.group(1).strip()
            val = key_match.group(2).strip().strip('"\'')
            if key == "name" and not name:
                name = val
            elif key == "description" and not description:
                description = val

    if not name:
        project_name = manifest.get("project_name", "")
        agent_name = FrameworkAdapter._slug_to_name(agent_slug)
        name = f"{agent_name} — {project_name}" if project_name else agent_name

    return name, description


def _inject_claude_front_matter(content: str, name: str, description: str) -> str:
    """Prepend a Claude Code-compatible YAML front matter block to content."""
    lines = ["---", f"name: {name}"]
    if description:
        lines.append(f'description: "{description}"')
    lines.append(f"allowed-tools: {_CLAUDE_DEFAULT_ALLOWED_TOOLS}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + content


