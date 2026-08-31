"""Canonical tool-scope vocabulary and per-framework capability mappings.

The canonical vocabulary is the 7 tokens ``read, edit, search, execute, todo,
agent, retrieval`` — exactly the keys of ``claude.py::_VSCODE_TO_CLAUDE_TOOLS``
(copilot-vscode's existing vocabulary, reused as canonical per the
durable-canonical-agent-format plan §5.2).

Direction ownership (single owner per direction, no duplication):

* **copilot-vscode -> claude** stays in ``claude.py`` (``_VSCODE_TO_CLAUDE_TOOLS``
  + ``_map_allowed_tools``), where it has always lived.
* **claude -> canonical** (reverse) lives here, reproducing ``claude.py``'s dedup
  rule: unrestricted ``Bash`` (canonical ``execute``) subsumes the scoped
  retrieval grant, so a file listing both collapses to ``execute`` alone.
* **copilot-vscode -> canonical** is a trivial identity check (tokens are already
  canonical); provided here for validation.
* **goose <-> canonical** is documented best-effort/coarse: Goose recipes have no
  per-agent tool-scope channel comparable to Claude ``allowed-tools``. Tool
  surface travels in the recipe ``extensions:`` list, which is coarser than the
  7-token vocabulary; recipe-level configuration that isn't tool-scope data at
  all (parameters/response/retry) belongs in ``framework_extensions.goose``, not
  here.
* **copilot-cli and agents-md have no capability channel at all** — capabilities
  travel only in CAI's ``raw`` escape hatch for those two frameworks.

Claude tool-collapse coarseness (disclosed, not fixable without redesigning the
canonical token granularity): the canonical ``edit`` token maps to BOTH Claude
``Edit`` and ``Write``, and the canonical ``search`` token maps to BOTH ``Grep``
and ``Glob``. A hand-scoped ``tools: Read, Edit, Bash`` (read+edit-only-existing-
files+shell) round-trips through canonical as ``tools: Read, Edit, Write, Bash``
(read+edit+write/create+shell) — a real grant-expansion risk, not just a formatting
difference. Similarly, ``tools: Read, Grep`` (read+search-via-grep) expands to
``tools: Read, Grep, Glob`` (read+search+filesystem-traversal). This is the same
class of coarseness goose's ``developer`` bundle already discloses above; it is
inherent to collapsing 10 Claude tool names into 7 canonical tokens. The
``capabilities.raw`` escape hatch (A.3) preserves the original tool string so an
operator can detect the expansion, but the canonical ``tool_scopes`` field cannot
distinguish the original sub-grants.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from agentteams.yaml_frontmatter import parse_yaml_front_matter

# Canonical vocabulary, in canonical order (matches claude.py dict key order).
CANONICAL_TOOL_SCOPES: tuple[str, ...] = (
    "read",
    "edit",
    "search",
    "execute",
    "todo",
    "agent",
    "retrieval",
)

# The scoped retrieval grant Claude emits for the canonical `retrieval` token.
# Must stay identical to claude.py::_RETRIEVAL_CLI_SCOPE.
_RETRIEVAL_CLI_SCOPE = "Bash(python -m agentteams.research:*)"

# Forward map (canonical -> claude). Mirrors claude.py::_VSCODE_TO_CLAUDE_TOOLS
# exactly; kept here ONLY to derive the reverse direction without importing
# claude.py (interop.py must not create an interop -> framework-render cycle).
_CANONICAL_TO_CLAUDE: dict[str, tuple[str, ...]] = {
    "read": ("Read",),
    "edit": ("Edit", "Write"),
    "search": ("Grep", "Glob"),
    "execute": ("Bash",),
    "todo": ("TodoWrite",),
    "agent": ("Task",),
    "retrieval": (_RETRIEVAL_CLI_SCOPE,),
}

# Reverse map (claude tool -> canonical token), derived, so it can never drift
# from _CANONICAL_TO_CLAUDE.
_CLAUDE_TO_CANONICAL: dict[str, str] = {
    claude_tool: token
    for token, claude_tools in _CANONICAL_TO_CLAUDE.items()
    for claude_tool in claude_tools
}

# --- copilot-vscode front-matter parsing ------------------------------------

# copilot-vscode writes an inline list: tools: ['read', 'edit'] or [read, edit]
_VSCODE_TOOLS_RE = re.compile(r"^tools:\s*\[([^\]]*)\]\s*$", re.MULTILINE)


def canonical_tools_for_copilot_vscode(content: str) -> list[str] | None:
    """Extract canonical tokens from a copilot-vscode agent file's ``tools:`` list.

    copilot-vscode's vocabulary already IS the canonical vocabulary, so this is
    an identity extraction with validation: unrecognized tokens are dropped
    (they cannot round-trip into CAI's typed enum), duplicates collapse, and
    canonical order is imposed for deterministic output.

    Returns:
        Canonical-order token list, or None when no ``tools:`` list is present
        (an agent authored without a tool scope — no capability signal to
        capture; CAI records no capabilities rather than inventing them).
    """
    yaml_block, _ = parse_yaml_front_matter(content)
    if yaml_block is None:
        return None
    m = _VSCODE_TOOLS_RE.search(yaml_block)
    if not m:
        return None
    tokens = {item.strip().strip("'\"").lower() for item in m.group(1).split(",") if item.strip()}
    return [t for t in CANONICAL_TOOL_SCOPES if t in tokens] or None


# --- claude reverse mapping --------------------------------------------------

_CLAUDE_TOOLS_LINE_RE = re.compile(r"^(?:allowed-tools|tools):\s*(.*)$")


def claude_allowed_tools_to_canonical(claude_tools: Iterable[str]) -> list[str]:
    """Reverse-map Claude ``allowed-tools`` entries to canonical tokens.

    Reproduces ``claude.py``'s dedup rule rather than doing a naive per-token
    inversion: ``Bash`` grants unrestricted shell, which subsumes the scoped
    retrieval command, so a file listing both collapses to ``execute`` alone
    (emitting both would read as though the scope constrained something when it
    constrains nothing — a misleading least-privilege signal).

    Args:
        claude_tools: Claude tool names as they appear in ``tools:`` /
            ``allowed-tools:`` (e.g. ``Read``, ``Bash``,
            ``Bash(python -m agentteams.research:*)``).

    Returns:
        Canonical-order token list (possibly empty when nothing maps).
    """
    mapped: set[str] = set()
    for tool in claude_tools:
        token = _CLAUDE_TO_CANONICAL.get(str(tool).strip())
        if token is not None:
            mapped.add(token)
    if "execute" in mapped and "retrieval" in mapped:
        mapped.discard("retrieval")
    return [t for t in CANONICAL_TOOL_SCOPES if t in mapped]


def canonical_tools_for_claude(content: str) -> list[str] | None:
    """Extract canonical tokens from a Claude sub-agent file's front matter.

    Accepts BOTH capability keys: ``tools:`` (current shape since 2026-08-06)
    and ``allowed-tools:`` (every previously generated team still on disk).
    Claude writes a bare comma-separated scalar; the bracket discriminates it
    from copilot-vscode's inline list and is rejected here.

    Returns:
        Canonical-order token list, or None when no capability key is present.
    """
    yaml_block, _ = parse_yaml_front_matter(content)
    if yaml_block is None:
        return None
    for line in yaml_block.splitlines():
        m = _CLAUDE_TOOLS_LINE_RE.match(line.strip())
        if not m or "[" in m.group(1):
            continue
        tools = [t.strip() for t in m.group(1).split(",") if t.strip()]
        return claude_allowed_tools_to_canonical(tools) or None
    return None


# --- goose mapping (best-effort, coarse) -------------------------------------

# Canonical token -> goose builtin extension names. Goose recipes declare tool
# surface via `extensions:`; the grain is coarser than the 7-token vocabulary:
#   * developer bundles shell + text-editor + file tools, covering
#     read/edit/search/execute indistinguishably.
#   * todo maps to goose's todo extension.
#   * agent has no extension: goose handoffs travel as sub_recipes / load()
#     references, a recipe-structure channel, not a tool grant.
#   * retrieval has no dedicated extension: it is `python -m agentteams.research`
#     run through developer's shell, already covered by the developer grant.
_CANONICAL_TO_GOOSE_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "read": ("developer",),
    "edit": ("developer",),
    "search": ("developer",),
    "execute": ("developer",),
    "todo": ("todo",),
    "agent": (),
    "retrieval": (),
}

# Reverse (goose extension -> canonical tokens). Deliberately coarse and
# conservative: unknown/MCP extensions map to nothing here — they are not
# tool-scope data and belong in framework_extensions.goose / mcp_servers.
_GOOSE_EXTENSION_TO_CANONICAL: dict[str, tuple[str, ...]] = {
    "developer": ("read", "edit", "search", "execute"),
    "todo": ("todo",),
}


def goose_extensions_to_canonical(extension_names: Iterable[str]) -> list[str]:
    """Best-effort reverse map of goose recipe ``extensions:`` names to canonical.

    Coarse by design (see module docstring): ``developer`` expands to the four
    tokens it bundles; unrecognized extensions contribute nothing.
    """
    mapped: set[str] = set()
    for name in extension_names:
        for token in _GOOSE_EXTENSION_TO_CANONICAL.get(str(name).strip().lower(), ()):
            mapped.add(token)
    return [t for t in CANONICAL_TOOL_SCOPES if t in mapped]


def canonical_to_goose_extensions(tokens: Iterable[str]) -> list[str]:
    """Forward map canonical tokens to goose extension names (deduped, ordered)."""
    seen: dict[str, None] = {}
    for token in tokens:
        for ext in _CANONICAL_TO_GOOSE_EXTENSIONS.get(str(token).strip().lower(), ()):
            seen.setdefault(ext, None)
    return list(seen)


def capabilities_from_tokens(tokens: list[str] | None, framework: str) -> dict[str, Any]:
    """Build a CAI v2 ``capabilities`` object from extracted canonical tokens.

    Args:
        tokens: canonical-order token list, or None when the source file carries
            no capability signal.
        framework: source framework id; recorded nowhere here (CAI's
            ``source_framework`` owns it) but kept in the signature so callers
            document intent.

    Returns:
        ``{"tool_scopes": [...], "model_hint": None, "raw": None}`` with None
        fields omitted, per agent-cai.schema.json v2 (all fields optional).
    """
    del framework  # documented; unused by design
    if not tokens:
        return {}
    return {"tool_scopes": list(tokens)}
