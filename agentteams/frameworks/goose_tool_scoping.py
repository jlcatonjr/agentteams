"""Grant-scoped Goose recipe extensions: derive each recipe's tools from the agent's declared ``tools:``.

Phase 2 of the Goose read-only-agents plan. Under the brief's
``goose_tool_scoping: "grant"`` a recipe no longer gets the whole ``developer`` builtin (shell and
file writes) by default. Instead each declared canonical tool maps to exactly the Goose tools it needs,
each listed in that extension's ``available_tools`` allowlist:

=========  ==========================================================================
Declared   Goose grant
=========  ==========================================================================
read       ``agentteams_readfs`` stdio server: read_file, list_dir, find, grep, stat
search     (same as read)
edit       ``developer``: write, edit, tree
execute    ``developer``: shell, tree
agent      ``summon``: delegate, load
=========  ==========================================================================

Facts behind the map (stub-model spike, Goose 1.37.0, ``references/goose-tool-scoping-spike.md``):

* ``available_tools`` is enforced at dispatch on builtin, platform AND stdio extensions; a withheld tool
  is neither offered nor callable. It is an allowlist, so a renamed tool is lost (fails closed).
* Goose adds the ``analyze`` platform extension beside ``developer`` unless the recipe lists it, and
  ``available_tools: []`` means *unrestricted*. So every recipe with ``developer`` lists ``analyze``
  with the non-matching allowlist ``[__none__]``, which disables it.
* ``developer``'s only content-reading tool is ``shell``; ``tree`` and ``analyze`` are not
  workspace-confined. Readers therefore get ``agentteams_readfs`` (realpath-confined, no write tool),
  never ``developer`` for reading.

Unknown tool tokens (``todo``, ``web``, ``retrieval``...) grant nothing on Goose. A recipe granted
nothing is emitted with ``extensions: []``, never a bare ``extensions:`` or no key: Goose 1.37 reads
those as "use the user's configured extensions", which typically means the full ``developer``
(verified live; ``references/goose-tool-scoping-spike.md``). Bridge recipes
(``bridge_subagents_goose.py``) and CAI interop imports (whose manifest never sets the mode) are
outside grant scoping and stay legacy-rendered. The map is pinned to
:data:`GOOSE_MAP_VERSION`; re-run ``scripts/goose-probe/`` before trusting it on a newer Goose.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
from typing import Any

from agentteams.frameworks.goose_recipe_emit import _MCP_EXT_TIMEOUT
from agentteams.frameworks.goose_recipe_read import UNPARSED, recipe_extension_grants

#: The Goose release the tool names below were verified against (stub-model spike, 2026-10-05).
#: Informational until the phase-3 version check reads it; re-run scripts/goose-probe/ before bumping.
GOOSE_MAP_VERSION = "1.37.0"

#: Disables an auto-added extension. ``[]`` would mean unrestricted (spike case A1).
DISABLED = "__none__"

READFS_NAME = "agentteams_readfs"
READFS_TOOLS: tuple[str, ...] = ("read_file", "list_dir", "find", "grep", "stat")
READFS_SCRIPT = "scripts/goose-readfs-mcp.py"
#: Under ``write_policy: "orchestrator-only"`` the server is installed here instead: inside the control plane,
#: which every session sandbox write-denies, so no agent can swap the program the recipe launches.
READFS_PROTECTED_PATH = ".agentteams/bin/goose-readfs-mcp.py"
#: Python flags for the launch: ``-I`` ignores PYTHON* variables, user site-packages and the script's own
#: directory on ``sys.path``; ``-S`` skips ``site`` (no ``.pth`` / ``sitecustomize`` code runs).
READFS_PYTHON_FLAGS: tuple[str, ...] = ("-I", "-S")
#: sha256 of the shipped server (``agentteams/data/goose-readfs-mcp.py``). This module is integrity-pinned, so
#: the runner can check an installed copy against it; ``tests/test_readfs_launch_integrity.py`` keeps it current.
READFS_SHA256 = "dc8413e8ec16298e79b5ff27e6629c792693ef88bcae4c9f9c40e85d127cab58"

#: Declared canonical tool -> ((extension, goose tool), ...).
GOOSE_TOOL_MAP: dict[str, tuple[tuple[str, str], ...]] = {
    "read": tuple((READFS_NAME, t) for t in READFS_TOOLS),
    "search": tuple((READFS_NAME, t) for t in READFS_TOOLS),
    "edit": (("developer", "write"), ("developer", "edit"), ("developer", "tree")),
    "execute": (("developer", "shell"), ("developer", "tree")),
    "agent": (("summon", "delegate"), ("summon", "load")),
}
#: Stable tool order per extension, so recipes are byte-stable.
_TOOL_ORDER: dict[str, tuple[str, ...]] = {
    "developer": ("write", "edit", "shell", "tree"),
    "summon": ("delegate", "load"),
}

_TOOLS_LINE_RE = re.compile(r"^tools:[ \t]*(\[.*\])[ \t]*$", re.MULTILINE)


def grant_mode(manifest: dict[str, Any]) -> bool:
    """Return True when the team opted into grant-scoped Goose extensions.

    Args:
        manifest: The team manifest.

    Returns:
        True for ``goose_tool_scoping == "grant"``; absent or ``"legacy"`` is False (legacy recipes,
        byte-identical except that an empty extension set is now ``extensions: []``).

    Raises:
        Nothing.
    """
    return manifest.get("goose_tool_scoping") == "grant"


def declared_tools(content: str) -> frozenset[str]:
    """Return the canonical tool tokens declared in a rendered agent's front-matter ``tools:`` list.

    Args:
        content: The rendered (Copilot-format) agent file.

    Returns:
        The lower-cased tokens; empty when the front matter has no parseable flow-list ``tools:``
        (which grants nothing on Goose: fail closed).

    Raises:
        Nothing.
    """
    front = content.split("\n---", 1)[0] if content.startswith("---") else ""
    matches = _TOOLS_LINE_RE.findall(front)
    # A second `tools:` line is ambiguous (a reader takes the first, YAML keeps the last), so it
    # grants nothing rather than whichever line happens to be broader.
    if len(matches) != 1 or len(re.findall(r"^tools:", front, re.MULTILINE)) != 1:
        return frozenset()
    try:
        tokens = ast.literal_eval(matches[0])
    except (ValueError, SyntaxError):
        return frozenset()
    return frozenset(str(t).strip().lower() for t in tokens if isinstance(t, str))


def grant_extensions(tools: frozenset[str], *, protected: bool = False,
                     ) -> tuple[list[str], dict[str, list[str]], list[dict[str, Any]]]:
    """Map declared tools to a recipe's extensions.

    Args:
        tools: Declared canonical tool tokens (:func:`declared_tools`).
        protected: Under ``write_policy: "orchestrator-only"``: launch readfs from :data:`READFS_PROTECTED_PATH`.

    Returns:
        ``(builtin_or_platform_names, available_tools, stdio_extensions)``:

        * names in emit order (``developer``, ``analyze``, ``summon``), each with an entry in
          ``available_tools``; ``analyze`` appears (disabled) exactly when ``developer`` does;
        * ``available_tools``: extension name -> its allowlisted tools;
        * the ``agentteams_readfs`` stdio extension when ``read`` or ``search`` is declared.

    Raises:
        Nothing.
    """
    granted: dict[str, set[str]] = {}
    for token in tools:
        for ext, tool in GOOSE_TOOL_MAP.get(token, ()):
            granted.setdefault(ext, set()).add(tool)
    names: list[str] = []
    allow: dict[str, list[str]] = {}
    if "developer" in granted:
        names += ["developer", "analyze"]
        allow["developer"] = [t for t in _TOOL_ORDER["developer"] if t in granted["developer"]]
        allow["analyze"] = [DISABLED]
    if "summon" in granted:
        names.append("summon")
        allow["summon"] = [t for t in _TOOL_ORDER["summon"] if t in granted["summon"]]
    stdio = [readfs_extension(protected=protected)] if READFS_NAME in granted else []
    return names, allow, stdio


def readfs_extension(*, protected: bool = False) -> dict[str, Any]:
    """Return the stdio extension entry for the shipped ``agentteams_readfs`` server.

    The path is relative to the project root, the same convention as the coordination server; Goose runs a stdio
    extension from the directory it was started in (verified on Goose 1.37.0). Python is started with
    :data:`READFS_PYTHON_FLAGS`.

    Args:
        protected: Point at :data:`READFS_PROTECTED_PATH` (under the switch) instead of :data:`READFS_SCRIPT`.

    Returns:
        The extension mapping, including its ``available_tools`` allowlist.

    Raises:
        Nothing.
    """
    return {
        "type": "stdio", "name": READFS_NAME, "cmd": "python3",
        "args": [*READFS_PYTHON_FLAGS, READFS_PROTECTED_PATH if protected else READFS_SCRIPT, "--root", "."],
        "env_keys": [], "timeout": _MCP_EXT_TIMEOUT,
        "available_tools": list(READFS_TOOLS),
    }



#: Extension names grant mode manages; an operator MCP server may not reuse them.
MANAGED_NAMES = frozenset({"developer", "analyze", "summon", READFS_NAME})


def filter_operator_mcp(extensions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Drop operator MCP servers whose names clash with a grant-managed extension.

    A server named ``developer`` (or ``agentteams_readfs``...) would sit beside, or be mistaken for,
    the scoped entry and could bring back the tools the scoping withheld.

    Args:
        extensions: Operator MCP extension entries for one recipe.

    Returns:
        ``(kept, notes)``: the entries to emit, and one recipe-comment note per dropped entry.

    Raises:
        Nothing.
    """
    kept, notes = [], []
    for ext in extensions:
        if "".join(str(ext.get("name", "")).split()).lower() in MANAGED_NAMES:  # all whitespace removed
            notes.append(f"{ext.get('name')}: not wired, the name is reserved by goose_tool_scoping grant mode")
        else:
            kept.append(ext)
    return kept, notes


