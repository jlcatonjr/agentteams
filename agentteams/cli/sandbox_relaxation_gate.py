"""sandbox_relaxation_gate.py — the render-time gate for turning a sandbox off or widening a WASM grant (Rule 15, D7).

Five transitions are constraint-relaxing effects (:mod:`agentteams.cli.sandbox_effects`): ``sandbox-os-off``,
``sandbox-wasm-off`` (incl. the projected model-substrate switch), ``wasm-grant-widen``, ``native-exec-keep`` and
``wasm-to-os-switch``. :func:`plan_relaxations` derives them from the previous and the new
:class:`~agentteams.sandbox_state.SandboxState`; :func:`authorize` clears them; :func:`retire_on_tighten` ends the
standing exceptions a tightening makes unnecessary. Modelled on :mod:`agentteams.cli.adopt_merge_gate`.

**Governed workspace** (``enforce_decision_signing``, read from disk before the render, so a render that flips it
cannot escape): every new relaxation needs a decision row that is not a waiver, whose ``scope`` is exactly the action
id, whose effect is exactly this run's (``effect_relaxes`` = the action, or ``effect_grants`` = exactly the added
tokens), and that clears the destructive-action gate, whose elevated path demands the operator's Ed25519 signature
(an HMAC signature is valid but insufficient) and a traceable ``derives_from`` lineage. An unretracted HALT on the
instance, on any lineage ancestor or on the whole family blocks it (C-2). A standing relaxation (os-off, widening,
native-exec keep, runner switch) is registered in the exception registry under the aggregate cap with a
:data:`SANDBOX_EXCEPTION_TTL_DAYS` sunset and a ``decision_sha256`` binding it to the decision row that cleared it.
Every later render that keeps it carries it over only on an entry that is active, has a present, unexpired
``expires``, and whose bound decision row is still in the log and re-verifies through the decision log's own
authenticity path (approved author, Ed25519 signature, lineage, hash chain); anything else is treated as lapsed
(fail-closed sunset; a freshly signed decision replaces it). All checks run before anything is consumed: a refusal
consumes nothing.

**Ungoverned workspace:** never refuses; the caller prints the ``SANDBOX RELAXATION`` notice
(``write_root_policy.warn_sandbox_relaxation``) and the records say ``notice-only``.

Rule 12 carve-outs (each is an audit target; see ``P15-governance.plan.md``): a ``sandbox-os-off`` that predates
governance (no registry entry was ever recorded) continues with a notice; ``sandbox-wasm-off`` is not standing;
ungoverned workspaces never refuse; the fixed native-exec exemption never reaches the keep set; raising limits within
``LIMIT_BOUNDS`` mints no token; and a HALT on one instance of a family (``sandbox-os-off:goose``) also blocks its
siblings, because the family check matches every instance id (over-blocking only).
"""

from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass, replace
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
    list_exceptions,
    register_exception,
    retire_exception,
)
from agentteams.cli.sandbox_effects import (
    MODEL_SUBSTRATE_QUALIFIER,
    NATIVE_EXEC_KEEP,
    SANDBOX_OS_OFF,
    SANDBOX_RELAXATION_KINDS,
    SANDBOX_WASM_OFF,
    WASM_GRANT_WIDEN,
    WASM_TO_OS_SWITCH,
    action_id,
    relaxation_kind_of,
)
from agentteams.sandbox_state import SandboxExceptions, SandboxState, keep_tokens, state_tokens

#: The sunset of a standing sandbox exception (Rule 15(d)); a later render re-checks it and refuses once it lapses.
SANDBOX_EXCEPTION_TTL_DAYS = 90

#: The OS-off reasons that mean the operator's brief or argv turned the boundary off (not a profile default).
_OPERATOR_OFF_REASONS = frozenset({"brief", "cli"})
_ACTIVE = "active"
_HEX64 = re.compile(r"[0-9a-f]{64}")
_AUTHORIZING = frozenset({"PASS", "CONDITIONAL PASS"})


