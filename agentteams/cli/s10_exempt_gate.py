"""s10_exempt_gate.py — the Rule 15 gate for Rule S-10's local interdependent-repository carve-out.

The brief key ``s10_cooldown_exempt_repos`` lifts the 14-day package-release cooldown for the project's own
repositories (Jim's ruling, 2026-11-03). Listing a repository there relaxes a security rule, so a brief edit alone must
not do it (@security, 2026-11-03): the render shows only the repositories this gate authorizes, and never reads the
raw brief value (``render`` falls back to "none" when the gate did not run, e.g. on a convert or interop path).

Each listed repository is the action ``s10-cooldown-exempt:<owner/name>``, a constraint-relaxing effect. Modelled on
:mod:`agentteams.cli.sandbox_relaxation_gate`, whose decision-binding and re-verification helpers it reuses:

* **Governed workspace** (``enforce_decision_signing``, read from disk): a repository is authorized when an active
  exception in the registry covers it and is still bound to a decision row that re-verifies (approved author, the
  operator's Ed25519 signature, lineage, hash chain), or when a new decision row (not a waiver) has ``scope`` and
  ``effect_relaxes`` equal to the action exactly, classifies as an operator-signed exception, and clears the
  destructive-action gate with no unretracted HALT on the action or the whole family. A new one is consumed and
  registered under the aggregate cap with a :data:`S10_EXEMPT_TTL_DAYS` sunset. Anything else is left out of the
  rendered rule, with a notice naming the row to record; the render itself goes ahead (fail-closed for the
  relaxation, never for the team).
* **Ungoverned workspace:** every valid listed repository renders, with a notice, as sandbox relaxations do there.

Rule 12, named: the ungoverned case trusts the brief, as every ungoverned relaxation does; and a refusal drops the
repository silently from the rule's text except for the notice, so the cooldown then applies to it (over-tightening).

Stdlib only.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentteams.cli import security_gate
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
from agentteams.cli.sandbox_relaxation_gate import _decision_binding, _split, _unbound_reason
from agentteams.manifest_format import _REPO_ID_RE

#: The action family; an action id is ``s10-cooldown-exempt:<owner/name>`` (lowercased).
S10_EXEMPT_KIND = "s10-cooldown-exempt"

#: The sunset of a registered exemption (Rule 15(d)); a later render re-checks it and drops it once it lapses.
S10_EXEMPT_TTL_DAYS = 90


def action_for(repo: str) -> str:
    """The gate action id for *repo* (``owner/name``)."""
    return f"{S10_EXEMPT_KIND}:{repo.lower()}"


def _shape(action: str) -> str:
    return (f"record a PASS row signed with the operator's Ed25519 key (--sign-decision): action_reviewed={action}, "
            f"scope={action}, effect_relaxes={action}, derives_from=<a recorded decision id>")


def _check_new(team_dir: Path, action: str) -> dict[str, str]:
    """Every check for a new decision row, consuming nothing; return the row (raises RuntimeError on refusal)."""
    if security_gate._latest_security_waiver(team_dir, action=action) is not None:
        raise RuntimeError(f"a waiver cannot authorize '{action}'")
    row = security_gate._latest_security_decision(team_dir, action=action)
    if row is None:
        raise RuntimeError("the decisions log has no unconsumed decision for it")
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
    exempt_action, exempt_family = action, S10_EXEMPT_KIND
    security_gate._assert_no_unretracted_halt(team_dir, action=exempt_family)   # the family (C-2)
    security_gate._assert_destructive_action_allowed(team_dir, action=exempt_action, consume=False)
    return row


def _authorize_one(team_dir: Path, repo: str, *, consume: bool, now: datetime) -> None:
    """Authorize *repo* or raise (RuntimeError, ExceptionRegistryError, OSError or ValueError)."""
    action = action_for(repo)
    exempt_action, exempt_family = action, S10_EXEMPT_KIND
    # C-2 first, before any carry-over: a HALT recorded after registration removes the repository at once.
    security_gate._assert_no_unretracted_halt(team_dir, action=exempt_family)
    security_gate._assert_no_unretracted_halt(team_dir, action=exempt_action)
    entries = entries_for(team_dir, action)
    active = active_relaxing_exceptions({"exceptions": entries}, now=now)
    if any(_unbound_reason(team_dir, e) is None for e in active):
        return
    lapsed = [str(e.get("id")) for e in entries if (e.get("status") or "active").strip().lower() == "active"]
    row = _check_new(team_dir, action)
    assert_within_aggregate_cap(team_dir, adding=1, now=now, retiring=lapsed)
    if not consume:
        return
    security_gate._assert_destructive_action_allowed(team_dir, action=exempt_action, consume=True)
    entry_id = f"{action}@{(row.get('signature') or '').strip()[:16]}"
    for old_id in lapsed:
        retire_exception(team_dir, old_id, reason=f"replaced by {entry_id}")
    register_exception(team_dir, {
        "id": entry_id, "action": action, "kind": S10_EXEMPT_KIND, "scope": action, "relaxing": True,
        "effect": {"relaxes": [action]},
        "lineage": sorted(_split(row.get("derives_from") or "")),
        "issued": now.isoformat(),
        "expires": (now + timedelta(days=S10_EXEMPT_TTL_DAYS)).isoformat(),
        "decision_key_id": (row.get("key_id") or "").strip(),
        "decision_sha256": _decision_binding(row),
    }, now=now)


def authorized_exempt_repos(team_dir: Path, repos: object, *, consume: bool,
                            now: datetime | None = None) -> list[str]:
    """Return the listed repositories the carve-out may name, printing a notice for each one left out.

    Args:
        team_dir: The team directory (decisions log, exception registry, signing switch).
        repos: The brief's ``s10_cooldown_exempt_repos`` value.
        consume: True to consume a new decision row and register its exception; False (dry run, ``--check``)
            checks only and writes nothing.
        now: Reference time (defaults to now, UTC).

    Returns:
        The authorized ``owner/name`` identifiers, in the brief's order, de-duplicated ignoring case. Invalid
        entries are dropped.

    Raises:
        RuntimeError: The signing switch can't be read (fail-closed). A per-repository refusal or an unreadable log
            raises nothing: it leaves that repository out (the cooldown applies to it).
    """
    by_key: dict[str, str] = {}  # case-folded: GitHub treats Foo/Bar and foo/bar as one repository
    for r in repos if isinstance(repos, list) else []:
        if isinstance(r, str) and _REPO_ID_RE.fullmatch(r):
            by_key.setdefault(r.lower(), r)
    valid = list(by_key.values())
    if not valid:
        return []
    if not _enforce_decision_signing(team_dir):
        print(f"  !  S-10 COOLDOWN CARVE-OUT: {', '.join(valid)} exempted from the release cooldown by the brief alone "
              "(this workspace is not governed, so no signed decision is required).", file=sys.stderr)
        return valid
    when = now or datetime.now(UTC)
    kept: list[str] = []
    for repo in valid:
        try:
            _authorize_one(team_dir, repo, consume=consume, now=when)
            kept.append(repo)
        except (RuntimeError, ExceptionRegistryError, OSError, ValueError) as exc:
            print(f"  ✗  S-10 carve-out: {repo} is NOT exempted from the cooldown ({exc}); {_shape(action_for(repo))}",
                  file=sys.stderr)
    return kept


def authorize_into(manifest: dict, team_dir: Path, *, consume: bool) -> None:
    """Set ``manifest["s10_cooldown_exempt_authorized"]`` through the gate; the render reads only that key.

    Args:
        manifest: The team manifest (``s10_cooldown_exempt_repos`` from the brief).
        team_dir: The team directory.
        consume: Passed to :func:`authorized_exempt_repos`.

    Returns:
        None.

    Raises:
        RuntimeError: As :func:`authorized_exempt_repos`.
    """
    manifest["s10_cooldown_exempt_authorized"] = authorized_exempt_repos(
        team_dir, manifest.get("s10_cooldown_exempt_repos"), consume=consume)


__all__ = ["S10_EXEMPT_KIND", "S10_EXEMPT_TTL_DAYS", "action_for", "authorize_into", "authorized_exempt_repos"]
