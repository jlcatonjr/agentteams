"""runner_mcp.py — install and launch facts for the ``agentteams_runner`` MCP server (R4).

The server (``agentteams/data/agentteams-runner-mcp.py``) is how a non-orchestrator agent writes and executes under
``write_policy: "orchestrator-only"``: its tools queue requests for the runner. Generation installs it into the
session-write-denied control plane at :data:`PROTECTED_PATH`, and every granted agent launches that copy with
``python3 -I -S``. The runner checks the installed copy against :data:`SHA256` on every poll and refuses to serve on a
mismatch (:func:`agentteams.proposal_runner.check_installed_servers`). This module is integrity-pinned, so the pin
itself can't be edited silently. Plan: ``references/plans/mcp-mediated-agent-writes.plan.md``.
"""

from __future__ import annotations

from importlib import resources

#: The MCP server name agents see (``mcp__agentteams_runner__<tool>``).
SERVER_NAME = "agentteams_runner"
#: The tools a grant may name (same list as ``proposal_policy.MCP_RUNNER_TOOLS``; a test keeps them equal).
TOOLS: tuple[str, ...] = ("write_file", "delete_file", "run_command", "request_status", "read_file_hashed")
#: Where generation installs the server: inside the control plane every session sandbox write-denies.
PROTECTED_PATH = ".agentteams/bin/agentteams-runner-mcp.py"
#: Python flags for the launch: no PYTHON* variables, user site-packages, script-dir imports or site hooks.
PYTHON_FLAGS: tuple[str, ...] = ("-I", "-S")
#: sha256 of the shipped server; ``tests/test_runner_mcp.py`` keeps it current.
SHA256 = "6bbde08ce2e9673a3c4edb06672a55a82e65516d2d1ea84870f3d28e0bb4c259"


def server_content() -> str:
    """The shipped server's source.

    Returns:
        The text of ``agentteams/data/agentteams-runner-mcp.py``.

    Raises:
        OSError: The package data is missing.
    """
    return resources.files("agentteams").joinpath("data", "agentteams-runner-mcp.py").read_text(encoding="utf-8")


def launch_args(agent: str, tools: list[str], approval: str = "staged") -> list[str]:
    """The canonical argument list for one agent's server instance (after the interpreter).

    Args:
        agent: The granted agent's slug (bound into the instance; the runner checks it against the nonce).
        tools: The granted tools, in :data:`TOOLS` order.
        approval: ``"staged"`` or ``"direct"`` (a direct grant is honoured only once verified by the runner).

    Returns:
        ``[*PYTHON_FLAGS, PROTECTED_PATH, "--agent", agent, "--approval", approval, "--tools", "a,b"]``. The server
        derives the project root from its install location, so no machine path is written into the team.

    Raises:
        ValueError: An unknown tool or approval, or an empty tool list.
    """
    if not tools or any(t not in TOOLS for t in tools):
        raise ValueError(f"tools must be a non-empty list from {', '.join(TOOLS)}")
    if approval not in ("staged", "direct"):
        raise ValueError("approval must be 'staged' or 'direct'")
    ordered = [t for t in TOOLS if t in tools]
    return [*PYTHON_FLAGS, PROTECTED_PATH, "--agent", agent, "--approval", approval, "--tools", ",".join(ordered)]


#: Interpreters tried, in order, for the launch command: absolute, system-wide (never under a home directory, so no
#: username is written into the team), and never a project-local or PATH-planted ``python3`` (@security R4 cond. 5).
SYSTEM_PYTHONS: tuple[str, ...] = ("/usr/bin/python3", "/usr/local/bin/python3", "/opt/homebrew/bin/python3")


def interpreter() -> str:
    """The absolute system ``python3`` agents launch the server with.

    Returns:
        The first of :data:`SYSTEM_PYTHONS` that exists.

    Raises:
        ValueError: None exists; the operator installs one or sets up the team on a machine that has one.
    """
    import os

    for path in SYSTEM_PYTHONS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    raise ValueError(f"no system python3 at {', '.join(SYSTEM_PYTHONS)}; the agentteams_runner server needs one")


def tool_names(tools: list[str]) -> list[str]:
    """Claude's exact tool names for granted tools: ``mcp__agentteams_runner__<tool>``, in :data:`TOOLS` order.

    Args:
        tools: The granted tools.

    Returns:
        The names.

    Raises:
        ValueError: An unknown tool.
    """
    if any(t not in TOOLS for t in tools):
        raise ValueError(f"unknown agentteams_runner tool in {tools!r}")
    return [f"mcp__{SERVER_NAME}__{t}" for t in TOOLS if t in tools]