class SandboxRelaxationError(RuntimeError):
    """A sandbox relaxation is refused (fail-closed); the message names what to record or change."""


@dataclass(frozen=True)
class Relaxation:
    """One sandbox relaxation a render makes.

    Attributes:
        kind: One of :data:`~agentteams.cli.sandbox_effects.SANDBOX_RELAXATION_KINDS`.
        qualifier: A framework id, ``model-substrate``, or an agent slug (``wasm-to-os-switch``).
        grants: ``wasm-grant-widen`` / ``native-exec-keep``: exactly the tokens concerned (added ones when new).
        standing: Whether it persists across renders (and so needs a registry entry and a sunset).
        continuing: True when an earlier render already made it: it is checked against the registry, not a new row.
    """

    kind: str
    qualifier: str
    grants: tuple[str, ...] = ()
    standing: bool = True
    continuing: bool = False

    @property
    def action(self) -> str:
        """The gate action id, ``<kind>:<qualifier>``."""
        return action_id(self.kind, self.qualifier)


def _pair(exceptions: tuple[SandboxExceptions | None, SandboxExceptions] | None
          ) -> tuple[SandboxExceptions | None, SandboxExceptions | None]:
    return exceptions if exceptions is not None else (None, None)


def plan_relaxations(old: SandboxState | None, new: SandboxState, *, framework_id: str, profile_would_emit: bool,
                     exceptions: tuple[SandboxExceptions | None, SandboxExceptions] | None = None
                     ) -> list[Relaxation]:
    """Return the relaxations ``new`` makes over ``old``, new ones and continuing standing ones.

    Args:
        old: The previous state (:func:`~agentteams.sandbox_state.previous_sandbox_state`), or None.
        new: The state this render emits.
        framework_id: The framework being rendered (the qualifier of the framework-wide kinds).
        profile_would_emit: Whether the privilege profile would emit an OS boundary were it not switched off.
        exceptions: ``(previous, new)`` gate inputs (native-exec keep set, runner program modes), or None.

    Returns:
        The relaxations in a stable order. Continuing ``wasm-to-os-switch`` entries are candidates (every agent on
        ``sandbox: os``); :func:`authorize` keeps only those the registry shows were switched.

    Raises:
        ValueError: A malformed slug, framework id or grant mount.
    """
    old_exc, new_exc = _pair(exceptions)
    out: list[Relaxation] = []
    if not new.os:
        if old is not None and old.os:
            out.append(Relaxation(SANDBOX_OS_OFF, framework_id))
        elif old is None and profile_would_emit and new.os_reason in _OPERATOR_OFF_REASONS:
            out.append(Relaxation(SANDBOX_OS_OFF, framework_id))
        elif old is not None and profile_would_emit:
            out.append(Relaxation(SANDBOX_OS_OFF, framework_id, continuing=True))
    if not new.wasm and old is not None and old.wasm:
        out.append(Relaxation(SANDBOX_WASM_OFF, framework_id, standing=False))
    if new.model_substrate != "on" and old is not None and old.model_substrate == "on":
        out.append(Relaxation(SANDBOX_WASM_OFF, MODEL_SUBSTRATE_QUALIFIER, standing=False))
    for kind, before, after in ((WASM_GRANT_WIDEN, state_tokens(old), state_tokens(new)),
                                (NATIVE_EXEC_KEEP, keep_tokens(old, old_exc), keep_tokens(new, new_exc))):
        if after - before:
            out.append(Relaxation(kind, framework_id, grants=tuple(sorted(after - before))))
        if after & before:
            out.append(Relaxation(kind, framework_id, grants=tuple(sorted(after & before)), continuing=True))
    if new_exc is not None:
        switched = old_exc.wasm_programs - new_exc.wasm_programs if old_exc is not None else frozenset()
        out += [Relaxation(WASM_TO_OS_SWITCH, a) for a in sorted(switched)]
        out += [Relaxation(WASM_TO_OS_SWITCH, a, continuing=True) for a in sorted(new_exc.os_programs - switched)]
    return out


