"""
grok.py — Framework adapter for xAI Grok Build (the ``grok`` CLI).

Agent files:  .grok/agents/<slug>.md  (Grok Build custom agents: YAML front matter + Markdown body)
Instructions: AGENTS.md (repo root), shared with codex / goose / agents-md and co-owned merge-only
Format:       Claude Code-compatible front matter (Grok Build is a Claude Code superset) + two Grok keys
Handoffs:     Removed from the body; kept in references/runtime-handoffs.json (``manifest`` delivery)

Grok Build facts this adapter relies on (the release pinned in ``FORMAT_SPECS["grok"].pinned_version``; see references/grok-agent-infrastructure-expert.md
and tmp/by-week/2026-W42/grok-integration.plan.md §9 for evidence):

* Custom agents load from ``.grok/agents/*.md``; ``.claude/agents/*.md`` load too, always (no compatibility
  switch turns them off, and folder trust does not gate them). ``inspect`` lists one agent per ``name:`` (spike S2),
  but which body runs for a shared name is not confirmed at runtime (G15), so every ``name:`` here is byte-identical
  to the Claude render (D4) and both teams are regenerated together. Custom agents run by definition path
  (``grok --agent .grok/agents/<slug>.md``) and cannot be spawned as subagents on the pinned release (G8).
* ``tools:`` takes bare Claude tool names (``Read``, ``Bash``…; ``Agent`` for spawning); scopes and ``MCPTool``
  are never emitted, and denies are applied by absence (G11, G18, G19; ``_grok_tool_scoping``). An EMPTY list means
  "inherit every tool", and ``injectDefaultTools`` defaults to true, layering session tools on top of the list,
  and ``mcpInheritance`` defaults to ``all`` (source-only: ``xai-grok-agent/src/config.rs``). Every agent
  therefore carries ``injectDefaultTools: false`` and ``mcpInheritance: none`` so ``tools:`` is the exact limit
  (C-3; @security step-6 ruling).
* Instruction files load only in a trusted folder; Grok reads ``AGENTS.md``, ``CLAUDE.md`` and
  ``.claude/CLAUDE.md`` together. The team brief goes in the shared ``AGENTS.md`` (operator decision D2), whose
  ownership rules live in :mod:`._agents_md_ownership`.

Codex posture (Phase 3): an interop target and source and an absorb source (``agentteams/_interop_grok.py``);
convert and bridge refuse ``grok`` (``cli/parser_validate``), and ``write_policy: "orchestrator-only"`` is refused
(``write_policy.py``).
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import sys
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
    GOOSE,
    GROK,
    GROK_AGENTS_MD_MARKER,
    GROK_FENCE_ID,
    classify_agents_md,
    fence_pattern,
    strip_agents_md_notice,
    symlink_on_path,
    upsert_fenced_block,
)
from ._grok_tool_scoping import EXACT_TOOLSET_LINES, effective_grok_tools, split_tool_list, translate_grok_tools
from agentteams.capability_map import _RETRIEVAL_CLI_SCOPE, grok_widening_entries
from .format_spec import COPILOT_INSTRUCTIONS_FILENAME, FORMAT_SPECS
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

GROK_AGENTS_MD_NOTICE = (
    f"{GROK_AGENTS_MD_MARKER} Grok Build loads AGENTS.md, CLAUDE.md, .claude/CLAUDE.md and "
    ".grok/rules/*.md from the repo root down to the working directory, in a trusted folder only. The "
    "specialist agents are Grok custom agents under .grok/agents/<slug>.md. Other agentteams targets (codex, "
    "goose, agents-md) may share this file; Grok-specific notes live in the grok_runtime block. -->"
)

_AGENTS_MD = "AGENTS.md"
#: Emitted so ``.grok/hooks`` always exists: the launcher protects ``.grok`` present-only, and a missing dir is
#: one a confined process could create and fill (@security step-12b, P2-A). Grok loads only ``*.json`` there.
_HOOKS_README_REL = "../hooks/README.md"
_HOOKS_README = (
    "# .grok/hooks\n\n"
    "Grok Build loads `*.json` hook files from this directory after the folder is trusted (`grok --trust`).\n"
    "agentteams keeps the directory present so the confinement launcher protects it; any hook file here that\n"
    "you did not write should be treated as suspect. Grok hooks fail open (a timeout or crash lets the call\n"
    "through), so they are advisory, never enforcement.\n"
)
_EXAMPLE_BLOCK_REL = "../examples/AGENTS.grok-block.md"
_NAME_RE = re.compile(r"""^name:\s*(?:"([^"\n]*)"|'([^'\n]*)'|([^\n]*?))\s*$""", re.MULTILINE)