def claude_block(agent: str, tools: list[str], approval: str, python: str) -> str:
    """The canonical ``mcpServers:`` front-matter block for a granted Claude agent (the audit compares it exactly).

    Args:
        agent: The agent.
        tools: Its granted tools.
        approval: ``staged`` or ``direct``.
        python: The interpreter (:func:`interpreter`).

    Returns:
        The YAML lines, ending in a newline.

    Raises:
        ValueError: From :func:`launch_args`.
    """
    import json as _json

    args = ", ".join(_json.dumps(a) for a in launch_args(agent, tools, approval))
    return (f"mcpServers:\n  - {SERVER_NAME}:\n      type: stdio\n      command: {_json.dumps(python)}\n"
            f"      args: [{args}]\n")


def goose_extension(agent: str, tools: list[str], approval: str, python: str) -> dict:
    """The canonical Goose stdio extension for a granted agent (the audit compares it exactly).

    Args:
        agent: The agent.
        tools: Its granted tools.
        approval: ``staged`` or ``direct``.
        python: The interpreter (:func:`interpreter`).

    Returns:
        The extension mapping, with an exact ``available_tools`` list.

    Raises:
        ValueError: From :func:`launch_args`.
    """
    return {"type": "stdio", "name": SERVER_NAME, "cmd": python, "args": launch_args(agent, tools, approval),
            "env_keys": [], "timeout": 300, "available_tools": [t for t in TOOLS if t in tools]}


def install_files(manifest: dict) -> list[tuple[str, str]]:
    """The server install for a rendered team: ``[("../../" + PROTECTED_PATH, source)]`` when any agent is granted.

    Relative to an agents directory two levels below the root (``.claude/agents``, ``.goose/recipes``). Empty
    unless the switch is on and ``mcp_grants`` names someone, so every other team is unchanged.

    Args:
        manifest: The team manifest.

    Returns:
        The ``(relative path, content)`` pairs for the emit phase.

    Raises:
        ValueError: The packaged server doesn't match :data:`SHA256` (fail closed: the runner would refuse it).
    """
    import hashlib

    if manifest.get("write_policy") != "orchestrator-only" or not manifest.get("mcp_grants"):
        return []
    source = server_content()
    if hashlib.sha256(source.encode("utf-8")).hexdigest() != SHA256:
        raise ValueError("the agentteams_runner server in this agentteams install doesn't match its pinned hash; "
                         "reinstall agentteams before rendering")
    return [(f"../../{PROTECTED_PATH}", source)]


#: Project files where an ``mcpServers`` entry would sit beside (and could shadow) the inline agentteams_runner.
SHADOW_FILES: tuple[str, ...] = (".mcp.json", ".claude/settings.json", ".claude/settings.local.json")
#: Machine-wide Claude Code MCP config (managed scope), per platform.
MANAGED_MCP_FILES: tuple[str, ...] = ("/Library/Application Support/ClaudeCode/managed-mcp.json",
                                      "/etc/claude-code/managed-mcp.json")
#: Live Claude rules a granted team needs (@security C4, C11).
REQUIRED_CLAUDE_DENY: tuple[str, ...] = ("Edit(/.agentteams/**)", "Edit(/.claude/agents/**)", "Read(/.agentteams/**)",
                                         "Read(/.agentteams-queue/**)")


def _json(path):
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def _shadowing(root, home) -> list[str]:
    """Every config scope that defines a server named agentteams_runner (@security C13, R7 review item 1).

    Which entry Claude Code prefers between a subagent's inline server and a configured one of the same name isn't
    something to rely on, so a match in any scope is refused: project ``.mcp.json`` and settings, the user's
    ``~/.claude.json`` (top level and this project's local entry), machine-managed config, and Goose's user config.
    """
    import os
    import re
    from pathlib import Path

    found: list[str] = []
    candidates = [(rel, Path(root) / rel) for rel in SHADOW_FILES]
    candidates += [(p, Path(p)) for p in MANAGED_MCP_FILES]
    for label, path in candidates:
        if not path.exists():
            continue
        try:
            servers = (_json(path) or {}).get("mcpServers")
        except (OSError, ValueError, AttributeError) as exc:
            found.append(f"{label} is unreadable ({exc}); can't rule out a shadowing {SERVER_NAME}")
            continue
        if isinstance(servers, dict) and SERVER_NAME in servers:
            found.append(f"{label} defines an MCP server named {SERVER_NAME}")
    user = Path(home) / ".claude.json"
    if user.exists():
        try:
            data = _json(user) or {}
            scopes = [("~/.claude.json", data.get("mcpServers"))]
            project = (data.get("projects") or {}).get(os.path.realpath(root)) or {}
            scopes.append(("~/.claude.json (this project)", project.get("mcpServers")))
            found += [f"{label} defines an MCP server named {SERVER_NAME}" for label, servers in scopes
                      if isinstance(servers, dict) and SERVER_NAME in servers]
        except (OSError, ValueError, AttributeError) as exc:
            found.append(f"~/.claude.json is unreadable ({exc}); can't rule out a shadowing {SERVER_NAME}")
    goose = Path(home) / ".config" / "goose" / "config.yaml"
    if goose.exists():
        try:
            if re.search(rf"^\s+{SERVER_NAME}\s*:", goose.read_text(encoding="utf-8"), re.MULTILINE):
                found.append(f"~/.config/goose/config.yaml defines an extension named {SERVER_NAME}")
        except OSError as exc:
            found.append(f"~/.config/goose/config.yaml is unreadable ({exc})")
    return found