def _split(cell: str) -> set[str]:
    return {p.strip().lower() for p in re.split(r"[;,]", cell or "") if p.strip()}


def _entry_tokens(entry: dict) -> set[str]:
    effect = entry.get("effect") if isinstance(entry.get("effect"), dict) else {}
    grants = effect.get("grants")
    return {str(g).lower() for g in grants} if isinstance(grants, list) else set()


def _shape(r: Relaxation) -> str:
    """The decision-row shape the refusal tells the operator to record."""
    effect = (f"effect_grants={';'.join(r.grants)}" if r.kind in (WASM_GRANT_WIDEN, NATIVE_EXEC_KEEP)
              else f"effect_relaxes={r.action}")
    return (f"record a PASS row signed with the operator's Ed25519 key (--sign-decision): action_reviewed={r.action}, "
            f"scope={r.action}, {effect}, derives_from=<a recorded decision id>")


def _check_new(team_dir: Path, r: Relaxation) -> dict[str, str]:
    """Run every check for a new relaxation without consuming anything; return the matched row."""
    relaxation_action, relaxation_family = r.action, r.kind
    if security_gate._latest_security_waiver(team_dir, action=relaxation_action) is not None:
        raise SandboxRelaxationError(f"a waiver cannot authorize '{relaxation_action}'; {_shape(r)}")
    row = security_gate._latest_security_decision(team_dir, action=relaxation_action)
    if row is None:
        raise SandboxRelaxationError(
            f"'{relaxation_action}' relaxes a sandbox and this workspace is governed (enforce_decision_signing), "
            f"but references/security-decisions.log.csv has no unconsumed decision for it; {_shape(r)}")
    if (row.get("scope") or "").strip().lower() != relaxation_action:
        raise SandboxRelaxationError(f"the '{relaxation_action}' decision must carry scope={relaxation_action} exactly")
    granted, relaxed = _split(row.get("effect_grants") or ""), _split(row.get("effect_relaxes") or "")
    if r.kind in (WASM_GRANT_WIDEN, NATIVE_EXEC_KEEP):
        wanted, also = set(r.grants), sorted(relaxed - {relaxation_action})
        if granted != wanted or also:
            raise SandboxRelaxationError(
                f"the '{relaxation_action}' decision must grant exactly this run's tokens (extra in decision: "
                f"{sorted(granted - wanted) or 'none'}; missing from decision: {sorted(wanted - granted) or 'none'}"
                f"{f'; it also relaxes {also}' if also else ''})")
    elif relaxed != {relaxation_action} or granted:
        raise SandboxRelaxationError(
            f"the '{relaxation_action}' decision must carry effect_relaxes={relaxation_action} exactly and no "
            f"effect_grants (effect_relaxes: {sorted(relaxed) or 'none'}; effect_grants: {sorted(granted) or 'none'})")
    try:
        exception = requires_operator_signature(row, kind="decision")
    except EffectClassifierError as exc:
        raise SandboxRelaxationError(f"the '{relaxation_action}' decision is inconsistent: {exc}") from exc
    if not exception:  # unreachable while the classifier's sandbox axis holds; fail closed if it ever drifts
        raise SandboxRelaxationError(f"the '{relaxation_action}' decision does not classify as an exception")
    # C-2 over the family too: halt_names_action does not let a HALT on `sandbox-os-off` block `sandbox-os-off:claude`.
    security_gate._assert_no_unretracted_halt(team_dir, action=relaxation_family)
    security_gate._assert_destructive_action_allowed(team_dir, action=relaxation_action, consume=False)
    return row


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


