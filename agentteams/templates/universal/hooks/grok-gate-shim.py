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

import json
import subprocess
import sys
from pathlib import Path

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
#: Grok input field -> Claude input field.
FIELD_NAMES = {"target_file": "file_path", "path": "file_path", "filePath": "file_path", "contents": "content"}

_GATE = Path(__file__).resolve().with_name("constitutional-gate.py")


def translate(payload: dict) -> dict:
    """Return *payload* with Grok tool names and input fields rewritten to Claude's (other keys untouched)."""
    out = dict(payload)
    name = payload.get("tool_name", payload.get("toolName"))
    raw = payload.get("tool_input", payload.get("toolInput"))
    if isinstance(name, str):
        out["tool_name"] = out["toolName"] = TOOL_NAMES.get(name, name)
    if isinstance(raw, dict):
        tool_input = {FIELD_NAMES.get(k, k): v for k, v in raw.items()}
        out["tool_input"] = out["toolInput"] = tool_input
    return out


def _fail_closed() -> bool:
    try:
        return "_FAIL_CLOSED_ON_ERROR = True" in _GATE.read_text(encoding="utf-8")
    except OSError:
        return False


def main() -> int:
    """Translate stdin, run the gate, relay its result."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            raise ValueError("hook payload is not a JSON object")
        proc = subprocess.run([sys.executable, str(_GATE)], input=json.dumps(translate(payload)),
                              capture_output=True, text=True, timeout=9, check=False)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"agentteams gate shim: {exc}", file=sys.stderr)
        return 2 if _fail_closed() else 0
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