def grok_runtime_block() -> str:
    """Return the fenced ``grok_runtime`` block of Grok-specific notes for the shared AGENTS.md.

    Returns:
        The block, BEGIN and END markers included, without a trailing newline.

    Raises:
        Nothing.
    """
    pinned = FORMAT_SPECS["grok"].pinned_version
    return (
        f"<!-- AGENTTEAMS:BEGIN {GROK_FENCE_ID} v=2 -->\n"
        "## Grok Build Notes\n"
        "\n"
        "- Grok Build loads this file only in a trusted folder. Review `.grok/hooks/`, `.grok/workflows/`,\n"
        "  `.grok/plugins/`, `.grok/lsp*`, `.mcp.json` and `.grok/config.toml` first, then run `grok --trust`\n"
        "  once in this repository (trust also turns on project hooks, MCP and LSP servers, and skills).\n"
        "- The team's custom agents are in `.grok/agents/`. Run one as the session agent by its file path:\n"
        f"  `grok --agent .grok/agents/<slug>.md` (in the TUI, pick it with `/agents`). In Grok Build {pinned} the\n"
        "  name form (`grok --agent \"<name>\"`, `GROK_AGENT`) silently runs the default agent instead.\n"
        f"- Grok Build {pinned} cannot spawn the team's custom agents as subagents (\"Unknown subagent type\"), so the\n"
        "  orchestrator cannot delegate to them: run each specialist directly, as above. If a spawn fails, never\n"
        "  retry it without a type: an untyped spawn starts a general-purpose agent that has every tool.\n"
        "- An agent whose `tools:` holds `Bash` has a full shell, which can write files whatever its other tools\n"
        "  are. An agent holding `Agent` can start an untyped, all-tool subagent.\n"
        "- Grok also loads `CLAUDE.md`, `.claude/CLAUDE.md` and `.claude/agents/`. Which body runs when a\n"
        "  `.grok/agents/` and a `.claude/agents/` agent share a `name:` is not confirmed; regenerate both teams\n"
        "  together so the two copies stay identical.\n"
        "- Keep this file tracked in git: Grok documents that gitignored instruction files are skipped\n"
        "  (behaviour on case-insensitive filesystems varies).\n"
        f"<!-- AGENTTEAMS:END {GROK_FENCE_ID} -->"
    )


#: Manifest key (underscore: excluded from the fingerprint) collecting every scoped entry a grok render dropped or widened;
#: build_team.py records it in the build log as ``grok_tool_notices``.
GROK_TOOL_NOTICES_KEY = "_grok_tool_notices"


def _record_tool_changes(manifest: dict[str, Any], slug: str, changes: list[str]) -> None:
    """Tell the operator, on stderr and in the manifest, about each scoped entry a grok render changed.

    Args:
        manifest: The team manifest (mutated: :data:`GROK_TOOL_NOTICES_KEY`).
        slug: The agent the change applies to.
        changes: Lines from :func:`._grok_tool_scoping.translate_grok_tools`.

    Raises:
        Nothing.
    """
    seen = manifest.setdefault(GROK_TOOL_NOTICES_KEY, [])
    for change in changes:
        effect = "; the agent loses the retrieval CLI on grok" if _RETRIEVAL_CLI_SCOPE in change else ""
        notice = f"{slug}: {change}{effect}"
        if notice not in seen:
            seen.append(notice)
            print(f"  [GROK-SCOPE] {notice}", file=sys.stderr)


