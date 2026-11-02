#!/usr/bin/env python3
"""agentteams Codex role gate: a PreToolUse hook for ``write_policy: "orchestrator-only"`` (stdlib-only).

WHY THIS EXISTS
---------------
Codex drops a custom agent's ``sandbox_mode`` (spawned agents inherit the session's sandbox), so the
tool narrowing agentteams applies to Claude and Goose agents has no Codex equivalent in the agent
files. Codex does tell a PreToolUse hook which agent is calling: a spawned agent's calls carry
``agent_type`` (its custom-agent name) and ``agent_id`` on stdin, filled in by the Codex runtime, and the
top-level session's calls (the orchestrator) carry neither (probed live on codex-cli 0.160.1; see
``references/plans/codex-enforced-sandbox.design.md``). This gate uses that to give every spawned agent
the same shape Goose agents get under the policy: the read-only ``agentteams_readfs`` tools and nothing
else.

DECISION RULE
-------------
* No ``agent_type`` and no ``agent_id`` (the top-level session): allowed. The orchestrator is the one writer.
* Any ``agent_type`` or ``agent_id``, whatever the name (a spawned ``orchestrator``, and a generic subagent,
  which Codex tags ``default``, included): only
  ``mcp__agentteams_readfs__<read tool>`` is allowed. Shell, patch, spawn, web and every other MCP
  tool are denied.
* Any event other than PreToolUse (only PreToolUse is wired): denied.
* ``AGENTTEAMS_CODEX_CONFINED`` not ``1`` (Codex not started by ``.codex/confined-run.example.sh``):
  everything is denied, so a direct launch that trusts this hook still cannot run tools.

WASM MODE (WASM plan P13)
-------------------------
Without ``write_policy`` but with WASM on, the runner sets ``AGENTTEAMS_CODEX_WASM=1`` and
``AGENTTEAMS_CODEX_NATIVE_KEEP`` (comma-separated agent names that keep the shell: the exempt orchestrator and the
operator's ``native_exec_keep``). Then a spawned agent outside that list is denied the shell tool names in
:data:`SHELL_TOOLS` and steered to ``run_wasm``; every other tool (``run_wasm`` included) passes. Only ``Bash``
was probed live (codex-cli 0.160.1); the other names are unprobed defensive spellings, and an unknown shell
tool name passes (stated in the team's ``wasm_enforcement`` record). A malformed keep list denies everything.

FAIL-CLOSED
-----------
Codex blocks a call when the hook exits 2 and lets it through on exit 1 (probed live), so every
deny, malformed input and unexpected error exits 2 with the reason on stderr. ``.codex/hooks.json``
runs this as ``"$AGENTTEAMS_PYTHON" -I -S <this> || exit 2`` (an absolute interpreter the runner resolves
outside the launcher), so a missing interpreter, file or variable also blocks.

LIMITS (stated, not hidden)
---------------------------
This is harness-level, like Claude's tool grants: it holds only while Codex runs the hook, which the
runner arranges (``--dangerously-bypass-hook-trust``, with ``.codex/`` and ``.agentteams/`` read-only
under the launcher). It relies on Codex's undocumented hook input staying as probed. Codex also lets a
call through when a hook TIMES OUT (probed), so the gate must stay fast: stdlib only, no I/O beyond a capped
stdin read. The OS boundary (the launcher) keeps the ledger key and ``.agentteams`` out of reach either way.

Integrity-pinned (``references/enforcement-integrity.json``); the runner also checks its sha256.
"""

from __future__ import annotations

import json
import os
import sys

#: The read-only server's tools (``goose_tool_scoping.READFS_TOOLS``), as Codex names MCP tools.
READFS_PREFIX = "mcp__agentteams_readfs__"
READFS_TOOLS = frozenset({"read_file", "list_dir", "find", "grep", "stat"})
#: WASM mode: shell tool names denied to a spawned agent ("Bash" probed live; the rest defensive, unprobed).
SHELL_TOOLS = frozenset({"Bash", "shell", "local_shell", "exec_command", "unified_exec"})
#: The keep list's agent-name grammar (agentteams slugs).
_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")
#: Hard cap on the stdin read, so a huge payload can't stall the gate.
_MAX_INPUT = 4 * 1024 * 1024