def _read_or_empty(path: Any) -> str:
    """Return a file's text, or ``""`` when it cannot be read (an unreadable recipe reports nothing)."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def unscoped_recipes(recipes_dir: Any) -> list[str]:
    """List on-disk recipes that still load ``developer`` with no ``available_tools`` allowlist.

    A merge keeps each recipe's on-disk ``extensions:``, so a team switched to grant mode with
    ``--update --merge`` keeps its legacy (unscoped) recipes. This lets the run say so.

    Args:
        recipes_dir: The Goose recipes directory (a ``pathlib.Path``).

    Returns:
        Sorted recipe file names; empty when the directory is missing.

    Raises:
        Nothing.
    """
    found = []
    for path in sorted(getattr(recipes_dir, "glob", lambda _p: [])("*.yaml")):
        text = _read_or_empty(path)
        if 'version: "1.0.0"' not in text or declared_from_marker(text) is not None:
            continue  # not a recipe, or a grant recipe (checked against its marker by the audit)
        extensions = recipe_extension_grants(text)
        # Unscoped = fail-open extensions, or a developer (or unreadable entry) with no allowlist.
        # A developer scoped by hand (e.g. [write, edit, tree] for a writer) is not unscoped.
        if extensions is None or any(
            e["name"] in ("developer", UNPARSED) and e["available_tools"] is None for e in extensions
        ):
            found.append(path.name)
    return found


def grant_merge_notice(recipes_dir: Any) -> list[str]:
    """Print (and return) a notice when grant mode is set but merged recipes are still unscoped.

    Args:
        recipes_dir: The Goose recipes directory.

    Returns:
        The unscoped recipe names that were reported (empty when none).

    Raises:
        Nothing.
    """
    names = unscoped_recipes(recipes_dir)
    if names:
        print(
            f"  !  goose_tool_scoping is \"grant\", but {len(names)} recipe(s) still load the whole developer "
            f"extension ({', '.join(names[:5])}{', ...' if len(names) > 5 else ''}). A merge keeps on-disk "
            "extensions; re-render with --update --overwrite (under its usual clearance) to apply the scoping."
        )
    return names


def goose_version_notice(goose_binary: str | None = None) -> str | None:
    """Compare the installed Goose with :data:`GOOSE_MAP_VERSION`; return a notice when they differ.

    The tool names in :data:`GOOSE_TOOL_MAP` were verified against one Goose release. A different
    release may rename a tool. That fails closed, since the agent silently loses it, but it should be
    re-verified with ``scripts/goose-probe/`` before being trusted.

    Args:
        goose_binary: The ``goose`` executable (default: found on ``PATH``).

    Returns:
        A one-line notice, or ``None`` when the installed version matches the pin.

    Raises:
        Nothing; an absent or unrunnable goose yields a notice saying so.
    """
    binary = goose_binary or shutil.which("goose")
    if not binary:
        return f"goose is not on PATH; cannot confirm the grant tool map (pinned to Goose {GOOSE_MAP_VERSION})."
    if goose_binary is None and os.name == "nt" and (
        os.path.normcase(os.path.dirname(os.path.abspath(binary))) == os.path.normcase(os.getcwd())
    ):
        # Windows' which() searches the current directory first: never run a goose planted in the repo.
        return "refusing a goose found in the current directory; put the real goose on PATH."
    try:
        proc = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return f"could not run `{binary} --version`; cannot confirm the grant tool map (pinned to {GOOSE_MAP_VERSION})."
    found = re.search(r"\b(\d+\.\d+\.\d+)(\S*)", f"{proc.stdout or ''} {proc.stderr or ''}")
    version = found.group(1) + found.group(2) if found else None
    if version == GOOSE_MAP_VERSION:
        return None
    return (
        f"installed Goose is {version or 'unknown'} but the grant tool map is pinned to "
        f"{GOOSE_MAP_VERSION}; re-run scripts/goose-probe/ before trusting goose_tool_scoping on this version."
    )


#: Column-0 recipe comment recording the agent's declared tools (Goose ignores comments). The contract
#: check compares what a recipe exposes with what these tools grant, so a hand-widened recipe is caught
#: whatever its prose says.
DECLARED_MARKER = "# agentteams-declared-tools:"
_DECLARED_MARKER_RE = re.compile(r"^# agentteams-declared-tools:[ \t]*(.*)$", re.MULTILINE)


def mark_declared(recipe: str, tools: frozenset[str]) -> str:
    """Insert the declared-tools marker immediately before the recipe's ``extensions`` key.

    Args:
        recipe: An emitted grant-mode recipe.
        tools: The agent's declared canonical tools.

    Returns:
        The recipe with one marker line (``none`` when nothing is declared).

    Raises:
        Nothing.
    """
    line = f"{DECLARED_MARKER} {', '.join(sorted(tools)) or 'none'}\n"
    head, sep, tail = recipe.partition("\nextensions")
    return f"{head}\n{line}extensions{tail}" if sep else recipe


def declared_from_marker(recipe: str) -> frozenset[str] | None:
    """Return the tools recorded by :func:`mark_declared`, or ``None`` when there is no single marker.

    Args:
        recipe: Recipe YAML.

    Returns:
        The declared tools (empty for ``none``); ``None`` for no marker or more than one.

    Raises:
        Nothing.
    """
    found = _DECLARED_MARKER_RE.findall(recipe)
    if len(found) != 1:
        return None
    value = found[0].strip()
    return frozenset() if value == "none" else frozenset(t.strip().lower() for t in value.split(",") if t.strip())