def _standing(team_dir: Path, r: Relaxation, now: datetime) -> tuple[str, object]:
    """Classify a continuing relaxation against the registry.

    Returns ``("active", entries)``, ``("pre-governance", None)``, ``("skip", None)`` or
    ``("refuse", (message, lapsed_ids))``.
    """
    entries = entries_for(team_dir, r.action)
    active = active_relaxing_exceptions({"exceptions": entries}, now=now)
    unbound = {}
    for e in active:
        why = _unbound_reason(team_dir, e)
        if why is not None:
            unbound[e.get("id")] = why
            print(f"  !  sandbox exception {e.get('id')} for {r.action} {why}; it cannot carry the relaxation over",
                  file=sys.stderr)
    active = [e for e in active if e.get("id") not in unbound]   # unbound counts as lapsed (S1)
    open_ids = [e.get("id") for e in entries if (e.get("status") or _ACTIVE).strip().lower() == _ACTIVE]
    lapsed = [i for i in open_ids if i not in {e.get("id") for e in active}]
    if r.grants:
        covered = set().union(*(_entry_tokens(e) for e in active))
        missing = sorted(set(r.grants) - covered)
        if not missing:
            return "active", [e for e in active if _entry_tokens(e) & set(r.grants)]
        ever = set().union(*(_entry_tokens(e) for e in entries))
        if set(missing) & ever:
            return "refuse", (f"the exception for {r.action} covering {missing} has lapsed; re-sign a decision or "
                              "remove the grant", lapsed)
        return "refuse", (f"{r.action} continues for {missing}, which no exception records (only sandbox-os-off "
                          "can predate governance); re-sign a decision or remove the grant", lapsed)
    if active:
        return "active", [active[-1]]
    if r.kind == WASM_TO_OS_SWITCH and not open_ids:
        return "skip", None  # never switched under governance, or switched back (the entry retired)
    if entries:
        named = ", ".join(str(i) for i in (lapsed or [e.get("id") for e in entries]))
        return "refuse", (f"exception {named} for {r.action} has lapsed; re-sign a decision or turn the sandbox back "
                          "on", lapsed)
    if r.kind == SANDBOX_OS_OFF:
        return "pre-governance", None
    return "refuse", (f"{r.action} continues but no exception was ever recorded for it (only sandbox-os-off can "
                      "predate governance); re-sign a decision or turn the sandbox back on", lapsed)


def excuse_stale_evidence(team_dir: Path, old: SandboxState | None, stale: frozenset[str], *,
                          framework_id: str) -> SandboxState | None:
    """Return the previous state the gate plans against, turning back on each stale key the operator never excused.

    ``sandbox_state.recorded_sandbox_state`` makes the build-log record the previous state and reports the keys
    whose leftover boundary files still say "on" (P17: an off render leaves ``sandbox/confine-run.sh`` and the
    installed ``agentteams_wasm`` server). Ungoverned, the record stands (nothing is gated; no repeated notice). In
    a governed workspace a stale key is excused only by the operator's authorization of that very off state, so a
    rewritten record alone steps around nothing (Rule 12): ``os`` by an active, bound ``sandbox-os-off:<fw>``
    exception (:func:`standing_authorization`), ``wasm`` by the newest ``sandbox-wasm-off:<fw>`` decision row being
    spent and still re-verifying (approved author, the operator's Ed25519 signature, lineage, intact chain).
    Anything else, or any read failure, keeps the evidence: "was on" (fail closed).

    Args:
        team_dir: The team directory (decisions log, exception registry).
        old: The record-authoritative previous state, or None.
        stale: The keys whose evidence disagrees with the record (a subset of ``{"os", "wasm"}``).
        framework_id: The framework being rendered.

    Returns:
        ``old`` with every unexcused stale key on (``old`` itself when nothing is stale or ungoverned).

    Raises:
        Nothing: an unreadable log or registry leaves the key on.
    """
    if old is None or not stale or not _enforce_decision_signing(team_dir):
        return old
    os_on = "os" in stale and not _covered_by_exception(team_dir, framework_id)
    wasm_on = "wasm" in stale and not _spent_off_decision(team_dir, action_id(SANDBOX_WASM_OFF, framework_id))
    return replace(old, os=old.os or os_on, wasm=old.wasm or wasm_on)


