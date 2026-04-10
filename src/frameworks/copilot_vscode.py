"""
copilot_vscode.py — Framework adapter for GitHub Copilot in VS Code.

Agent files:  .github/agents/<slug>.agent.md
Instructions: copilot-instructions.md (at project root)
Format:       YAML front matter + Markdown body
Handoffs:     Supported
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .base import FrameworkAdapter


class CopilotVSCodeAdapter(FrameworkAdapter):

    @property
    def framework_id(self) -> str:
        return "copilot-vscode"

    def render_agent_file(self, content: str, agent_slug: str, manifest: dict[str, Any]) -> str:
        """Validate and normalize YAML front matter for VS Code Copilot format."""
        content = _ensure_yaml_front_matter(content, agent_slug, manifest)
        return content

    def render_instructions_file(self, content: str, manifest: dict[str, Any]) -> str:
        return content  # instructions format is plain Markdown; no adjustments needed

    def get_file_extension(self, file_type: str) -> str:
        if file_type == "agent":
            return ".agent.md"
        return ".md"

    def supports_handoffs(self) -> bool:
        return True

    def get_agents_dir(self, project_path: Path) -> Path:
        return project_path / ".github" / "agents"


# ---------------------------------------------------------------------------
# YAML front matter helpers
# ---------------------------------------------------------------------------

_YAML_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# Required YAML keys for VS Code Copilot agent files
_REQUIRED_YAML_KEYS = {"name", "description", "user-invokable", "tools", "model"}

# Default values for missing required fields
_YAML_DEFAULTS = {
    "user-invokable": "false",
    "tools": "['read', 'edit', 'search']",
    "model": '["Claude Sonnet 4.6 (copilot)"]',
}


def _ensure_yaml_front_matter(content: str, agent_slug: str, manifest: dict[str, Any]) -> str:
    """Verify YAML front matter is present and has required keys; add defaults if not."""
    match = _YAML_FRONT_MATTER_RE.match(content)
    if not match:
        # No front matter — prepend minimal YAML
        project_name = manifest.get("project_name", "Project")
        agent_name = _slug_to_name(agent_slug)
        front_matter = (
            f"---\n"
            f"name: {agent_name} — {project_name}\n"
            f"description: \"{agent_name} agent for {project_name}\"\n"
            f"user-invokable: false\n"
            f"tools: ['read', 'edit', 'search']\n"
            f"model: [\"Claude Sonnet 4.6 (copilot)\"]\n"
            f"---\n\n"
        )
        return front_matter + content

    yaml_body = match.group(1)

    # Check for missing required keys and append defaults
    missing_lines: list[str] = []
    for key in _REQUIRED_YAML_KEYS:
        if not re.search(rf"^{re.escape(key)}\s*:", yaml_body, re.MULTILINE):
            if key in _YAML_DEFAULTS:
                missing_lines.append(f"{key}: {_YAML_DEFAULTS[key]}")

    if missing_lines:
        # Insert missing keys before closing ---
        new_yaml = yaml_body + "\n" + "\n".join(missing_lines)
        content = content[: match.start(1)] + new_yaml + content[match.end(1):]

    return content


def _slug_to_name(slug: str) -> str:
    """Convert 'my-agent-slug' to 'My Agent Slug'."""
    return " ".join(word.capitalize() for word in slug.replace("_", "-").split("-"))
