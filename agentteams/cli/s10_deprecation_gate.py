"""s10_deprecation_gate.py — the Rule 15 gate for deprecating Rule S-10's package-release cooldown (agentteams#592).

The brief key ``s10_cooldown_deprecated: true`` asks the render to show Rule S-10's 14-day package-release cooldown as
deprecated in the security agent, the vulnerability-watch reference and the instructions files. That relaxes a
security rule, so a brief edit alone must not do it in a governed workspace (@security design review, 2026-11-21). The
render reads only ``manifest["s10_cooldown_deprecated_authorized"]``, which this gate sets to ``"signed"``,
``"brief"`` or None; a path that skips the gate (convert, interop) renders the cooldown in force.

The action is the single id ``s10-cooldown-deprecated`` (:data:`DEPRECATION_ACTION`), a constraint-relaxing effect:

* **Governed workspace** (``enforce_decision_signing``, read from the project's canonical ``.github/agents`` as
  :func:`agentteams.cli.s10_exempt_gate.governance_team_dir` resolves it): authorized when an active exception in the
  registry covers the action and is still bound to a decision row that re-verifies, or when a new decision row (not a
  waiver) has ``scope`` and ``effect_relaxes`` equal to the action exactly and no ``effect_grants``, classifies as an
  operator-signed exception, and clears the destructive-action gate. A new one is consumed and registered under the
  aggregate cap with a :data:`DEPRECATION_TTL_DAYS` sunset, which the operator re-signs (Rule 15(d); a permanent,
  no-slot form would need its own operator ruling). Anything else renders the cooldown, with a notice naming the row
  to record and an ``::error`` line when a registered deprecation stopped carrying over (agentteams#476). The render
  always goes ahead (fail-closed for the relaxation, never for the team).
* **Ungoverned workspace:** the brief value renders, with a notice (operator ruling T3, extended to this field on
  2026-11-21).

**HALTs (C-2).** The deprecation lifts the cooldown for every repository, so it would get around a HALT on any
narrower cooldown relaxation. An unretracted HALT on any of :data:`HALT_ACTIONS` in the canonical dir, this run's dir
or any surface dir removes it, before carry-over and before a new row is consumed. ``s10-cooldown`` is checked as a
HALT family only: no gate may ever clear the bare ``s10-cooldown`` action, since every ``s10-cooldown-*`` row would
match it as a prefix.

Rule 12, named: the ungoverned case trusts the brief, as every ungoverned relaxation does; and the deprecated wording
is fixed text in :mod:`agentteams.manifest_format`, never taken from the brief (C-4).

Stdlib only.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentteams.cli import security_gate, toolchain_group
from agentteams.cli.decision_log import _enforce_decision_signing
from agentteams.cli.effect_classifier import EffectClassifierError, requires_operator_signature
from agentteams.cli.exception_registry import (
    ExceptionRegistryError,
    active_relaxing_exceptions,
    assert_within_aggregate_cap,
    entries_for,
    register_exception,
    retire_exception,
)
from agentteams.cli.s10_exempt_gate import (
    CANONICAL_TEAM_DIR,
    S10_EXEMPT_KIND,
    _project_root_of,
    _surface_dirs,
    governance_team_dir,
)
from agentteams.cli.sandbox_relaxation_gate import _decision_binding, _split, _unbound_reason
from agentteams.manifest_format import S10_DEPRECATION_TTL_DAYS, s10_cooldown_deprecation_authorized

#: The action id, which is also its own family.
DEPRECATION_ACTION = "s10-cooldown-deprecated"

#: The sunset of a registered deprecation (Rule 15(d)); the operator re-signs it, and a lapse renders the cooldown.
DEPRECATION_TTL_DAYS = S10_DEPRECATION_TTL_DAYS

#: Every action whose unretracted HALT removes the deprecation. ``s10-cooldown`` matches any ``s10-cooldown-*`` HALT
#: (an exempt repository, the exempt family, this action); the toolchain group's family is a separate id.
HALT_ACTIONS: tuple[str, ...] = (DEPRECATION_ACTION, "s10-cooldown", S10_EXEMPT_KIND, toolchain_group.FAMILY)


def _shape() -> str:
    return (f"record a PASS row signed with the operator's Ed25519 key (--sign-decision): "
            f"action_reviewed={DEPRECATION_ACTION}, scope={DEPRECATION_ACTION}, effect_relaxes={DEPRECATION_ACTION}, "
            f"derives_from=<a recorded decision id>")


class CarryOverError(RuntimeError):
    """A registered deprecation did not carry over; ``env`` is True when the cause is a missing Ed25519 backend."""

    def __init__(self, message: str, *, env: bool = False) -> None:
        super().__init__(message)
        self.env = env


def _assert_no_halt(halt_dirs: tuple[Path, ...]) -> None:
    """Raise RuntimeError when an unretracted HALT on any of :data:`HALT_ACTIONS` is in any of *halt_dirs*."""
    for halt_dir in dict.fromkeys(halt_dirs):
        for halt_action in HALT_ACTIONS:
            security_gate._assert_no_unretracted_halt(halt_dir, action=halt_action)


def _check_new(team_dir: Path) -> dict[str, str]:
    """Every check for a new decision row, consuming nothing; return the row (raises RuntimeError on refusal)."""
    action = DEPRECATION_ACTION
    if security_gate._latest_security_waiver(team_dir, action=action) is not None:
        raise RuntimeError(f"a waiver cannot authorize '{action}'")
    row = security_gate._latest_security_decision(team_dir, action=action)
    if row is None:
        raise RuntimeError("the decisions log has no unconsumed decision for it")
    if (row.get("action_reviewed") or "").strip().lower() != action:
        raise RuntimeError(f"the decision must carry action_reviewed={action} exactly")
    if (row.get("scope") or "").strip().lower() != action:
        raise RuntimeError(f"the decision must carry scope={action} exactly")
    relaxed, granted = _split(row.get("effect_relaxes") or ""), _split(row.get("effect_grants") or "")
    if relaxed != {action} or granted:
        raise RuntimeError(f"the decision must carry effect_relaxes={action} exactly and no effect_grants")
    try:
        exception = requires_operator_signature(row, kind="decision")
    except EffectClassifierError as exc:
        raise RuntimeError(f"the decision is inconsistent: {exc}") from exc
    if not exception:  # unreachable while effect_relaxes classifies as relaxing; fail closed if that ever drifts
        raise RuntimeError("the decision does not classify as an operator-signed exception")
    security_gate._assert_destructive_action_allowed(team_dir, action=DEPRECATION_ACTION, consume=False)
    return row


def _authorize(team_dir: Path, *, consume: bool, now: datetime, halt_dirs: tuple[Path, ...]) -> None:
    """Authorize the deprecation in governed *team_dir* or raise (RuntimeError, ExceptionRegistryError, OSError,
    ValueError, or :class:`CarryOverError` when a registered deprecation stopped carrying over)."""
    action = DEPRECATION_ACTION
    _assert_no_halt((team_dir, *halt_dirs))   # C-2 first, before any carry-over
    entries = entries_for(team_dir, action)
    reasons: list[str] = []
    for entry in active_relaxing_exceptions({"exceptions": entries}, now=now):
        reason = _unbound_reason(team_dir, entry)
        if reason is None:
            return
        reasons.append(f"registered deprecation {entry.get('id')} did not carry over: it {reason}")
    lapsed = [str(e.get("id")) for e in entries if (e.get("status") or "active").strip().lower() == "active"]
    try:
        row = _check_new(team_dir)
    except RuntimeError as exc:
        if not reasons:
            raise
        raise CarryOverError("; ".join(reasons) + f" (and no new row: {exc})",
                             env=any("'signing' extra" in r for r in reasons)) from exc
    assert_within_aggregate_cap(team_dir, adding=1, now=now, retiring=lapsed)
    if not consume:
        return
    security_gate._assert_destructive_action_allowed(team_dir, action=DEPRECATION_ACTION, consume=True)
    entry_id = f"{action}@{(row.get('signature') or '').strip()[:16]}"
    for old_id in lapsed:
        retire_exception(team_dir, old_id, reason=f"replaced by {entry_id}")
    register_exception(team_dir, {
        "id": entry_id, "action": action, "kind": DEPRECATION_ACTION, "scope": action, "relaxing": True,
        "effect": {"relaxes": [action]},
        "lineage": sorted(_split(row.get("derives_from") or "")),
        "issued": now.isoformat(),
        "expires": (now + timedelta(days=DEPRECATION_TTL_DAYS)).isoformat(),
        "decision_key_id": (row.get("key_id") or "").strip(),
        "decision_sha256": _decision_binding(row),
    }, now=now)


def authorized_deprecation(team_dir: Path, requested: object, *, consume: bool,
                           now: datetime | None = None) -> str | None:
    """Return how the cooldown deprecation is authorized (``"signed"``, ``"brief"``) or None, printing why when not.

    Args:
        team_dir: This run's team directory. A native surface authorizes against
            :func:`~agentteams.cli.s10_exempt_gate.governance_team_dir`; HALTs in every surface dir still bind.
        requested: The manifest's ``s10_cooldown_deprecated``; only the boolean True requests anything.
        consume: True to consume a new decision row and register its exception; False (dry run, ``--check``) checks
            only and writes nothing.
        now: Reference time (defaults to now, UTC).

    Returns:
        ``"signed"`` (governed, authorized), ``"brief"`` (ungoverned), or None (not requested, or refused).

    Raises:
        Nothing: an unreadable signing switch, a refusal or an unreadable log renders the cooldown (fail-closed for
        the relaxation, never for the render).
    """
    if requested is not True:
        return None
    try:
        own, team_dir = team_dir, governance_team_dir(team_dir)
        governed = _enforce_decision_signing(team_dir)
        root = _project_root_of(own)
        halt_dirs = tuple(d for d in (own, *(_surface_dirs(root) if root is not None else ())) if d != team_dir)
    except (RuntimeError, OSError) as exc:   # a symlink loop, a permission error, an unreadable switch
        print(f"  ✗  S-10 cooldown deprecation NOT applied (the {CANONICAL_TEAM_DIR} signing switch or the team dirs "
              f"can't be read: {exc}); the cooldown renders in force.", file=sys.stderr)
        return None
    if not governed:
        try:
            _assert_no_halt((team_dir, *halt_dirs))
        except (RuntimeError, OSError, ValueError) as exc:
            print(f"  ✗  S-10 cooldown deprecation NOT applied ({exc}); the cooldown renders in force.",
                  file=sys.stderr)
            return None
        print("  !  S-10 COOLDOWN DEPRECATED by the brief alone (this workspace is not governed, so no signed decision "
              "is required).", file=sys.stderr)
        return "brief"
    try:
        _authorize(team_dir, consume=consume, now=now or datetime.now(UTC), halt_dirs=halt_dirs)
    except (RuntimeError, ExceptionRegistryError, OSError, ValueError) as exc:
        print(f"  ✗  S-10 cooldown deprecation NOT applied ({exc}); the cooldown renders in force. To apply it, "
              f"{_shape()}", file=sys.stderr)
        if isinstance(exc, CarryOverError):   # agentteams#476: a dropped registered relaxation is loud in CI
            kind = "environment" if exc.env else "carry-over"
            print(f"::error title=S-10 cooldown deprecation {kind}::{exc}", file=sys.stderr)
        return None
    return "signed"


def authorize_deprecation_into(manifest: dict, team_dir: Path, *, consume: bool) -> bool:
    """Set ``manifest["s10_cooldown_deprecated_authorized"]`` through the gate; the render reads only that key.

    Args:
        manifest: The team manifest (``s10_cooldown_deprecated`` from the brief).
        team_dir: The team directory.
        consume: Passed to :func:`authorized_deprecation`.

    Returns:
        Whether the deprecation is authorized (the value ``build_security_placeholders`` takes as
        ``cooldown_deprecated``).

    Raises:
        Nothing.
    """
    value = authorized_deprecation(team_dir, manifest.get("s10_cooldown_deprecated"), consume=consume)
    manifest["s10_cooldown_deprecated_authorized"] = value
    return s10_cooldown_deprecation_authorized(value)


__all__ = ["DEPRECATION_ACTION", "DEPRECATION_TTL_DAYS", "HALT_ACTIONS", "authorize_deprecation_into",
           "authorized_deprecation"]
