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

**Fail-open vs fail-closed on internal error (CC-2).** By default this hook fails OPEN on an
internal error — an attacker who can make it crash gets an allow. That is accepted for a
COOPERATIVE workspace, because a hook that can brick an operator's session gets deleted, and a
deleted hook enforces nothing. The mitigation is `agentteams.integrity`, which pins the scanner
it calls (this file itself is not in the manifest — a gap recorded 2026-08-16 in the remediation
log). For a CONFINED/EXCLUSIVE `privilege_profile`, agentteams emits this hook with
`_FAIL_CLOSED_ON_ERROR = True` (see the entry point), so a crash instead emits a `deny` rather
than a silent allow — the operator opted into a boundary, so a gate crash must not drop it.
`--allow-fallback-fail-open` restores the fail-open default even for those profiles.

The default fail-open is supplied by the HARNESS, not by a catch-all: a PreToolUse hook exiting
with any code other than 0 or 2 is a non-blocking error and the action proceeds. The single
`except` in `_entrypoint` is a process-boundary handler that ACTS (emits a deny) and reports —
not a swallow (CH-24) — and runs only under the fail-closed policy. A verdict is expressed as
JSON on stdout with exit 0; the fail-closed belt additionally uses exit code 2.

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
    (r"\b(?:crontab|launchctl)\b|/etc/sudoers|LaunchAgents",
     "persistence mechanism — outlives the current session (Rule S-9 criterion 4)"),
    (r"\b(?:brew|apt|apt-get|dnf|yum)\s+install\b|\bpip\s+install\b|\bnpm\s+i(?:nstall)?\b",
     "package installation — unreviewed third-party code on the host"),
    (r"\bgit\b[^\n]*\bpush\b[^\n]*--force|\bgit\b[^\n]*\breset\s+--hard\b",
     "history-destructive git operation"),
    # ── Delete-authorization gate (operator-directed; C-5: clearance precedes destruction) ──────────
    # BEST-EFFORT, COOPERATIVE speed bump — NOT a boundary. It routes a NAMED SUBSET of Bash delete
    # idioms to the operator ("ask") when the harness honors PreToolUse AND a human answers. It does
    # NOT cover: Write/Edit content-shrink, MCP / non-Bash tool deletes, interpreter-mediated deletion
    # not matched below, shell aliases or variable/quote obfuscation, or non-Claude consumers that do
    # not implement PreToolUse. Under headless / auto-approve it may be bypassed. Its own patterns can
    # be stripped by editing this file — an edit that agentteams.integrity.verify() detects (the hook
    # is pinned in ENFORCEMENT_MODULES), not this hook self-checking. A green delete-gate test suite
    # means "these spellings are gated", never "deletion is prevented". Authoritative limits:
    # security.template.md.
    (r"\bgh\b[^\n]*\s\w+\s+delete\b|\bgh\s+api\b[^\n]*(?:-X\s*DELETE|--method\s+DELETE)",
     "GitHub resource deletion via gh (repo/release/etc.) — irreversible"),
    (r"\bgit\b[^\n]*\bpush\b[^\n]*(?:--delete|--mirror|--prune)|\bgit\b[^\n]*\bpush\b[^\n]*\s:\S",
     "remote branch/tag deletion or mirror/prune push — irreversible remote loss"),
    (r"\bgit\b[^\n]*\b(?:branch|tag)\s+(?:-[a-zA-Z]*[dD]\b|--delete\b)|"
     r"\bgit\b[^\n]*\bupdate-ref\s+-d\b|\bgit\b[^\n]*\bworktree\s+remove\b",
     "git branch/tag/ref/worktree deletion (any -C/--git-dir prefix)"),
    (r"\brm\s+\S|\b(?:unlink|srm|wipe|rmdir|shred|truncate)\b|\bfind\b[^\n]*\s-delete\b|"
     r"\bdd\b[^\n]*\bof=|>\|\s*\S|(?:^|[;&|])\s*:\s*>\s*\S|\bmv\b[^\n]*\s/dev/null\b|\bcp\s+/dev/null\b",
     "irreversible filesystem deletion or truncation/overwrite"),
    (r"\bkubectl\b[^\n]*\bdelete\b|\bhelm\s+uninstall\b|\bterraform\s+(?:destroy|state\s+rm)\b|"
     r"\bterraform\s+apply\b[^\n]*-destroy|"
     r"\bdocker\s+(?:container\s+rm|image\s+rm|rm|rmi|volume\s+(?:rm|prune)|system\s+prune)\b|"
     r"\bdocker\s+compose\s+down\b[^\n]*(?:-v|--volumes)|"
     r"\baws\s+\S+\s+delete-|\baws\s+s3\s+r[bm]\b|\bgcloud\b[^\n]*\sdelete\b|\baz\b[^\n]*\sdelete\b|"
     r"\bdropdb\b",
     "infrastructure resource deletion"),
    (r"\b(?:psql|mysql|mariadb|sqlite3?|mongo|redis-cli|clickhouse[-a-z]*)\b[^\n]*"
     r"(?:\bDROP\s+(?:TABLE|DATABASE|SCHEMA)\b|\bTRUNCATE\b|\bDELETE\s+FROM\b)",
     "database deletion via a client invocation"),
    (r"\b(?:shutil\.rmtree|os\.(?:remove|unlink|rmdir)|rimraf)\b|\.unlink\(|\bfs\.rm(?:Sync|dirSync)?\(",
     "interpreter-mediated deletion (python/node/etc.)"),
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


#: CC-2: agentteams flips this to True at emit time for a confined/exclusive
#: privilege_profile (unless --allow-fallback-fail-open was passed at generation). When
#: True, an UNEXPECTED crash in the gate blocks the tool call instead of letting the harness
#: fail open (a crash = silent allow, which disables the gate exactly where it matters). The
#: entry-point handler below is a process-BOUNDARY reporting handler — it acts (emits a
#: deny) and reports; it is not a swallow (CH-24), and it is the only broad catch here.
_FAIL_CLOSED_ON_ERROR = False


def _entrypoint() -> int:
    """Run the gate, applying the fail-open/fail-closed policy on an unexpected crash.

    Default (``_FAIL_CLOSED_ON_ERROR`` False): an unexpected error propagates and the
    harness treats the non-zero exit as an allow — the historical fail-open behavior a
    PreToolUse hook is designed around, so a buggy gate never bricks a session.

    Confined/exclusive (``_FAIL_CLOSED_ON_ERROR`` True): an unexpected error instead emits a
    ``deny`` decision, so a gate crash cannot become a silent allow in a workspace that
    explicitly opted into a boundary.

    Returns:
        The exit code from :func:`main` on the normal path.
    """
    try:
        return main()
    except SystemExit:
        raise
    except Exception as exc:  # process-boundary handler: acts (deny) + reports, never swallows
        if not _FAIL_CLOSED_ON_ERROR:
            raise
        _decide(
            "deny",
            f"the constitutional gate crashed ({type(exc).__name__}: {exc}); failing closed "
            "because this workspace uses a confined/exclusive privilege_profile — a gate "
            "crash must not become a silent allow. Fix the gate, or regenerate with "
            "--allow-fallback-fail-open to restore the harness fail-open default.",
        )
        raise SystemExit(2)  # belt: if _decide's exit-0 deny contract ever changes, still block


if __name__ == "__main__":
    raise SystemExit(_entrypoint())
