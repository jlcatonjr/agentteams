"""_claude_wiring_check.py — is the emitted Claude settings example actually merged into the live settings.

``agentteams --check-wiring`` tells the operator whether a chosen boundary is in place. For Claude that boundary
is three things in ``.claude/settings.json``: the ``sandbox`` block, the ``hooks.PreToolUse`` entry that runs
``constitutional-gate.py``, and the ``permissions.deny`` rules (the built-in tools are bound by permissions, not by
the sandbox). The check used to compare only the sandbox block, so a live file holding that block alone passed
with no gate and no denies (IM item 03, G-22-1, reproduced on 70b2c4aa).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

#: The hook script whose PreToolUse entry must be merged.
GATE_SCRIPT = "constitutional-gate.py"
#: The floor, required whatever the (editable) emitted example says (@security C4): the gate command and the
#: tools its matcher must cover. Mirrors templates/universal/hooks/settings.hooks.example.json.
GATE_COMMAND = 'python3 "${CLAUDE_PROJECT_DIR}/.claude/hooks/constitutional-gate.py"'
GATE_TOOLS: frozenset[str] = frozenset({"Write", "Edit", "NotebookEdit", "MultiEdit", "Bash"})
_PLAIN_MATCHER = re.compile(r"^[A-Za-z0-9_|]*$")


def _read_live(live: Path) -> dict[str, Any] | None:
    """The live settings as a dict; ``{}`` when absent, ``None`` when present but unreadable or not an object."""
    if not live.is_file():
        return {}
    try:
        data = json.loads(live.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _str_rules(value: Any) -> set[str]:
    """String entries of a deny list; anything that is not a list (or a non-string entry) contributes nothing."""
    return {r for r in value if isinstance(r, str)} if isinstance(value, list) else set()


def _covers(matcher: Any, tools: set[str]) -> bool:
    """Whether a Claude ``matcher`` (a regex) matches every tool in ``tools``.

    ``""`` and ``*`` match every tool. A plain alternation (``^[A-Za-z0-9_|]*$``) is compared as a set; anything
    else is compiled as a regex and must fullmatch each tool. A non-string, or a regex that does not compile,
    covers nothing (@security C3).
    """
    if not isinstance(matcher, str):
        return False
    if matcher in ("", "*"):
        return True
    if _PLAIN_MATCHER.match(matcher):
        return tools <= set(matcher.split("|"))
    try:
        rx = re.compile(matcher)
    except re.error:
        return False
    return all(rx.fullmatch(t) for t in tools)


def _gate_entries(settings: dict[str, Any], command: str) -> list[dict[str, Any]]:
    """``PreToolUse`` entries with a ``type: command`` hook whose command is exactly ``command`` (@security C2)."""
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    out = []
    for entry in hooks.get("PreToolUse") or []:
        if not isinstance(entry, dict):
            continue
        for h in entry.get("hooks") or []:
            if isinstance(h, dict) and h.get("type") == "command" and str(h.get("command", "")).strip() == command:
                out.append(entry)
                break
    return out


def _example_gate(example: dict[str, Any]) -> tuple[str, set[str]]:
    """The gate command (always :data:`GATE_COMMAND`) and the tools to require (the floor, widened by the example)."""
    command, tools = GATE_COMMAND, set(GATE_TOOLS)
    hooks = example.get("hooks") if isinstance(example.get("hooks"), dict) else {}
    for entry in hooks.get("PreToolUse") or []:
        for h in (entry.get("hooks") or []) if isinstance(entry, dict) else []:
            if isinstance(h, dict) and GATE_SCRIPT in str(h.get("command", "")):
                # The command is always GATE_COMMAND: the example is editable, so it may widen the tools
                # required but never change (e.g. append `|| true` to) the command (@security re-check).
                matcher = entry.get("matcher")
                if isinstance(matcher, str) and _PLAIN_MATCHER.match(matcher):
                    tools |= {t for t in matcher.split("|") if t}
    return command, tools


def gate_and_deny_merged(example: dict[str, Any], live: dict[str, Any],
                         local: dict[str, Any] | None = None) -> tuple[bool, list[str]]:
    """Check the live settings carry the gate hook (never narrower than the floor) and every emitted deny.

    Args:
        example: The emitted ``settings.hooks.example.json``.
        live: The live ``.claude/settings.json`` (``{}`` when absent).
        local: The live ``.claude/settings.local.json``, also read by Claude Code (``{}``/None when absent).

    Returns:
        ``(ok, messages)``: False with a WARNING per gap; live values are never echoed, only emitted ones.

    Raises:
        None.
    """
    ok = True
    msgs: list[str] = []
    local = local or {}
    if any(s.get("disableAllHooks") is True for s in (live, local)):
        ok = False
        msgs.append("WARNING: disableAllHooks is true in the live Claude settings, so the constitutional gate never "
                    "runs. Remove it (settings.json / settings.local.json).")
    command, tools = _example_gate(example)
    entries = _gate_entries(live, command) + _gate_entries(local, command)
    if not any(_covers(e.get("matcher"), tools) for e in entries):
        ok = False
        msgs.append(
            f"WARNING: the constitutional gate is NOT wired — no hooks.PreToolUse entry runs exactly {command!r} "
            f"(type command) with a matcher covering {sorted(tools)}. Without it no content-bearing write is "
            "checked. Merge the hooks block from .claude/settings.hooks.example.json."
        )
    ex_perm = example.get("permissions") if isinstance(example.get("permissions"), dict) else {}
    lv_perm = live.get("permissions") if isinstance(live.get("permissions"), dict) else {}
    missing = sorted(_str_rules(ex_perm.get("deny")) - _str_rules(lv_perm.get("deny")))
    if missing:
        ok = False
        msgs.append(
            f"WARNING: the live permissions.deny is missing {len(missing)} emitted rule(s) {missing!r} — the built-in "
            "tools (Read/Edit/Write) are bound by these, not by the sandbox. Merge the permissions block."
        )
    return ok, msgs


def verify_emitted_sandbox_merged(project_root: Path) -> tuple[bool, list[str]]:
    """Is the emitted settings example merged into the live ``.claude/settings.json``.

    The P1-3 sandbox check, plus the gate hook and the permission denies (IM item 03, G-22-1): a live file
    holding only the sandbox block used to pass, with no ``PreToolUse`` gate and no ``permissions.deny``. When a
    sandbox boundary was requested, a missing gate hook or deny rule fails the check. On a cooperative team
    (no boundary) it is reported as a WARNING without failing, so teams that passed before do not start failing
    (warn-first).

    Args:
        project_root: The generated project root (holds ``.claude/``).

    Returns:
        ``(ok, messages)``.

    Raises:
        None.
    """
    claude_dir = project_root / ".claude"
    example = claude_dir / "settings.hooks.example.json"
    live = claude_dir / "settings.json"
    if not example.is_file():
        return True, ["no emitted settings example found — nothing to verify."]
    try:
        ex = json.loads(example.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True, [f"could not read {example} as JSON — skipping wiring check."]
    ex_sandbox = ex.get("sandbox") if isinstance(ex, dict) else None
    if not (isinstance(ex_sandbox, dict) and ex_sandbox.get("enabled")):
        note = "no sandbox confinement was requested for this team (cooperative)."
        if live.is_file() and _read_live(live) is None:
            return True, [note, "WARNING: .claude/settings.json is unreadable or invalid JSON; cannot confirm the "
                                "gate hook is wired."]
        _ok, gate_msgs = gate_and_deny_merged(ex, _read_live(live) or {}, _read_live(live.with_name("settings.local.json")))
        return True, [note, *gate_msgs]
    if not live.is_file():
        return False, [
            "WARNING: unmerged boundary — NOT enforced. A sandbox confinement was emitted "
            "but .claude/settings.json does not exist. Merge the emitted example block."
        ]
    try:
        lv = json.loads(live.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, [
            "WARNING: .claude/settings.json is unreadable or invalid JSON; cannot confirm "
            "the boundary is live."
        ]
    lv_sandbox = lv.get("sandbox") if isinstance(lv, dict) else None
    if not (isinstance(lv_sandbox, dict) and lv_sandbox.get("enabled") is True):
        return False, [
            "WARNING: unmerged boundary — NOT enforced. .claude/settings.json has no "
            "enabled sandbox block. Merge the emitted example block."
        ]
    msgs: list[str] = []
    ok = True
    if lv_sandbox.get("allowUnsandboxedCommands") is not False:
        ok = False
        msgs.append(
            "WARNING: sandbox.allowUnsandboxedCommands is not false in the live settings — "
            "the unsandboxed-command escape hatch is open, so confinement can be bypassed."
        )
    ex_fs = ex_sandbox.get("filesystem") or {}
    lv_fs = lv_sandbox.get("filesystem") or {}
    ex_roots = ex_fs.get("allowWrite")
    lv_roots = lv_fs.get("allowWrite")
    if ex_roots != lv_roots:
        ok = False
        # Follow-up #2: a WIDENING (the emitted example adds roots) must not be waved through as
        # "re-merge": the brief is agent-writable. Only emitted values are echoed, never live ones.
        live_set = set(lv_roots) if isinstance(lv_roots, list) else set()
        added = [r for r in (ex_roots or []) if r not in live_set]
        if added:
            msgs.append(
                f"WARNING: the emitted sandbox block WIDENS allowWrite by {added!r} "
                f"(expected allowWrite={ex_roots!r}). Review where each root came from before "
                "re-merging: an agent-writable brief can add roots (generation prints the source)."
            )
        else:
            msgs.append(
                "WARNING: the live sandbox write roots differ from the emitted expectation "
                f"(expected allowWrite={ex_roots!r}) — re-merge so the boundary matches."
            )
    # denyWrite (control-plane protection) + denyRead (exclusive read-exclusion) must not be
    # dropped/shortened on merge (audit 2026-W39, security F4). A merge that keeps the block
    # but strips denyWrite re-enables the in-sandbox agent to edit its own gate/switch; a
    # shortened denyRead silently un-seals credential paths. Compare as sets (order-agnostic)
    # and require the live set to be a SUPERSET of the emitted expectation.
    for key, label in (("denyWrite", "control-plane denyWrite"), ("denyRead", "read-exclusion denyRead")):
        ex_vals = ex_fs.get(key)
        if not ex_vals:
            continue  # not emitted for this profile (e.g. denyRead only for exclusive)
        missing = set(ex_vals) - set(lv_fs.get(key) or [])
        if missing:
            ok = False
            msgs.append(
                f"WARNING: the live sandbox {label} is missing entries the emitted block "
                f"expects ({sorted(missing)!r}) — re-merge so the boundary matches; a dropped "
                f"{key} weakens the boundary while it still looks enabled."
            )
    gate_ok, gate_msgs = gate_and_deny_merged(ex, lv, _read_live(live.with_name("settings.local.json")))
    msgs += gate_msgs
    ok = ok and gate_ok
    if ok:
        msgs.append("OK: sandbox confinement, the gate hook and the permission denies are merged in "
                    ".claude/settings.json.")
    return ok, msgs


def gate_warnings(project_root: Path) -> list[str]:
    """The gate/deny WARNINGs for a team without a boundary (cooperative or switched off); never a failure.

    Args:
        project_root: The generated project root.

    Returns:
        The warning lines (empty when the gate and denies are merged, or no example was emitted).

    Raises:
        None.
    """
    claude_dir = project_root / ".claude"
    example = claude_dir / "settings.hooks.example.json"
    ex = _read_live(example)
    if not ex:
        return []
    live = claude_dir / "settings.json"
    lv = _read_live(live)
    if lv is None:
        return ["WARNING: .claude/settings.json is unreadable or invalid JSON; cannot confirm the gate hook is wired."]
    return gate_and_deny_merged(ex, lv, _read_live(claude_dir / "settings.local.json"))[1]


__all__ = ["GATE_COMMAND", "GATE_SCRIPT", "GATE_TOOLS", "gate_and_deny_merged", "gate_warnings",
           "verify_emitted_sandbox_merged"]
