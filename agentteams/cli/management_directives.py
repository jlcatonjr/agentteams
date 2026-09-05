"""management_directives.py — authenticated management-repository endowment (crypto core).

A **management directive** is a signed, scoped, time-bounded operator artifact that lets a
*managed* repository's agents proceed on an **already-user-authorized, NON-DESTRUCTIVE** task
without re-asking the user — but *only* if the directive verifies. It is the endowment analogue
of a capability grant (:mod:`agentteams.cli.grants`): a grant *widens a write boundary*; a
directive *waives a re-ask on an authorized, benign task*. They are kept as separate ledgers,
signed with separate keys, so the two authorities rotate independently.

**What a directive is NOT.** A directive is not a Tier-2 elevation and confers no privilege it
did not already have. By construction it can NEVER:

* clear a destructive, bulk, or cross-repository action (C-5 — enforced mechanically by the
  :data:`_REFUSED_SCOPE_TOKENS` denylist in :func:`scope_is_allowed`, which a valid signature
  cannot override);
* pierce or retract a ``@security`` HALT (C-2);
* touch a governance or trust root — the constitution, an invariant, a capability grant, the
  roster, a signing key, the security-decision log, a waiver (also denylisted).

**The security boundary is EXACT scope.** A directive authorizes *exactly* one ``task_scope``
string; callers match by literal equality (:func:`directive_authorizes`). This module
deliberately does NOT reuse ``decision_log._action_matches`` suffix-matching, which *widens*: a
clearance written as ``overwrite-single-readme-only`` would match the bare ``overwrite`` action
under suffix matching. Exact match is the only match here.

**Where a directive lives.** The ledger is the MANAGED repo's
``references/management-directives.log.csv``, so the managed repo's own agents read the
directives issued to them (a bearer model, like grants).

**Trust model (symmetric).** One shared :data:`MGMT_KEY_ENV` key, HMAC-SHA256 via
:mod:`agentteams.cli.signed_ledger`. Signing defends a *keyless* agent from fabricating a
directive; it does not defend against an actor holding the key. The key must be issued
out-of-band and never enter an agent session. Asymmetric, cross-team-unforgeable signing would
need a non-stdlib dependency; :mod:`agentteams.cli.signed_ledger` is the swap point.

**Fail-closed everywhere.** Any uncertainty — unset key, malformed deadline, absent roster,
unrecognized scope — refuses. The signing key value is never logged, emitted, or returned.
"""

from __future__ import annotations

import csv
import hashlib
import os
import re
from datetime import datetime
from pathlib import Path

from agentteams.atomicio import _atomic_write_text, atomic_rewrite_csv_rows
from agentteams.cli.signed_ledger import (
    canonical_payload,
    hmac_sign,
    hmac_verify,
    is_expired,
)

#: Env var holding the shared management-directive signing secret. Separate from the grant,
#: waiver, and decision keys so the four authorities rotate independently.
MGMT_KEY_ENV = "AGENTTEAMS_MANAGEMENT_SIGNING_KEY"

#: Relative path of the append-only management-directive ledger within the MANAGED workspace.
MGMT_DIRECTIVES_LOG_REL = "references/management-directives.log.csv"

#: Relative path of the authorized-manager roster within the MANAGED workspace. One
#: manager-team id per line; ``#`` comments and blank lines ignored; ``@`` and case normalized.
AUTHORIZED_MANAGERS_REL = "references/authorized-managers.txt"

#: Full ordered column set of a management-directive row.
MGMT_DIRECTIVE_COLUMNS: tuple[str, ...] = (
    "timestamp", "directive_id", "manager_team", "managed_team", "task_scope",
    "expires_at", "max_uses", "uses", "approver", "prev_digest", "signature",
)

#: Business fields that MUST be present (non-empty) on every row, in fixed order (excludes
#: ``timestamp``, ``prev_digest``, and ``signature``). ``uses`` is signed so a tampered
#: use-counter invalidates the row.
_MGMT_SIGNATURE_FIELDS: tuple[str, ...] = (
    "directive_id", "manager_team", "managed_team", "task_scope",
    "expires_at", "max_uses", "uses", "approver",
)

