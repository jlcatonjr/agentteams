"""grants.py — cross-workspace capability grants (P2).

A capability grant is a signed, scoped, time-bounded authorization by one team
(``issuer_team``) for another (``holder_team``) to perform an operation on a specific
path in the issuer's workspace. It is the cross-workspace analogue of a security waiver,
but inverted in intent: a waiver *lifts a stop* (clearance to proceed past a HALT); a
grant *permits a reach* (widens a boundary). They are kept as separate ledgers for that
reason — see ``security_gate.py`` for the waiver side.

**Where a grant lives.** The ledger is the HOLDER's
``references/capability-grants.log.csv`` — the holder holds the grants issued to it
(a bearer-capability model). ``--issue-grant`` deposits into the holder workspace so the
holder's own generation reads it; ``issuer_team`` records who authorized it.

Enforcement is **generation-time**: when a holder team is (re)generated with the Claude
sandbox on, the paths of the valid grants it holds that permit ``write`` are merged into
its sandbox ``allowWrite`` (see the generate path). A freshly-issued grant is therefore
inert until the operator re-runs an update — deliberately: there is no runtime path by
which an agent widens its own OS boundary.

**On ``max_uses``.** The generation-time widening path does NOT consume a use — widening
is not a per-write event, so there is nothing to count there. Under this (only) enforcement
path, ``expires_at`` is the active temporal bound; ``max_uses`` is validated (an exhausted
grant is rejected) and reserved for a future per-write runtime-consume path, but it is not
decremented today. Do not rely on ``max_uses`` to bound generation-time widening.

Trust model (symmetric, per the 2026-08-21 decision): one shared
``AGENTTEAMS_GRANT_SIGNING_KEY``. Signing defends a *keyless* agent from fabricating a
grant; it does not defend against an actor that holds the key (an adversarial peer
team). The key must be issued out-of-band and never enter an agent session — the same
precondition as the waiver key. Asymmetric, cross-team-unforgeable signing would need a
non-stdlib dependency; :mod:`agentteams.cli.signed_ledger` is the swap point.

**C-2 parity:** a grant widens a write boundary; it never overrides a ``@security``
HALT. A holder still cannot proceed past a HALT on the granted write.
"""

from __future__ import annotations

import csv
import hashlib
import os
import sys
from datetime import datetime
from pathlib import Path

from agentteams.atomicio import _atomic_write_text, atomic_rewrite_csv_rows
from agentteams.cli.signed_ledger import (
    canonical_payload,
    hmac_sign,
    hmac_verify,
    is_expired,
    path_within,
)

#: Env var holding the shared grant signing secret. Separate from the waiver key so the
#: two authorities can be rotated independently.
GRANT_KEY_ENV = "AGENTTEAMS_GRANT_SIGNING_KEY"

#: Relative path of the append-only grant ledger within a workspace.
GRANT_LOG_REL = "references/capability-grants.log.csv"

#: Full ordered column set of a grant row.
GRANT_COLUMNS: tuple[str, ...] = (
    "timestamp", "grant_id", "issuer_team", "holder_team", "target_path",
    "permitted_ops", "expires_at", "max_uses", "uses", "approver", "ticket_id",
    "reason_code", "issuer_root", "prev_digest", "signature",
)

#: Business fields that MUST be present (non-empty) on every row, in fixed order (excludes
#: ``timestamp``, ``prev_digest`` and ``signature``). ``uses`` is signed so a tampered
#: counter invalidates the row (a future runtime-consume path would re-sign on increment;
#: the generation-time widening path does not consume — see the module docstring).
_GRANT_SIGNATURE_FIELDS: tuple[str, ...] = (
    "grant_id", "issuer_team", "holder_team", "target_path", "permitted_ops",
    "expires_at", "max_uses", "uses", "approver", "ticket_id", "reason_code",
)

