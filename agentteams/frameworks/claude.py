"""
claude.py — Framework adapter for Claude (claude.ai Projects or CLAUDE.md).

Agent files:  CLAUDE.md (single consolidated file) or individual <slug>.md files
Instructions: CLAUDE.md (merged with agents for smaller teams)
Format:       Plain Markdown; no YAML front matter; no handoffs block
Handoffs:     Not supported
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .base import FrameworkAdapter


class ClaudeAdapter(FrameworkAdapter):

    @property
    def framework_id(self) -> str:
        return "claude"

    def render_agent_file(self, content: str, agent_slug: str, manifest: dict[str, Any]) -> str:
        """Strip YAML front matter; reformat for Claude system prompt style."""
        content = _strip_yaml_front_matter(content)
        content = _strip_handoffs_section(content)
        return content.strip() + "\n"

    def render_instructions_file(self, content: str, manifest: dict[str, Any]) -> str:
        return content

    def get_file_extension(self, file_type: str) -> str:
        return ".md"

    def supports_handoffs(self) -> bool:
        return False

    def get_agents_dir(self, project_path: Path) -> Path:
        # Claude agents are placed alongside CLAUDE.md at the project root
        return project_path / ".claude" / "agents"


# ---------------------------------------------------------------------------
# Stripping helpers (shared with copilot_cli)
# ---------------------------------------------------------------------------

_YAML_FRONT_MATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)

_HANDOFFS_HEADING_RE = re.compile(
    r"^#{1,3}\s+Handoff.*?(?=^#{1,3}\s|\Z)",
    re.MULTILINE | re.DOTALL,
)


def _strip_yaml_front_matter(content: str) -> str:
    return _YAML_FRONT_MATTER_RE.sub("", content, count=1)


def _strip_handoffs_section(content: str) -> str:
    return _HANDOFFS_HEADING_RE.sub("", content)
