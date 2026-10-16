"""
_grok_tool_scoping.py — Claude tool names → Grok Build agent ``tools:`` values (fail-closed).

Grok Build reads Claude-style tool names in a custom agent's ``tools:`` list (Grok Build user guide,
``22-permissions-and-safety.md`` "Tool Names"; ``16-subagents.md`` for ``Agent(...)``). Two of the names the
Claude adapter can emit are not in that vocabulary: ``Task`` (Grok spells the spawn tool ``Agent``) and
``TodoWrite`` (no Grok equivalent). Everything else must already be a name Grok recognises.

Kept out of ``grok.py`` and integrity-pinned (``agentteams/integrity.py``) on the ``goose_tool_scoping``
precedent: this module decides what an agent may do, so it changes rarely and only under review
(@security step-6 ruling, plan §11 condition 1/5).

Fail-closed rules:

* an allow-list entry Grok would not recognise raises — Grok *skips* unknown names, and an agent whose every
  entry was skipped has an empty list, which Grok reads as "inherit every tool" (``config.rs``: "Empty means
  inherit all");
* an allow-list that translates to nothing raises, for the same reason;
* ``TodoWrite`` is dropped from an allow-list (a narrowing), but a deny-list entry that cannot be translated
  raises — dropping a deny would widen the agent;
* a **scoped** entry (``Bash(cmd:*)``, ``Agent(type)``, any ``Name(...)``) is never emitted. Grok Build 1.0.52 does
  not honour the scope: an agent with ``Read, Bash(echo:*)`` was offered a full shell plus ``search_replace`` (an edit
  tool), ``monitor`` and ``workflow`` (R8 spike, grok's own ``tool_definitions.json``; remediation GROK-FU-R8-SCOPE).
  So a scoped allow entry is **dropped** (a narrowing: the agent loses that command), and a scoped deny entry becomes
  the **whole tool** (a wider deny: also a narrowing). :func:`translate_grok_tools` reports every such change so the
  adapter can tell the operator.
"""

from __future__ import annotations

import re

#: Tool names (pinned Grok Build release, FORMAT_SPECS["grok"]) usable in agent ``tools:`` / ``disallowedTools:``. Only the
#: bare name is emitted: grok does not honour a ``Name(scope)`` form in an agent's tool list (see the module docstring).
#: Live mapping (grok-scope-verify, 2026-10-15): Read->read_file, Grep->grep, Glob->list_dir, Edit->search_replace,
#: Bash->run_terminal_command, Agent->the spawn tools; Write, WebFetch and WebSearch are accepted but grant nothing.
GROK_TOOL_NAMES = frozenset({"Bash", "Read", "Edit", "Write", "Grep", "Glob", "WebFetch", "WebSearch", "Agent"})

#: Names grok does NOT accept in an agent's list: like a scoped entry, one discards the whole list and the agent gets a
#: preset with a shell and an edit tool (`Read, MCPTool` -> grep, list_dir, monitor, run_terminal_command,
#: search_replace, todo_write, workflow; grok-scope-verify h-mcptool). Dropped, with a notice, never emitted.
_GROK_LIST_BREAKERS = frozenset({"MCPTool"})

#: Accepted names that grant nothing on the pinned release (grok-scope-verify h-write/h-webfetch/h-websearch). They never
#: count toward a non-empty effective toolset: a list of only these may behave like an empty one (@security delta C2).
_GROK_NO_GRANT = frozenset({"Write", "WebFetch", "WebSearch"})

#: Front-matter lines every Grok agent carries so ``tools:`` is the exact limit. Grok's defaults widen it:
#: ``injectDefaultTools`` (default true) layers session tools on top of the list and ``mcpInheritance``
#: (default ``all``) passes every parent MCP server (source-only: ``xai-grok-agent/src/config.rs``).
GROK_EXACT_TOOLSET_KEYS: tuple[str, str] = ("injectDefaultTools", "mcpInheritance")
EXACT_TOOLSET_LINES: tuple[str, ...] = (f"{GROK_EXACT_TOOLSET_KEYS[0]}: false", f"{GROK_EXACT_TOOLSET_KEYS[1]}: none")

#: Claude names with no identical Grok name. ``None`` means drop (allow-list only; a narrowing).
_CLAUDE_TO_GROK = {"Task": "Agent", "TodoWrite": None}

# `Name` or `Name(scope)`; the scope may hold anything but an unbalanced parenthesis.
_ENTRY_RE = re.compile(r"^(?P<name>[A-Za-z][A-Za-z0-9_]*)(?P<scope>\(.*\))?$")
# Claude Code's MCP tool spelling, which Grok's matchers also accept.
_MCP_RE = re.compile(r"^mcp__[A-Za-z0-9_-]+__[A-Za-z0-9_*-]+$")