def parse_keep(raw: str) -> frozenset[str]:
    """Parse ``AGENTTEAMS_CODEX_NATIVE_KEEP`` (comma-separated agent names; empty allowed).

    Args:
        raw: The variable's value.

    Returns:
        The names.

    Raises:
        ValueError: A name outside ``[a-z0-9-]`` or starting with ``-``.
    """
    names = frozenset(n for n in raw.split(",") if n)
    for name in names:
        if name.startswith("-") or not set(name) <= _NAME_CHARS:
            raise ValueError("malformed AGENTTEAMS_CODEX_NATIVE_KEEP")
    return names


def decide(event: object, confined: bool, wasm: bool = False, keep: frozenset[str] = frozenset()) -> str | None:
    """Return a deny reason for one PreToolUse event, or ``None`` to allow it.

    Args:
        event: The parsed hook input.
        confined: Whether the runner started Codex (``AGENTTEAMS_CODEX_CONFINED=1``).
        wasm: WASM mode (``AGENTTEAMS_CODEX_WASM=1``): deny only :data:`SHELL_TOOLS` to spawned agents.
        keep: WASM mode: agent names that keep the shell.

    Returns:
        ``None`` to allow; otherwise the reason shown to the agent.
    """
    if not confined:
        return ("Codex was not started by .codex/confined-run.example.sh; under write_policy "
                "orchestrator-only this team's tools run only inside agentteams' launcher")
    if not isinstance(event, dict):
        return "malformed hook input"
    if event.get("hook_event_name") != "PreToolUse":
        return f"unexpected hook event {event.get('hook_event_name')!r} (only PreToolUse is wired)"
    agent_type, agent_id = event.get("agent_type"), event.get("agent_id")
    if agent_type in (None, "") and agent_id in (None, ""):
        return None
    if not isinstance(agent_type, str) or not agent_type:
        agent_type = f"<unnamed {agent_id!r}>"
    tool = event.get("tool_name")
    if wasm:
        if not isinstance(tool, str) or tool in SHELL_TOOLS and agent_type not in keep:
            return (f"WASM sandbox: agent {agent_type!r} may not use native exec ({tool!r}); run code with "
                    "mcp__agentteams_wasm__run_wasm instead")
        return None
    if isinstance(tool, str) and tool.startswith(READFS_PREFIX) and tool[len(READFS_PREFIX):] in READFS_TOOLS:
        return None
    return (f"write_policy orchestrator-only: agent {agent_type!r} may use only the read-only "
            f"agentteams_readfs tools, not {tool!r}. Return a proposal to the orchestrator instead")


def main() -> int:
    """Read one hook event from stdin and exit 0 (allow) or 2 (deny).

    Returns:
        The exit status.
    """
    try:
        raw = sys.stdin.buffer.read(_MAX_INPUT + 1)
        if len(raw) > _MAX_INPUT:
            reason: str | None = "hook input too large"
        else:
            wasm = os.environ.get("AGENTTEAMS_CODEX_WASM") == "1"
            keep = parse_keep(os.environ.get("AGENTTEAMS_CODEX_NATIVE_KEEP", "")) if wasm else frozenset()
            reason = decide(json.loads(raw.decode("utf-8")),
                            os.environ.get("AGENTTEAMS_CODEX_CONFINED") == "1", wasm, keep)
    except BaseException as exc:  # noqa: BLE001 - any failure must deny, never fall through to allow
        reason = f"role gate error ({type(exc).__name__}); denying"
    if reason is None:
        return 0
    sys.stderr.write(f"agentteams role gate: {reason}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
