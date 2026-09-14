"""exception_registry.py — the aggregate/proliferation guard for constraint-relaxing exceptions.

Workstream C of the exception-governance hardening. The per-item guards (the C-4 denylist, the
effect classifier, the lineage HALT scan, asymmetric signing) each answer "is THIS exception
safe?" None of them answers "are there now too MANY exceptions, or one that has quietly outlived
its sunset?" — the gap the impact report labelled **G1 (no aggregate guard)**. This module is that
missing guard.

It maintains a **git-tracked, structured** registry at
``references/exception-registry.json`` — structured (not prose) so the live set of active
relaxing exceptions is externally reconstructable and auditable by a party outside the drafting
chain, and tracked so the record is durable rather than a local file the same chain can quietly
rewrite. Each entry records the exception's id, scope, derived effect summary, lineage, issue and
expiry timestamps, an activation ceiling, and a terminal status.

The guard is **pre-activation and fail-closed** (the Rule-12 discipline: an exclusion is itself an
audit target). Before a relaxing exception may activate, :func:`assert_within_aggregate_cap`
refuses when the number of *active* relaxing exceptions is at the configured cap, or when any
active exception has passed its sunset/expiry (an expired-but-active exception is itself a finding,
not silently ignored). Uncertainty — a malformed registry, an unparseable timestamp, a missing cap
— refuses.

Stdlib-only. Records/reads only; it never grants or executes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agentteams.atomicio import _atomic_write_text
from agentteams.cli.signed_ledger import is_expired

#: Relative path of the git-tracked exception registry within a workspace.
EXCEPTION_REGISTRY_REL = "references/exception-registry.json"

#: Terminal (no-longer-active) statuses. An entry in any of these does not count toward the cap
#: and cannot activate. ``declined`` is the refusal-as-valid terminal state (Workstream F): an
#: exception that was considered and refused stays recorded so it cannot be silently re-minted.
_TERMINAL_STATUSES: frozenset[str] = frozenset({"expired", "sunset", "declined", "revoked"})

#: The one active status.
_ACTIVE_STATUS = "active"

#: Default cap when the registry declares none. Deliberately small and fail-closed: a project that
#: wants more must say so explicitly in the registry, making the aggregate ceiling a visible,
#: git-tracked decision rather than an unbounded default.
_DEFAULT_MAX_ACTIVE_RELAXING = 1


class ExceptionRegistryError(RuntimeError):
    """Raised when the registry is malformed, a cap is exceeded, or an entry is invalid."""


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def load_registry(root: Path) -> dict:
    """Return the parsed exception registry, or an empty skeleton when absent (fail-closed on bad).

    Args:
        root: The workspace root.

    Returns:
        The registry mapping. An absent file yields ``{"version": 1, "cap": {}, "exceptions": []}``.

    Raises:
        ExceptionRegistryError: The file exists but is not valid JSON, or its top-level shape is
            wrong (not an object, or ``exceptions`` not a list) — a corrupt guard must not be
            treated as an empty one.
    """
    path = root / EXCEPTION_REGISTRY_REL
    if not path.exists():
        return {"version": 1, "cap": {}, "exceptions": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExceptionRegistryError(f"exception registry is unreadable: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("exceptions"), list):
        raise ExceptionRegistryError(
            "exception registry is malformed: expected an object with an 'exceptions' list"
        )
    return data


def _configured_cap(registry: dict) -> int:
    """Return the max active relaxing exceptions the registry permits (fail-closed on a bad value)."""
    cap = registry.get("cap", {})
    if not isinstance(cap, dict):
        raise ExceptionRegistryError("exception registry 'cap' must be an object")
    raw = cap.get("max_active_relaxing", _DEFAULT_MAX_ACTIVE_RELAXING)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ExceptionRegistryError(
            f"exception registry cap.max_active_relaxing is not an integer ({raw!r})"
        ) from exc
    if value < 0:
        raise ExceptionRegistryError("exception registry cap.max_active_relaxing must be >= 0")
    return value


def _is_relaxing_entry(entry: dict) -> bool:
    """Return True when a registry entry represents a constraint-relaxing exception.

    An entry is relaxing unless it explicitly declares ``relaxing: false``. Fail-closed: an entry
    that omits the field is counted as relaxing (it is in the *exception* registry, after all), so
    a mislabelled entry cannot slip under the aggregate cap.
    """
    return entry.get("relaxing", True) is not False


def _entry_expired(entry: dict, now: datetime) -> bool:
    """Return True when an entry has passed its ``expires`` timestamp (fail-closed on malformed)."""
    expires = (entry.get("expires") or "").strip()
    if not expires:
        return False
    try:
        return is_expired(expires, now=now)
    except (ValueError, KeyError) as exc:
        raise ExceptionRegistryError(
            f"exception {entry.get('id')!r} has a malformed expires ({expires!r}): {exc}"
        ) from exc


def _entry_activations_left(entry: dict) -> bool:
    """Return True when an entry still has activations remaining (fail-closed on non-integers)."""
    if "max_activations" not in entry:
        return True
    try:
        max_activations = int(entry.get("max_activations"))
        activations = int(entry.get("activations", 0))
    except (TypeError, ValueError) as exc:
        raise ExceptionRegistryError(
            f"exception {entry.get('id')!r} has non-integer activation counters"
        ) from exc
    return max_activations > 0 and activations < max_activations


def active_relaxing_exceptions(registry: dict, *, now: datetime | None = None) -> list[dict]:
    """Return the relaxing exceptions that are currently active (status, expiry, activations).

    An exception counts as active iff its status is ``active``, it is relaxing, it has not passed
    its expiry, and it has activations remaining. Entries in a terminal status, expired, or
    exhausted are excluded.

    Args:
        registry: A loaded registry.
        now: Reference time (defaults to now, UTC).

    Returns:
        The list of active relaxing entries.
    """
    ref = _now(now)
    active: list[dict] = []
    for entry in registry.get("exceptions", []):
        status = (entry.get("status") or _ACTIVE_STATUS).strip().lower()
        if status in _TERMINAL_STATUSES or status != _ACTIVE_STATUS:
            continue
        if not _is_relaxing_entry(entry):
            continue
        if _entry_expired(entry, ref) or not _entry_activations_left(entry):
            continue
        active.append(entry)
    return active


def lapsed_active_exceptions(registry: dict, *, now: datetime | None = None) -> list[dict]:
    """Return entries still marked ``active`` but past their expiry — a sunset that lapsed.

    These are findings in their own right (Rule-12): an exception that outlived its sunset without
    being flipped to a terminal status. The aggregate guard refuses while any exist, because a
    lapsed exception means the registry is not being maintained and its counts cannot be trusted.
    """
    ref = _now(now)
    lapsed: list[dict] = []
    for entry in registry.get("exceptions", []):
        status = (entry.get("status") or _ACTIVE_STATUS).strip().lower()
        if status != _ACTIVE_STATUS:
            continue
        if _entry_expired(entry, ref):
            lapsed.append(entry)
    return lapsed


def assert_within_aggregate_cap(
    root: Path, *, adding: int = 1, now: datetime | None = None
) -> None:
    """Refuse (fail-closed) when activating ``adding`` more relaxing exceptions would exceed the cap.

    The Rule-12 pre-activation guard. Refuses when:

    * any active exception has lapsed past its sunset/expiry (the registry is stale — its counts
      are untrustworthy until the lapsed entry is retired); or
    * the count of active relaxing exceptions plus ``adding`` exceeds ``cap.max_active_relaxing``.

    Args:
        root: The workspace root.
        adding: How many new relaxing exceptions are about to activate (default 1).
        now: Reference time.

    Raises:
        ExceptionRegistryError: A lapsed active exception exists, or the cap would be exceeded.
    """
    registry = load_registry(root)
    cap = _configured_cap(registry)
    lapsed = lapsed_active_exceptions(registry, now=now)
    if lapsed:
        ids = ", ".join(sorted((e.get("id") or "?") for e in lapsed))
        raise ExceptionRegistryError(
            f"exception registry has active-but-expired exception(s) [{ids}]; retire them "
            f"(flip to a terminal status) before activating another — an aggregate guard cannot "
            f"trust a stale registry (fail-closed)."
        )
    current = len(active_relaxing_exceptions(registry, now=now))
    if current + adding > cap:
        raise ExceptionRegistryError(
            f"activating {adding} more relaxing exception(s) would bring the active total to "
            f"{current + adding}, over the cap of {cap} (references/{EXCEPTION_REGISTRY_REL} "
            f"cap.max_active_relaxing). Raise the cap deliberately in the registry or retire an "
            f"existing exception (fail-closed)."
        )


def audit_exceptions(root: Path, *, now: datetime | None = None) -> list[str]:
    """Return a read-only list of problems with the registry (empty when clean).

    Mirrors ``verify_directives``/``verify_grants``: consumes nothing, reports every lapsed active
    exception and a cap breach, plus a whole-registry parse/shape error.

    Args:
        root: The workspace root.
        now: Reference time.

    Returns:
        A list of problem strings (empty when the registry is clean and within cap).
    """
    try:
        registry = load_registry(root)
        cap = _configured_cap(registry)
    except ExceptionRegistryError as exc:
        return [str(exc)]
    problems: list[str] = []
    try:
        for entry in lapsed_active_exceptions(registry, now=now):
            problems.append(
                f"exception {entry.get('id')!r} is still 'active' but expired "
                f"({entry.get('expires')}) — retire it to a terminal status"
            )
        active = active_relaxing_exceptions(registry, now=now)
    except ExceptionRegistryError as exc:
        return problems + [str(exc)]
    if len(active) > cap:
        problems.append(
            f"active relaxing exceptions ({len(active)}) exceed the cap ({cap})"
        )
    return problems


def _find_entry(registry: dict, exception_id: str) -> dict | None:
    for entry in registry.get("exceptions", []):
        if (entry.get("id") or "") == exception_id:
            return entry
    return None


def same_chain(chain_a: str, chain_b: str) -> bool:
    """Return True when two chain-ids denote the same drafting/review chain (Workstream F).

    An out-of-chain reviewer must not share a chain-id with the drafting chain. For a solo
    developer this is *advisory* (an agent reviewer cannot be an accountable second party — the
    charter recharacterization), but recording and comparing chain-ids still mechanizes the
    "same session cannot review its own governance change" check where more than one chain exists.
    Empty ids never match (an unrecorded chain is treated as distinct-unknown, surfaced by the
    caller rather than silently accepted).
    """
    a, b = (chain_a or "").strip(), (chain_b or "").strip()
    return bool(a) and bool(b) and a == b


def register_exception(
    root: Path, entry: dict, *, now: datetime | None = None, reactivate: bool = False
) -> dict:
    """Activate a new exception, fail-closed against the cap and against silent re-mint (WS-F).

    Refuses when:

    * the id already exists in a **terminal** state (``declined``/``revoked``/``expired``/
      ``sunset``) and ``reactivate`` was not explicitly set — this is the refusal-as-valid rule: a
      *declined* exception cannot be silently re-minted by re-adding it as active; reactivating is a
      deliberate, separate act that must carry its own justification (``entry['reactivation_reason']``);
    * activating it would breach the aggregate cap (:func:`assert_within_aggregate_cap`).

    Args:
        root: The workspace root.
        entry: The exception entry (must carry an ``id``).
        now: Reference time.
        reactivate: Explicitly permit re-activating a previously terminal id.

    Returns:
        The registry as written.

    Raises:
        ExceptionRegistryError: Missing id, silent re-mint of a terminal id, or a cap breach.
    """
    exception_id = (entry.get("id") or "").strip()
    if not exception_id:
        raise ExceptionRegistryError("an exception entry must carry a non-empty id")
    registry = load_registry(root)
    existing = _find_entry(registry, exception_id)
    if existing is not None:
        status = (existing.get("status") or "").strip().lower()
        if status in _TERMINAL_STATUSES and not reactivate:
            raise ExceptionRegistryError(
                f"exception {exception_id!r} is in terminal state {status!r}; refusing to silently "
                f"re-mint it as active. Reactivation is a deliberate act — pass reactivate=True with "
                f"a reactivation_reason (fail-closed)."
            )
        if status in _TERMINAL_STATUSES and reactivate and not (entry.get("reactivation_reason") or "").strip():
            raise ExceptionRegistryError(
                f"reactivating {exception_id!r} requires a reactivation_reason"
            )
    # Aggregate cap on the resulting active set (count this activation).
    assert_within_aggregate_cap(root, adding=(0 if existing is not None else 1), now=now)
    entry = {**entry, "status": _ACTIVE_STATUS}
    registry["exceptions"] = [
        e for e in registry.get("exceptions", []) if (e.get("id") or "") != exception_id
    ] + [entry]
    write_registry(root, registry)
    return registry


def mark_declined(
    root: Path, exception_id: str, *, reason: str, chain_id: str = ""
) -> dict:
    """Flip an exception to the terminal ``declined`` state (WS-F refusal-as-valid).

    A declined exception stays recorded so it cannot be silently re-minted (only a deliberate
    :func:`register_exception` with ``reactivate=True`` can bring it back). Records the decline
    reason and the reviewer ``chain_id`` for the out-of-chain check.

    Args:
        root: The workspace root.
        exception_id: The id to decline.
        reason: Why it was declined (required — a terminal state must record its cause).
        chain_id: The declining reviewer's chain-id (recorded for :func:`same_chain`).

    Returns:
        The registry as written.

    Raises:
        ExceptionRegistryError: The id is unknown, or no reason was given.
    """
    if not (reason or "").strip():
        raise ExceptionRegistryError("declining an exception requires a reason")
    registry = load_registry(root)
    entry = _find_entry(registry, exception_id)
    if entry is None:
        raise ExceptionRegistryError(f"unknown exception id {exception_id!r}")
    entry["status"] = "declined"
    entry["declined_reason"] = reason
    if chain_id:
        entry["declined_by_chain"] = chain_id
    write_registry(root, registry)
    return registry


def list_exceptions(root: Path) -> list[dict]:
    """Return every registry entry (for ``--list-exceptions``), or an empty list when absent."""
    return list(load_registry(root).get("exceptions", []))


def write_registry(root: Path, registry: dict) -> None:
    """Atomically write the registry back (used by tests and the register verb).

    Args:
        root: The workspace root.
        registry: The registry mapping to persist (pretty-printed, stable key order).
    """
    if not isinstance(registry, dict) or not isinstance(registry.get("exceptions"), list):
        raise ExceptionRegistryError("refusing to write a malformed registry")
    path = root / EXCEPTION_REGISTRY_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, json.dumps(registry, indent=2, sort_keys=True) + "\n")


__all__ = [
    "EXCEPTION_REGISTRY_REL",
    "ExceptionRegistryError",
    "active_relaxing_exceptions",
    "assert_within_aggregate_cap",
    "audit_exceptions",
    "lapsed_active_exceptions",
    "list_exceptions",
    "load_registry",
    "mark_declined",
    "register_exception",
    "same_chain",
    "write_registry",
]
