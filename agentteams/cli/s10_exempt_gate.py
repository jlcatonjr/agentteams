"""s10_exempt_gate.py — the Rule 15 gate for Rule S-10's local interdependent-repository carve-out.

The brief key ``s10_cooldown_exempt_repos`` lifts the 14-day package-release cooldown for the project's own
repositories (Jim's ruling, 2026-11-03). Listing a repository there relaxes a security rule, so a brief edit alone must
not do it (@security, 2026-11-03): the render shows only the repositories this gate authorizes, and never reads the
raw brief value (``render`` falls back to "none" when the gate did not run, e.g. on a convert or interop path).

Each listed repository is the action ``s10-cooldown-exempt:<owner/name>``, a constraint-relaxing effect. Modelled on main's
``cli/sandbox_relaxation_gate`` (not on this release line), whose decision-binding helpers it carries locally:

* **Governed workspace** (``enforce_decision_signing``, read from disk): a repository is authorized when an active
  exception in the registry covers it and is still bound to a decision row that re-verifies (approved author, the
  operator's Ed25519 signature, lineage, hash chain), or when a new decision row (not a waiver) has ``scope`` and
  ``effect_relaxes`` equal to the action exactly, classifies as an operator-signed exception, and clears the
  destructive-action gate with no unretracted HALT on the action or the whole family. A new one is consumed and
  registered under the aggregate cap with a :data:`S10_EXEMPT_TTL_DAYS` sunset. Anything else is left out of the
  rendered rule, with a notice naming the row to record; the render itself goes ahead (fail-closed for the
  relaxation, never for the team).
* **Ungoverned workspace:** every valid listed repository renders, with a notice, as sandbox relaxations do there.
* **Native surfaces** (``.claude/agents``, ``.goose/recipes``, ``.codex/agents``, ...): the carve-out is a project-wide
  policy, so a surface authorizes against the project's canonical ``.github/agents`` (:func:`governance_team_dir`)
  when that is governed. All surfaces then share one decision row, one consumption and one registered exception.
  A HALT in the surface's own log still removes the repository there (C-2: HALTs are a union). The surfaces share
  the canonical registry; their renders are serialized by the per-checkout update lock (:mod:`agentteams.update_lock`,
  which ``generate`` holds around this gate), outside that lock's documented fail-open cases. That lock is keyed on
  the run's project root, this gate's root on the output path: a custom ``--output`` into another checkout takes a
  different lock (a concurrent-write window, not a bypass: every write still passes every check).

Rule 12, named: the ungoverned case trusts the brief, as every ungoverned relaxation does; and a refusal drops the
repository silently from the rule's text except for the notice, so the cooldown then applies to it (over-tightening).
The canonical redirect is derived from the output path alone (never a brief field such as ``existing_project_path``),
is taken only when the canonical dir is a real directory inside the project and is governed, and never drops a HALT
recorded in the surface's own log.

Stdlib only.
"""

from __future__ import annotations

import hashlib
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentteams.cli import security_gate
from agentteams.cli.decision_log import (
    _assert_authorizing_row_is_authentic,
    _assert_decision_chain_intact,
    _decision_signature_values,
    _decision_signing_active,
    _enforce_decision_signing,
    _read_decision_rows,
)
from agentteams.cli.effect_classifier import EffectClassifierError, requires_operator_signature
from agentteams.cli.exception_registry import (
    ExceptionRegistryError,
    active_relaxing_exceptions,
    assert_within_aggregate_cap,
    entries_for,
    register_exception,
    retire_exception,
)
from agentteams.frameworks._sandbox_emit import team_agents_dir
from agentteams.manifest_format import _REPO_ID_RE
from agentteams.prompt_roots import team_project_root

#: The action family; an action id is ``s10-cooldown-exempt:<owner/name>`` (lowercased).
S10_EXEMPT_KIND = "s10-cooldown-exempt"

#: The sunset of a registered exemption (Rule 15(d)); a later render re-checks it and drops it once it lapses.
S10_EXEMPT_TTL_DAYS = 90

#: The project's canonical team dir, whose decisions log holds the operator's signed policy rows.
CANONICAL_TEAM_DIR = team_agents_dir("copilot")