def _covered_by_exception(team_dir: Path, framework_id: str) -> bool:
    """Whether an active, bound ``sandbox-os-off:<fw>`` exception covers the off state (never raises)."""
    try:
        standing_authorization(team_dir, SANDBOX_OS_OFF, framework_id)
    except SandboxRelaxationError:
        return False
    return True


def _spent_off_decision(team_dir: Path, action: str) -> bool:
    """Whether the newest decision row scoped exactly to ``action`` is consumed and still re-verifies (never raises)."""
    try:
        read = _read_decision_rows(team_dir)
        if read is None:
            return False
        rows, fieldnames = read
        _assert_decision_chain_intact(rows, fieldnames)
        mine = [row for row in rows if (row.get("scope") or "").strip().lower() == action]
        if not mine:
            return False
        row = mine[-1]
        verdict = (row.get("verdict") or row.get("status") or "").strip().upper()
        if (row.get("consumed") or "").strip().lower() not in {"yes", "true", "1"} or verdict not in _AUTHORIZING:
            return False
        _assert_authorizing_row_is_authentic(row, output_dir=team_dir, signing_active=_decision_signing_active(
            fieldnames), action=action)
    except (RuntimeError, OSError, ValueError):
        return False
    return True


def standing_authorization(team_dir: Path, kind: str, qualifier: str, grants: tuple[str, ...] = (), *,
                           now: datetime | None = None) -> dict[str, str]:
    """Re-check, read-only, that a standing relaxation is still covered by an active exception (launch time).

    The same carry-over test a later render applies (:func:`authorize` on a continuing relaxation): every token in
    *grants* is covered by an active, unexpired registry entry whose bound decision row still re-verifies (approved
    author, the operator's Ed25519 signature, lineage, hash chain). Nothing is consumed, registered or retired. The
    ``--run-wasm-agent`` launcher calls it in a governed workspace for every token an agent's grant holds (C2), so a
    rewritten ``grants.json`` plus a re-hashed lock still cannot run a widening the operator never signed.

    Args:
        team_dir: The team directory (decisions log, exception registry).
        kind: A standing kind (``wasm-grant-widen``, ``native-exec-keep``, ``sandbox-os-off``, ...).
        qualifier: The action qualifier (a framework id or agent slug).
        grants: The tokens that must be covered (grant kinds).
        now: Reference time (defaults to now, UTC).

    Returns:
        The ``active-exception`` record (``kind``, ``action``, ``authorization``, ``exception_id``).

    Raises:
        SandboxRelaxationError: Not covered (never recorded, lapsed, unbound), or the registry or decisions log is
            unreadable or broken (fail-closed).
    """
    r = Relaxation(kind, qualifier, grants=tuple(sorted(grants)), continuing=True)
    try:
        status, detail = _standing(team_dir, r, now or datetime.now(UTC))
    except (ExceptionRegistryError, RuntimeError, OSError, ValueError) as exc:
        raise SandboxRelaxationError(str(exc)) from exc
    if status != "active":
        message = detail[0] if status == "refuse" else f"{r.action} has no active exception"  # type: ignore[index]
        raise SandboxRelaxationError(f"{message}. {_shape(r)}")
    entries = list(detail)  # type: ignore[call-overload]
    return _record(r, "active-exception", exception_id=";".join(str(e.get("id")) for e in entries))


def _record(r: Relaxation, authorization: str, **extra: str) -> dict[str, str]:
    return {"kind": r.kind, "action": r.action, "authorization": authorization,
            **{k: v for k, v in extra.items() if v}}


