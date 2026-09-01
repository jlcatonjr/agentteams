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

VS Code Copilot keys (user-invocable:, agents:) are NOT recognised by Claude Code and
must NOT be passed through. `tools:` IS recognised, but its VALUE FORMAT differs: copilot
writes an inline quoted list, Claude a bare comma-separated scalar.

External retrieval note: `WebSearch`/`WebFetch` appear in the list above because Claude
Code recognises them, NOT because this adapter grants them — no vocabulary token maps to
either. Generated teams reach the web through the `retrieval` token, which grants a scoped
Bash permission for this package's own research CLI. That is a deliberate standing
constraint, not an oversight; see references/retrieval-transport-policy.md.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .base import FrameworkAdapter
from ._sandbox_emit import (  # re-exported so existing importers keep resolving these from claude
    _DEFAULT_PROTECTED_READ_PATHS,
    _READ_EXCLUSION_ABSPATH_COMMENT_LINES,
    _READ_EXCLUSION_COMMENT_LINES,
    _SANDBOX_COMMENT_LINES,
    _build_sandbox_block,
    _exclusive_read_deny_paths,
    _inject_sandbox_block,
    _sandbox_feature_enabled,
)
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

#: DELIBERATE narrow-by-default (C1, decided 2026-08-15 —
#: references/claude-agent-infrastructure-expert.md): current upstream docs
#: state that omitting `tools:` entirely grants inheritance of every tool
#: available to sub-agents. This fallback default is applied only on the
#: no-front-matter / no-tools-block / empty-mapping paths in
#: `_map_allowed_tools` below — never on a normal render with a real tools
#: vocabulary — and intentionally stays narrower than upstream's inherit-all
#: semantics: a fallback path is exactly where a silent capability grant is
#: least likely to be reviewed, and this project's own C-3 ("capability
#: declarations are binding, widening one is privileged") favors the
#: conservative reading when the source data gives no explicit signal.
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
        1. Extract name, description, and optional Claude-specific keys
           (model, disallowedTools, permissionMode) from the YAML front matter
           (if present).
        2. Map the per-agent tool scope BEFORE the front matter is stripped.
        3. Strip the VS Code YAML front matter entirely (keys are incompatible).
        4. Strip handoffs sections (Claude Code does not support them).
        5. Prepend a Claude Code-compatible front matter block, including the
           optional keys captured in step 1 (A.5).
        """
        name, description = _extract_name_description(content, agent_slug, manifest)
        # Map the per-agent tool scope BEFORE the VS Code front matter is stripped.
        allowed_tools = _map_allowed_tools(content)
        # A.5: Extract optional Claude-specific keys before stripping front matter.
        model, disallowed_tools, permission_mode = _extract_claude_optional_keys(content)
        content = self._strip_yaml_front_matter(content)
        content = self._strip_handoffs_section(content)
        content = _inject_claude_front_matter(
            content, name, description, allowed_tools,
            model=model,
            disallowed_tools=disallowed_tools,
            permission_mode=permission_mode,
        )
        return content.strip() + "\n"

    def render_instructions_file(self, content: str, manifest: dict[str, Any]) -> str:
        return content

    def render_skill_file(self, content: str, slug: str, manifest: dict[str, Any]) -> str:
        """Produce a Claude Code skill file from an operational tool-doc body.

        Tool docs are authored without front matter (so the Copilot target can
        reuse them verbatim as reference docs). For Claude we strip any stray
        front matter / handoffs and prepend a minimal skill front matter block
        (name + description) — the same shape as the recall and todo-from-plan
        skills.

        This renders the *body* only. The caller writes it to
        ``.claude/skills/<slug>/SKILL.md``: Claude Code discovers a project
        skill only as a directory containing ``SKILL.md``, and the directory
        name — not the ``name:`` key — is the invocable command name.
        See https://code.claude.com/docs/en/skills.md.
        """
        content = self._strip_yaml_front_matter(content)
        content = self._strip_handoffs_section(content)
        description = _skill_description(slug, manifest)
        return _inject_skill_front_matter(content, slug, description).strip() + "\n"

    def has_skill_concept(self) -> bool:
        return True

    def tool_doc_rel_path(self, slug: str) -> str:
        # Directory-per-skill: Claude Code loads only `<name>/SKILL.md`.
        return f"skills/{slug}/SKILL.md"

    def framework_root_prefix(self) -> str:
        return ".claude"

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

        When the ``claude:sandbox`` host feature is active, the emitted settings example also
        carries a ``sandbox`` block (workspace write-confinement). It follows the SAME
        inert-until-merged convention — the operator must merge it into their own
        ``settings.json`` for the OS-level boundary to take effect. See
        :func:`_inject_sandbox_block`.

        Args:
            manifest: The team manifest. ``host_features`` gates the sandbox block;
                ``workspace_write_roots`` (optional) overrides the confined roots.

        Returns:
            List of ``(relative_path, content)`` tuples for the emit phase to write.
        """
        # Framework-neutral baseline first (the Linux confinement launcher on linux); the
        # constitutional hook below is Claude-specific and layered on top.
        files = super().extra_output_files(manifest)
        hook = _read_template_asset("hooks/constitutional-gate.py")
        example = _read_template_asset("hooks/settings.hooks.example.json")
        if not hook:
            return files
        files.append(("../hooks/constitutional-gate.py", _apply_fail_closed_policy(hook, manifest)))
        if example:
            if _sandbox_feature_enabled(manifest):
                example = _inject_sandbox_block(
                    example,
                    manifest.get("workspace_write_roots") or None,
                    _exclusive_read_deny_paths(manifest),
                    deny_read_resolved_abspath=bool(
                        manifest.get("resolve_deny_read_abspath")
                    ),
                )
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


