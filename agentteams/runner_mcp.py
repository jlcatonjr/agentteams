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
SHA256 = "48a40d0f09c7aa906b48edcf791a76153db89b07d338dbc76245069823d7fa34"


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
        ``[*PYTHON_FLAGS, PROTECTED_PATH, "--root", ".", "--agent", agent, "--approval", approval, "--tools", "a,b"]``.

    Raises:
        ValueError: An unknown tool or approval, or an empty tool list.
    """
    if not tools or any(t not in TOOLS for t in tools):
        raise ValueError(f"tools must be a non-empty list from {', '.join(TOOLS)}")
    if approval not in ("staged", "direct"):
        raise ValueError("approval must be 'staged' or 'direct'")
    ordered = [t for t in TOOLS if t in tools]
    return [*PYTHON_FLAGS, PROTECTED_PATH, "--root", ".", "--agent", agent, "--approval", approval,
            "--tools", ",".join(ordered)]


__all__ = ["PROTECTED_PATH", "PYTHON_FLAGS", "SERVER_NAME", "SHA256", "TOOLS", "launch_args", "server_content"]