def authorize(team_dir: Path, relaxations: list[Relaxation], *, consume: bool,
              now: datetime | None = None) -> list[dict[str, str]]:
    """Clear this render's sandbox relaxations, or refuse.

    Args:
        team_dir: The team directory (decisions log, exception registry, signing switch).
        relaxations: From :func:`plan_relaxations`.
        consume: True to consume the matched decision rows and register standing exceptions; False (dry run) checks
            only and writes nothing.
        now: Reference time (defaults to now, UTC).

    Returns:
        One record per relaxation for ``manifest["sandbox_relaxations"]``: ``kind``, ``action``, ``authorization``
        (``ed25519-decision`` | ``notice-only`` | ``pre-governance`` | ``active-exception``) and, when known,
        ``exception_id``, ``decision_date``, ``key_id``, ``signature_prefix``. Identifiers only.

    Raises:
        SandboxRelaxationError: Any condition fails; nothing was consumed.
    """
    try:
        return _authorize(team_dir, relaxations, consume=consume, now=now or datetime.now(UTC))
    except SandboxRelaxationError:
        raise
    except (ExceptionRegistryError, RuntimeError, OSError, ValueError) as exc:
        raise SandboxRelaxationError(str(exc)) from exc


def _authorize(team_dir: Path, relaxations: list[Relaxation], *, consume: bool,
               now: datetime) -> list[dict[str, str]]:
    if not _enforce_decision_signing(team_dir):
        return [_record(r, "notice-only") for r in relaxations
                if not (r.continuing and r.kind == WASM_TO_OS_SWITCH)]
    records: dict[int, dict[str, str]] = {}
    fresh: list[tuple[int, Relaxation, list[str]]] = []  # (index, relaxation, lapsed ids it replaces)
    for i, r in enumerate(relaxations):
        if not r.continuing:
            fresh.append((i, r, []))
            continue
        status, detail = _standing(team_dir, r, now)
        if status == "active":
            entries = list(detail)  # type: ignore[call-overload]
            for entry in entries:
                print(f"  ·  sandbox relaxation {r.action} continues under exception {entry.get('id')} "
                      f"(expires {entry.get('expires') or 'never'})", file=sys.stderr)
            records[i] = _record(r, "active-exception", exception_id=";".join(str(e.get("id")) for e in entries))
        elif status == "pre-governance":
            print(f"  !  SANDBOX RELAXATION: {r.action} predates sandbox governance (no exception was ever "
                  "recorded for it) and continues without a signature; turn the sandbox back on, or sign a decision "
                  "to record it.", file=sys.stderr)
            records[i] = _record(r, "pre-governance")
        elif status == "refuse":
            message, lapsed = detail  # type: ignore[misc]
            if security_gate._latest_security_decision(team_dir, action=r.action) is None:
                raise SandboxRelaxationError(f"{message}. {_shape(r)}")
            fresh.append((i, r, list(lapsed)))  # a freshly signed decision replaces the lapsed exception
    rows = {i: _check_new(team_dir, r) for i, r, _ in fresh}
    standing = [(i, r, lapsed) for i, r, lapsed in fresh if r.standing]
    retiring = [x for _, _, lapsed in standing for x in lapsed]
    if standing:
        assert_within_aggregate_cap(team_dir, adding=len(standing), now=now, retiring=retiring)
    for i, r, lapsed in fresh:
        row = rows[i]
        entry_id = f"{r.action}@{(row.get('signature') or '').strip()[:16]}" if r.standing else ""
        if consume:
            relaxation_action = r.action
            security_gate._assert_destructive_action_allowed(team_dir, action=relaxation_action, consume=True)
            for old_id in lapsed:
                retire_exception(team_dir, old_id, reason=f"replaced by {entry_id or 'a new decision'}")
            if r.standing:
                register_exception(team_dir, {
                    "id": entry_id, "action": r.action, "kind": r.kind, "scope": r.action, "relaxing": True,
                    "effect": {"grants": list(r.grants)} if r.grants else {"relaxes": [r.action]},
                    "lineage": sorted(_split(row.get("derives_from") or "")),
                    "issued": now.isoformat(),
                    "expires": (now + timedelta(days=SANDBOX_EXCEPTION_TTL_DAYS)).isoformat(),
                    "decision_key_id": (row.get("key_id") or "").strip(),
                    "decision_sha256": _decision_binding(row),
                }, now=now)
        records[i] = _record(r, "ed25519-decision", exception_id=entry_id,
                             decision_date=(row.get("timestamp") or row.get("date") or "").strip(),
                             key_id=(row.get("key_id") or "").strip(),
                             signature_prefix=(row.get("signature") or "").strip()[:16])
    return [records[i] for i in sorted(records)]