#: Fields the HMAC actually covers: the business fields PLUS ``prev_digest``. Signing the
#: chain link is load-bearing — if it were unsigned, a keyless attacker could delete a row
#: and rewrite the next row's ``prev_digest`` to re-chain over the gap (the signature would
#: still verify), hiding the deletion. The genesis row carries ``prev_digest=""`` (allowed
#: empty here; it is not in the required-non-empty set above).
_GRANT_SIGNED_FIELDS: tuple[str, ...] = (*_GRANT_SIGNATURE_FIELDS, "prev_digest")


class GrantError(RuntimeError):
    """Raised when a grant is malformed, invalid, expired, exhausted, or unsigned."""


def _signing_key() -> str:
    """Return the grant signing key from the environment, or fail closed.

    Returns:
        The signing secret.

    Raises:
        GrantError: The env var is unset or empty — signing/verification cannot proceed.
    """
    key = os.environ.get(GRANT_KEY_ENV, "")
    if not key:
        raise GrantError(
            f"{GRANT_KEY_ENV} is not set; refusing to sign or verify a capability grant "
            "without a signing key (fail-closed)."
        )
    return key


def _signed_values(record: dict[str, str]) -> list[str]:
    """The ordered signed field VALUES for a grant.

    ``issuer_root`` (G-6, the P2-4 containment anchor) is an **optional appended signed field**:
    it is included only when non-empty. Because :func:`~signed_ledger.canonical_payload` joins
    values with ``|``, omitting an empty ``issuer_root`` reproduces the exact pre-G-6 payload —
    so a grant issued without it (an older grant, or a spec that omits it) keeps a valid
    signature, while a grant that carries an ``issuer_root`` binds it into the signature so an
    attacker cannot forge or widen it (e.g. set it to ``/``) to defeat the containment check.
    """
    values = [record.get(f, "") for f in _GRANT_SIGNED_FIELDS]
    issuer_root = (record.get("issuer_root") or "").strip()
    if issuer_root:
        values.append(issuer_root)
    return values


def sign_grant(record: dict[str, str], *, key: str | None = None) -> str:
    """Return the signature for a grant record.

    Args:
        record: The grant fields (at least the signed fields).
        key: Override the signing key (defaults to the env key).

    Returns:
        The hex signature.
    """
    signing_key = key or _signing_key()
    return hmac_sign(signing_key, _signed_values(record))


def verify_grant_signature(record: dict[str, str], *, key: str | None = None) -> bool:
    """Return True iff the record's ``signature`` matches its signed fields.

    Args:
        record: The grant row.
        key: Override the signing key (defaults to the env key).

    Returns:
        Whether the signature is valid.
    """
    signing_key = key or _signing_key()
    return hmac_verify(
        signing_key,
        _signed_values(record),
        record.get("signature", ""),
    )


def _permitted_ops(record: dict[str, str]) -> set[str]:
    """Return the set of operations a grant permits (from a ``;``-separated field)."""
    return {op.strip() for op in (record.get("permitted_ops") or "").split(";") if op.strip()}


def grant_covers(
    record: dict[str, str], *, holder_team: str, target_path: str, op: str, base: Path | None = None
) -> bool:
    """Return True iff ``record`` authorizes ``holder_team`` to ``op`` on ``target_path``.

    Scope match only — does NOT check signature/expiry/uses (see :func:`validate_grant`).
    Holder must match by id, ``op`` must be permitted, and the canonicalized
    ``target_path`` must be within the grant's target path.

    Args:
        record: The grant row.
        holder_team: The team seeking the reach.
        target_path: The path the holder wants to write.
        op: The operation (e.g. ``"write"``).
        base: Directory relative paths resolve against.

    Returns:
        Whether the grant's scope covers the request.
    """
    if (record.get("holder_team") or "").strip() != holder_team.strip():
        return False
    if op.strip() not in _permitted_ops(record):
        return False
    return path_within(target_path, record.get("target_path", ""), base=base)


