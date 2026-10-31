"""_sandbox_drift.py — brief/live drift helpers for ``--check-wiring`` (P00 D8). Read-only; never writes live config.

When the brief (or ``--os-sandbox``) switches the OS sandbox off but the operator's live config still enables one,
the verifiers report ``DRIFT`` and leave the change to the operator. The dedicated switch's "off" always counts. A
profile-derived off (``cooperative``) counts only when a previous boundary existed (the build-log records a
``sandbox_state``, which a render writes only once a switch or a boundary was in play; wave-2 @security S2): then it
is reported as ``RELAXED`` (or ``DRIFT`` while the live config still enables one), never as "no confinement was
requested". A cooperative team that never had one keeps each verifier's pre-P00 behaviour, so an operator's own
hand-merged sandbox there is not reported. A present but unparsable live ``settings.json`` is ``UNREADABLE``
(not OK), never "nothing to verify". Live values are never echoed (they may hold secrets).

While WASM is on, the live config must also carry the control-plane rules (METAPLAN R1); P13 reads
:func:`verify_control_plane_merged` before claiming Track-A enforcement.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from agentteams.sandbox_state import SandboxState

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


#: The message for a present but unparsable live ``.claude/settings.json`` (H2: not OK, never "nothing to verify").
_CLAUDE_UNREADABLE = ("UNREADABLE: .claude/settings.json exists but is not a readable JSON object, so whether a "
                      "sandbox is still live cannot be confirmed. Fix the file, then re-run --check-wiring.")


def _as_state(state: object) -> SandboxState | None:
    """Return ``state`` when it is a :class:`~agentteams.sandbox_state.SandboxState`, else None (H3 guard)."""
    from agentteams.sandbox_state import SandboxState

    return state if isinstance(state, SandboxState) else None


def switched_off(state: SandboxState | None, *, previous_boundary: bool = False) -> bool:
    """Return whether ``state`` is an OS-off the drift check must report rather than pass as "not requested".

    Args:
        state: The sandbox state, or None (anything else is treated as None).
        previous_boundary: Whether a previous boundary existed for this team (:func:`previous_boundary_recorded`).

    Returns:
        True for ``os`` off with reason ``brief`` or ``cli`` (the dedicated switch), or for any other off reason
        (``profile:cooperative``) when ``previous_boundary`` is True.

    Raises:
        Nothing.
    """
    st = _as_state(state)
    return st is not None and not st.os and (st.os_reason in ("brief", "cli") or previous_boundary)


def previous_boundary_recorded(team_dir: Path) -> bool:
    """Return whether a previous render of this team recorded a ``sandbox_state`` (a boundary or switch existed).

    Args:
        team_dir: The team directory (holds ``references/build-log.json``).

    Returns:
        True when the build-log carries the key, or exists but cannot be read (fail toward reporting).

    Raises:
        Nothing.
    """
    from agentteams.sandbox_state import has_recorded_state

    return has_recorded_state(team_dir)


def off_message(state: SandboxState | None) -> str:
    """Return the line for an off team whose live config no longer enables a sandbox.

    Args:
        state: The (off) sandbox state, or None.

    Returns:
        "nothing to verify" for the dedicated switch; a ``RELAXED`` line for a profile-derived off (a previous
        boundary existed); a reason-less line for None.

    Raises:
        Nothing.
    """
    st = _as_state(state)
    if st is None:
        return "OS sandbox is off for this team; nothing to verify."
    if st.os_reason in ("brief", "cli"):
        return f"OS sandbox is off for this team by {st.os_reason}; nothing to verify."
    return (f"RELAXED: the OS sandbox was on for this team and is now off ({st.os_reason}); no OS boundary is "
            "emitted or live. Set \"os_sandbox\": \"on\" (or a confined privilege_profile) to restore it.")


def _why_off(state: SandboxState | None) -> str:
    """The DRIFT line's subject: the switch turned it off, or a profile-derived off relaxed a previous boundary."""
    st = _as_state(state)
    if st is None or st.os_reason in ("brief", "cli"):
        return "the brief turns the OS sandbox off"
    return f"the OS sandbox was relaxed to off ({st.os_reason})"


def _load_live_claude_settings(project_root: Path) -> dict[str, Any] | None:
    """Return the live ``.claude/settings.json`` object: ``{}`` when absent, None when present but unusable."""
    path = Path(project_root) / ".claude" / "settings.json"
    if not os.path.lexists(path):
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def claude_off_drift(project_root: Path, state: SandboxState | None) -> tuple[bool, list[str]]:
    """Report whether an off Claude team's live settings still enable a sandbox.

    Args:
        project_root: The project root (holds ``.claude/settings.json``).
        state: The (off) sandbox state.

    Returns:
        ``(ok, messages)``: ``ok`` False with a ``DRIFT`` line when ``sandbox.enabled`` is still True, or with an
        ``UNREADABLE`` line when the live settings exist but do not parse to a JSON object.

    Raises:
        Nothing.
    """
    live = _load_live_claude_settings(project_root)
    if live is None:
        return False, [_CLAUDE_UNREADABLE]
    live_sb = live.get("sandbox")
    if isinstance(live_sb, dict) and live_sb.get("enabled") is True:
        return False, [f"DRIFT: {_why_off(state)} but .claude/settings.json still enables it. "
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
        ``(control_plane_merged, missing rule labels)``; only expected rules are named, never live values. A present
        but unparsable live settings file is ``(False, ["UNREADABLE .claude/settings.json"])``.

    Raises:
        Nothing.
    """
    live = _load_live_claude_settings(project_root)
    if live is None:
        return False, ["UNREADABLE .claude/settings.json"]
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


def goose_off_drift(state: SandboxState | None, live_enabled: bool | None) -> tuple[bool, list[str]]:
    """Report whether an off Goose team's live (user-global) config still enables ``GOOSE_SANDBOX``.

    Args:
        state: The (off) sandbox state.
        live_enabled: ``_live_goose_sandbox_enabled`` of the live config (None when unreadable).

    Returns:
        ``(ok, messages)``: ``ok`` False with a ``DRIFT`` line (and the user-global note) when it is still on.

    Raises:
        Nothing.
    """
    if live_enabled is True:
        return False, [f"DRIFT: {_why_off(state)} but the live goose config still enables "
                       "GOOSE_SANDBOX. To match, remove GOOSE_SANDBOX (or set it to 0) by hand. "
                       + GOOSE_USER_GLOBAL_NOTE]
    return True, [off_message(state)]


def goose_control_plane_ok(state: SandboxState | None, profile_text: str, path_expr: Callable[[str], str | None],
                           msgs: list[str]) -> bool:
    """Check that ``.goose/sandbox.sb`` carries the R1 deny while WASM is on; append the finding to ``msgs``.

    Args:
        state: The sandbox state, or None.
        profile_text: The emitted profile.
        path_expr: ``_goose_sandbox_emit._seatbelt_path_expr``.
        msgs: The verifier's messages (a ``WASM CONTROL PLANE NOT MERGED`` line is appended when it fails).

    Returns:
        True when WASM is off or the deny block carries ``.agentteams`` and ``.goose/recipes``.

    Raises:
        Nothing.
    """
    st = _as_state(state)
    if st is None or not st.wasm:
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
    "previous_boundary_recorded",
    "switched_off",
    "verify_control_plane_merged",
]
