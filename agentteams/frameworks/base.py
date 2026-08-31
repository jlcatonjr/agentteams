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

from agentteams.yaml_frontmatter import parse_yaml_front_matter as _parse_yaml_front_matter

# ---------------------------------------------------------------------------
# Shared stripping helpers (used by CLI and Claude adapters)
# ---------------------------------------------------------------------------

_BODY_HANDOFFS_SECTION_RE = re.compile(
    r"^## Handoff Instructions\s*$\n(?P<body>.*?)(?=^##\s+|\Z)",
    re.MULTILINE | re.DOTALL,
)

# A fence marker terminates the strip exactly as a heading does. Without that branch the
# stripper treats fence markers as ordinary prose and can delete them: in
# `conflict-auditor.template.md` a fenced, heading-only `## Handoff Payload Conflict Codes` is
# followed by no further `##` heading, so the match ran to `\Z` and consumed THREE markers —
# that fence's END and both markers of the whole next fence — across 877 characters. The BEGIN
# above the heading survived, leaving the emitted file at 8 BEGIN / 7 END, which then made
# `_extract_fenced_regions` fail, which made `_normalize_generated_content` wrap the body, which
# finally surfaced as "Nested fence not allowed: invariant_core inside content" — an error three
# steps from its cause, and one that got this file misdiagnosed twice.
#
# A strip may leave a fence empty. It must never leave one unbalanced.
#
# The heading must ALSO be an exact section title — `Handoffs`, `Handoff`, or
# `Handoff Instructions` — not merely prefixed by the word. Template-owned sections like
# `## Handoff Payload Conflict Codes` share the prefix; before the title was anchored to
# end-of-line, the only thing keeping those headings on disk was `preserve_on_shrink`
# coincidentally suppressing the emptied render — protection by accident, not design.
_HANDOFFS_HEADING_RE = re.compile(
    r"^#{1,3}\s+Handoffs?(?: Instructions)?\s*$.*?(?=^#{1,3}\s|^<!--\s*AGENTTEAMS(?:-BRIDGE)?:|\Z)",
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

    def render_skill_file(self, content: str, slug: str, manifest: dict[str, Any]) -> str:
        """Post-process a rendered operational tool-doc emitted as a skill.

        Only frameworks with a first-class skill concept (Claude Code) emit
        skill files; for every other framework operational tool docs are emitted
        as reference documents and this method is never invoked. The default is
        a no-op so all adapters satisfy the interface.
        """
        return content

    def has_skill_concept(self) -> bool:
        """Whether this framework has a first-class skill concept.

        Today only Claude Code (``skills/<slug>/SKILL.md`` directories) does.
        Drives the doc-text phrasing ("skill" vs "reference doc") where render
        paths name an operational tool doc; frameworks without one never get
        skill phrasing. (D.1: replaces hardcoded ``framework == "claude"``
        branches in render.py / manifest_format.py.)
        """
        return False

    def tool_doc_rel_path(self, slug: str) -> str:
        """Project-root-relative placement path for an operational tool doc.

        Frameworks with a skill concept place it as a per-slug skill directory;
        every other framework places it as a reference document under
        ``references/``. Callers derive display forms from this single path
        instead of duplicating per-framework placement logic. (D.1: replaces
        hardcoded ``framework == "claude"`` branches in render.py /
        manifest_format.py.)
        """
        base = slug[len("tool-"):] if slug.startswith("tool-") else slug
        return f"references/ref-{base}-reference.md"

    def framework_root_prefix(self) -> str:
        """Project-root-relative prefix of this framework's root dir.

        Empty for frameworks whose emitted paths are already project-root
        relative; ``.claude`` for Claude Code. Used to build display paths
        (e.g. ``.claude/skills/<slug>/SKILL.md``) from ``tool_doc_rel_path``
        without re-hardcoding framework roots at call sites. (D.1)
        """
        return ""

    def render_builder_file(self, content: str, manifest: dict[str, Any]) -> str:
        """Post-process the rendered team-builder meta-agent for this framework.

        The default is identity: copilot/claude emit the builder as a markdown
        agent file unchanged. Frameworks whose agent files are NOT markdown (e.g.
        Goose, whose agents are recipe YAML) override this to wrap the builder in
        the framework's agent structure so it is runnable, not a stray markdown
        file in the agents directory.
        """
        return content

    def extra_output_files(self, manifest: dict[str, Any]) -> list[tuple[str, str]]:
        """Return additional (rel_path, content) files to emit for this framework.

        Default: none. Frameworks that need framework-specific sidecar files not
        derived from a template (e.g. Goose's ``.goosehints`` integrator) override
        this. Paths are relative to the agents output directory, like every other
        emitted path.
        """
        return []

    def vscode_tasks_rel_path(self) -> str | None:
        """Return the path to .vscode/tasks.json relative to this framework's agents dir.

        Default ``None`` disables tasks.json generation for this framework.
        Override in adapters whose agents dir is exactly two levels deep from the
        project root (e.g. ``.github/agents/``, ``.claude/agents/``, ``.goose/recipes/``).
        AgentsMdAdapter must NOT override — its agents dir is one level deep and
        ``../../`` would resolve above the project root.
        """
        return None

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

    def required_front_matter_keys(self) -> tuple[str, ...]:
        """Front-matter keys every agent file of this framework must declare.

        The audit's key list was a **copilot-vscode** contract that had been applied as if it
        were universal. It went unnoticed only because the audit's file filter was hardcoded to
        `.agent.md`, so on every other framework the check inspected zero files. Fixing the
        filter surfaced the real disagreement — the three frameworks emit three genuinely
        different headers:

            copilot-vscode   name, description, user-invocable, tools, model
            claude           name, description, allowed-tools
            copilot-cli      no front matter at all

        Asserting one framework's header shape against another produced 83 findings on a tree
        with nothing wrong with it.

        The default is empty: *this framework asserts no front-matter contract*. That is the
        honest default for an adapter whose header shape has not been established, and it
        preserves today's behaviour, since nothing was being checked there anyway. An adapter
        declares a contract only when its header shape is known.
        """
        return ()

    def normalize_output_path(self, output: Path) -> Path:
        """Normalize a user-supplied --output path for this framework.

        Called when the user passes --output explicitly in the generate pipeline.
        The default is identity (the path is used as-is as the agents directory).
        Frameworks whose agents directory is nested under a conventional path
        (e.g. Goose's .goose/recipes/) override this to detect when the user
        passed the project root and derive the agents dir from it.
        """
        return output

    def handoff_delivery_mode(self) -> str:
        """Return how this framework receives handoff semantics.

        Returns:
            `native` when handoffs are preserved inline in agent files,
            `manifest` when handoffs are delivered via a sidecar manifest,
            or `none` when no handoff delivery is supported.
        """
        return "native" if self.supports_handoffs() else "none"

    def parse_agent_source(self, content: str) -> dict[str, Any] | None:
        """Framework-native source parse for CAI export (F.1).

        Frameworks whose agent files are NOT front-matter Markdown — goose
        recipes are YAML — override this and return pre-shaped fields::

            {"name": str, "description": str, "body": str,
             "capabilities": dict, "handoffs": list[{"agent", "label",
             "prompt", "send"}]}

        Returning ``None`` (the default) tells ``interop.export_to_cai`` to
        use its standard Markdown path: front-matter name/description,
        wrapper-stripped body, capability/handoff extraction per framework.
        """
        return None

    def framework_extensions_from_sources(
        self, parsed_sources: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Project-level framework-owned config captured on CAI export (F.3).

        Receives every non-``None`` ``parse_agent_source`` result collected
        during discovery and returns the ``framework_extensions`` object for
        the CAI document (keyed by framework id). Default: no project-level
        config. goose overrides to fill the ``framework_extensions.goose``
        bucket (recipe_parameters/response/retry + non-MCP extension names).
        """
        return {}

    def apply_framework_extensions(self, manifest: dict[str, Any], cai: dict[str, Any]) -> None:
        """Merge this framework's CAI framework_extensions bucket into the
        import manifest stub (F.3). Default: no framework-owned config.
        goose overrides to restore recipe configuration at render time."""
        return

    def extract_handoffs(self, content: str) -> list[dict[str, Any]]:
        """Extract YAML handoff entries from rendered agent content.

        The parser is intentionally narrow and only supports the handoff block
        format emitted by AgentTeams templates.
        """
        handoffs: list[dict[str, Any]] = []
        yaml_body, _ = _parse_yaml_front_matter(content)
        if yaml_body is not None:
            lines = yaml_body.splitlines()
            idx = 0
            while idx < len(lines):
                if lines[idx].strip() != "handoffs:":
                    idx += 1
                    continue

                idx += 1
                current: dict[str, Any] | None = None
                while idx < len(lines):
                    line = lines[idx]
                    if line and not line.startswith("  "):
                        break

                    stripped = line.strip()
                    if not stripped:
                        idx += 1
                        continue

                    if stripped.startswith("- label:"):
                        if current and current.get("agent"):
                            handoffs.append(current)
                        current = {
                            "label": stripped.split(":", 1)[1].strip().strip('"\''),
                            "agent": "",
                            "prompt": "",
                            "send": False,
                        }
                    elif current is not None and ":" in stripped:
                        key, value = stripped.split(":", 1)
                        key = key.strip()
                        value = value.strip().strip('"\'')
                        if key in {"agent", "prompt", "label"}:
                            current[key] = value
                        elif key == "send":
                            current[key] = value.lower() == "true"
                    idx += 1

                if current and current.get("agent"):
                    handoffs.append(current)

        body_match = _BODY_HANDOFFS_SECTION_RE.search(content)
        if body_match:
            for line in body_match.group("body").splitlines():
                stripped = line.strip()
                if not stripped.startswith("-"):
                    continue
                agent_match = re.search(r"@([a-z0-9-]+)", stripped, re.IGNORECASE)
                if not agent_match:
                    continue
                handoffs.append({
                    "label": stripped[1:].strip(),
                    "agent": agent_match.group(1),
                    "prompt": stripped[1:].strip(),
                    "send": False,
                })

        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for handoff in handoffs:
            key = (str(handoff.get("agent", "")), str(handoff.get("prompt", "")))
            if not key[0] or key in seen:
                continue
            seen.add(key)
            deduped.append(handoff)

        return deduped

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
        _, body = _parse_yaml_front_matter(content)
        return body

    @staticmethod
    def _strip_handoffs_section(content: str) -> str:
        """Remove handoff heading blocks from body prose."""
        return _HANDOFFS_HEADING_RE.sub("", content)