def validate_grant(
    record: dict[str, str], *, output_dir: Path | None = None, now: datetime | None = None,
    key: str | None = None,
) -> None:
    """Validate a grant row's signature and lifecycle; raise on any failure (fail-closed).

    Checks, in order: required fields present, signature valid, not expired, use-counter
    not exhausted, and (when ``output_dir`` is given) the approver is on the roster.

    Args:
        record: The grant row.
        output_dir: Workspace root for roster lookup; skip roster check when None.
        now: Reference time for expiry (defaults to now, UTC).
        key: Override the signing key.

    Raises:
        GrantError: Any field/signature/expiry/use-counter/roster check fails.
    """
    for field in _GRANT_SIGNATURE_FIELDS:
        if not (record.get(field) or "").strip():
            raise GrantError(f"grant is missing required field {field!r}")
    if not verify_grant_signature(record, key=key):
        raise GrantError(f"grant {record.get('grant_id')!r} has an invalid signature")
    try:
        expired = is_expired(record["expires_at"], now=now)
    except (ValueError, KeyError) as exc:
        # A malformed expires_at must fail CLOSED as an invalid grant, not escape as an
        # unhandled ValueError that crashes the caller (P2-8 / audit L-1). A grant whose
        # deadline cannot be parsed is not trustworthy.
        raise GrantError(
            f"grant {record.get('grant_id')!r} has a malformed expires_at "
            f"({record.get('expires_at')!r}): {exc}"
        ) from exc
    if expired:
        raise GrantError(f"grant {record.get('grant_id')!r} has expired ({record['expires_at']})")
    try:
        max_uses = int(record["max_uses"])
        uses = int(record["uses"])
    except (TypeError, ValueError) as exc:
        raise GrantError(f"grant {record.get('grant_id')!r} has non-integer max_uses/uses") from exc
    if max_uses <= 0 or uses >= max_uses:
        raise GrantError(
            f"grant {record.get('grant_id')!r} is exhausted (uses={uses}, max_uses={max_uses})"
        )
    if output_dir is not None:
        _assert_approver_on_roster(record.get("approver", ""), output_dir)


def _roster_names_an_approver(output_dir: Path) -> bool:
    """True when the security-approver roster exists and names at least one approver.

    Mirrors ``decision_log._approved_decision_authors``' parse: a line is an approver only
    when it is non-blank and not a ``#`` comment. An absent file, an unreadable file, or a
    file of only blanks/comments all return ``False`` — the cases where
    ``_approved_decision_authors`` would fall back to the built-in default.

    Args:
        output_dir: Workspace root (roster read from ``references/security-approvers.txt``).

    Returns:
        Whether a named approver is present.
    """
    roster_path = output_dir / "references" / "security-approvers.txt"
    if not roster_path.exists():
        return False
    try:
        raw = roster_path.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(
        line.strip() and not line.strip().startswith("#")
        for line in raw.splitlines()
    )


def _assert_approver_on_roster(approver: str, output_dir: Path) -> None:
    """Raise unless ``approver`` is on the workspace's security-approver roster.

    Reuses the same roster the waiver/decision authorities use
    (``references/security-approvers.txt``), so cross-workspace grants are approved by
    the same trusted principals as destructive local actions.

    A cross-workspace grant requires an EXPLICIT roster: when the roster file is absent
    or names no approver, ``_approved_decision_authors`` falls back to the built-in
    ``{security, @security}`` default (decision_log.py), which lets a grant naming
    ``@security`` as its own approver self-clear. That fallback is correct for ordinary
    local HALT-clearance but unsafe for cross-workspace widening (P2-2, C-5): a grant that
    widens another repo's write boundary must be cleared by a recorded, project-chosen
    principal, never by an unrecorded default. So this grant chokepoint hard-errors
    (fail-closed) when no approver roster is present.

    Args:
        approver: The approver named on the grant (``@`` and case are normalized).
        output_dir: Workspace root.

    Raises:
        GrantError: The roster is absent/empty, or the approver is not on the roster.
    """
    from agentteams.cli.decision_log import _approved_decision_authors

    if not _roster_names_an_approver(output_dir):
        raise GrantError(
            "cross-workspace grant authorization requires an explicit approver roster; "
            "references/security-approvers.txt is absent or names no approver "
            "(refusing the built-in security/@security self-clear fallback — add at least "
            "one approver to the roster before issuing or honouring a grant)"
        )

    normalized = approver.strip().lstrip("@").lower()
    roster = {a.strip().lstrip("@").lower() for a in _approved_decision_authors(output_dir)}
    if normalized not in roster:
        raise GrantError(
            f"grant approver {approver!r} is not on the security-approver roster "
            f"(references/security-approvers.txt)"
        )