def split_tool_list(value: str) -> list[str]:
    """Split a comma-separated tool list, ignoring commas inside parentheses.

    Args:
        value: A Claude-style ``tools`` value, e.g. ``Read, Bash(git diff:*), Grep``.

    Returns:
        The stripped, non-empty entries in order.

    Raises:
        ValueError: The parentheses are unbalanced.
    """
    items: list[str] = []
    depth = 0
    current = ""
    for ch in value:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                raise ValueError(f"unbalanced ')' in tool list {value!r}")
        if ch == "," and depth == 0:
            items.append(current.strip())
            current = ""
        else:
            current += ch
    if depth:
        raise ValueError(f"unbalanced '(' in tool list {value!r}")
    items.append(current.strip())
    return [i for i in items if i]


def _translate(entry: str, *, deny: bool, changes: list[str]) -> str | None:
    if _MCP_RE.match(entry):
        return entry
    m = _ENTRY_RE.match(entry)
    if not m:
        raise ValueError(f"tool entry {entry!r} is not a recognisable tool name")
    name, scope = m.group("name"), m.group("scope") or ""
    if name in _CLAUDE_TO_GROK:
        mapped = _CLAUDE_TO_GROK[name]
        if mapped is None:
            if deny:
                raise ValueError(
                    f"disallowedTools entry {entry!r} has no Grok Build equivalent; dropping a deny would widen "
                    "the agent, so the render is refused"
                )
            if scope:
                changes.append(f"dropped {entry!r} (no Grok Build equivalent; grok does not honour scopes either)")
            return None
        name = mapped
    if name in _GROK_LIST_BREAKERS:
        changes.append(f"dropped {entry!r} (grok does not accept it: one such entry discards the whole list and "
                       "grants a shell and an edit tool)")
        return None
    if name not in GROK_TOOL_NAMES:
        raise ValueError(
            f"tool {entry!r} is not in Grok Build's tool vocabulary ({', '.join(sorted(GROK_TOOL_NAMES))}); Grok "
            "skips unknown names, which can leave an empty list that inherits every tool"
        )
    if scope:
        if deny:
            changes.append(f"widened deny {entry!r} to the whole {name!r} tool (grok does not honour scopes)")
            return name
        changes.append(f"dropped {entry!r} (grok does not honour scopes and would widen it past the list)")
        return None
    return name


def translate_grok_tools(value: str, *, deny: bool = False) -> tuple[str, list[str]]:
    """Translate a Claude ``tools`` (or ``disallowedTools``) value to Grok Build's vocabulary, reporting changes.

    Args:
        value: The Claude-style comma-separated list.
        deny: True for a ``disallowedTools`` value: an untranslatable entry raises instead of being dropped, a
            scoped entry becomes the whole tool, and an empty result is allowed (an empty deny-list widens nothing).

    Returns:
        ``(grok_value, changes)``: the Grok value, comma-separated and de-duplicated in first-seen order, and one
        human-readable line per scoped entry that was dropped (allow) or widened to the whole tool (deny).

    Raises:
        ValueError: An entry is outside the vocabulary, a deny entry cannot be translated, or an allow-list
            translates to nothing (which Grok would read as "inherit every tool"); checked after scoped entries
            are dropped.
    """
    out: list[str] = []
    changes: list[str] = []
    for entry in split_tool_list(value):
        mapped = _translate(entry, deny=deny, changes=changes)
        if mapped is not None and mapped not in out:
            out.append(mapped)
    if not out and not deny:
        raise ValueError(
            f"tool list {value!r} translates to nothing for Grok Build; an empty tools list inherits every tool, "
            "so the render is refused"
        )
    return ", ".join(out), changes


def to_grok_tools(value: str, *, deny: bool = False) -> str:
    """Translate a Claude ``tools`` (or ``disallowedTools``) value to Grok Build's vocabulary.

    Args:
        value: The Claude-style comma-separated list.
        deny: True for a ``disallowedTools`` value (see :func:`translate_grok_tools`).

    Returns:
        The Grok value; scoped entries dropped (allow) or widened to the whole tool (deny), unreported. Callers that
        must tell the operator use :func:`translate_grok_tools`.

    Raises:
        ValueError: As :func:`translate_grok_tools`.
    """
    return translate_grok_tools(value, deny=deny)[0]


def effective_grok_tools(tools: str, disallowed: str | None) -> list[str]:
    """Return the built-in tools an allow-list keeps after a deny-list (bare names, as emitted).

    Only names in :data:`GROK_TOOL_NAMES` that grant something count (not :data:`_GROK_NO_GRANT`). An ``mcp__`` entry does not: under ``mcpInheritance: none`` the agent
    has no MCP servers, so an allow-list of only MCP tools (or one whose MCP entries a wildcard deny removes) leaves
    nothing usable (@security grok-scope-fix step 5, condition 1).

    Args:
        tools: The translated Grok ``tools:`` value.
        disallowed: The translated Grok ``disallowedTools:`` value, or None.

    Returns:
        The surviving built-in tools, in order. Empty means the agent's effective toolset is empty, which Grok may read
        as "inherit every tool" (untested), so callers refuse that render.

    Raises:
        Nothing.
    """
    denied = set(split_tool_list(disallowed)) if disallowed else set()
    return [t for t in split_tool_list(tools) if t in GROK_TOOL_NAMES and t not in _GROK_NO_GRANT and t not in denied]
