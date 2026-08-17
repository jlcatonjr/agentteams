#!/usr/bin/env python3
"""constitutional-gate.py — PreToolUse hook enforcing C-4 and C-5 on agent tool calls.

**Why this exists.** ``agentteams/cli/security_gate.py`` is a real, fail-closed gate — and it
guards four CLI entry points, not the agents. A 2026-08-06 red-team audit (probe E3) recorded
the consequence: an agent deleting files with ``Bash``, or writing a credential with ``Write``,
never reaches it. For agent-initiated actions C-5 was procedural text.

This hook is the counterpart. The harness runs it *before* the tool call, so unlike every
control inside ``agentteams/`` it is not merely another file the agents can edit on their way
past it (probe E4).

**Verdict policy — deliberately split.**

* ``deny`` for **deterministic** findings: content about to be written that the scanner rates
  high severity (credentials, PII paths, injected override text). There is no legitimate reason
  to write those, so a block costs nothing and asking would train the operator to click through.
* ``ask`` for **procedural** findings: Bash commands matching a Mandatory Review Trigger.
  Whether ``sudo`` or a pipe-to-shell is appropriate is a judgment call the constitution routes
  to a human, and a hook that denied them outright would be wrong often enough to get removed.

**Known limitation, stated rather than discovered later.** This hook fails OPEN on internal
error — an attacker who can make it crash gets an allow. That gap is real and is accepted,
because a hook that can brick an operator's session gets deleted, and a deleted hook enforces
nothing. The mitigation is `agentteams.integrity`, which pins the scanner it calls (this file itself
is not in the manifest — a gap recorded 2026-08-16 in the remediation log).

The fail-open is supplied by the HARNESS, not by a catch-all here: a PreToolUse hook exiting
with any code other than 0 or 2 is a non-blocking error and the action proceeds. So this file
carries no blanket `except` (CH-24) and still cannot brick a session. Only exit code 2 blocks,
and nothing here uses it — a verdict is expressed as JSON on stdout with exit 0.

Input:  a JSON tool-call payload on stdin (``tool_name``, ``tool_input``).
Output: exit 0, plus a ``hookSpecificOutput`` JSON decision on stdout when acting.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HOOK_EVENT = "PreToolUse"

#: Bash patterns that match a Mandatory Review Trigger in security.template.md. Each is a
#: judgment call routed to the operator, never an automatic deny — see the verdict policy above.
_BASH_REVIEW_TRIGGERS: tuple[tuple[str, str], ...] = (
    (r"\bsudo\b|\bdoas\b", "elevated privilege — effects outside the project tree"),
    (r"curl[^|]*\|\s*(?:ba)?sh|wget[^|]*\|\s*(?:ba)?sh|iwr[^|]*\|\s*iex",
     "remote content piped straight into a shell with no inspectable step (Rule S-9 criterion 2)"),
    (r"\brm\s+-[a-zA-Z]*[rf]", "recursive/forced delete — irreversible file loss"),
    (r"\b(?:crontab|launchctl)\b|/etc/sudoers|LaunchAgents",
     "persistence mechanism — outlives the current session (Rule S-9 criterion 4)"),
    (r"\b(?:brew|apt|apt-get|dnf|yum)\s+install\b|\bpip\s+install\b|\bnpm\s+i(?:nstall)?\b",
     "package installation — unreviewed third-party code on the host"),
    (r"\bgit\s+push\b.*--force|\bgit\s+reset\s+--hard\b",
     "history-destructive git operation"),
)

_WRITE_TOOLS = frozenset({"Write", "Edit", "NotebookEdit", "MultiEdit"})


def _decide(decision: str, reason: str) -> None:
    """Emit a hook decision and exit 0. Exit 0 + JSON is the structured-decision contract."""
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": HOOK_EVENT,
                "permissionDecision": decision,
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    sys.stdout.write("\n")
    raise SystemExit(0)


def _written_content(tool_name: str, tool_input: dict) -> tuple[str, str]:
    """Return ``(content, target_path)`` for a write-shaped tool call."""
    if tool_name == "Write":
        return tool_input.get("content", ""), tool_input.get("file_path", "")
    if tool_name in {"Edit", "MultiEdit"}:
        edits = tool_input.get("edits")
        if isinstance(edits, list):
            return "\n".join(str(e.get("new_string", "")) for e in edits), tool_input.get("file_path", "")
        return str(tool_input.get("new_string", "")), tool_input.get("file_path", "")
    if tool_name == "NotebookEdit":
        return str(tool_input.get("new_source", "")), tool_input.get("notebook_path", "")
    return "", ""


def main() -> int:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0

    if tool_name == "Bash":
        command = str(tool_input.get("command", ""))
        for pattern, why in _BASH_REVIEW_TRIGGERS:
            if re.search(pattern, command, re.IGNORECASE):
                _decide(
                    "ask",
                    f"Mandatory Review Trigger: {why}. C-5 requires a recorded clearance BEFORE "
                    f"execution, not after. Approve only if this is the operation you intended.",
                )
        return 0

    if tool_name in _WRITE_TOOLS:
        content, target = _written_content(tool_name, tool_input)
        if not content:
            return 0

        # Verify the scanner before trusting its verdict. Without this the hook faithfully asks
        # a possibly-tampered module whether the write is safe, which is the E4 failure wearing
        # a hook. `ask` rather than `deny`: a stale manifest after a legitimate edit to scan.py
        # is the common case and must not brick the session — and `allow` would be the failure
        # this exists to catch.
        #
        # This does NOT escape E4. An attacker who can edit scan.py can edit the manifest and
        # this file. It raises the cost from one edit to three and makes each visible in git.
        try:
            from agentteams import integrity
        except ModuleNotFoundError:
            # The interpreter puts this script's own directory on sys.path, not the repo
            # root, so on a checkout where agentteams isn't pip-installed these imports
            # crash — and a crash is a silent allow (any exit other than 0/2 is a
            # non-blocking error), disabling the gate exactly where it matters. Derive
            # the repo root from this file's location rather than trusting cwd.
            sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
            from agentteams import integrity

        tampered = [f for f in integrity.verify(Path.cwd()) if f.rel_path.endswith("scan.py")]
        if tampered:
            _decide(
                "ask",
                "the content scanner does not match the integrity manifest ("
                + "; ".join(f.describe() for f in tampered)
                + "). Its verdict on this write cannot be trusted. Regenerate the manifest if "
                "you changed scan.py deliberately, or investigate.",
            )

        from agentteams.scan import scan_content, verdict_for_findings
        findings = scan_content(content, filename=target or "<hook>")
        high = [f for f in findings if f.severity == "high"]
        if high:
            detail = "; ".join(f"L{f.line} [{f.category}] {f.message}" for f in high[:3])
            _decide(
                "deny",
                f"agentteams.scan returned HALT ({verdict_for_findings(findings)}) for this "
                f"write: {detail}. Rule S-1/S-5/S-8 apply to any committed file. Remove the "
                f"flagged content rather than re-issuing the write.",
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