def _grant_chain_digest(record: dict[str, str]) -> str:
    """Return a grant row's chain digest: SHA-256 over the fields the signature covers.

    The next row's ``prev_digest`` equals this value, so a removed or reordered row breaks
    the chain. Computed over :data:`_GRANT_SIGNED_FIELDS` (which includes ``prev_digest``),
    so the digest is bound to authenticated content and each link transitively depends on
    every earlier one back to the genesis row.

    Args:
        record: The grant row.

    Returns:
        The hex SHA-256 digest.
    """
    # Use the SAME canonicalization (strip + '|'-join) AND the same field set the HMAC signs
    # over (incl. the optional appended issuer_root, G-6), so the digest chain and the signature
    # never disagree on serialization and tampering issuer_root breaks the chain too.
    payload = canonical_payload(_signed_values(record))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _assert_grant_chain_intact(rows: list[dict[str, str]], columns: list[str]) -> None:
    """Verify the ledger's hash chain when it carries one (opt-in on the column).

    Per-row signing proves who wrote a row; chaining proves nobody removed one — deleting a
    signed grant otherwise leaves every remaining signature valid (a silent loss of a
    holder's reach). A ledger with no ``prev_digest`` column is unchained and passes, the
    same way signing is opt-in, so a pre-chain ledger keeps working until it is re-issued.

    Args:
        rows: The ledger rows in file order.
        columns: The ledger's header columns.

    Raises:
        GrantError: A row's ``prev_digest`` does not match the running chain — a row was
            removed, reordered, or edited.
    """
    if "prev_digest" not in {c.strip() for c in columns}:
        return
    previous = ""
    for index, row in enumerate(rows):
        declared = (row.get("prev_digest") or "").strip()
        if declared != previous:
            raise GrantError(
                f"capability-grant ledger chain broken at row {index + 1}: prev_digest is "
                f"{declared!r}, expected {previous!r} — a grant row was removed, reordered, "
                f"or edited."
            )
        previous = _grant_chain_digest(row)


def _read_grant_rows(repo_root: Path) -> list[dict[str, str]]:
    """Return all grant rows from a workspace's ledger (empty list if absent).

    Verifies the ``prev_digest`` hash chain (when present) before returning, so every reader
    — generation-time widening, ``--verify-grants``, and the append path — fails closed on a
    tampered or truncated ledger rather than trusting rows around a deleted one.

    Args:
        repo_root: Workspace root containing ``references/capability-grants.log.csv``.

    Returns:
        The rows as dicts, values coerced to ``""`` for missing cells.

    Raises:
        GrantError: The ledger exists but its header is malformed, or its hash chain is broken.
    """
    log_path = repo_root / GRANT_LOG_REL
    if not log_path.exists():
        return []
    try:
        with log_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            columns = [c.strip() for c in (reader.fieldnames or [])]
            if not set(GRANT_COLUMNS).issubset(columns):
                raise GrantError(
                    "capability-grants log is malformed: expected header "
                    + ",".join(GRANT_COLUMNS)
                )
            rows = [{k: (v or "") for k, v in row.items()} for row in reader]
    except (OSError, csv.Error) as exc:
        raise GrantError(f"unable to read capability-grants log: {exc}") from exc
    _assert_grant_chain_intact(rows, columns)
    return rows