#: Fields the HMAC actually covers: the business fields PLUS ``prev_digest``. Signing the chain
#: link is load-bearing — if it were unsigned, a keyless attacker could delete a row and rewrite
#: the next row's ``prev_digest`` to re-chain over the gap (the signature would still verify),
#: hiding the deletion. The genesis row carries ``prev_digest=""`` (allowed empty here; it is not
#: in the required-non-empty set above).
_MGMT_SIGNED_FIELDS: tuple[str, ...] = (*_MGMT_SIGNATURE_FIELDS, "prev_digest")

#: The signature-non-overridable scope denylist — THE security boundary (@security condition [2]).
#: A ``task_scope`` whose lowercased text CONTAINS any of these tokens is refused by
#: :func:`scope_is_allowed` regardless of a valid signature. Two documented groups:
#:
#: 1. **Destructive / bulk / cross-repository actions** — C-5: a directive can never clear
#:    destruction. A directive only ever waives a re-ask on a benign, already-authorized task.
#: 2. **Governance / trust roots** — a directive can never change governance or trust: the
#:    constitution, an invariant, a capability grant, the roster, a signing key, the
#:    security-decision log, a waiver, enforcement/integrity machinery.
_REFUSED_SCOPE_TOKENS: tuple[str, ...] = (
    # (1) destructive / bulk / cross-repo
    "delete", "remove", "rm", "overwrite", "prune", "destroy", "drop", "purge",
    "force", "reset", "bulk", "cross-repo", "push", "deploy", "merge",
    # (2) governance / trust roots (incl. this feature's own ledger + roster + key config,
    # so a directive can never widen its own trust base — @security condition 2c)
    "constitution", "invariant", "governance", "reference-template", "capability-grant",
    "grant", "grants", "roster", "authorized-manager", "authorized-managers",
    "management-authority", "management-directive", "management-directives", "directive",
    "directives", "signing-key", "integrity", "enforcement", "security-decision", "waiver",
)


class MgmtDirectiveError(RuntimeError):
    """Raised when a directive is malformed, invalid, denylisted, off-roster, or unsigned."""


def _signing_key() -> str:
    """Return the management-directive signing key from the environment, or fail closed.

    The key value is never logged, emitted, or returned anywhere but here (to the internal
    signing primitive).

    Returns:
        The signing secret.

    Raises:
        MgmtDirectiveError: The env var is unset or empty — signing/verification cannot proceed.
    """
    key = os.environ.get(MGMT_KEY_ENV, "")
    if not key:
        raise MgmtDirectiveError(
            f"{MGMT_KEY_ENV} is not set; refusing to sign or verify a management directive "
            "without a signing key (fail-closed)."
        )
    return key


def _signed_values(record: dict[str, str]) -> list[str]:
    """The ordered signed field VALUES for a directive.

    Returns the values of :data:`_MGMT_SIGNED_FIELDS` in order (the business fields plus
    ``prev_digest``). Because :func:`~signed_ledger.canonical_payload` joins values with ``|``,
    a **new signed axis** may later be added as an *optional appended signed field* — the grants
    ``issuer_root`` seam (:func:`grants._signed_values`): append it only when non-empty, so a
    directive minted without it keeps a valid signature while one that carries it binds it into
    the signature. No such optional axis exists today; this note marks where one would slot in.

    Args:
        record: The directive fields (at least the signed fields).

    Returns:
        The ordered signed values.
    """
    return [record.get(field, "") for field in _MGMT_SIGNED_FIELDS]


def sign_directive(record: dict[str, str], *, key: str | None = None) -> str:
    """Return the signature for a directive record.

    Args:
        record: The directive fields (at least the signed fields).
        key: Override the signing key (defaults to the env key).

    Returns:
        The hex HMAC-SHA256 signature.

    Raises:
        MgmtDirectiveError: No key was supplied and the env var is unset (fail-closed).
    """
    signing_key = key or _signing_key()
    return hmac_sign(signing_key, _signed_values(record))


def verify_directive_signature(record: dict[str, str], *, key: str | None = None) -> bool:
    """Return True iff the record's ``signature`` matches its signed fields (constant-time).

    Args:
        record: The directive row.
        key: Override the signing key (defaults to the env key).

    Returns:
        Whether the signature is valid.

    Raises:
        MgmtDirectiveError: No key was supplied and the env var is unset (fail-closed).
    """
    signing_key = key or _signing_key()
    return hmac_verify(signing_key, _signed_values(record), record.get("signature", ""))


