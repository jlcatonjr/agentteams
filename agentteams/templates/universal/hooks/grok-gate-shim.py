#!/usr/bin/env python3
"""agentteams-managed: Grok Build -> Claude payload shim for the constitutional gate (advisory).

Grok Build sends PreToolUse hooks a Claude-shaped envelope (``hook_event_name``, ``tool_name``, ``tool_input``,
``cwd``, plus camelCase duplicates) but with its OWN tool names and input fields (spike R5, 2026-10-14):
``run_terminal_command{command}``, ``read_file{target_file}``, ``search_replace{file_path,old_string,new_string}``.
The constitutional gate reads Claude names, so this shim rewrites the INPUT only, runs the unmodified sibling
``constitutional-gate.py`` with it, and relays the gate's stdout, stderr and exit code. Grok reads the Claude reply
format (``hookSpecificOutput.permissionDecision``), so the output needs no translation.

Grok hooks fail open (a timeout, crash or malformed reply lets the call through), so this gate is ADVISORY,
never enforcement. When the gate copy beside this shim was emitted fail-closed (an explicit confined/exclusive
profile), a shim-internal failure denies (exit 2) instead of allowing.
"""

from __future__ import annotations

import sys

# Drop this script's own directory from the import path BEFORE any other import: Grok runs the shim from the
# repo's .grok/hooks, where a planted json.py or subprocess.py would otherwise shadow the stdlib (@security
# step 12e). `sys` is built in, so importing it first is safe.
sys.path[:] = [p for p in sys.path if p not in ("", sys.path[0])]

import json  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402
from pathlib import Path  # noqa: E402

#: Grok tool name -> Claude tool name (Grok's own alias table, user guide 10-hooks "Tool Name Aliases").
TOOL_NAMES = {
    "run_terminal_command": "Bash",
    "read_file": "Read",
    "search_replace": "Edit",
    "write": "Write",
    "grep": "Grep",
    "list_dir": "Glob",
    "web_search": "WebSearch",
    "web_fetch": "WebFetch",
    "spawn_subagent": "Task",
}
#: Grok input field -> Claude input field, for the file tools only: Claude's Grep and Glob take `path` themselves.
FILE_TOOLS = frozenset({"Read", "Edit", "Write"})
FIELD_NAMES = {"target_file": "file_path", "path": "file_path", "filePath": "file_path", "contents": "content"}

_GATE = Path(__file__).resolve().with_name("constitutional-gate.py")


def translate(payload: dict[str, object]) -> dict[str, object]:
    """Rewrite Grok tool names and file-tool input fields to Claude's.

    Args:
        payload: The hook payload Grok sent.

    Returns:
        A copy with ``tool_name``/``toolName`` and ``tool_input``/``toolInput`` translated; every other key kept.

    Raises:
        Nothing.
    """
    out = dict(payload)
    name = payload.get("tool_name", payload.get("toolName"))
    raw = payload.get("tool_input", payload.get("toolInput"))
    claude = TOOL_NAMES.get(name, name) if isinstance(name, str) else name
    if isinstance(name, str):
        out["tool_name"] = out["toolName"] = claude
    if isinstance(raw, dict):
        fields = FIELD_NAMES if claude in FILE_TOOLS else {}
        tool_input = {fields.get(k, k): v for k, v in raw.items()}
        out["tool_input"] = out["toolInput"] = tool_input
    return out


def _fail_closed() -> bool:
    """Whether the sibling gate copy was emitted fail-closed: its sentinel ASSIGNMENT line, not the docstring
    that mentions it."""
    try:
        return re.search(r"^_FAIL_CLOSED_ON_ERROR = True\s*$", _GATE.read_text(encoding="utf-8"), re.M) is not None
    except OSError:
        return False


def main() -> int:
    """Translate the stdin payload, run the sibling gate isolated (``-I``), relay its output and exit code.

    Returns:
        The gate's exit code; on a shim-internal failure 2 when the gate copy is fail-closed, else 0.

    Raises:
        Nothing.
    """
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            raise ValueError("hook payload is not a JSON object")
        proc = subprocess.run([sys.executable, "-I", str(_GATE)], input=json.dumps(translate(payload)),
                              capture_output=True, text=True, timeout=9, check=False)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"agentteams gate shim: {exc}", file=sys.stderr)
        return 2 if _fail_closed() else 0
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
