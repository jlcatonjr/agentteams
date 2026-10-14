"""
grok.py — Framework adapter for xAI Grok Build (the ``grok`` CLI).

Agent files:  .grok/agents/<slug>.md  (Grok Build custom agents: YAML front matter + Markdown body)
Instructions: AGENTS.md (repo root), shared with codex / goose / agents-md and co-owned merge-only
Format:       Claude Code-compatible front matter (Grok Build is a Claude Code superset) + two Grok keys
Handoffs:     Removed from the body; kept in references/runtime-handoffs.json (``manifest`` delivery)

Grok Build facts this adapter relies on (the release pinned in ``FORMAT_SPECS["grok"].pinned_version``; see references/grok-agent-infrastructure-expert.md
and tmp/by-week/2026-W42/grok-integration.plan.md §9 for evidence):

* Custom agents load from ``.grok/agents/*.md``; ``.claude/agents/*.md`` load too, always (no compatibility
  switch turns them off, and folder trust does not gate them). A ``.grok/agents`` file shadows the Claude agent
  with the same ``name:`` (spike S1/S2), so every ``name:`` here is byte-identical to the Claude render (D4).
* ``tools:`` takes Claude tool names (``Read``, ``Bash(...)``…; ``Agent`` for spawning). An EMPTY list means
  "inherit every tool", and ``injectDefaultTools`` defaults to true, layering session tools on top of the list,
  and ``mcpInheritance`` defaults to ``all`` (source-only: ``xai-grok-agent/src/config.rs``). Every agent
  therefore carries ``injectDefaultTools: false`` and ``mcpInheritance: none`` so ``tools:`` is the exact limit
  (C-3; @security step-6 ruling).
* Instruction files load only in a trusted folder; Grok reads ``AGENTS.md``, ``CLAUDE.md`` and
  ``.claude/CLAUDE.md`` together. The team brief goes in the shared ``AGENTS.md`` (operator decision D2), whose
  ownership rules live in :mod:`._agents_md_ownership`.

Generate-only in this release: convert / bridge / interop / absorb flows refuse ``grok`` (``cli/parser_validate``),
and ``write_policy: "orchestrator-only"`` is refused (``write_policy.py``).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .base import FrameworkAdapter
from .claude import (
    _YAML_TOOLS_RE,
    ClaudeAdapter,
    _extract_claude_optional_keys,
    _extract_name_description,
    _map_allowed_tools,
    _runner_grant,
)
from .agents_md import _neutralize_instructions
from ._agents_md_rules import apply_constitutional_rules_baseline
from ._agents_md_ownership import (
    ABSENT,
    AGENTS_MD,
    CODEX,
    FENCED,
    FOREIGN,
    GROK,
    GROK_AGENTS_MD_MARKER,
    GROK_FENCE_ID,
    classify_agents_md,
    strip_agents_md_notice,
    upsert_fenced_block,
)
from ._grok_tool_scoping import to_grok_tools
from .format_spec import COPILOT_INSTRUCTIONS_FILENAME
from agentteams.yaml_frontmatter import parse_yaml_front_matter as _parse_yaml_front_matter

#: Every agent front-matter key the pinned Grok Build release accepts (source-only: ``xai-grok-agent/src/config.rs``).
GROK_AGENT_FRONT_MATTER_KEYS = (
    "name", "description", "tools", "disallowedTools", "model", "permissionMode", "effort", "maxTurns",
    "isolation", "background", "mcpServers", "mcpInheritance", "hooks", "memory", "skills", "discoverSkills",
    "inheritSkills", "agentsMd", "promptMode", "injectDefaultTools", "capabilityMode",
)
#: The keys this adapter writes (a subset of the above).
GROK_EMITTED_KEYS = ("name", "description", "tools", "disallowedTools", "injectDefaultTools", "mcpInheritance")
#: Grok's built-in agent types; a project agent with the same name shadows the built-in.
GROK_BUILTIN_AGENT_TYPES = frozenset({"general-purpose", "explore", "plan"})

#: The fixed front-matter lines that make ``tools:`` an exact limit (see the module docstring).
_EXACT_TOOLSET_LINES = ("injectDefaultTools: false", "mcpInheritance: none")

GROK_AGENTS_MD_NOTICE = (
    f"{GROK_AGENTS_MD_MARKER} Grok Build loads AGENTS.md, CLAUDE.md, .claude/CLAUDE.md and "
    ".grok/rules/*.md from the repo root down to the working directory, in a trusted folder only. The "
    "specialist agents are Grok custom agents under .grok/agents/<slug>.md. Other agentteams targets (codex, "
    "goose, agents-md) may share this file; Grok-specific notes live in the grok_runtime block. -->"
)

_AGENTS_MD = "AGENTS.md"
_EXAMPLE_BLOCK_REL = "../examples/AGENTS.grok-block.md"
_NAME_RE = re.compile(r'^name:\s*"?([^"\n]+?)"?\s*$', re.MULTILINE)


def grok_runtime_block() -> str:
    """Return the fenced ``grok_runtime`` block of Grok-specific notes for the shared AGENTS.md.

    Returns:
        The block, BEGIN and END markers included, without a trailing newline.

    Raises:
        Nothing.
    """
    return (
        f"<!-- AGENTTEAMS:BEGIN {GROK_FENCE_ID} v=1 -->\n"
        "## Grok Build Notes\n"
        "\n"
        "- Grok Build loads this file only in a trusted folder. Review `.grok/hooks/`, `.mcp.json` and\n"
        "  `.grok/config.toml` first, then run `grok --trust` once in this repository (trust also turns on\n"
        "  project hooks, MCP servers and skills).\n"
        "- The team's custom agents are in `.grok/agents/`. Subagents cannot spawn subagents (depth one), so run\n"
        "  the orchestrator as the session agent: `grok --agent \"<orchestrator name>\"`.\n"
        "- Grok also loads `CLAUDE.md`, `.claude/CLAUDE.md` and `.claude/agents/`. A `.grok/agents/` file replaces\n"
        "  the `.claude/agents/` agent with the same `name:`; regenerate both teams together so the names stay\n"
        "  identical.\n"
        "- Keep this file tracked in git: Grok documents that gitignored instruction files are skipped.\n"
        f"<!-- AGENTTEAMS:END {GROK_FENCE_ID} -->"
    )


def _front_matter(name: str, description: str, tools: str, disallowed: str | None) -> str:
    """The Grok agent front matter; ``name``/``description`` lines are byte-identical to the Claude adapter's."""
    lines = ["---", f"name: {name}"]
    if description:
        lines.append(f'description: "{description}"')
    lines.append(f"tools: {tools}")
    if disallowed:
        lines.append(f"disallowedTools: {disallowed}")
    lines.extend(_EXACT_TOOLSET_LINES)
    lines.extend(["---", ""])
    return "\n".join(lines)