def scope_is_allowed(task_scope: str) -> bool:
    """Return True iff ``task_scope`` is a benign scope a directive may authorize.

    THE security boundary (@security condition [2]). Mechanical and signature-non-overridable:
    a scope naming any :data:`_REFUSED_SCOPE_TOKENS` token — matched on WORD BOUNDARIES
    (case-insensitive), where ``-``/``_``/``.``/``/`` and whitespace delimit words — is ALWAYS
    refused, even under a valid signature, because a directive can never clear destruction (C-5)
    or touch a governance/trust root. Word-boundary (not raw substring) matching is deliberate:
    a raw ``"rm" in scope`` test refuses benign scopes like ``transform``/``confirm``/``format``;
    the boundary test still catches ``rm-tmp``, ``delete-logs``, ``cross-repo`` while allowing
    ``ingest-transform-data``. An empty or whitespace-only scope is refused too (unrecognized
    scope => refuse).

    This is a denylist gate, not the scope *match*: whether a given directive authorizes a
    specific requested task is decided separately, by EXACT equality, in
    :func:`directive_authorizes`.

    Args:
        task_scope: The task-scope string carried by (or requested of) a directive.

    Returns:
        Whether the scope is permitted (benign, recognized, non-denylisted).
    """
    scope = (task_scope or "").strip().lower()
    if not scope:
        return False
    for token in _REFUSED_SCOPE_TOKENS:
        # Word-boundary match: the token must not be flanked by ASCII letters, so "rm" matches
        # "rm-tmp" but not "transform". Hyphenated tokens (cross-repo, capability-grant) match
        # as-is since their own boundaries are non-letters.
        if re.search(r"(?<![a-z])" + re.escape(token) + r"(?![a-z])", scope):
            return False
    return True


def directive_authorizes(record: dict[str, str], task_scope: str) -> bool:
    """Return True iff ``record`` authorizes EXACTLY ``task_scope`` (literal equality).

    Scope match only — does NOT check signature, expiry, uses, roster, or the denylist (see
    :func:`validate_directive`). EXACT-scope by design: a directive for
    ``overwrite-single-readme-only`` does NOT authorize the bare ``overwrite`` task, and a
    directive for ``draft-weekly-report`` authorizes exactly itself, never ``draft``. This
    deliberately avoids the ``decision_log._action_matches`` suffix-widening defect.

    Args:
        record: The directive row.
        task_scope: The requested task scope.

    Returns:
        Whether the directive's ``task_scope`` equals the request exactly (after strip).
    """
    row_scope = (record.get("task_scope") or "").strip()
    requested = (task_scope or "").strip()
    if not row_scope or not requested:
        return False
    return row_scope == requested


