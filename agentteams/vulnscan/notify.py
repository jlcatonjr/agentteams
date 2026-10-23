"""Operator notifications with **fixed text only**.

Agent-derived text (rationale, escalation evidence, report content) never goes into a
notification. A ``command:`` notifier may forward the message off the machine, so letting agent
text through would turn notifications into an exfiltration channel (R6 security review, M-5).
Every message here is a fixed template filled with identifiers and counts only.

Delivery follows the config's ``notify`` setting: ``none``, ``desktop`` (macOS ``osascript`` /
Linux ``notify-send``) or ``command:<program and args>``. The command receives the message as
its last argument; it no longer receives a summary path.
"""

from __future__ import annotations

import json
import platform
import re
import shlex
import shutil
import subprocess
import sys
from typing import Any

from agentteams.vulnscan.escalations import FLOOD_KIND, KINDS

TITLE = "agentteams vulnscan"
_ID = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")

#: The only messages vulnscan sends. Placeholders take ids / counts / fixed state words only.
MESSAGES: dict[str, str] = {
    "escalation": "Escalation {id} ({kind}) opened — remediation is blocked for its scope. Run: vulnscan halts",
    "halt-refusal": "A @security HALT blocked remediation this run. Run: vulnscan halts",
    "run-done": "Daily run {state}: {findings} findings ({new} new). Run: vulnscan review",
    "agent-not-completed": "Agent run {state}. Run: vulnscan review",
    "publish-failed": "Publishing the run report FAILED (kept locally). Run: vulnscan review",
    "publish-done": "Run report published to the ops repository.",
}
_STATES = {"completed", "agent-error", "timed-out", "not-run", "action needed", "partially unscanned",
           "COMPROMISE INDICATORS", "new findings", "clean"}
_KINDS = {*KINDS, FLOOD_KIND}


def render(key: str, **fields: Any) -> str:
    """Render one fixed message, refusing any field that is not an id, a count or a known word.

    Args:
        key: A :data:`MESSAGES` key.
        **fields: Placeholder values.

    Returns:
        The message text.

    Raises:
        KeyError: Unknown message key.
        ValueError: A field value is not a safe identifier, integer, state or kind.
    """
    template = MESSAGES[key]
    safe: dict[str, str] = {}
    for name, value in fields.items():
        if isinstance(value, int) and not isinstance(value, bool):
            safe[name] = str(value)
        elif name == "state" and value in _STATES:
            safe[name] = str(value)
        elif name == "kind" and value in _KINDS:
            safe[name] = str(value)
        elif name == "id" and isinstance(value, str) and _ID.match(value):
            safe[name] = value
        else:
            raise ValueError(f"notification field {name!r} is not a safe fixed value")
    return template.format(**safe)


def send(cfg: dict[str, Any], key: str, **fields: Any) -> bool:
    """Deliver one fixed message according to ``cfg["notify"]``.

    Args:
        cfg: Daily config.
        key: A :data:`MESSAGES` key.
        **fields: Placeholder values (see :func:`render`).

    Returns:
        True when a notifier was invoked; False for ``none``, when no notifier is available, or when
        the message could not be rendered (an unknown key or an unsafe field is refused and reported
        on stderr, never sent, and never stops the caller's run).
    """
    try:
        message = render(key, **fields)
    except (KeyError, ValueError) as exc:
        print(f"vulnscan: notification {key!r} refused: {exc}", file=sys.stderr)
        return False
    how = str(cfg.get("notify", "desktop"))
    if how == "none":
        return False
    if how.startswith("command:"):
        try:
            cmd = [*shlex.split(how[len("command:"):]), message]
        except ValueError as exc:   # unbalanced quotes in the config
            print(f"vulnscan: notify command is malformed: {exc}", file=sys.stderr)
            return False
    elif platform.system() == "Darwin" and shutil.which("osascript"):
        cmd = ["osascript", "-e", f"display notification {json.dumps(message)} with title {json.dumps(TITLE)}"]
    elif shutil.which("notify-send"):
        cmd = ["notify-send", TITLE, message]
    else:
        return False
    try:
        subprocess.run(cmd, capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"vulnscan: notification failed: {exc}", file=sys.stderr)
        return False
    return True