#: Team dirs outside :data:`~agentteams.prompt_roots.TEAM_AGENT_GLOBS` (not prompt roots): agents-md, wasm.
_OTHER_TEAM_DIRS: tuple[str, ...] = (".agents", ".agentteams-wasm/agents")


def _project_root_of(team_dir: Path) -> Path | None:
    """The project root *team_dir* sits in, from its own path alone (None for a non-default layout)."""
    split = team_project_root(team_dir)
    if split is not None:
        return split[0].resolve()
    resolved = team_dir.resolve()
    for sub in _OTHER_TEAM_DIRS:
        if resolved.as_posix().endswith("/" + sub):
            return resolved.parents[sub.count("/")]
    return None


def governance_team_dir(team_dir: Path) -> Path:
    """The team dir whose decisions log and registry authorize the carve-out for *team_dir*.

    A native surface's own log carries none of the signed policy rows (their lineage lives in the canonical log), so
    it authorizes against ``<project>/.github/agents`` when that dir is a real directory inside the project and is
    governed (with or without a log yet: a governed canonical dir with no row authorizes nothing, fail-closed). The
    project root comes from *team_dir*'s own path, never from the brief.

    Args:
        team_dir: This run's agents dir.

    Returns:
        The canonical team dir, or *team_dir* itself (non-default layout, the canonical dir, or no governed one).

    Raises:
        RuntimeError: The canonical dir's signing switch can't be read (fail-closed).
    """
    root = _project_root_of(team_dir)
    if root is None:
        return team_dir
    canonical = root / CANONICAL_TEAM_DIR
    if canonical.resolve() == team_dir.resolve() or not canonical.is_dir():
        return team_dir
    if any(p.is_symlink() for p in (root / ".github", canonical)) or not canonical.resolve().is_relative_to(root):
        return team_dir
    return canonical if _enforce_decision_signing(canonical) else team_dir


#: release/1.1 has no cli/sandbox_relaxation_gate, so the decision-binding helpers it shares on main live here
#: (copied verbatim from main's sandbox_relaxation_gate, 2026-11-03).
_HEX64 = re.compile(r"[0-9a-f]{64}")
_AUTHORIZING = frozenset({"PASS", "CONDITIONAL PASS"})


def _split(cell: str) -> set[str]:
    return {p.strip().lower() for p in re.split(r"[;,]", cell or "") if p.strip()}


def _decision_binding(row: dict[str, str]) -> str:
    """The sha256 that binds a registry entry to the decision row that cleared it: the row's signed payload plus its
    signature (the unsigned ``consumed`` cell is excluded, so spending the row does not break the binding)."""
    payload = "|".join(_decision_signature_values(row)) + "|signature=" + (row.get("signature") or "").strip()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _unbound_reason(team_dir: Path, entry: dict) -> str | None:
    """Why an active registry entry may NOT carry its relaxation over (S1), or None when it may.

    It may only when its ``expires`` is present (an expiry-less entry never lapses, so it is refused) and its
    ``decision_sha256`` names a row still in the decisions log that re-verifies now through
    :func:`~agentteams.cli.decision_log._assert_authorizing_row_is_authentic` (approved author, the operator's
    Ed25519 signature, traceable lineage) with the log's hash chain intact. Raises when the log is unreadable or its
    chain is broken (fail-closed).
    """
    if not str(entry.get("expires") or "").strip():
        return "records no expiry"
    binding = str(entry.get("decision_sha256") or "").strip().lower()
    if not _HEX64.fullmatch(binding):
        return "is not bound to a decision row"
    read = _read_decision_rows(team_dir)
    if read is None:
        return "is bound to a decision row, but the decisions log is gone"
    rows, fieldnames = read
    _assert_decision_chain_intact(rows, fieldnames)
    # A row with a '|' in a signed field raises here: a malformed log refuses the carry-over (fail-closed).
    matches = [row for row in rows if _decision_binding(row) == binding]
    if not matches:
        return "is bound to a decision row that is no longer in the decisions log"
    row = matches[-1]
    verdict = (row.get("verdict") or row.get("status") or "").strip().upper()
    if verdict not in _AUTHORIZING or (verdict == "CONDITIONAL PASS" and
                                       (row.get("conditions_verified") or "").strip().lower() != "verified"):
        return f"is bound to a decision row whose verdict is {verdict or 'empty'}"
    try:
        _assert_authorizing_row_is_authentic(row, output_dir=team_dir, signing_active=_decision_signing_active(
            fieldnames), action=str(entry.get("action") or ""))
    except RuntimeError as exc:
        return f"is bound to a decision row that no longer verifies ({exc})"
    return None

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


