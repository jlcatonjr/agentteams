"""
copilot_cli.py — Framework adapter for GitHub Copilot CLI.

Agent files:  <slug>.md  (system prompt format, no YAML front matter)
Instructions: copilot-instructions.md
Format:       Plain Markdown system prompts
Handoffs:     Not supported (removed from output)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .base import FrameworkAdapter


class CopilotCLIAdapter(FrameworkAdapter):

    @property
    def framework_id(self) -> str:
        return "copilot-cli"

    def render_agent_file(self, content: str, agent_slug: str, manifest: dict[str, Any]) -> str:
        """Strip YAML front matter and handoffs blocks for CLI format."""
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
        # CLI agents live in .github/copilot/ by convention for this module
        return project_path / ".github" / "copilot"


# ---------------------------------------------------------------------------
# Stripping helpers
# ---------------------------------------------------------------------------

_YAML_FRONT_MATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)

_HANDOFFS_SECTION_RE = re.compile(
    r"^handoffs\s*:\s*\n(?:[ \t]+-.*\n)*",
    re.MULTILINE,
)

_HANDOFFS_HEADING_RE = re.compile(
    r"^#{1,3}\s+Handoff.*?(?=^#{1,3}\s|\Z)",
    re.MULTILINE | re.DOTALL,
)


def _strip_yaml_front_matter(content: str) -> str:
    return _YAML_FRONT_MATTER_RE.sub("", content, count=1)


def _strip_handoffs_section(content: str) -> str:
    """Remove handoffs blocks from body prose (CLI doesn't support them)."""
    content = _HANDOFFS_HEADING_RE.sub("", content)
    return content