class GrokAdapter(ClaudeAdapter):
    """Grok Build target: ``.grok/agents/<slug>.md`` custom agents + a co-owned repo-root AGENTS.md."""

    @property
    def framework_id(self) -> str:
        return "grok"

    def render_agent_file(self, content: str, agent_slug: str, manifest: dict[str, Any]) -> str:
        """Produce a Grok Build custom agent from canonical template content.

        Args:
            content: The rendered canonical agent (VS Code-style front matter + body).
            agent_slug: The agent slug.
            manifest: The team manifest.

        Returns:
            The agent file: Grok front matter (``tools:`` translated fail-closed, plus ``injectDefaultTools:
            false`` and ``mcpInheritance: none``) followed by the body with handoffs removed.

        Raises:
            ValueError: The manifest grants the agent an ``agentteams_runner`` server (``write_policy:
                "orchestrator-only"`` is not supported for grok), or the tool list cannot be translated without
                widening (see :func:`._grok_tool_scoping.to_grok_tools`).
        """
        if _runner_grant(agent_slug, manifest) is not None:
            raise ValueError(
                "write_policy 'orchestrator-only' is not supported for --framework grok; no agentteams_runner "
                f"grant can be rendered for {agent_slug!r}"
            )
        name, description = _extract_name_description(content, agent_slug, manifest)
        tools = to_grok_tools(_map_allowed_tools(content))
        _model, disallowed, _permission = _extract_claude_optional_keys(content)
        disallowed = to_grok_tools(disallowed, deny=True) if disallowed else None
        body = self._strip_handoffs_section(self._strip_yaml_front_matter(content))
        return (_front_matter(name, description, tools, disallowed) + body).strip() + "\n"

    def render_instructions_file(self, content: str, manifest: dict[str, Any]) -> str:
        """Render the framework-neutral team AGENTS.md with the Grok notice and the ``grok_runtime`` block.

        Args:
            content: The rendered team instructions.
            manifest: The team manifest.

        Returns:
            The AGENTS.md text. Re-rendering its own output never stacks a second notice.

        Raises:
            Nothing.
        """
        body = self._strip_yaml_front_matter(content)
        body = apply_constitutional_rules_baseline(body, manifest)
        agents_dir = None if manifest.get("interop_source_framework") else ".grok/agents"
        body = _neutralize_instructions(body, agents_dir)
        body = strip_agents_md_notice(body)
        body = re.sub(
            rf"\n*<!--\s*AGENTTEAMS:BEGIN\s+{GROK_FENCE_ID}\s.*?<!--\s*AGENTTEAMS:END\s+{GROK_FENCE_ID}\s*-->\n*",
            "\n\n", body, flags=re.DOTALL,
        )
        return f"{GROK_AGENTS_MD_NOTICE}\n\n{body.strip()}\n\n{grok_runtime_block()}\n"

    def render_builder_file(self, content: str, manifest: dict[str, Any]) -> str:
        """Render the team-builder as a Grok custom agent, with the same exact-toolset keys as every agent.

        Args:
            content: The rendered builder template (canonical ``tools: [...]`` list in its front matter).
            manifest: The team manifest.

        Returns:
            The builder file.

        Raises:
            ValueError: The builder front matter has no canonical ``tools: [...]`` line (it would inherit every
                tool), or its tools cannot be translated.
        """
        yaml_text, body = _parse_yaml_front_matter(content)
        if yaml_text is None or not _YAML_TOOLS_RE.search(yaml_text):
            raise ValueError("the Grok team-builder template needs a canonical `tools: [...]` front-matter line")
        head = content[: len(content) - len(body)]
        replacement = "\n".join([f"tools: {to_grok_tools(_map_allowed_tools(content))}", *_EXACT_TOOLSET_LINES])
        head = _YAML_TOOLS_RE.sub(lambda _m: replacement, head, count=1)
        return head + body

    def framework_root_prefix(self) -> str:
        return ".grok"

    def required_front_matter_keys(self) -> tuple[str, ...]:
        """``tools`` plus the two keys that make it an exact limit; omitting any of them widens the agent."""
        return ("name", "description", "tools", "injectDefaultTools", "mcpInheritance")

    def extra_output_files(self, manifest: dict[str, Any]) -> list[tuple[str, str]]:
        """Return only the framework-neutral baseline (launcher + verify-key sentinel).

        Deliberately skips :meth:`ClaudeAdapter.extra_output_files`: its constitutional hook, settings example
        and runner files are Claude Code artifacts Grok would never read from ``.grok/``. Grok's own sandbox
        profile and hook gate arrive in Phase 2, after @security.

        Args:
            manifest: The team manifest.

        Returns:
            ``(rel_path, content)`` pairs relative to the agents output directory.

        Raises:
            Nothing.
        """
        return FrameworkAdapter.extra_output_files(self, manifest)

    def get_agents_dir(self, project_path: Path) -> Path:
        return project_path / ".grok" / "agents"

    def vscode_tasks_rel_path(self) -> str | None:
        return None

    def normalize_output_path(self, output: Path) -> Path:
        """Append ``.grok/agents`` to a project-root ``--output`` (mirrors codex)."""
        parts = output.parts
        if len(parts) >= 2 and parts[-2] == ".grok" and parts[-1] == "agents":
            return output
        if parts and parts[-1] == ".grok":
            return output / "agents"
        return output / ".grok" / "agents"

    def finalize_output_path(self, rel_path: str, file_type: str) -> str:
        """Place the instructions at the repo-root AGENTS.md (two levels above ``.grok/agents``)."""
        if file_type == "instructions" and rel_path.endswith(COPILOT_INSTRUCTIONS_FILENAME):
            return f"../../{_AGENTS_MD}"
        return FrameworkAdapter.finalize_output_path(self, rel_path, file_type)

    def guard_rendered_files(
        self, rendered_files: list[tuple[str, str]], output_dir: Path
    ) -> tuple[list[tuple[str, str]], list[str]]:
        """Apply the shared-AGENTS.md co-ownership rules (D2) and the name-parity advisories (D4).

        AGENTS.md, by owner (:func:`._agents_md_ownership.classify_agents_md`):

        * absent / Grok-generated: kept as rendered;
        * Codex / agents-md / agentteams-fenced: only the ``grok_runtime`` block is added or refreshed — every
          byte outside it is the file's own;
        * hand-written without fences: never written; the block goes to ``.grok/examples/AGENTS.grok-block.md``
          for the operator to paste once;
        * unsafe (symlink, unreadable, malformed fences, escaping the project): never written.

        Args:
            rendered_files: ``(rel_path, content)`` pairs relative to *output_dir*.
            output_dir: The agents output directory.

        Returns:
            ``(kept_files, notices)``.

        Raises:
            Nothing.
        """
        kept: list[tuple[str, str]] = []
        notices: list[str] = []
        root = Path(os.path.normpath(output_dir)).parent.parent
        linked = _symlinked_below(Path(os.path.normpath(output_dir)), root)
        for rel_path, content in rendered_files:
            target = Path(os.path.normpath(output_dir / rel_path))
            if target.name != _AGENTS_MD or target.parent != root:
                kept.append((rel_path, content))
                continue
            if linked is not None:
                notices.append(f"{target}: left untouched; {linked} is a symlink, so the Grok team dir may not "
                               "be what it appears.")
                continue
            owner = classify_agents_md(target, repo_root=root)
            if owner in (ABSENT, GROK):
                kept.append((rel_path, content))
                continue
            if owner in (CODEX, AGENTS_MD, FENCED):
                label = {CODEX: "codex", AGENTS_MD: "agents-md", FENCED: "agentteams (fenced)"}[owner]
                try:
                    merged = upsert_fenced_block(target.read_text(encoding="utf-8"), GROK_FENCE_ID,
                                                 grok_runtime_block())
                except (OSError, UnicodeDecodeError, ValueError) as exc:
                    notices.append(f"{target}: left untouched ({exc}).")
                    continue
                kept.append((rel_path, merged))
                notices.append(
                    f"{target}: AGENTS.md is owned by the {label} render; Grok reads it as-is. Only the "
                    f"{GROK_FENCE_ID} block is added or refreshed (use --merge or --overwrite to write it)."
                )
                continue
            if owner == FOREIGN:
                kept.append((_EXAMPLE_BLOCK_REL, grok_runtime_block() + "\n"))
                notices.append(
                    f"{target}: hand-written AGENTS.md left untouched. To add the Grok notes, paste the block in "
                    f".grok/examples/AGENTS.grok-block.md into it; later runs refresh only that block."
                )
                continue
            notices.append(
                f"{target}: AGENTS.md is a symlink, unreadable, outside the project, or has malformed AGENTTEAMS "
                "fences; left untouched."
            )
        if output_dir.name == "agents" and output_dir.parent.name == ".grok":
            notices.extend(_parity_notices(kept, Path(os.path.normpath(output_dir)), root))
        return kept, notices