def _authorize_one(team_dir: Path, repo: str, *, consume: bool, now: datetime,
                   halt_dirs: tuple[Path, ...] = ()) -> None:
    """Authorize *repo* or raise (RuntimeError, ExceptionRegistryError, OSError or ValueError).

    *halt_dirs* are further team dirs whose HALTs also bind (the surface's own dir when *team_dir* is canonical).
    """
    action = action_for(repo)
    exempt_action, exempt_family = action, S10_EXEMPT_KIND
    # C-2 first, before any carry-over: a HALT recorded after registration removes the repository at once.
    for halt_dir in (team_dir, *halt_dirs):
        security_gate._assert_no_unretracted_halt(halt_dir, action=exempt_family)
        security_gate._assert_no_unretracted_halt(halt_dir, action=exempt_action)
    entries = entries_for(team_dir, action)
    active = active_relaxing_exceptions({"exceptions": entries}, now=now)
    if any(_unbound_reason(team_dir, e) is None for e in active):
        return
    lapsed = [str(e.get("id")) for e in entries if (e.get("status") or "active").strip().lower() == "active"]
    row = _check_new(team_dir, action)
    # release/1.1's cap check has no ``retiring``: an entry about to be replaced still counts (over-tightening only).
    assert_within_aggregate_cap(team_dir, adding=1, now=now)
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
        team_dir: This run's team directory. A native surface authorizes against :func:`governance_team_dir`
            (decisions log, exception registry, signing switch); its own HALTs still bind.
        repos: The brief's ``s10_cooldown_exempt_repos`` value.
        consume: True to consume a new decision row and register its exception; False (dry run, ``--check``)
            checks only and writes nothing.
        now: Reference time (defaults to now, UTC).

    Returns:
        The authorized ``owner/name`` identifiers, in the brief's order, de-duplicated ignoring case. Invalid
        entries are dropped.

    Raises:
        RuntimeError: This run's own signing switch can't be read (fail-closed). An unreadable canonical switch, a
            per-repository refusal or an unreadable log raises nothing: it leaves the repositories out (the
            cooldown applies to them).
    """
    by_key: dict[str, str] = {}  # case-folded: GitHub treats Foo/Bar and foo/bar as one repository
    for r in repos if isinstance(repos, list) else []:
        if isinstance(r, str) and _REPO_ID_RE.fullmatch(r):
            by_key.setdefault(r.lower(), r)
    valid = list(by_key.values())
    if not valid:
        return []
    try:
        own, team_dir = team_dir, governance_team_dir(team_dir)
    except RuntimeError as exc:  # the canonical switch: not exempted, and the render goes on (never a brief-alone pass)
        print(f"  ✗  S-10 carve-out: {', '.join(valid)} NOT exempted from the cooldown (the canonical "
              f"{CANONICAL_TEAM_DIR} signing switch can't be read: {exc}).", file=sys.stderr)
        return []
    halt_dirs = (own,) if team_dir != own else ()
    if not _enforce_decision_signing(team_dir):
        print(f"  !  S-10 COOLDOWN CARVE-OUT: {', '.join(valid)} exempted from the release cooldown by the brief alone "
              "(this workspace is not governed, so no signed decision is required).", file=sys.stderr)
        return valid
    when = now or datetime.now(UTC)
    kept: list[str] = []
    for repo in valid:
        try:
            _authorize_one(team_dir, repo, consume=consume, now=when, halt_dirs=halt_dirs)
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


__all__ = ["CANONICAL_TEAM_DIR", "S10_EXEMPT_KIND", "S10_EXEMPT_TTL_DAYS", "action_for", "authorize_into",
           "authorized_exempt_repos", "governance_team_dir"]