def _translate_agent_tools(manifest: dict[str, Any], slug: str, allow: str, deny: str | None) -> tuple[str, str | None]:
    """Translate an agent's allow and deny lists for grok, applying the deny by subtraction.

    The pinned Grok Build release ignores ``disallowedTools`` (grok-scope-verify d/d2: ``Read, Bash`` with ``disallowedTools: Bash``
    was still offered the shell). The only limit it honours is absence from ``tools:``, so every denied tool is left
    out of the emitted allow-list; the ``disallowedTools:`` line is kept as a record of intent, not as enforcement.

    Args:
        manifest: The team manifest (notices are recorded on it).
        slug: The agent slug.
        allow: The Claude-style ``tools`` value.
        deny: The Claude-style ``disallowedTools`` value, or None.

    Returns:
        ``(tools, disallowed)`` in grok's vocabulary; ``tools`` already excludes every denied entry.

    Raises:
        ValueError: A list cannot be translated, or the deny-list leaves no built-in tool (an empty ``tools:`` list
            inherits every tool; @security grok-scope-fix C2).
    """
    tools, changes = translate_grok_tools(allow)
    disallowed = None
    if deny:
        disallowed, deny_changes = translate_grok_tools(deny, deny=True)
        changes = changes + deny_changes
    if not effective_grok_tools(tools, disallowed):
        raise ValueError(
            f"{slug}: no built-in tool that grants anything is left in ({tools})"
            + (f" after disallowedTools ({disallowed})" if disallowed else "")
            + " for grok (Write, WebFetch, WebSearch and MCP entries grant nothing there): an empty effective toolset "
            "(an empty tools: list inherits every tool), so the render is refused"
        )
    if disallowed:
        denied = set(split_tool_list(disallowed))
        mcp_patterns = [d for d in denied if d.startswith("mcp__")]

        def _is_denied(entry: str) -> bool:  # an MCP wildcard deny (mcp__srv__*) removes the matching allow entries
            return entry in denied or (entry.startswith("mcp__") and any(fnmatch.fnmatchcase(entry, p) for p in mcp_patterns))

        kept = [t for t in split_tool_list(tools) if not _is_denied(t)]
        removed = [t for t in split_tool_list(tools) if _is_denied(t)]
        if removed:
            changes.append(f"left {', '.join(removed)} out of tools: (grok ignores disallowedTools, so the deny is applied "
                           "by absence)")
            tools = ", ".join(kept)
    _record_tool_changes(manifest, slug, changes)
    return tools, disallowed


def _front_matter(name: str, description: str, tools: str, disallowed: str | None) -> str:
    """The Grok agent front matter; ``name``/``description`` lines are byte-identical to the Claude adapter's."""
    lines = ["---", f"name: {name}"]
    if description:
        lines.append(f'description: "{description}"')
    lines.append(f"tools: {tools}")
    if disallowed:
        lines.append(f"disallowedTools: {disallowed}")
    lines.extend(EXACT_TOOLSET_LINES)
    lines.extend(["---", ""])
    return "\n".join(lines)