def _symlinked_below(path: Path, root: Path) -> Path | None:
    """The first symlink on the lexical chain from *path* up to (not including) *root*, else None."""
    current = path
    while current != root and current.parent != current:
        if current.is_symlink():
            return current
        current = current.parent
    return None


def _front_matter_name(text: str) -> str | None:
    yaml_text, _ = _parse_yaml_front_matter(text)
    if yaml_text is None:
        return None
    m = _NAME_RE.search(yaml_text)
    return m.group(1).strip() if m else None


def _parity_notices(kept: list[tuple[str, str]], output_dir: Path, root: Path) -> list[str]:
    """Advisories for D4: Grok/Claude name mismatches, unshadowed Claude agents, built-in shadowing, gitignore."""
    notices: list[str] = []
    claude_dir = root / ".claude" / "agents"
    grok_names: set[str] = set()
    for rel_path, content in kept:
        if "/" in rel_path or not rel_path.endswith(".md") or rel_path == "SETUP-REQUIRED.md":
            continue
        name = _front_matter_name(content)
        if name is None:
            continue
        grok_names.add(rel_path)
        if Path(rel_path).stem in GROK_BUILTIN_AGENT_TYPES or name.lower() in GROK_BUILTIN_AGENT_TYPES:
            notices.append(f"{output_dir / rel_path}: name {name!r} shadows Grok Build's built-in agent type.")
        twin = claude_dir / rel_path
        if twin.is_file() and not twin.is_symlink():
            try:
                with twin.open(encoding="utf-8") as fh:
                    twin_name = _front_matter_name(fh.read(4096))
            except (OSError, UnicodeDecodeError):
                twin_name = None
            if twin_name is not None and twin_name != name:
                notices.append(
                    f"{output_dir / rel_path}: Grok name {name!r} differs from the Claude twin's {twin_name!r}, so "
                    "Grok loads both. Regenerate the Claude team (--framework claude --update --merge)."
                )
    if claude_dir.is_dir() and not claude_dir.is_symlink():
        orphans = sorted(p.name for p in claude_dir.glob("*.md")
                         if p.name != "SETUP-REQUIRED.md" and p.name not in grok_names)
        if orphans:
            notices.append(
                f"{claude_dir}: {len(orphans)} Claude agent(s) have no Grok twin and Grok always loads them "
                f"(e.g. {', '.join(orphans[:5])})."
            )
    try:
        ignored = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", _AGENTS_MD],
                                 capture_output=True, timeout=5, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ignored = False
    if ignored:
        notices.append(f"{root / _AGENTS_MD}: AGENTS.md is gitignored; Grok may skip it (keep it tracked).")
    return notices