def _read_manager_roster(root: Path) -> set[str]:
    """Return the normalized authorized-manager team ids from the roster (empty set if none).

    A line is a manager id only when it is non-blank and not a ``#`` comment. Each id is
    normalized by stripping surrounding whitespace, a leading ``@``, and lowercasing — so
    ``@Team-Ops`` and ``team-ops`` compare equal. An absent or unreadable file returns an
    empty set (fail-closed — an empty roster accepts nothing).

    Args:
        root: The managed workspace root.

    Returns:
        The normalized manager-team ids named on the roster.
    """
    roster_path = root / AUTHORIZED_MANAGERS_REL
    if not roster_path.exists():
        return set()
    try:
        raw = roster_path.read_text(encoding="utf-8")
    except OSError:
        return set()
    return {
        line.strip().lstrip("@").lower()
        for line in raw.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def _roster_names_a_manager(root: Path) -> bool:
    """True when the authorized-manager roster exists and names at least one manager team.

    Mirrors ``grants._roster_names_an_approver``: an absent file, an unreadable file, or a file
    of only blanks/comments all return ``False``. A cross-team directive HARD-REQUIRES a
    non-empty roster (fail-closed, like grants' cross-workspace roster requirement) — an empty
    roster accepts nothing.

    Args:
        root: The managed workspace root.

    Returns:
        Whether a named manager team is present on the roster.
    """
    return bool(_read_manager_roster(root))


def _manager_on_roster(manager_team: str, root: Path) -> tuple[bool, str]:
    """Return ``(ok, reason)`` for whether ``manager_team`` is an authorized manager.

    A cross-team directive requires an EXPLICIT, non-empty roster: when the roster is absent or
    names no manager, this refuses (fail-closed) rather than falling back to any default — a
    directive that waives a managed repo's re-ask must be issued by a recorded, project-chosen
    manager team, never an unrecorded default.

    Args:
        manager_team: The manager team named on the directive (``@`` and case normalized).
        root: The managed workspace root.

    Returns:
        ``(True, "")`` when authorized; ``(False, reason)`` otherwise.
    """
    if not _roster_names_a_manager(root):
        return (
            False,
            "cross-team management directive requires an explicit authorized-managers roster; "
            f"{AUTHORIZED_MANAGERS_REL} is absent or names no manager team (fail-closed — add at "
            "least one manager team before issuing or honouring a directive)",
        )
    normalized = (manager_team or "").strip().lstrip("@").lower()
    if normalized not in _read_manager_roster(root):
        return (
            False,
            f"manager team {manager_team!r} is not on the authorized-managers roster "
            f"({AUTHORIZED_MANAGERS_REL})",
        )
    return (True, "")


def validate_directive(
    record: dict[str, str], root: Path, *, now: datetime | None = None, key: str | None = None,
) -> tuple[bool, str]:
    """Validate a directive's signature and lifecycle; return ``(ok, reason)`` (fail-closed).

    Checks IN THIS FAIL-CLOSED ORDER — ALL must pass:

    a. the signature verifies against the key;
    b. the directive is NOT expired (a malformed ``expires_at`` fails closed as invalid,
       like :func:`grants.validate_grant`, rather than escaping as an unhandled error);
    c. the use-counter is not exhausted (``uses < max_uses``);
    d. ``manager_team`` is on the authorized-managers roster (a non-empty roster is required);
    e. ``task_scope`` passes :func:`scope_is_allowed` (the denylist / recognized-scope gate).

    Any failure returns ``(False, reason)``. Uncertainty refuses. This does NOT verify that the
    directive authorizes a *particular* requested task — that is EXACT-scope, decided by
    :func:`directive_authorizes`.

    Args:
        record: The directive row.
        root: The managed workspace root (for the roster lookup).
        now: Reference time for expiry (defaults to now, UTC).
        key: Override the signing key.

    Returns:
        ``(True, "ok")`` when every check passes; ``(False, reason)`` on the first failure.
    """
    # (a) signature — a missing key fails closed here rather than escaping.
    try:
        signature_ok = verify_directive_signature(record, key=key)
    except MgmtDirectiveError as exc:
        return (False, str(exc))
    if not signature_ok:
        return (False, f"directive {record.get('directive_id')!r} has an invalid signature")

    # (b) expiry — a malformed deadline fails CLOSED as invalid (not an unhandled ValueError).
    try:
        expired = is_expired(record.get("expires_at", ""), now=now)
    except (ValueError, KeyError) as exc:
        return (
            False,
            f"directive {record.get('directive_id')!r} has a malformed expires_at "
            f"({record.get('expires_at')!r}): {exc}",
        )
    if expired:
        return (
            False,
            f"directive {record.get('directive_id')!r} has expired ({record.get('expires_at')})",
        )

    # (c) use-counter.
    try:
        max_uses = int(record.get("max_uses", ""))
        uses = int(record.get("uses", ""))
    except (TypeError, ValueError):
        return (False, f"directive {record.get('directive_id')!r} has non-integer max_uses/uses")
    if max_uses <= 0 or uses >= max_uses:
        return (
            False,
            f"directive {record.get('directive_id')!r} is exhausted "
            f"(uses={uses}, max_uses={max_uses})",
        )

    # (d) manager roster (non-empty roster required).
    on_roster, reason = _manager_on_roster(record.get("manager_team", ""), root)
    if not on_roster:
        return (False, reason)

    # (e) scope denylist / recognized-scope gate — the security boundary.
    if not scope_is_allowed(record.get("task_scope", "")):
        return (
            False,
            f"directive {record.get('directive_id')!r} names a refused task_scope "
            f"({record.get('task_scope')!r}) — a directive can never clear a destructive, bulk, "
            "cross-repo, or governance/trust scope (a valid signature cannot override this)",
        )

    return (True, "ok")


def _directive_chain_digest(record: dict[str, str]) -> str:
    """Return a directive row's chain digest: SHA-256 over the fields the signature covers.

    The next row's ``prev_digest`` equals this value, so a removed or reordered row breaks the
    chain. Computed over :data:`_MGMT_SIGNED_FIELDS` (which includes ``prev_digest``) using the
    SAME canonicalization the HMAC signs over, so the digest chain and the signature never
    disagree on serialization, and each link transitively depends on every earlier one back to
    the genesis row.

    Args:
        record: The directive row.

    Returns:
        The hex SHA-256 digest.
    """
    payload = canonical_payload(_signed_values(record))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def assert_directive_chain_intact(rows: list[dict[str, str]], columns: list[str]) -> None:
    """Verify the ledger's hash chain when it carries one (opt-in on the ``prev_digest`` column).

    Per-row signing proves who wrote a row; chaining proves nobody removed one — deleting a
    signed directive otherwise leaves every remaining signature valid (a silent loss of the
    record). A ledger with no ``prev_digest`` column is unchained and passes, the same way
    signing is opt-in, so a pre-chain ledger keeps working until it is re-issued.

    Args:
        rows: The ledger rows in file order.
        columns: The ledger's header columns.

    Raises:
        MgmtDirectiveError: A row's ``prev_digest`` does not match the running chain — a row was
            removed, reordered, or edited.
    """
    if "prev_digest" not in {c.strip() for c in columns}:
        return
    previous = ""
    for index, row in enumerate(rows):
        declared = (row.get("prev_digest") or "").strip()
        if declared != previous:
            raise MgmtDirectiveError(
                f"management-directive ledger chain broken at row {index + 1}: prev_digest is "
                f"{declared!r}, expected {previous!r} — a directive row was removed, reordered, "
                f"or edited."
            )
        previous = _directive_chain_digest(row)


def _read_directive_rows(root: Path) -> list[dict[str, str]]:
    """Return all directive rows from the managed workspace's ledger (empty list if absent).

    Verifies the ``prev_digest`` hash chain (when present) before returning, so every reader
    fails closed on a tampered or truncated ledger rather than trusting rows around a deleted one.

    Args:
        root: The managed workspace root containing :data:`MGMT_DIRECTIVES_LOG_REL`.

    Returns:
        The rows as dicts, values coerced to ``""`` for missing cells.

    Raises:
        MgmtDirectiveError: The ledger exists but its header is malformed, or its chain is broken.
    """
    log_path = root / MGMT_DIRECTIVES_LOG_REL
    if not log_path.exists():
        return []
    try:
        with log_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            columns = [c.strip() for c in (reader.fieldnames or [])]
            if not set(MGMT_DIRECTIVE_COLUMNS).issubset(columns):
                raise MgmtDirectiveError(
                    "management-directives log is malformed: expected header "
                    + ",".join(MGMT_DIRECTIVE_COLUMNS)
                )
            rows = [{k: (v or "") for k, v in row.items()} for row in reader]
    except (OSError, csv.Error) as exc:
        raise MgmtDirectiveError(f"unable to read management-directives log: {exc}") from exc
    assert_directive_chain_intact(rows, columns)
    return rows


def verify_directives(
    root: Path, *, now: datetime | None = None, key: str | None = None,
) -> list[str]:
    """Validate every directive row read-only; return a list of human-readable problems.

    Mirrors ``--verify-grants``: consumes nothing, reports every invalid/expired/exhausted/
    off-roster/denylisted-scope row (and a whole-ledger chain or header break).

    Args:
        root: The managed workspace root.
        now: Reference time for expiry.
        key: Override the signing key.

    Returns:
        A list of problem strings (empty when all rows are valid).
    """
    problems: list[str] = []
    try:
        rows = _read_directive_rows(root)
    except MgmtDirectiveError as exc:
        # A broken hash chain (or malformed header) is a whole-ledger problem — report it
        # rather than crash the read-only audit.
        return [str(exc)]
    for row in rows:
        ok, reason = validate_directive(row, root, now=now, key=key)
        if not ok:
            problems.append(reason)
    return problems


def issue_directive(
    root: Path, *, manager_team: str, managed_team: str, task_scope: str, expires_at: str,
    max_uses: int, approver: str, directive_id: str, timestamp: str, key: str | None = None,
) -> dict[str, str]:
    """Mint, sign, and append a management directive to the MANAGED repo's ledger.

    Refuses to issue a denylisted scope (:func:`scope_is_allowed`) and requires the manager team
    to be on the managed repo's authorized-managers roster at issue time (fail-closed) — so an
    unsafe or unauthorized directive can never enter the ledger and later waive a re-ask.

    Args:
        root: The MANAGED workspace root (ledger written under it; roster checked here).
        manager_team: The managing team's id (must be on the authorized-managers roster).
        managed_team: The managed team's id (recorded as the directive's subject).
        task_scope: The EXACT, benign, non-denylisted task scope the directive authorizes.
        expires_at: ISO-8601 expiry.
        max_uses: Positive use ceiling.
        approver: The principal who approved the directive (recorded for audit).
        directive_id: Caller-supplied unique id (no clock/rng available here).
        timestamp: Caller-supplied ISO-8601 issue time.
        key: Override the signing key.

    Returns:
        The signed directive record as written.

    Raises:
        MgmtDirectiveError: ``max_uses`` is not positive, ``task_scope`` is denylisted/empty,
            ``expires_at`` is not valid ISO-8601, the manager team is off-roster (or no roster is
            present), or the signing key is unset.
    """
    if max_uses <= 0:
        raise MgmtDirectiveError("max_uses must be a positive integer")
    if not scope_is_allowed(task_scope):
        raise MgmtDirectiveError(
            f"refusing to issue a management directive for task_scope {task_scope!r}: it is a "
            "destructive, bulk, cross-repo, or governance/trust scope (or is empty). A directive "
            "may only waive a re-ask on a benign, already-authorized, non-destructive task."
        )
    try:
        is_expired(expires_at)  # parse-only: reject a malformed deadline at issue.
    except (ValueError, KeyError) as exc:
        raise MgmtDirectiveError(
            f"expires_at is not a valid ISO-8601 timestamp ({expires_at!r}): {exc}"
        ) from exc
    on_roster, reason = _manager_on_roster(manager_team, root)
    if not on_roster:
        raise MgmtDirectiveError(reason)

    # Chain the new row to the ledger's current tail so a later deletion is detectable (also
    # verifies the existing chain is intact — fail closed on a tampered ledger).
    existing = _read_directive_rows(root)
    prev_digest = _directive_chain_digest(existing[-1]) if existing else ""
    record: dict[str, str] = {
        "timestamp": timestamp, "directive_id": directive_id, "manager_team": manager_team,
        "managed_team": managed_team, "task_scope": task_scope, "expires_at": expires_at,
        "max_uses": str(max_uses), "uses": "0", "approver": approver,
        "prev_digest": prev_digest, "signature": "",
    }
    record["signature"] = sign_directive(record, key=key)
    _append_directive_row(root, record)
    return record


def _append_directive_row(root: Path, record: dict[str, str]) -> None:
    """Append a directive row to the ledger, creating it with a header if absent.

    Args:
        root: The managed workspace root.
        record: The fully-signed directive row to append.
    """
    log_path = root / MGMT_DIRECTIVES_LOG_REL
    existing = _read_directive_rows(root) if log_path.exists() else []
    log_path.parent.mkdir(parents=True, exist_ok=True)
    rows = existing + [record]
    if not log_path.exists():
        header = ",".join(MGMT_DIRECTIVE_COLUMNS) + "\n"
        _atomic_write_text(log_path, header)
    atomic_rewrite_csv_rows(log_path, rows, list(MGMT_DIRECTIVE_COLUMNS))


__all__ = [
    "AUTHORIZED_MANAGERS_REL",
    "MGMT_DIRECTIVE_COLUMNS",
    "MGMT_DIRECTIVES_LOG_REL",
    "MGMT_KEY_ENV",
    "MgmtDirectiveError",
    "assert_directive_chain_intact",
    "directive_authorizes",
    "issue_directive",
    "scope_is_allowed",
    "sign_directive",
    "validate_directive",
    "verify_directive_signature",
    "verify_directives",
]