class GrokAdapter(ClaudeAdapter):
    """Grok Build target: ``.grok/agents/<slug>.md`` custom agents + a co-owned repo-root AGENTS.md."""

    @property
    def framework_id(self) -> str:
        """Return ``"grok"``."""
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
                widening (see :func:`._grok_tool_scoping.translate_grok_tools`); a scoped entry is dropped (allow) or
                widened to the whole tool (deny), with a ``[GROK-SCOPE]`` notice; or the deny-list empties the toolset.
        """
        if _runner_grant(agent_slug, manifest) is not None:
            raise ValueError(
                "write_policy 'orchestrator-only' is not supported for --framework grok; no agentteams_runner "
                f"grant can be rendered for {agent_slug!r}"
            )
        name, description = _extract_name_description(content, agent_slug, manifest)
        _model, disallowed, _permission = _extract_claude_optional_keys(content)
        tools, disallowed = _translate_agent_tools(manifest, agent_slug, _map_allowed_tools(content), disallowed)
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
        body = fence_pattern(GROK_FENCE_ID).sub("", body)
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
        tools, _ = _translate_agent_tools(manifest, "team-builder", _map_allowed_tools(content), None)
        replacement = "\n".join([f"tools: {tools}", *EXACT_TOOLSET_LINES])
        head = _YAML_TOOLS_RE.sub(lambda _m: replacement, head, count=1)
        return head + body

    def framework_root_prefix(self) -> str:
        """Return ``.grok``: tool docs render as skills under ``.grok/skills/<slug>/SKILL.md``.

        Returns:
            The project-relative framework root.

        Raises:
            Nothing.
        """
        return ".grok"

    def required_front_matter_keys(self) -> tuple[str, ...]:
        """Return the keys every Grok agent must carry; omitting any of the last three widens the agent.

        Returns:
            ``name``, ``description``, ``tools`` and the two exact-toolset keys.

        Raises:
            Nothing.
        """
        return ("name", "description", "tools", "injectDefaultTools", "mcpInheritance")

    def extra_output_files(self, manifest: dict[str, Any]) -> list[tuple[str, str]]:
        """Return the framework-neutral baseline (launcher + verify-key sentinel) plus ``.grok/hooks/README.md``.

        Deliberately skips :meth:`ClaudeAdapter.extra_output_files`: its constitutional hook, settings example
        and runner files are Claude Code artifacts Grok would never read from ``.grok/``. When confinement is
        requested, :func:`._grok_sandbox_emit.grok_sandbox_output_files` adds the Grok sandbox profile, the
        confined-run launcher wrapper and the advisory hook gate (shim + gate + hook JSON).

        Args:
            manifest: The team manifest.

        Returns:
            ``(rel_path, content)`` pairs relative to the agents output directory.

        Raises:
            Nothing.
        """
        from agentteams.frameworks._grok_sandbox_emit import grok_sandbox_output_files

        return [*FrameworkAdapter.extra_output_files(self, manifest), (_HOOKS_README_REL, _HOOKS_README),
                *grok_sandbox_output_files(manifest)]

    def get_agents_dir(self, project_path: Path) -> Path:
        """Return ``<project>/.grok/agents``.

        Args:
            project_path: The project root.

        Returns:
            The default agents directory.

        Raises:
            Nothing.
        """
        return project_path / ".grok" / "agents"

    def vscode_tasks_rel_path(self) -> str | None:
        """Return None: no ``.vscode/tasks.json`` for Grok teams (Claude's adapter emits one).

        Returns:
            None.

        Raises:
            Nothing.
        """
        return None

    def normalize_output_path(self, output: Path) -> Path:
        """Append ``.grok/agents`` to a project-root ``--output`` (mirrors codex).

        Args:
            output: The ``--output`` path.

        Returns:
            The agents directory under *output*.

        Raises:
            Nothing.
        """
        parts = output.parts
        if len(parts) >= 2 and parts[-2] == ".grok" and parts[-1] == "agents":
            return output
        if parts and parts[-1] == ".grok":
            return output / "agents"
        return output / ".grok" / "agents"

    def finalize_output_path(self, rel_path: str, file_type: str) -> str:
        """Place the instructions at the repo-root AGENTS.md (two levels above ``.grok/agents``).

        Args:
            rel_path: The planned path, relative to the agents dir.
            file_type: The planned file's type.

        Returns:
            ``../../AGENTS.md`` for the instructions file; otherwise the base mapping (Claude's
            ``CLAUDE.md`` mapping is deliberately skipped).

        Raises:
            Nothing.
        """
        if file_type == "instructions" and rel_path.endswith(COPILOT_INSTRUCTIONS_FILENAME):
            return f"../../{_AGENTS_MD}"
        return FrameworkAdapter.finalize_output_path(self, rel_path, file_type)

    def guard_rendered_files(
        self, rendered_files: list[tuple[str, str]], output_dir: Path
    ) -> tuple[list[tuple[str, str]], list[str]]:
        """Apply the shared-AGENTS.md co-ownership rules (D2) and the name-parity advisories (D4).

        AGENTS.md, by owner (:func:`._agents_md_ownership.classify_agents_md`):

        * absent / Grok-generated: kept as rendered;
        * Codex / Goose / agents-md / agentteams-fenced: only the ``grok_runtime`` block is added or refreshed —
          every byte outside it is the file's own;
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
        out_dir = Path(os.path.normpath(output_dir))
        root = out_dir.parent.parent
        linked = symlink_on_path(out_dir, root)
        for rel_path, content in rendered_files:
            target = Path(os.path.normpath(output_dir / rel_path))
            if target.name != _AGENTS_MD or target.parent != root:
                refusal = _owned_file_refusal(rel_path, target, root)
                if refusal:
                    notices.append(refusal)
                else:
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
            if owner in (CODEX, GOOSE, AGENTS_MD, FENCED):
                label = {CODEX: "codex", GOOSE: "goose", AGENTS_MD: "agents-md", FENCED: "agentteams (fenced)"}[owner]
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
                # The file itself too: emit resolves links, so a planted one would redirect the write.
                examples = symlink_on_path(Path(os.path.normpath(out_dir / _EXAMPLE_BLOCK_REL)), root)
                if examples is not None:
                    notices.append(f"{target}: hand-written AGENTS.md left untouched; {examples} is a symlink, so "
                                   "no example block was written.")
                    continue
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
        if out_dir.name == "agents" and out_dir.parent.name == ".grok":
            notices.extend(parity_notices(kept, out_dir, root))
            notices.extend(stale_scope_notices(out_dir))
        # A refused (operator-owned) hooks .gitignore means no pin either: the machine path would be committable.
        from agentteams.frameworks.claude import guard_gate_pin_files

        kept, pin_notices = guard_gate_pin_files(kept, output_dir)
        return kept, [*notices, *pin_notices]


def _owned_file_refusal(rel_path: str, target: Path, root: Path) -> str | None:
    """Return why an emitted file outside the agents dir must not be written, else None.

    Args:
        rel_path: The emitted path, relative to the agents dir.
        target: The lexically normalised destination.
        root: The project root.

    Returns:
        A notice when the destination (or a parent) is a symlink, or when it is an existing profile, runner,
        hook JSON, shim or gate copy that agentteams did not write; otherwise None.

    Raises:
        Nothing.
    """
    from agentteams.frameworks._grok_sandbox_emit import (
        GROK_GATE_JSON_REL,
        GROK_GATE_PIN_IGNORE_REL,
        GROK_GATE_REL,
        GROK_GATE_SHIM_REL,
        GROK_GATE_SIGNATURE,
        GROK_OWNED_MARKER,
        GROK_RUNNER_REL,
        GROK_SANDBOX_PROFILE_REL,
    )

    if not rel_path.startswith("../"):
        return None
    linked = symlink_on_path(target, root)
    if linked is not None:
        return f"{target}: left untouched; {linked} is a symlink."
    # The gate copy is the unmodified Claude template, so it is recognised by its own docstring, not the marker.
    signature = {GROK_GATE_REL: GROK_GATE_SIGNATURE}
    marked = (GROK_SANDBOX_PROFILE_REL, GROK_RUNNER_REL, GROK_GATE_JSON_REL, GROK_GATE_SHIM_REL, GROK_GATE_REL,
              GROK_GATE_PIN_IGNORE_REL)
    if rel_path in marked and target.is_file():
        try:
            with target.open(encoding="utf-8") as fh:
                head = fh.read(400)
        except (OSError, UnicodeDecodeError):
            head = ""
        if signature.get(rel_path, GROK_OWNED_MARKER) not in head:
            return (f"{target}: existing file is not agentteams-managed; left untouched (remove it, or add "
                    f"'{GROK_OWNED_MARKER}' to its first lines, to let agentteams regenerate it).")
    return None


def front_matter_name(text: str) -> str | None:
    """Return the ``name:`` value from an agent file's YAML front matter (quoted or bare).

    Args:
        text: The agent file text (only its front matter is read; it is data, never instructions).

    Returns:
        The name, or None when there is no front matter or no ``name:`` key.

    Raises:
        Nothing.
    """
    yaml_text, _ = _parse_yaml_front_matter(text)
    if yaml_text is None:
        return None
    m = _NAME_RE.search(yaml_text)
    if not m:
        return None
    return next(g for g in m.groups() if g is not None).strip() or None


def stale_scope_notices(agents_dir: Path) -> list[str]:
    """Name existing grok agent files whose ``tools:`` / ``disallowedTools:`` lists grok widens.

    The shapes are those :func:`agentteams.capability_map.grok_widening_entries` reports: a scoped entry or ``MCPTool``
    (grok discards the list), a tool both allowed and denied (grok ignores the deny), a duplicated key or an unbalanced
    parenthesis (GROK-FU-R8-SCOPE). This run's render drops a scoped allow entry and
    widens a scoped deny, but ``--update --merge`` keeps an edited file's front matter and never auto-applies a deny
    change (not a strict narrowing), so a deployed team can keep the widening line after the update.

    Args:
        agents_dir: The ``.grok/agents`` directory (read only; a symlinked file is skipped).

    Returns:
        One notice per affected file.

    Raises:
        Nothing.
    """
    found: list[str] = []
    if not agents_dir.is_dir():
        return found
    for path in sorted(agents_dir.glob("*.md")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            yaml_text, _ = _parse_yaml_front_matter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            found.append(f"[GROK-SCOPE] {path}: could not be read to check for scoped tool entries ({exc}).")
            continue
        problems = grok_widening_entries(yaml_text or "")
        if problems:
            found.append(
                f"[GROK-SCOPE] {path}: existing grok agent has {'; '.join(problems)}, so grok offers it more than its "
                "list. This run's render removes these, but --update --merge keeps an edited file's line and never "
                "auto-applies a deny change: check the file after this run, or run "
                "--reconcile-front-matter --reconcile-apply."
            )
    return found


def parity_notices(kept: list[tuple[str, str]], output_dir: Path, root: Path) -> list[str]:
    """Return the D4 advisories: Grok/Claude name mismatches, unshadowed Claude agents, built-in shadowing, gitignore."""
    notices: list[str] = []
    claude_dir = root / ".claude" / "agents"
    grok_files: set[str] = set()
    for rel_path, content in kept:
        if "/" in rel_path or not rel_path.endswith(".md") or rel_path == "SETUP-REQUIRED.md":
            continue
        name = front_matter_name(content)
        if name is None:
            continue
        grok_files.add(rel_path)
        if Path(rel_path).stem in GROK_BUILTIN_AGENT_TYPES or name.lower() in GROK_BUILTIN_AGENT_TYPES:
            notices.append(f"{output_dir / rel_path}: name {name!r} shadows Grok Build's built-in agent type.")
        twin = claude_dir / rel_path
        if twin.is_file() and not twin.is_symlink():
            try:
                with twin.open(encoding="utf-8") as fh:
                    twin_name = front_matter_name(fh.read(4096))
            except (OSError, UnicodeDecodeError):
                twin_name = None
            if twin_name is not None and twin_name != name:
                notices.append(
                    f"{output_dir / rel_path}: Grok name {name!r} differs from the Claude twin's {twin_name!r}, so "
                    "Grok loads both. Regenerate the Claude team (--framework claude --update --merge)."
                )
    if claude_dir.is_dir() and not claude_dir.is_symlink():
        orphans = sorted(p.name for p in claude_dir.glob("*.md")
                         if p.name != "SETUP-REQUIRED.md" and p.name not in grok_files)
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