def held_grants(
    repo_root: Path, *, holder_team: str, now: datetime | None = None, key: str | None = None,
) -> list[dict[str, str]]:
    """Return the valid, in-force grants ``holder_team`` holds in ``repo_root``.

    A grant is included only if it passes :func:`validate_grant` (signature, expiry,
    use-counter) — malformed or invalid rows are skipped, never trusted. Used by
    generation-time sandbox widening to collect the extra write roots a holder has
    earned. Does NOT consume uses (widening is not a per-write event).

    Args:
        repo_root: Workspace whose ledger is read.
        holder_team: The team whose grants to collect.
        now: Reference time for expiry.
        key: Override the signing key.

    Returns:
        The valid grant rows held by ``holder_team``.
    """
    held: list[dict[str, str]] = []
    for row in _read_grant_rows(repo_root):
        if (row.get("holder_team") or "").strip() != holder_team.strip():
            continue
        try:
            # Enforce the approver roster here too (output_dir=repo_root), not only in
            # the manual --verify-grants lint: a grant naming an off-roster approver must
            # not silently widen the sandbox (adversarial defect 2). The roster lives in
            # the holder workspace being generated.
            validate_grant(row, output_dir=repo_root, now=now, key=key)
        except GrantError as exc:
            # An invalid/expired/exhausted grant is not trusted — but skipping it
            # silently would hide a real problem (a tampered or lapsed authorization),
            # so surface it rather than swallow it (CH-24).
            print(
                f"  NOTE: skipping invalid capability grant {row.get('grant_id')!r}: {exc}",
                file=sys.stderr,
            )
            continue
        held.append(row)
    return held


def granted_write_roots(
    repo_root: Path, *, holder_team: str, now: datetime | None = None, key: str | None = None,
) -> list[str]:
    """Return the target paths of the valid grants ``holder_team`` holds in ``repo_root``.

    The list of extra write roots to merge into a holder's sandbox ``allowWrite`` at
    generation time. Order-preserving and de-duplicated. Only valid (signed, unexpired,
    non-exhausted) grants contribute; invalid rows are silently skipped by
    :func:`held_grants`.

    Args:
        repo_root: The holder workspace whose ledger is read.
        holder_team: The holder team id.
        now: Reference time for expiry.
        key: Override the signing key.

    Returns:
        The de-duplicated target paths (may be empty).
    """
    roots: list[str] = []
    for row in held_grants(repo_root, holder_team=holder_team, now=now, key=key):
        # Only grants that permit `write` widen a write boundary — a read-only grant must
        # NOT hand the holder OS write access (adversarial defect 1).
        if "write" not in _permitted_ops(row):
            continue
        target = (row.get("target_path") or "").strip()
        # P2-4: re-assert the guards on the REAL enforcement path, not only at issue time.
        # `target_path` (and `issuer_root`) are signature-covered, so a row reaching here already
        # passed the issue-time checks — but repeating them on the widening path is defense in
        # depth: they fail closed (raise, halting generation) if any future code path ever
        # deposits a `write` grant that bypassed `issue_grant`, so an unsafe value can never
        # become real OS write reach by a path that skipped the guard.
        _reject_unsafe_target_path(target)
        # G-6: full `path_within(issuer_root)` containment — when the grant carries a signed
        # `issuer_root`, an absolute target outside it is rejected here too (an in-issuer-tree-
        # but-wrong-subtree absolute target the shape guard alone would permit).
        _assert_target_within_issuer_root(target, (row.get("issuer_root") or ""))
        if target and target not in roots:
            roots.append(target)
    return roots


def _reject_unsafe_target_path(target_path: str) -> None:
    """Reject a grant ``target_path`` that would widen a boundary too far (P2-4).

    A grant's target path is merged verbatim into a holder's sandbox ``allowWrite`` at
    generation time (:func:`granted_write_roots`), so a dangerous value here becomes real
    OS write reach. Reject the values that can never be a legitimate scoped grant:

    * empty/whitespace — an unbounded or accidental grant;
    * ``/`` — the filesystem root;
    * a home-rooted ``~`` / ``~/...`` path — grants write across the operator's home;
    * a relative path that escapes its anchor via ``..`` (``../`` or a normalized path
      that starts above its root) — the classic containment break.

    Called at BOTH issue time (:func:`issue_grant`) and on the generation-time widening
    path (:func:`granted_write_roots`), so the shape guard sits on the real enforcement
    path, not only at issue (P2-4).

    Absolute paths are permitted here (a grant into the issuer's tree is a documented case);
    the *full* ``path_within(issuer_root)`` containment — rejecting an absolute target that is
    well-shaped yet points outside the issuer's declared tree — is enforced separately by
    :func:`_assert_target_within_issuer_root` when the grant carries a signed ``issuer_root``
    (G-6). This function is the shape guard; that one is the anchored-containment guard.

    Args:
        target_path: The path the grant would authorize the holder to write.

    Raises:
        GrantError: The path is one of the rejected shapes above.
    """
    raw = (target_path or "").strip()
    if not raw:
        raise GrantError("grant target_path is empty; a grant must name a scoped path")
    if raw == "/":
        raise GrantError("grant target_path '/' would widen write access to the filesystem root")
    if raw == "~" or raw.startswith("~/") or raw.startswith("~\\"):
        raise GrantError(
            f"grant target_path {target_path!r} is home-rooted; refusing a grant across "
            "the operator's home directory"
        )
    if not Path(raw).is_absolute():
        normalized = os.path.normpath(raw)
        if normalized == ".." or normalized.startswith(".." + os.sep) or normalized.startswith("../"):
            raise GrantError(
                f"grant target_path {target_path!r} escapes its workspace via '..'"
            )


