"""_sandbox_drift.py — brief/live drift helpers for ``--check-wiring`` (P00 D8). Read-only; never writes live config.

When the brief (or ``--os-sandbox``) switches the OS sandbox off but the operator's live config still enables one,
the verifiers report ``DRIFT`` and leave the change to the operator. Only the dedicated switch's "off" counts: a
profile-derived off (``cooperative``) keeps each verifier's pre-P00 behaviour, so an operator's own hand-merged
sandbox on a cooperative team is not reported. Live values are never echoed (they may hold secrets).

While WASM is on, the live config must also carry the control-plane rules (METAPLAN R1); P13 reads
:func:`verify_control_plane_merged` before claiming Track-A enforcement.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

#: Appended to every message that tells the operator to change the live goose config.
GOOSE_USER_GLOBAL_NOTE = ("This file is user-global (~/.config/goose/config.yaml): changing it affects every Goose "
                          "project on this machine, not only this one.")

#: The control-plane rules (METAPLAN R1) the live Claude settings must carry while WASM is on:
#: ``(label, section, value)``; ``section`` is ``denyWrite`` (sandbox.filesystem) or ``deny`` (permissions).
_CLAUDE_CONTROL_PLANE_RULES: tuple[tuple[str, str, str], ...] = (
    ("sandbox.filesystem.denyWrite .agentteams", "denyWrite", ".agentteams"),
    ("permissions.deny Edit(/.agentteams/**)", "deny", "Edit(/.agentteams/**)"),
    ("permissions.deny Edit(/.claude/agents/**)", "deny", "Edit(/.claude/agents/**)"),
)


def switched_off(state: Any) -> bool:
    """Return whether ``state`` is an OS-off set by the dedicated switch (brief ``os_sandbox`` or ``--os-sandbox``).

    Args:
        state: A :class:`~agentteams.sandbox_state.SandboxState`, or None.

    Returns:
        True only for ``os`` off with reason ``brief`` or ``cli``.
    """
    return state is not None and not state.os and state.os_reason in ("brief", "cli")


def off_message(state: Any) -> str:
    """Return the "nothing to verify" line for a switched-off team.

    Args:
        state: The (off) sandbox state.

    Returns:
        The message.
    """
    return f"OS sandbox is off for this team by {state.os_reason}; nothing to verify."


def _load_live_claude_settings(project_root: Path) -> dict[str, Any]:
    try:
        data = json.loads((Path(project_root) / ".claude" / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def claude_off_drift(project_root: Path, state: Any) -> tuple[bool, list[str]]:
    """Report whether a switched-off Claude team's live settings still enable a sandbox.

    Args:
        project_root: The project root (holds ``.claude/settings.json``).
        state: The (switched-off) sandbox state.

    Returns:
        ``(ok, messages)``: ``ok`` False with a ``DRIFT`` line when ``sandbox.enabled`` is still True.
    """
    live_sb = _load_live_claude_settings(project_root).get("sandbox")
    if isinstance(live_sb, dict) and live_sb.get("enabled") is True:
        return False, ["DRIFT: the brief turns the OS sandbox off but .claude/settings.json still enables it. "
                       "To match, remove the \"sandbox\" key (or set sandbox.enabled false) by hand."]
    return True, [off_message(state)]


def verify_control_plane_merged(project_root: Path,
                                extra_protected: tuple[str, ...] = ()) -> tuple[bool, list[str]]:
    """Report whether the live Claude settings carry the WASM control-plane rules (METAPLAN R1; read by P13).

    Track-A WASM enforcement may be claimed only when the OS sandbox is on and this returns True.

    Args:
        project_root: The project root (holds ``.claude/settings.json``).
        extra_protected: MCP config paths that launch ``agentteams_wasm`` (each needs a denyWrite and an ``Edit``).

    Returns:
        ``(control_plane_merged, missing rule labels)``; only expected rules are named, never live values.
    """
    live = _load_live_claude_settings(project_root)
    sandbox = live.get("sandbox") if isinstance(live.get("sandbox"), dict) else {}
    fs = sandbox.get("filesystem") if isinstance(sandbox.get("filesystem"), dict) else {}
    perms = live.get("permissions") if isinstance(live.get("permissions"), dict) else {}
    have = {"denyWrite": {v for v in (fs.get("denyWrite") or []) if isinstance(v, str)},
            "deny": {v for v in (perms.get("deny") or []) if isinstance(v, str)}}
    rules = list(_CLAUDE_CONTROL_PLANE_RULES)
    for p in extra_protected:
        rules += [(f"sandbox.filesystem.denyWrite {p}", "denyWrite", p),
                  (f"permissions.deny Edit(/{p})", "deny", f"Edit(/{p})")]
    missing = [label for label, section, value in rules if value not in have[section]]
    return not missing, missing


def goose_off_drift(state: Any, live_enabled: bool | None) -> tuple[bool, list[str]]:
    """Report whether a switched-off Goose team's live (user-global) config still enables ``GOOSE_SANDBOX``.

    Args:
        state: The (switched-off) sandbox state.
        live_enabled: ``_live_goose_sandbox_enabled`` of the live config (None when unreadable).

    Returns:
        ``(ok, messages)``: ``ok`` False with a ``DRIFT`` line (and the user-global note) when it is still on.
    """
    if live_enabled is True:
        return False, ["DRIFT: the brief turns the OS sandbox off but the live goose config still enables "
                       "GOOSE_SANDBOX. To match, remove GOOSE_SANDBOX (or set it to 0) by hand. "
                       + GOOSE_USER_GLOBAL_NOTE]
    return True, [off_message(state)]


def goose_control_plane_ok(state: Any, profile_text: str, path_expr: Callable[[str], str | None],
                           msgs: list[str]) -> bool:
    """Check that ``.goose/sandbox.sb`` carries the R1 deny while WASM is on; append the finding to ``msgs``.

    Args:
        state: The sandbox state, or None.
        profile_text: The emitted profile.
        path_expr: ``_goose_sandbox_emit._seatbelt_path_expr``.
        msgs: The verifier's messages (a ``WASM CONTROL PLANE NOT MERGED`` line is appended when it fails).

    Returns:
        True when WASM is off or the deny block carries ``.agentteams`` and ``.goose/recipes``.
    """
    if state is None or not state.wasm:
        return True
    missing = [p for p in (".agentteams", ".goose/recipes") if (path_expr(p) or p) not in profile_text]
    if missing:
        msgs.append(f"WASM CONTROL PLANE NOT MERGED: .goose/sandbox.sb deny block lacks {', '.join(missing)}")
    return not missing


__all__ = [
    "GOOSE_USER_GLOBAL_NOTE",
    "claude_off_drift",
    "goose_control_plane_ok",
    "goose_off_drift",
    "off_message",
    "switched_off",
    "verify_control_plane_merged",
]