def _ended(entry: dict, new: SandboxState, framework_id: str, new_exc: SandboxExceptions | None) -> str | None:
    """Return why an active sandbox exception no longer applies under ``new``, or None while it still does."""
    action = str(entry.get("action") or "").strip().lower()
    kind = relaxation_kind_of(action)
    qualifier = action.split(":", 1)[1] if ":" in action else ""
    if kind == SANDBOX_OS_OFF and qualifier == framework_id and new.os:
        return "the OS sandbox is back on"
    if kind == WASM_GRANT_WIDEN and qualifier == framework_id and not _entry_tokens(entry) & state_tokens(new):
        return "the WASM grant it widened was removed"
    if kind == NATIVE_EXEC_KEEP and qualifier == framework_id and not _entry_tokens(entry) & keep_tokens(new, new_exc):
        return "no agent it covered keeps native exec"
    if kind == WASM_TO_OS_SWITCH and new_exc is not None:
        if qualifier in new_exc.wasm_programs:
            return "the runner program is back on sandbox: wasm"
        if qualifier not in new_exc.os_programs:
            return "the agent no longer has a runner program"
    return None


def retire_on_tighten(team_dir: Path, old: SandboxState | None, new: SandboxState, *, framework_id: str,
                      exceptions: tuple[SandboxExceptions | None, SandboxExceptions] | None = None,
                      dry_run: bool) -> list[str]:
    """Flip each active sandbox exception that ``new`` no longer needs to ``sunset`` (strictly tightening).

    What ended is read from the registry and ``new`` (OS back on, a widened grant or keep entry removed, an agent back
    on ``sandbox: wasm`` or off the runner); ``old`` is accepted for symmetry with :func:`plan_relaxations`.

    Args:
        team_dir: The team directory (exception registry).
        old: The previous state (unused; see above).
        new: The state this render emits.
        framework_id: The framework being rendered (only its own framework-wide entries are retired).
        exceptions: ``(previous, new)`` gate inputs, or None.
        dry_run: True to report without writing.

    Returns:
        The retired (or, under ``dry_run``, retirable) exception ids.

    Raises:
        SandboxRelaxationError: The registry is unreadable or malformed.
    """
    del old  # tightening is decided from the registry and the new state alone
    _, new_exc = _pair(exceptions)
    retired: list[str] = []
    try:
        for entry in list_exceptions(team_dir):
            if not isinstance(entry, dict) or (entry.get("status") or _ACTIVE).strip().lower() != _ACTIVE:
                continue
            if relaxation_kind_of(str(entry.get("action") or "")) not in SANDBOX_RELAXATION_KINDS:
                continue
            reason = _ended(entry, new, framework_id, new_exc)
            if reason is None:
                continue
            if not dry_run:
                retire_exception(team_dir, str(entry.get("id")), reason=reason)
            retired.append(str(entry.get("id")))
    except (ExceptionRegistryError, ValueError) as exc:
        raise SandboxRelaxationError(str(exc)) from exc
    return retired


__all__ = [
    "SANDBOX_EXCEPTION_TTL_DAYS",
    "Relaxation",
    "SandboxRelaxationError",
    "authorize",
    "excuse_stale_evidence",
    "plan_relaxations",
    "retire_on_tighten",
    "standing_authorization",
]
