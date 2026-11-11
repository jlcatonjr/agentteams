"""_claude_wiring_check.py — is the emitted Claude settings example actually merged into the live settings.

``agentteams --check-wiring`` tells the operator whether a chosen boundary is in place. For Claude that boundary
is three things in ``.claude/settings.json``: the ``sandbox`` block, the ``hooks.PreToolUse`` entry that runs
``constitutional-gate.py``, and the ``permissions.deny`` rules (the built-in tools are bound by permissions, not by
the sandbox). The check used to compare only the sandbox block, so a live file holding that block alone passed
with no gate and no denies (IM item 03, G-22-1, reproduced on 70b2c4aa).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: The hook script whose PreToolUse entry must be merged.
GATE_SCRIPT = "constitutional-gate.py"


def _read_live(live: Path) -> dict[str, Any]:
    """The live settings as a dict, or ``{}`` when absent or unreadable (callers report that separately)."""
    try:
        data = json.loads(live.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _matcher_tools(matcher: Any) -> set[str] | None:
    """Tool names an alternation matcher covers; ``None`` means every tool (empty or ``*``)."""
    if not isinstance(matcher, str) or matcher.strip() in ("", "*", ".*"):
        return None
    return {t.strip() for t in matcher.split("|") if t.strip()}


def _gate_entries(settings: dict[str, Any]) -> list[dict[str, Any]]:
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    out = []
    for entry in hooks.get("PreToolUse") or []:
        if not isinstance(entry, dict):
            continue
        commands = [h.get("command", "") for h in entry.get("hooks") or [] if isinstance(h, dict)]
        if any(GATE_SCRIPT in str(cmd) for cmd in commands):
            out.append(entry)
    return out


def gate_and_deny_merged(example: dict[str, Any], live: dict[str, Any]) -> tuple[bool, list[str]]:
    """Check the live settings carry the emitted gate hook and every emitted permission deny.

    Args:
        example: The emitted ``settings.hooks.example.json``.
        live: The live ``.claude/settings.json`` (``{}`` when absent or unreadable).

    Returns:
        ``(ok, messages)``: False with a WARNING per gap; live values are never echoed, only emitted ones.

    Raises:
        None.
    """
    ok = True
    msgs: list[str] = []
    for want in _gate_entries(example):
        need = _matcher_tools(want.get("matcher"))
        covered = False
        for have in _gate_entries(live):
            got = _matcher_tools(have.get("matcher"))
            if got is None or (need is not None and need <= got):
                covered = True
                break
        if not covered:
            ok = False
            msgs.append(
                f"WARNING: the constitutional gate is NOT wired — .claude/settings.json has no hooks.PreToolUse entry "
                f"running {GATE_SCRIPT} for matcher {want.get('matcher')!r}. Without it no content-bearing write is "
                "checked. Merge the hooks block from .claude/settings.hooks.example.json."
            )
    ex_perm = example.get("permissions") if isinstance(example.get("permissions"), dict) else {}
    lv_perm = live.get("permissions") if isinstance(live.get("permissions"), dict) else {}
    missing = sorted(set(ex_perm.get("deny") or []) - set(lv_perm.get("deny") or []))
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
        _ok, gate_msgs = gate_and_deny_merged(ex, _read_live(live))
        return True, ["no sandbox confinement was requested for this team (cooperative).", *gate_msgs]
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
    gate_ok, gate_msgs = gate_and_deny_merged(ex, lv)
    msgs += gate_msgs
    ok = ok and gate_ok
    if ok:
        msgs.append("OK: sandbox confinement, the gate hook and the permission denies are merged in "
                    ".claude/settings.json.")
    return ok, msgs


__all__ = ["GATE_SCRIPT", "gate_and_deny_merged", "verify_emitted_sandbox_merged"]
