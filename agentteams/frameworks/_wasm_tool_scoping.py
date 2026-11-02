"""
_wasm_tool_scoping.py — template ``tools:`` scopes -> the WASM agent runtime's tool vocabulary (fail-closed, P08 §4.2).

The input is an agent template's canonical ``tools:`` list (``read``, ``edit``, ``search``, ``execute``, ``todo``,
``agent``, ``retrieval``; :data:`agentteams.capability_map.CANONICAL_TOOL_SCOPES`). The output is a subset of the
runtime's vocabulary (:data:`agentteams.wasm_agent.tools` ``IN_GUEST_TOOLS`` + ``HOST_TOOLS``), intersected with what
the agent's grant can actually serve:

=============  =============================  ==============================================================
template       wasm tools                     dropped (a narrowing, with a notice) when
=============  =============================  ==============================================================
``read``       ``read_file``, ``list_dir``    the grant has no ``/project/<rel>`` and no ``/scratch`` mount
``search``     ``search_files``, ``list_dir`` the same
``edit``       ``write_file``                 the grant has no ``/scratch`` mount (project files are never written)
``execute``    ``run_code``                   never: it runs in a fresh compute sandbox on the agent's own grant
``agent``      ``delegate``                   the agent has no delegation edge to a team member (P09)
``todo``       --                             always (no equivalent)
``retrieval``  --                             always (network is never granted, D5)
anything else  --                             **refused** (:class:`WasmToolScopeError`)
=============  =============================  ==============================================================

There is no blanket default (Rule 12): an agent with no ``tools:`` block gets an **empty** toolset and a notice,
never the Claude fallback. Nothing here can widen an agent beyond its template scopes; the grant only narrows.
The loader (:mod:`agentteams.wasm_agent.team`) re-applies :func:`grant_supports` at run time, so a hand-edited
agent file cannot claim a tool its grant cannot serve.

Integrity-pinned (``agentteams/integrity.py``): it decides what a WASM agent may do (C-3).
Stdlib only; no intra-package imports beyond the vocabulary constants restated (and pinned equal by a test).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: The runtime's tool vocabulary, in a fixed order (pinned equal to ``wasm_agent.tools.known_tools()`` by a test;
#: restated so the adapter does not import the runtime at render time).
WASM_TOOLS: tuple[str, ...] = ("read_file", "list_dir", "search_files", "write_file", "run_code", "delegate")

#: Template scope -> the wasm tools it maps to (an empty tuple: always dropped).
SCOPE_TO_WASM: dict[str, tuple[str, ...]] = {
    "read": ("read_file", "list_dir"),
    "search": ("search_files", "list_dir"),
    "edit": ("write_file",),
    "execute": ("run_code",),
    "agent": ("delegate",),
    "todo": (),
    "retrieval": (),
}

_READ_TOOLS = frozenset({"read_file", "list_dir", "search_files"})
_ALWAYS_DROPPED_WHY = {"todo": "no WASM runtime equivalent", "retrieval": "network is never granted (D5)"}
_TOOLS_LINE_RE = re.compile(r"^tools:\s*\[([^\]]*)\]\s*$", re.MULTILINE)
_SCOPE_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


class WasmToolScopeError(ValueError):
    """A template tool scope with no WASM translation (fail closed: never silently dropped or widened)."""


def parse_tools_line(yaml_text: str | None) -> list[str] | None:
    """Return the scopes of a front matter's ``tools: [...]`` flow list, or None when there is none.

    Args:
        yaml_text: The front-matter text (without the ``---`` fences), or None.

    Returns:
        The scopes in order (quotes stripped), or None when no ``tools:`` flow list is present.

    Raises:
        WasmToolScopeError: A ``tools:`` line that is not a flow list of plain names.
    """
    if yaml_text is None:
        return None
    if not re.search(r"^tools:", yaml_text, re.MULTILINE):
        return None
    match = _TOOLS_LINE_RE.search(yaml_text)
    if match is None:
        raise WasmToolScopeError("tools: must be a one-line flow list such as ['read', 'search']")
    scopes = [item.strip().strip("'\"") for item in match.group(1).split(",") if item.strip()]
    bad = [s for s in scopes if not _SCOPE_RE.match(s)]
    if bad:
        raise WasmToolScopeError(f"tools: entries {bad!r} are not plain scope names")
    return scopes


def grant_supports(tool: str, *, has_read_mount: bool, has_scratch: bool, has_delegates: bool) -> bool:
    """Return whether a grant can serve *tool* (the §4.2 drop rules; shared with the run-time loader).

    Args:
        tool: A wasm tool name.
        has_read_mount: The grant has a ``/project/<rel>`` mount.
        has_scratch: The grant has the ``/scratch`` mount.
        has_delegates: The agent has at least one delegation edge.

    Returns:
        False when the tool would have nothing to act on (or is unknown).

    Raises:
        Nothing.
    """
    if tool in _READ_TOOLS:
        return has_read_mount or has_scratch
    if tool == "write_file":
        return has_scratch
    if tool == "run_code":
        return True
    if tool == "delegate":
        return has_delegates
    return False


def translate_wasm_tools(scopes: Iterable[str] | None, *, has_read_mount: bool, has_scratch: bool,
                         has_delegates: bool, agent: str = "") -> tuple[tuple[str, ...], list[str]]:
    """Translate template scopes into the agent's wasm toolset, narrowed by its grant.

    Args:
        scopes: The template's ``tools:`` scopes, or None when the template declares none.
        has_read_mount: The agent's grant has a ``/project/<rel>`` mount.
        has_scratch: The agent's grant has the ``/scratch`` mount.
        has_delegates: The agent has delegation edges.
        agent: The agent slug (for notices).

    Returns:
        ``(tools, notices)``: the tools in :data:`WASM_TOOLS` order (no duplicates) and at most one notice line
        naming every narrowing.

    Raises:
        WasmToolScopeError: A scope with no translation.
    """
    who = agent or "agent"
    if scopes is None:
        return (), [f"{who}: no tools: block in the template; the WASM agent gets an empty toolset (no default)"]
    unknown = [s for s in scopes if s not in SCOPE_TO_WASM]
    if unknown:
        raise WasmToolScopeError(f"{who}: tool scope(s) {unknown!r} have no WASM runtime translation; known: "
                                 f"{', '.join(SCOPE_TO_WASM)} (refused rather than dropped or widened)")
    wanted: set[str] = set()
    parts = [f"'{scope}' ({_ALWAYS_DROPPED_WHY[scope]})" for scope in dict.fromkeys(scopes)
             if scope in _ALWAYS_DROPPED_WHY]
    dropped: list[str] = []
    for scope in dict.fromkeys(scopes):
        for tool in SCOPE_TO_WASM[scope]:
            if grant_supports(tool, has_read_mount=has_read_mount, has_scratch=has_scratch,
                              has_delegates=has_delegates):
                wanted.add(tool)
            elif tool not in dropped:
                dropped.append(tool)
    for why, group in _GROUPS:
        lost = [t for t in dropped if t in group and t not in wanted]
        if lost:
            parts.append(f"{', '.join(lost)} ({why})")
    notices = [f"{who}: dropped {'; '.join(parts)}"] if parts else []
    return tuple(t for t in WASM_TOOLS if t in wanted), notices


#: Why a grant cannot serve a tool, grouped for one notice line per agent.
_GROUPS: tuple[tuple[str, frozenset[str]], ...] = (
    ("its grant has no read path and no scratch dir", frozenset({"read_file", "list_dir", "search_files"})),
    ("its grant has no /scratch; project files are never writable", frozenset({"write_file"})),
    ("no delegation edge to a team member", frozenset({"delegate"})),
)


__all__ = ["SCOPE_TO_WASM", "WASM_TOOLS", "WasmToolScopeError", "grant_supports", "parse_tools_line",
           "translate_wasm_tools"]
