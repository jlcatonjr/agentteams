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
  raises — dropping a deny would widen the agent.
"""

from __future__ import annotations

import re

#: Grok Build 1.0.52 tool names usable in agent ``tools:`` / ``disallowedTools:`` (bare or with a scope).
GROK_TOOL_NAMES = frozenset({"Bash", "Read", "Edit", "Write", "Grep", "Glob", "MCPTool", "WebFetch", "WebSearch", "Agent"})

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


def _translate(entry: str, *, deny: bool) -> str | None:
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
            return None
        name = mapped
    if name not in GROK_TOOL_NAMES:
        raise ValueError(
            f"tool {entry!r} is not in Grok Build's tool vocabulary ({', '.join(sorted(GROK_TOOL_NAMES))}); Grok "
            "skips unknown names, which can leave an empty list that inherits every tool"
        )
    return name + scope


def to_grok_tools(value: str, *, deny: bool = False) -> str:
    """Translate a Claude ``tools`` (or ``disallowedTools``) value to Grok Build's vocabulary.

    Args:
        value: The Claude-style comma-separated list.
        deny: True for a ``disallowedTools`` value: an untranslatable entry raises instead of being dropped,
            and an empty result is allowed (an empty deny-list widens nothing).

    Returns:
        The Grok value, comma-separated, de-duplicated in first-seen order.

    Raises:
        ValueError: An entry is outside the vocabulary, a deny entry cannot be translated, or an allow-list
            translates to nothing (which Grok would read as "inherit every tool").
    """
    out: list[str] = []
    for entry in split_tool_list(value):
        mapped = _translate(entry, deny=deny)
        if mapped is not None and mapped not in out:
            out.append(mapped)
    if not out and not deny:
        raise ValueError(
            f"tool list {value!r} translates to nothing for Grok Build; an empty tools list inherits every tool, "
            "so the render is refused"
        )
    return ", ".join(out)