def _assert_target_within_issuer_root(target_path: str, issuer_root: str) -> None:
    """G-6 (P2-4 full containment): when a **signed** ``issuer_root`` is recorded, an ABSOLUTE
    ``target_path`` must resolve inside it — rejecting a well-shaped absolute path that points
    outside the issuer's declared tree (the gap :func:`_reject_unsafe_target_path` alone leaves,
    since it permits any non-``/``, non-``~``, non-``..`` absolute path). ``issuer_root`` is part
    of the signed payload, so an attacker cannot forge it (e.g. to ``/``) to defeat this check.

    An empty ``issuer_root`` is the backward-compatible case (older grants, or a spec that omits
    it): no anchor is enforced and only the shape guard applies. A relative ``target_path`` needs
    no containment check here — it is anchored to the holder workspace and already bounded by the
    shape guard's ``..``-escape rejection.

    Raises:
        GrantError: ``issuer_root`` is non-empty but not absolute, or an absolute ``target_path``
            resolves outside it.
    """
    root = (issuer_root or "").strip()
    if not root:
        return
    if not Path(root).is_absolute():
        raise GrantError(f"grant issuer_root {issuer_root!r} must be an absolute path")
    target = (target_path or "").strip()
    if Path(target).is_absolute() and not path_within(target, root):
        raise GrantError(
            f"grant target_path {target_path!r} is outside its signed issuer_root {issuer_root!r}"
        )


