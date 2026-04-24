"""
base.py — Abstract base class for framework adapters.

Framework adapters control how agent template content is adjusted for a
specific target framework (VS Code Copilot, Copilot CLI, Claude).
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Shared stripping helpers (used by CLI and Claude adapters)
# ---------------------------------------------------------------------------

_YAML_FRONT_MATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)

_HANDOFFS_HEADING_RE = re.compile(
    r"^#{1,3}\s+Handoff.*?(?=^#{1,3}\s|\Z)",
    re.MULTILINE | re.DOTALL,
)


class FrameworkAdapter(ABC):
    """Abstract interface for per-framework agent file generation."""

    @property
    @abstractmethod
    def framework_id(self) -> str:
        """Short identifier for this framework (e.g., 'copilot-vscode')."""

    @abstractmethod
    def render_agent_file(self, content: str, agent_slug: str, manifest: dict[str, Any]) -> str:
        """Post-process rendered agent content for this framework.

        The base render.py already resolves placeholders. This method handles
        any framework-specific structural adjustments (e.g., YAML stripping,
        reformatting, etc.).
        """

    @abstractmethod
    def render_instructions_file(self, content: str, manifest: dict[str, Any]) -> str:
        """Post-process rendered copilot-instructions content."""

    @abstractmethod
    def get_file_extension(self, file_type: str) -> str:
        """Return the file extension for a given file type.

        file_type: 'agent', 'instructions', 'builder'
        """

    @abstractmethod
    def supports_handoffs(self) -> bool:
        """Whether this framework supports YAML handoff blocks in agent files."""

    @abstractmethod
    def get_agents_dir(self, project_path: Path) -> Path:
        """Return the default agent file directory for a given project path."""

    def finalize_output_path(self, rel_path: str, file_type: str) -> str:
        """Adjust an output path for this framework.

        Args:
            rel_path: Planned output path from manifest/output planning.
            file_type: Logical file type (agent, builder, instructions, etc.).

        Returns:
            Framework-adjusted relative output path.
        """
        if file_type in {"agent", "builder"}:
            ext = self.get_file_extension(file_type)
            if rel_path.endswith(".agent.md") and ext != ".agent.md":
                return rel_path[: -len(".agent.md")] + ext
            if rel_path.endswith(".md") and ext not in {".md", ".agent.md"}:
                return rel_path[:-3] + ext
        return rel_path

    # ------------------------------------------------------------------
    # Protected helpers shared by non-handoff adapters (CLI, Claude)
    # ------------------------------------------------------------------

    @staticmethod
    def _slug_to_name(slug: str) -> str:
        """Convert 'my-agent-slug' to 'My Agent Slug'."""
        return " ".join(word.capitalize() for word in slug.replace("_", "-").split("-"))

    @staticmethod
    def _strip_yaml_front_matter(content: str) -> str:
        """Remove YAML front matter block from agent content."""
        return _YAML_FRONT_MATTER_RE.sub("", content, count=1)

    @staticmethod
    def _strip_handoffs_section(content: str) -> str:
        """Remove handoff heading blocks from body prose."""
        return _HANDOFFS_HEADING_RE.sub("", content)
