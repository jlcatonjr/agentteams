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


def wiring_problems(root, framework: str, manifest: dict) -> list[str]:
    """Live-wiring problems for a team with ``mcp_grants`` (R7; @security C11, C13). Empty when none apply.

    * **C13:** no project ``.mcp.json`` or Claude settings file may define a server named ``agentteams_runner``:
      Claude Code could resolve the name to that entry instead of the canonical inline one.
    * **C11 (Claude):** the live ``.claude/settings.json`` must keep every session from writing the installed server
      and the agent files: ``permissions.deny`` holds ``Edit(/.agentteams/**)``, and the merged sandbox's
      ``filesystem.denyWrite`` holds ``.agentteams`` and ``.claude``. (Goose's Seatbelt profile is verified by its own
      wiring check.)

    Args:
        root: The project root.
        framework: ``claude`` or ``goose``.
        manifest: The team manifest.

    Returns:
        One message per problem.

    Raises:
        Nothing: an unreadable file is a problem.
    """
    import json
    from pathlib import Path

    if manifest.get("write_policy") != "orchestrator-only" or not manifest.get("mcp_grants"):
        return []
    root = Path(root)
    problems: list[str] = []
    for rel in SHADOW_FILES:
        path = root / rel
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"{rel} is unreadable ({exc}); can't rule out a shadowing {SERVER_NAME}")
            continue
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        if isinstance(servers, dict) and SERVER_NAME in servers:
            problems.append(f"{rel} defines an MCP server named {SERVER_NAME}, which could shadow the canonical inline "
                            "entry granted agents launch; remove it (@security C13)")
    if framework == "claude":
        settings = root / ".claude" / "settings.json"
        try:
            data = json.loads(settings.read_text(encoding="utf-8")) if settings.exists() else {}
        except (OSError, ValueError):
            data = {}
        deny = ((data.get("permissions") or {}).get("deny") or []) if isinstance(data, dict) else []
        fs = (((data.get("sandbox") or {}).get("filesystem") or {}) if isinstance(data, dict) else {})
        deny_write = fs.get("denyWrite") or []
        if "Edit(/.agentteams/**)" not in deny:
            problems.append("live .claude/settings.json doesn't deny Edit(/.agentteams/**): a session's Write/Edit "
                            "could replace the installed agentteams_runner server (@security C11)")
        for rel in (".agentteams", ".claude"):
            if rel not in deny_write:
                problems.append(f"live .claude/settings.json's sandbox doesn't denyWrite {rel}: a session's shell "
                                f"could change {'the installed server' if rel == '.agentteams' else 'agent files'} "
                                "(@security C11); merge the emitted sandbox block")
    return problems


__all__ = ["PROTECTED_PATH", "PYTHON_FLAGS", "SERVER_NAME", "SHA256", "SYSTEM_PYTHONS", "TOOLS", "claude_block",
           "goose_extension", "install_files", "interpreter", "launch_args", "server_content", "tool_names",
           "SHADOW_FILES", "wiring_problems"]