#: The sentinel line in the emitted constitutional-gate hook that
#: :func:`_apply_fail_closed_policy` flips for a confined/exclusive profile (CC-2).
_FAIL_CLOSED_SENTINEL = "_FAIL_CLOSED_ON_ERROR = False"


def _apply_fail_closed_policy(hook_text: str, manifest: dict[str, Any]) -> str:
    """Emit the constitutional gate fail-CLOSED for a confined/exclusive profile (CC-2).

    The gate defaults to fail-OPEN (a crash is a harness allow) so a buggy gate never bricks
    a cooperative session. But a ``confined``/``exclusive`` ``privilege_profile`` means the
    operator opted into a boundary a crash must not silently drop — so unless
    ``fallback_fail_open`` is set (from ``--allow-fallback-fail-open``), flip the emitted
    hook's ``_FAIL_CLOSED_ON_ERROR`` sentinel to ``True``. Byte-identical to the template for
    cooperative teams and for the opt-out.

    Args:
        hook_text: The verbatim ``constitutional-gate.py`` template text.
        manifest: The team manifest (read for ``privilege_profile`` + ``fallback_fail_open``).

    Returns:
        The hook text, fail-closed sentinel flipped only when the profile warrants it.
    """
    if manifest.get("privilege_profile") not in {"confined", "exclusive"}:
        return hook_text
    if manifest.get("fallback_fail_open"):
        return hook_text
    return hook_text.replace(
        _FAIL_CLOSED_SENTINEL, "_FAIL_CLOSED_ON_ERROR = True", 1
    )