def wiring_problems(root, framework: str, manifest: dict, *, home=None) -> list[str]:
    """Live-wiring problems for a team with ``mcp_grants`` (R7; @security C4, C11, C13). Empty when none apply.

    * **C13:** no config scope may define a server named ``agentteams_runner`` (:func:`_shadowing`).
    * **Claude (C4, C11):** the live ``.claude/settings.json`` denies :data:`REQUIRED_CLAUDE_DENY`, and its merged
      sandbox ``denyWrite`` holds ``.agentteams`` and ``.claude``; ``settings.local.json`` may not switch the
      sandbox off or allow unsandboxed commands.
    * **Goose (C11):** the emitted Seatbelt profile (``.goose/sandbox.sb``) denies writes to ``.agentteams`` and
      ``.goose/recipes``.

    Args:
        root: The project root.
        framework: ``claude`` or ``goose``.
        manifest: The team manifest.
        home: The home directory to read user config from (tests); defaults to ``~``.

    Returns:
        One message per problem.

    Raises:
        Nothing: an unreadable file is a problem.
    """
    import os
    from pathlib import Path

    if manifest.get("write_policy") != "orchestrator-only" or not manifest.get("mcp_grants"):
        return []
    root = Path(root)
    problems = [f"{p}; remove it so it can't shadow the canonical inline server granted agents launch "
                "(@security C13)" for p in _shadowing(root, home or os.path.expanduser("~"))]
    if framework == "claude":
        def load(rel):
            path = root / ".claude" / rel
            try:
                return _json(path) if path.exists() else {}
            except (OSError, ValueError):
                problems.append(f"live .claude/{rel} is unreadable")
                return {}
        data, local = load("settings.json"), load("settings.local.json")
        deny = ((data.get("permissions") or {}).get("deny") or []) if isinstance(data, dict) else []
        for rule in REQUIRED_CLAUDE_DENY:
            if rule not in deny:
                problems.append(f"live .claude/settings.json doesn't deny {rule} (@security C4/C11); merge the "
                                "emitted permissions")
        fs = ((data.get("sandbox") or {}).get("filesystem") or {}) if isinstance(data, dict) else {}
        for rel in (".agentteams", ".claude"):
            if rel not in (fs.get("denyWrite") or []):
                problems.append(f"live .claude/settings.json's sandbox doesn't denyWrite {rel} (@security C11); "
                                "merge the emitted sandbox block")
        sandbox_local = (local.get("sandbox") or {}) if isinstance(local, dict) else {}
        if sandbox_local.get("enabled") is False or sandbox_local.get("allowUnsandboxedCommands") is True:
            problems.append(".claude/settings.local.json turns the sandbox off or allows unsandboxed commands, which "
                            "overrides settings.json (@security C11)")
    elif framework == "goose":
        profile = root / ".goose" / "sandbox.sb"
        text = profile.read_text(encoding="utf-8") if profile.exists() else ""
        for rel in (".agentteams", ".goose/recipes"):
            if f'"{rel}"' not in text and f"/{rel}\"" not in text:
                problems.append(f".goose/sandbox.sb doesn't deny writes to {rel} (@security C11); re-render with the "
                                "switch and wire the profile in")
    return problems


__all__ = ["PROTECTED_PATH", "PYTHON_FLAGS", "SERVER_NAME", "SHA256", "SYSTEM_PYTHONS", "TOOLS", "claude_block",
           "goose_extension", "install_files", "interpreter", "launch_args", "server_content", "tool_names",
           "MANAGED_MCP_FILES", "REQUIRED_CLAUDE_DENY", "SHADOW_FILES", "wiring_problems"]
