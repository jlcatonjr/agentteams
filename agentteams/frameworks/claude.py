"""
claude.py — Framework adapter for Claude Code sub-agents.

Agent files:  .claude/agents/<slug>.md  (Claude Code sub-agent format)
Instructions: CLAUDE.md (merged with agents for smaller teams)
Format:       Claude Code front matter (name, description, tools) + Markdown body
Handoffs:     Inline handoffs removed from prompt body; extracted handoffs can
              be preserved in references/runtime-handoffs.json by the build pipeline

Claude Code sub-agent front matter specification
-------------------------------------------------
Source: https://docs.anthropic.com/en/docs/claude-code/sub-agents

Recognised front matter keys (all optional, but name + description are strongly recommended):
  name:          Display name shown in the agent picker
  description:   When/how to invoke this agent (used for automatic routing)
  tools:         Comma-separated list of Claude tool names the agent may use
                 (Bash, Read, Write, Edit, MultiEdit, Glob, Grep, LS,
                  WebFetch, WebSearch, TodoRead, TodoWrite), optionally with a
                  parenthesised command scope, e.g. Bash(git diff:*).
                 OMITTING THIS KEY INHERITS EVERY TOOL. `allowed-tools` is the
                 slash-command key and is ignored here — emitting it was finding
                 F-1 of the 2026-08-06 constitutional audit.
  disallowedTools: Comma-separated deny list, subtracted from the inherited pool
  model:         Claude model variant (e.g. claude-opus-4-5, claude-sonnet-4-5)

VS Code Copilot keys (user-invokable:, agents:) are NOT recognised by Claude Code and
must NOT be passed through. `tools:` IS recognised, but its VALUE FORMAT differs: copilot
writes an inline quoted list, Claude a bare comma-separated scalar.

External retrieval note: `WebSearch`/`WebFetch` appear in the list above because Claude
Code recognises them, NOT because this adapter grants them — no vocabulary token maps to
either. Generated teams reach the web through the `retrieval` token, which grants a scoped
Bash permission for this package's own research CLI. That is a deliberate standing
constraint, not an oversight; see references/retrieval-transport-policy.md.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .base import FrameworkAdapter
from agentteams.yaml_frontmatter import parse_yaml_front_matter as _parse_yaml_front_matter


# ---------------------------------------------------------------------------
# Claude Code front matter constants
# ---------------------------------------------------------------------------

#: The front-matter key Claude Code actually reads as a subagent capability limit.
#: NOT ``allowed-tools`` — that key belongs to slash commands and is ignored in a subagent
#: file, which silently granted every generated agent the full tool pool until 2026-08-06.
CLAUDE_CAPABILITY_KEY = "tools"

#: The superseded key. Retained as a named constant so the detectors that must still
#: RECOGNISE it (framework detection, front-matter migration) share one definition with
#: the emitter that no longer WRITES it.
CLAUDE_LEGACY_CAPABILITY_KEY = "allowed-tools"

_CLAUDE_DEFAULT_ALLOWED_TOOLS = "Bash, Read, Write, Edit"

# The command the `retrieval` token grants, and nothing else. Scoped `Bash(<prefix>:*)` rather
# than bare `Bash`: an agent that needs to look something up on the web should not thereby gain
# arbitrary shell execution. See references/retrieval-transport-policy.md for why retrieval is
# CLI-mediated here rather than delivered through WebSearch/WebFetch or an MCP server.
#
# Honest limitation: whether a given Claude Code version honours the scoped `Bash(...)` form
# inside SUB-AGENT front matter (as opposed to slash-command front matter, where it is
# long-established) is not verifiable from inside this repository, and this project already
# tracks exactly that class of upstream drift in `agentteams/framework_research.py`. If a host
# were to ignore the parenthesised scope, it would read the entry as plain `Bash` and grant MORE
# than intended. That is why `retrieval` is NOT added to any read-only auditor's tool list: the
# blast radius of the uncertainty is bounded to agents that were already going to hold `execute`
# or that a template author opted in deliberately.
_RETRIEVAL_CLI_SCOPE = "Bash(python -m agentteams.research:*)"

# VS Code Copilot tool → Claude Code allowed-tools. Per-agent scoping matters:
# read-only governance/audit agents (tools: ['read','search']) must NOT receive
# Bash/Write/Edit, or their template-declared read-only invariant becomes false
# in generated Claude teams (least-privilege regression).
_VSCODE_TO_CLAUDE_TOOLS: dict[str, tuple[str, ...]] = {
    "read": ("Read",),
    "search": ("Grep", "Glob"),
    "edit": ("Edit", "Write"),
    "execute": ("Bash",),
    "todo": ("TodoWrite",),
    "agent": ("Task",),
    "retrieval": (_RETRIEVAL_CLI_SCOPE,),
}

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
        # Map the per-agent tool scope BEFORE the VS Code front matter is stripped.
        allowed_tools = _map_allowed_tools(content)
        content = self._strip_yaml_front_matter(content)
        content = self._strip_handoffs_section(content)
        content = _inject_claude_front_matter(content, name, description, allowed_tools)
        return content.strip() + "\n"

    def render_instructions_file(self, content: str, manifest: dict[str, Any]) -> str:
        return content

    def render_skill_file(self, content: str, slug: str, manifest: dict[str, Any]) -> str:
        """Produce a Claude Code skill file from an operational tool-doc body.

        Tool docs are authored without front matter (so the Copilot target can
        reuse them verbatim as reference docs). For Claude we strip any stray
        front matter / handoffs and prepend a minimal skill front matter block
        (name + description) — the same flat-file shape as the recall and
        todo-from-plan skills.
        """
        content = self._strip_yaml_front_matter(content)
        content = self._strip_handoffs_section(content)
        description = _skill_description(slug, manifest)
        return _inject_skill_front_matter(content, slug, description).strip() + "\n"

    def get_file_extension(self, file_type: str) -> str:
        return ".md"

    def supports_handoffs(self) -> bool:
        return False

    def required_front_matter_keys(self) -> tuple[str, ...]:
        """Claude subagent headers carry `tools`, not copilot-vscode's `tools`/`model` pair.

        The key is `tools` and not `allowed-tools`: Claude Code's subagent front-matter
        schema defines `name`, `description`, `tools`, `disallowedTools`, `model` and
        `permissionMode`, and documents that omitting `tools` makes the subagent "inherit
        every tool available to subagents". `allowed-tools` is the *slash-command* key and
        is silently ignored in a subagent file — so emitting it declared a capability limit
        that the runtime never applied, which is a C-3 violation rather than a cosmetic
        naming choice. See references/plans/constitutional-redteam-audit-2026-08-06.report.md
        finding F-1.
        """
        return ("name", "description", "tools")

    def handoff_delivery_mode(self) -> str:
        return "manifest"

    def extra_output_files(self, manifest: dict[str, Any]) -> list[tuple[str, str]]:
        """Ship the constitutional PreToolUse hook with every generated Claude team.

        The hook is what gives C-5 any reach over agent tool calls: without it, the
        destructive-action gate guards four CLI entry points and an agent using ``Write`` or
        ``Bash`` never touches it (2026-08-06 audit, probe E3).

        Two files, landing outside the agents dir (``../`` → ``.claude/``):

        * ``hooks/constitutional-gate.py`` — the hook itself, executable content;
        * ``settings.hooks.example.json`` — the block an operator merges into their own
          ``settings.json``.

        **The settings file is deliberately NOT written.** That is the standing convention in
        ``hooks_emit.py`` and it is right: clobbering an operator's config is a worse failure
        than an unwired hook. It does mean the hook arrives inert until someone wires it, which
        is stated in the example's own comment block rather than left to be discovered.
        """
        hook = _read_template_asset("hooks/constitutional-gate.py")
        example = _read_template_asset("hooks/settings.hooks.example.json")
        if not hook:
            return []
        files = [("../hooks/constitutional-gate.py", hook)]
        if example:
            files.append(("../settings.hooks.example.json", example))
        return files

    def get_agents_dir(self, project_path: Path) -> Path:
        return project_path / ".claude" / "agents"

    def vscode_tasks_rel_path(self) -> str | None:
        return "../../.vscode/tasks.json"

    def finalize_output_path(self, rel_path: str, file_type: str) -> str:
        """Map generic planned paths to Claude-native file names/locations."""
        if file_type == "instructions" and rel_path.endswith("copilot-instructions.md"):
            return "../CLAUDE.md"
        return super().finalize_output_path(rel_path, file_type)



def _read_template_asset(rel_path: str) -> str:
    """Return a shipped template asset's text, or "" when it is absent.

    Absent rather than raising: a source checkout missing an optional asset should degrade to
    "no hook emitted" rather than break generation. The consequence — a team that silently gets
    no hook — is covered by tests/test_constitutional_gate_hook.py asserting the asset exists.
    """
    asset = Path(__file__).resolve().parents[1] / "templates" / "universal" / rel_path
    try:
        return asset.read_text(encoding="utf-8")
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Claude Code front matter helpers
# ---------------------------------------------------------------------------

# Matches a single-line YAML scalar: key: value (with optional surrounding quotes)
_YAML_SCALAR_RE = re.compile(r'^([a-zA-Z][a-zA-Z0-9_-]*)\s*:\s*"?([^"\n]+)"?\s*$', re.MULTILINE)

# Captures an inline-list `tools: ['read', 'search']` line in the VS Code front matter.
_YAML_TOOLS_RE = re.compile(r"^tools:\s*\[([^\]]*)\]\s*$", re.MULTILINE)


def _map_allowed_tools(content: str) -> str:
    """Map an agent's VS Code ``tools:`` list to a Claude ``allowed-tools`` value.

    Falls back to the blanket default only when no recognizable ``tools:`` block
    is present, so an agent authored without a tool scope keeps working.
    """
    yaml_text, _ = _parse_yaml_front_matter(content)
    if yaml_text is None:
        return _CLAUDE_DEFAULT_ALLOWED_TOOLS
    tools_match = _YAML_TOOLS_RE.search(yaml_text)
    if not tools_match:
        return _CLAUDE_DEFAULT_ALLOWED_TOOLS
    vscode_tools = [item.strip().strip("'\"") for item in tools_match.group(1).split(",") if item.strip()]
    mapped: list[str] = []
    for vt in vscode_tools:
        for claude_tool in _VSCODE_TO_CLAUDE_TOOLS.get(vt.lower(), ()):
            if claude_tool not in mapped:
                mapped.append(claude_tool)
    # `execute` already grants unrestricted Bash, which subsumes the scoped retrieval command.
    # Emitting both would read as though the scope constrained something when it constrains
    # nothing — a misleading least-privilege signal in the generated file.
    if "Bash" in mapped and _RETRIEVAL_CLI_SCOPE in mapped:
        mapped.remove(_RETRIEVAL_CLI_SCOPE)
    return ", ".join(mapped) if mapped else _CLAUDE_DEFAULT_ALLOWED_TOOLS


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

    yaml_body, _ = _parse_yaml_front_matter(content)
    if yaml_body is not None:
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


def _inject_claude_front_matter(
    content: str,
    name: str,
    description: str,
    allowed_tools: str = _CLAUDE_DEFAULT_ALLOWED_TOOLS,
) -> str:
    """Prepend a Claude Code-compatible YAML front matter block to content.

    The capability key is ``tools`` (comma-separated, e.g. ``tools: Read, Glob, Grep``).
    It was ``allowed-tools`` until 2026-08-06; that key is not part of Claude Code's
    subagent schema, so every grant emitted under it was inert and every agent inherited
    the full tool pool. See :meth:`ClaudeAdapter.required_front_matter_keys`.
    """
    lines = ["---", f"name: {name}"]
    if description:
        lines.append(f'description: "{description}"')
    lines.append(f"{CLAUDE_CAPABILITY_KEY}: {allowed_tools}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + content


def _skill_description(slug: str, manifest: dict[str, Any]) -> str:
    """Build a one-line skill description from the tool-doc spec, if present."""
    tool_name = ""
    for ta in manifest.get("tool_agents", []):
        if ta.get("slug") == slug:
            tool_name = ta.get("tool_name", "")
            break
    label = tool_name or FrameworkAdapter._slug_to_name(slug)
    project = manifest.get("project_name", "")
    suffix = f" in {project}" if project else ""
    return (
        f"{label} operational reference{suffix} — configuration, API surface, "
        f"invocation, and verification. Consult when working with {label}."
    )


def _inject_skill_front_matter(content: str, slug: str, description: str) -> str:
    """Prepend a Claude Code skill front matter block (name + description)."""
    lines = ["---", f"name: {slug}"]
    if description:
        # Escape embedded double quotes so the YAML scalar stays well-formed.
        safe = description.replace('"', '\\"')
        lines.append(f'description: "{safe}"')
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + content