def issue_grant(
    repo_root: Path, *, issuer_team: str, holder_team: str, target_path: str,
    permitted_ops: str, expires_at: str, max_uses: int, approver: str, ticket_id: str,
    reason_code: str, grant_id: str, timestamp: str, issuer_root: str = "",
    key: str | None = None,
) -> dict[str, str]:
    """Mint, sign, and append a capability grant to the HOLDER's ledger.

    The ledger is the holder's ``references/capability-grants.log.csv`` (a bearer
    capability the holder holds), so the holder's own generation reads it. The approver
    is checked against ``repo_root``'s security-approver roster at issue time
    (fail-closed) so an off-roster grant cannot enter the ledger and later widen a
    boundary unchecked.

    Args:
        repo_root: The HOLDER workspace root (ledger written under it; roster checked here).
        issuer_team: The granting team's id (recorded as the authorizer).
        holder_team: The receiving team's id.
        target_path: The path in the issuer's workspace the holder may write.
        permitted_ops: ``;``-separated operations (e.g. ``"write"``).
        expires_at: ISO-8601 expiry.
        max_uses: Positive use ceiling (validated; not consumed by generation-time widening).
        approver: A principal on ``repo_root``'s security-approver roster.
        ticket_id: Audit ticket reference.
        reason_code: Short reason code.
        grant_id: Caller-supplied unique id (no clock/rng available here).
        timestamp: Caller-supplied ISO-8601 issue time.
        key: Override the signing key.

    Returns:
        The signed grant record as written.

    Raises:
        GrantError: ``max_uses`` is not positive, ``target_path`` is unsafe (empty, ``/``,
            home-rooted, or ``..``-escaping), ``expires_at`` is not valid ISO-8601, the
            approver is off-roster (or no roster is present), or the signing key is unset.
    """
    if max_uses <= 0:
        raise GrantError("max_uses must be a positive integer")
    _reject_unsafe_target_path(target_path)
    _assert_target_within_issuer_root(target_path, issuer_root)  # G-6: containment at issue time
    try:
        is_expired(expires_at)  # parse-only: reject a malformed deadline at issue (P2-8)
    except (ValueError, KeyError) as exc:
        raise GrantError(
            f"expires_at is not a valid ISO-8601 timestamp ({expires_at!r}): {exc}"
        ) from exc
    _assert_approver_on_roster(approver, repo_root)
    if max_uses != 1:
        # max_uses is validated at every generation but NOT decremented per write — the
        # generation-time widening path re-reads the ledger and does not consume a use
        # (expiry is the only active bound). An operator who sets max_uses=N expecting N
        # writes gets a grant that behaves as reusable-until-expiry. Surface that confused
        # affordance loudly rather than let it mislead (P2-6). Prefer a short expires_at
        # for a tight window.
        print(
            f"  NOTE: grant {grant_id!r} sets max_uses={max_uses}; the use counter is "
            "validated but not consumed by generation-time widening (expiry is the active "
            "bound). Use a short expires_at to limit the window.",
            file=sys.stderr,
        )
    # Chain the new row to the ledger's current tail so a later deletion is detectable
    # (also verifies the existing chain is intact — fail closed on a tampered ledger).
    existing = _read_grant_rows(repo_root)
    prev_digest = _grant_chain_digest(existing[-1]) if existing else ""
    record: dict[str, str] = {
        "timestamp": timestamp, "grant_id": grant_id, "issuer_team": issuer_team,
        "holder_team": holder_team, "target_path": target_path,
        "permitted_ops": permitted_ops, "expires_at": expires_at,
        "max_uses": str(max_uses), "uses": "0", "approver": approver,
        "ticket_id": ticket_id, "reason_code": reason_code,
        "issuer_root": (issuer_root or "").strip(),
        "prev_digest": prev_digest, "signature": "",
    }
    record["signature"] = sign_grant(record, key=key)
    _append_grant_row(repo_root, record)
    return record


def _append_grant_row(repo_root: Path, record: dict[str, str]) -> None:
    """Append a grant row to the ledger, creating it with a header if absent."""
    log_path = repo_root / GRANT_LOG_REL
    existing = _read_grant_rows(repo_root) if log_path.exists() else []
    log_path.parent.mkdir(parents=True, exist_ok=True)
    rows = existing + [record]
    if not log_path.exists():
        header = ",".join(GRANT_COLUMNS) + "\n"
        _atomic_write_text(log_path, header)
    atomic_rewrite_csv_rows(log_path, rows, list(GRANT_COLUMNS))


def verify_grants(output_dir: Path, *, now: datetime | None = None, key: str | None = None) -> list[str]:
    """Validate every grant row read-only; return a list of human-readable problems.

    Mirrors ``--verify-waivers``: consumes nothing, reports every invalid row.

    Args:
        output_dir: Workspace root.
        now: Reference time for expiry.
        key: Override the signing key.

    Returns:
        A list of problem strings (empty when all rows are valid).
    """
    problems: list[str] = []
    try:
        rows = _read_grant_rows(output_dir)
    except GrantError as exc:
        # A broken hash chain (or malformed header) is a whole-ledger problem — report it
        # rather than crash the read-only audit.
        return [str(exc)]
    for row in rows:
        try:
            validate_grant(row, output_dir=output_dir, now=now, key=key)
        except GrantError as exc:
            problems.append(str(exc))
    return problems


__all__ = [
    "GRANT_COLUMNS",
    "GRANT_KEY_ENV",
    "GRANT_LOG_REL",
    "GrantError",
    "grant_covers",
    "granted_write_roots",
    "held_grants",
    "issue_grant",
    "sign_grant",
    "validate_grant",
    "verify_grant_signature",
    "verify_grants",
]