def verify_sandbox_wiring(project_root: Path) -> tuple[bool, list[str]]:
    """Verify an emitted sandbox boundary was actually merged into live settings (P1-3).

    The emitted ``sandbox`` block lives in ``.claude/settings.hooks.example.json`` and is
    **inert until the operator merges it** into ``.claude/settings.json``; nothing otherwise
    confirms the boundary took effect, so a team can believe it is confined when it is not.
    This read-only check closes that silent-failure gap (audit H-1 / CC-5).

    It is **output-only**: it reports booleans and the *expected* (agentteams-generated)
    write roots, and it never echoes values read out of the operator's live
    ``settings.json`` (which may hold secrets) — a mismatch is reported as a boolean, not
    by printing the live content.

    Args:
        project_root: The generated project root (holds ``.claude/``).

    Returns:
        ``(ok, messages)``. ``ok`` is ``False`` only when a confinement WAS emitted but the
        live settings do not enable it (or leave the unsandboxed-command escape hatch open,
        or carry different write roots) — the "unmerged/ineffective boundary" case. When no
        confinement was requested, or the example is absent, ``ok`` is ``True`` with an
        explanatory message (nothing to enforce).
    """
    claude_dir = project_root / ".claude"
    example = claude_dir / "settings.hooks.example.json"
    live = claude_dir / "settings.json"
    if not example.is_file():
        return True, ["no emitted settings example found — nothing to verify."]
    try:
        ex = json.loads(example.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True, [f"could not read {example} as JSON — skipping wiring check."]
    ex_sandbox = ex.get("sandbox") if isinstance(ex, dict) else None
    if not (isinstance(ex_sandbox, dict) and ex_sandbox.get("enabled")):
        return True, ["no sandbox confinement was requested for this team (cooperative)."]
    if not live.is_file():
        return False, [
            "WARNING: unmerged boundary — NOT enforced. A sandbox confinement was emitted "
            "but .claude/settings.json does not exist. Merge the emitted example block."
        ]
    try:
        lv = json.loads(live.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, [
            "WARNING: .claude/settings.json is unreadable or invalid JSON; cannot confirm "
            "the boundary is live."
        ]
    lv_sandbox = lv.get("sandbox") if isinstance(lv, dict) else None
    if not (isinstance(lv_sandbox, dict) and lv_sandbox.get("enabled") is True):
        return False, [
            "WARNING: unmerged boundary — NOT enforced. .claude/settings.json has no "
            "enabled sandbox block. Merge the emitted example block."
        ]
    msgs: list[str] = []
    ok = True
    if lv_sandbox.get("allowUnsandboxedCommands") is not False:
        ok = False
        msgs.append(
            "WARNING: sandbox.allowUnsandboxedCommands is not false in the live settings — "
            "the unsandboxed-command escape hatch is open, so confinement can be bypassed."
        )
    ex_roots = (ex_sandbox.get("filesystem") or {}).get("allowWrite")
    lv_roots = (lv_sandbox.get("filesystem") or {}).get("allowWrite")
    if ex_roots != lv_roots:
        ok = False
        msgs.append(
            "WARNING: the live sandbox write roots differ from the emitted expectation "
            f"(expected allowWrite={ex_roots!r}) — re-merge so the boundary matches."
        )
    if ok:
        msgs.append("OK: sandbox confinement is merged and enabled in .claude/settings.json.")
    return ok, msgs


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


def _extract_claude_optional_keys(content: str) -> tuple[str | None, str | None, str | None]:
    """Extract optional Claude-specific front-matter keys (A.5).

    Returns ``(model, disallowedTools, permissionMode)`` — each ``None`` when
    absent. These keys are recognized by Claude Code's subagent schema but were
    previously dropped on round trip because ``render_agent_file`` stripped all
    front matter before re-injecting only name/description/tools.
    """
    model: str | None = None
    disallowed: str | None = None
    permission: str | None = None

    yaml_body, _ = _parse_yaml_front_matter(content)
    if yaml_body is None:
        return model, disallowed, permission
    for key_match in _YAML_SCALAR_RE.finditer(yaml_body):
        key = key_match.group(1).strip()
        val = key_match.group(2).strip().strip('"\'')
        if key == "model" and not model:
            model = val
        elif key == "disallowedTools" and not disallowed:
            disallowed = val
        elif key == "permissionMode" and not permission:
            permission = val

    return model, disallowed, permission


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
    *,
    model: str | None = None,
    disallowed_tools: str | None = None,
    permission_mode: str | None = None,
) -> str:
    """Prepend a Claude Code-compatible YAML front matter block to content.

    The capability key is ``tools`` (comma-separated, e.g. ``tools: Read, Glob, Grep``).
    It was ``allowed-tools`` until 2026-08-06; that key is not part of Claude Code's
    subagent schema, so every grant emitted under it was inert and every agent inherited
    the full tool pool. See :meth:`ClaudeAdapter.required_front_matter_keys`.

    A.5: ``model``, ``disallowedTools``, and ``permissionMode`` are optional
    Claude Code front-matter keys recognized per Claude's subagent schema but
    previously dropped on round trip. When provided, they are emitted after
    ``tools`` so the output carries the full Claude front-matter contract.
    """
    lines = ["---", f"name: {name}"]
    if description:
        lines.append(f'description: "{description}"')
    lines.append(f"{CLAUDE_CAPABILITY_KEY}: {allowed_tools}")
    # A.5: emit optional Claude-specific front-matter keys when provided.
    if model:
        lines.append(f"model: {model}")
    if disallowed_tools:
        lines.append(f"disallowedTools: {disallowed_tools}")
    if permission_mode:
        lines.append(f"permissionMode: {permission_mode}")
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


