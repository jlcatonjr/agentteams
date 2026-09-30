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

Trust model (PR-E, 2026-09-30 — supersedes the 2026-08-21 symmetric-only decision).
**A grant that widens ``allowWrite`` (``permitted_ops`` contains ``write``) must be
Ed25519-signed** by the operator (``--sign-grant``; private key read from the file named by
``AGENTTEAMS_DECISION_ED25519_KEYFILE``, kept in the read-denied ``~/.config/agentteams/keys``),
verify against the HOLDER TEAM dir's write-protected store
``<team dir>/references/authorized-verify-keys/<key_id>.pub.pem``, and name an approver on the
TEAM-dir roster ``<team dir>/references/security-approvers.txt``. The shared
``AGENTTEAMS_GRANT_SIGNING_KEY`` is inherited by a Claude-sandboxed process, so an HMAC-signed
``write`` grant is REFUSED everywhere (issue, ``--verify-grants``, widening) with a migration
message; there is no transitional flag. HMAC remains only for non-widening grants (e.g.
``read``). The Ed25519 payload starts with :data:`GRANT_PURPOSE_TAG` (domain separation), so a
decision signature can never be replayed as a grant, and ``decision_log`` refuses a payload
carrying the tag, so a grant signature is never accepted as a decision.

**Which team verifies (multi-framework projects):** the team whose ``--output`` the update
targets — that run's ``output_dir`` is the ``team_dir`` passed to :func:`held_grants`; the
ledger is read from that run's workspace root (``project_root``). There is **no project-root
roster fallback** (E3): a project-root ``references/security-approvers.txt`` is exactly what an
in-sandbox agent can plant, so it is ignored for grants, with a warning naming the remedy.

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
    SIG_SCHEME_ED25519,
    SIG_SCHEME_HMAC,
    canonical_payload,
    ed25519_sign,
    hmac_sign,
    hmac_verify,
    is_expired,
    path_within,
    verify_by_scheme,
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

#: Optional trailing ledger columns (PR-E). A pre-PR-E ledger header lacks them: the reader
#: requires only :data:`GRANT_COLUMNS`, and the append path rewrites the header with these added.
GRANT_SCHEME_COLUMNS: tuple[str, ...] = ("sig_scheme", "key_id")

#: The column set written on every append.
GRANT_LEDGER_COLUMNS: tuple[str, ...] = (*GRANT_COLUMNS, *GRANT_SCHEME_COLUMNS)

#: Domain-separation tag: the FIRST value of every Ed25519 grant payload. A decision row's
#: payload starts with its ``date`` value, and ``decision_log`` refuses (to sign or to accept) any
#: payload that begins with this tag, so an operator signature over one kind never verifies as
#: the other.
GRANT_PURPOSE_TAG = "agentteams-grant-v1"

#: The approver roster, relative to the holder TEAM dir — never the project root (E3).
GRANT_ROSTER_REL = "references/security-approvers.txt"

#: The operation that widens the holder's sandbox ``allowWrite``.
_WIDENING_OP = "write"

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

    ``sig_scheme`` / ``key_id`` (PR-E) follow as LABELLED tokens (``sig_scheme=ed25519``), each
    only when non-empty, so a pre-PR-E row's payload is byte-identical while a relabelled scheme
    or key breaks both the signature and the hash chain.
    """
    values = [record.get(f, "") for f in _GRANT_SIGNED_FIELDS]
    issuer_root = (record.get("issuer_root") or "").strip()
    if issuer_root:
        values.append(issuer_root)
    for axis in GRANT_SCHEME_COLUMNS:
        value = (record.get(axis) or "").strip()
        if value:
            values.append(f"{axis}={value}")
    return values


def _ed25519_signed_values(record: dict[str, str]) -> list[str]:
    """The Ed25519 payload: :data:`GRANT_PURPOSE_TAG`, then :func:`_signed_values`."""
    return [GRANT_PURPOSE_TAG, *_signed_values(record)]


def payload_claims_grant_purpose(values: list[str]) -> bool:
    """True iff the canonical payload of ``values`` begins with the grant purpose tag.

    The decision minter and verifier call this to refuse a decision payload that could double as
    a grant payload. ``canonical_payload`` does not escape ``|``, so the test is on the joined
    bytes, not on the first value alone.

    Args:
        values: Ordered signed field values of some other ledger row.

    Returns:
        Whether that payload lies in the grant signing domain.
    """
    payload = canonical_payload(values)
    return payload == GRANT_PURPOSE_TAG or payload.startswith(GRANT_PURPOSE_TAG + "|")


def _grant_scheme(record: dict[str, str]) -> str:
    """Return the row's declared (signed) scheme; ``hmac`` when empty."""
    return (record.get("sig_scheme") or "").strip().lower() or SIG_SCHEME_HMAC


def _hmac_write_refusal(record: dict[str, str]) -> GrantError:
    """The migration refusal for a non-Ed25519 grant that would widen ``allowWrite``."""
    return GrantError(
        f"grant {record.get('grant_id')!r} permits 'write' but is {_grant_scheme(record)}-signed: "
        "a grant that widens the sandbox allowWrite must be Ed25519-signed by the operator (the "
        "shared AGENTTEAMS_GRANT_SIGNING_KEY is inherited by sandboxed agents). REFUSED. Migrate: "
        "re-issue it with `agentteams --sign-grant SPEC.json --framework <fw> --output <holder "
        "team dir>` (operator key via AGENTTEAMS_DECISION_ED25519_KEYFILE; public key at <team "
        "dir>/references/authorized-verify-keys/<key_id>.pub.pem)."
    )


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


def verify_grant_signature(
    record: dict[str, str], *, key: str | None = None, team_dir: Path | None = None,
) -> bool:
    """Return True iff the record's ``signature`` is valid under its DECLARED scheme.

    Validity only. Whether the scheme is *sufficient* (a ``write`` grant needs Ed25519) is
    decided by :func:`validate_grant`.

    Args:
        record: The grant row.
        key: Override the HMAC signing key (defaults to the env key; hmac rows only).
        team_dir: Holder TEAM dir whose ``authorized-verify-keys`` store verifies an ed25519 row.

    Returns:
        Whether the signature is valid.

    Raises:
        GrantError: An ed25519 row with no ``team_dir``, a missing/invalid verify key, an absent
            Ed25519 backend, an unknown scheme, or (hmac) an unset key. All fail closed.
    """
    scheme = _grant_scheme(record)
    if scheme == SIG_SCHEME_ED25519:
        if team_dir is None:
            raise GrantError(
                f"grant {record.get('grant_id')!r} is ed25519-signed but no holder team dir was "
                "given to verify it against (fail-closed)"
            )
        from agentteams.cli.decision_log import _load_verify_key

        try:
            public_pem = _load_verify_key(team_dir, (record.get("key_id") or "").strip())
            return verify_by_scheme(
                SIG_SCHEME_ED25519, _ed25519_signed_values(record),
                (record.get("signature") or "").strip(), public_pem=public_pem,
            )
        except (RuntimeError, ValueError) as exc:
            raise GrantError(
                f"grant {record.get('grant_id')!r} cannot be Ed25519-verified: {exc}"
            ) from exc
    if scheme != SIG_SCHEME_HMAC:
        raise GrantError(f"grant {record.get('grant_id')!r} declares unknown sig_scheme {scheme!r}")
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
    record: dict[str, str], *, team_dir: Path | None = None, now: datetime | None = None,
    key: str | None = None,
) -> None:
    """Validate a grant row's signature and lifecycle; raise on any failure (fail-closed).

    Checks, in order: required fields present; scheme sufficient (a ``write`` grant must be
    ed25519, and an HMAC one is refused with a migration message); signature valid; not
    expired; use-counter not exhausted; and (when ``team_dir`` is given) the approver is on the
    TEAM-dir roster.

    Args:
        record: The grant row.
        team_dir: Holder TEAM dir (roster + verify-key store). The roster check is skipped when
            None, and an ed25519 row cannot verify without it.
        now: Reference time for expiry (defaults to now, UTC).
        key: Override the HMAC signing key.

    Raises:
        GrantError: Any field/scheme/signature/expiry/use-counter/roster check fails.
    """
    for field in _GRANT_SIGNATURE_FIELDS:
        if not (record.get(field) or "").strip():
            raise GrantError(f"grant is missing required field {field!r}")
    if _WIDENING_OP in _permitted_ops(record) and _grant_scheme(record) != SIG_SCHEME_ED25519:
        raise _hmac_write_refusal(record)
    if not verify_grant_signature(record, key=key, team_dir=team_dir):
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
    if team_dir is not None:
        _assert_approver_on_roster(record.get("approver", ""), team_dir)


def _roster_names_an_approver(output_dir: Path) -> bool:
    """True when the security-approver roster exists and names at least one approver.

    Mirrors ``decision_log._approved_decision_authors``' parse: a line is an approver only
    when it is non-blank and not a ``#`` comment. An absent file, an unreadable file, or a
    file of only blanks/comments all return ``False`` — the cases where
    ``_approved_decision_authors`` would fall back to the built-in default.

    Args:
        output_dir: The holder TEAM dir (roster read from ``references/security-approvers.txt``).

    Returns:
        Whether a named approver is present.
    """
    roster_path = output_dir / GRANT_ROSTER_REL
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
        output_dir: The holder TEAM dir (E3: never the project root; no fallback).

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


def warn_if_only_root_roster(repo_root: Path, team_dir: Path) -> str | None:
    """E3: warn when grants would need a roster that exists only at the project root.

    Grants read the approver roster from the TEAM dir only. A project-root
    ``references/security-approvers.txt`` is ignored (an in-sandbox agent can plant it); when the
    team-dir roster names no approver but a project-root one does, print the remedy to stderr.

    Args:
        repo_root: The holder workspace root (where the ledger lives).
        team_dir: The holder TEAM dir whose roster grants use.

    Returns:
        The warning text when one was printed, else None.
    """
    try:
        same = repo_root.resolve() == team_dir.resolve()
    except OSError:
        same = False
    if same or _roster_names_an_approver(team_dir) or not _roster_names_an_approver(repo_root):
        return None
    message = (
        f"WARNING: {repo_root / GRANT_ROSTER_REL} is IGNORED for capability grants (a project-root "
        f"roster is plantable from inside the sandbox; there is no fallback). Grants read "
        f"{team_dir / GRANT_ROSTER_REL}, which names no approver, so every grant is refused. "
        f"Remedy: move the approver list into {team_dir / GRANT_ROSTER_REL}."
    )
    print(f"  {message}", file=sys.stderr)
    return message


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
    repo_root: Path, *, holder_team: str, team_dir: Path, now: datetime | None = None,
    key: str | None = None,
) -> list[dict[str, str]]:
    """Return the valid, in-force grants ``holder_team`` holds in ``repo_root``.

    A grant is included only if it passes :func:`validate_grant` (scheme sufficiency,
    signature, expiry, use-counter, TEAM-dir roster). Malformed or invalid rows, including
    every HMAC-signed ``write`` grant, are skipped with a NOTE, never trusted. Used by
    generation-time sandbox widening to collect the extra write roots a holder has
    earned. Does NOT consume uses (widening is not a per-write event).

    Args:
        repo_root: Workspace whose ledger is read.
        holder_team: The team whose grants to collect.
        team_dir: The holder TEAM dir (the run's ``--output``): roster + verify-key store.
        now: Reference time for expiry.
        key: Override the HMAC signing key.

    Returns:
        The valid grant rows held by ``holder_team``.
    """
    held: list[dict[str, str]] = []
    rows = _read_grant_rows(repo_root)
    if any((r.get("holder_team") or "").strip() == holder_team.strip() for r in rows):
        warn_if_only_root_roster(repo_root, team_dir)
    for row in rows:
        if (row.get("holder_team") or "").strip() != holder_team.strip():
            continue
        try:
            # Enforce the approver roster here too, not only in the manual --verify-grants
            # lint: a grant naming an off-roster approver must not silently widen the sandbox
            # (adversarial defect 2). E3: the roster is the TEAM dir's, never the project root's.
            validate_grant(row, team_dir=team_dir, now=now, key=key)
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
    repo_root: Path, *, holder_team: str, team_dir: Path, now: datetime | None = None,
    key: str | None = None,
) -> list[str]:
    """Return the target paths of the valid grants ``holder_team`` holds in ``repo_root``.

    The list of extra write roots to merge into a holder's sandbox ``allowWrite`` at
    generation time. Order-preserving and de-duplicated. Only valid grants contribute:
    Ed25519-signed by a key in ``team_dir``'s verify-key store, approver on ``team_dir``'s
    roster, unexpired, non-exhausted. Invalid rows are skipped with a NOTE by
    :func:`held_grants`.

    Args:
        repo_root: The holder workspace whose ledger is read.
        holder_team: The holder team id.
        team_dir: The holder TEAM dir (the run's ``--output``).
        now: Reference time for expiry.
        key: Override the HMAC signing key.

    Returns:
        The de-duplicated target paths (may be empty).
    """
    roots: list[str] = []
    for row in held_grants(repo_root, holder_team=holder_team, team_dir=team_dir, now=now, key=key):
        # Only grants that permit `write` widen a write boundary — a read-only grant must
        # NOT hand the holder OS write access (adversarial defect 1).
        if _WIDENING_OP not in _permitted_ops(row):
            continue
        # PR-E: re-assert scheme sufficiency on the real enforcement path (defense in depth —
        # held_grants already refused it via validate_grant).
        if _grant_scheme(row) != SIG_SCHEME_ED25519:
            raise _hmac_write_refusal(row)
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


def _prepare_grant_record(
    repo_root: Path, *, team_dir: Path, issuer_team: str, holder_team: str, target_path: str,
    permitted_ops: str, expires_at: str, max_uses: int, approver: str, ticket_id: str,
    reason_code: str, grant_id: str, timestamp: str, issuer_root: str = "",
) -> dict[str, str]:
    """Validate a grant spec and return its unsigned, chained record (shared by both minters).

    Raises:
        GrantError: ``max_uses`` is not positive, ``target_path`` is unsafe (empty, ``/``,
            home-rooted, ``..``-escaping, or outside a signed ``issuer_root``), ``expires_at`` is
            not ISO-8601, the approver is not on the TEAM-dir roster, or the ledger is tampered.
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
    warn_if_only_root_roster(repo_root, team_dir)
    _assert_approver_on_roster(approver, team_dir)
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
    return {
        "timestamp": timestamp, "grant_id": grant_id, "issuer_team": issuer_team,
        "holder_team": holder_team, "target_path": target_path,
        "permitted_ops": permitted_ops, "expires_at": expires_at,
        "max_uses": str(max_uses), "uses": "0", "approver": approver,
        "ticket_id": ticket_id, "reason_code": reason_code,
        "issuer_root": (issuer_root or "").strip(),
        "prev_digest": prev_digest, "signature": "", "sig_scheme": "", "key_id": "",
    }


def issue_grant(
    repo_root: Path, *, team_dir: Path, issuer_team: str, holder_team: str, target_path: str,
    permitted_ops: str, expires_at: str, max_uses: int, approver: str, ticket_id: str,
    reason_code: str, grant_id: str, timestamp: str, issuer_root: str = "",
    key: str | None = None,
) -> dict[str, str]:
    """Mint, HMAC-sign, and append a NON-WIDENING capability grant to the HOLDER's ledger.

    The ledger is the holder's ``references/capability-grants.log.csv`` (a bearer
    capability the holder holds), so the holder's own generation reads it. The approver
    is checked against ``team_dir``'s roster at issue time (fail-closed). A grant whose
    ``permitted_ops`` include ``write`` is REFUSED here (PR-E): it must be Ed25519-signed with
    :func:`sign_ed25519_grant` (``--sign-grant``).

    Args:
        repo_root: The HOLDER workspace root (ledger written under it).
        team_dir: The HOLDER team dir (approver roster).
        issuer_team: The granting team's id (recorded as the authorizer).
        holder_team: The receiving team's id.
        target_path: The path in the issuer's workspace the grant covers.
        permitted_ops: ``;``-separated operations (never ``write`` here).
        expires_at: ISO-8601 expiry.
        max_uses: Positive use ceiling (validated; not consumed by generation-time widening).
        approver: A principal on ``team_dir``'s security-approver roster.
        ticket_id: Audit ticket reference.
        reason_code: Short reason code.
        grant_id: Caller-supplied unique id (no clock/rng available here).
        timestamp: Caller-supplied ISO-8601 issue time.
        issuer_root: Optional signed containment anchor (G-6).
        key: Override the HMAC signing key.

    Returns:
        The signed grant record as written.

    Raises:
        GrantError: The grant permits ``write`` (migration message), any spec check in
            :func:`_prepare_grant_record` fails, or the signing key is unset.
    """
    if _WIDENING_OP in _permitted_ops({"permitted_ops": permitted_ops}):
        raise _hmac_write_refusal({"grant_id": grant_id, "sig_scheme": SIG_SCHEME_HMAC})
    record = _prepare_grant_record(
        repo_root, team_dir=team_dir, issuer_team=issuer_team, holder_team=holder_team,
        target_path=target_path, permitted_ops=permitted_ops, expires_at=expires_at,
        max_uses=max_uses, approver=approver, ticket_id=ticket_id, reason_code=reason_code,
        grant_id=grant_id, timestamp=timestamp, issuer_root=issuer_root,
    )
    record["signature"] = sign_grant(record, key=key)
    _append_grant_row(repo_root, record)
    return record


def sign_ed25519_grant(
    repo_root: Path, *, team_dir: Path, private_pem: str, key_id: str, issuer_team: str,
    holder_team: str, target_path: str, permitted_ops: str, expires_at: str, max_uses: int,
    approver: str, ticket_id: str, reason_code: str, grant_id: str, timestamp: str,
    issuer_root: str = "", password: bytes | None = None,
) -> dict[str, str]:
    """Mint an operator Ed25519-signed grant and append it to the HOLDER's ledger (``--sign-grant``).

    The only minter of a grant that can widen ``allowWrite``. The payload is
    ``[GRANT_PURPOSE_TAG, …signed fields…, sig_scheme=ed25519, key_id=<id>]``. The row is
    **verified before it is appended** against ``team_dir``: the team's verify-key store must
    already hold ``<key_id>.pub.pem`` matching ``private_pem``, and the approver must be on the
    team-dir roster. A row whose signature or approver could not later verify never enters the
    ledger.

    Args:
        repo_root: The HOLDER workspace root (ledger written under it).
        team_dir: The HOLDER team dir (the ``--output`` the update targets): roster + key store.
        private_pem: The operator's Ed25519 private key PEM.
        key_id: Names ``<team_dir>/references/authorized-verify-keys/<key_id>.pub.pem``.
        issuer_team: The granting team's id.
        holder_team: The receiving team's id.
        target_path: The path the holder may reach.
        permitted_ops: ``;``-separated operations (e.g. ``"write"``).
        expires_at: ISO-8601 expiry.
        max_uses: Positive use ceiling.
        approver: A principal on ``team_dir``'s roster.
        ticket_id: Audit ticket reference.
        reason_code: Short reason code.
        grant_id: Caller-supplied unique id.
        timestamp: Caller-supplied ISO-8601 issue time.
        issuer_root: Optional signed containment anchor (G-6).
        password: Optional passphrase for an encrypted PEM.

    Returns:
        The signed grant record as written.

    Raises:
        GrantError: A spec check fails, the key cannot sign (backend absent / not Ed25519), or
            verify-before-append fails (no matching public key in the store, off-roster approver).
    """
    record = _prepare_grant_record(
        repo_root, team_dir=team_dir, issuer_team=issuer_team, holder_team=holder_team,
        target_path=target_path, permitted_ops=permitted_ops, expires_at=expires_at,
        max_uses=max_uses, approver=approver, ticket_id=ticket_id, reason_code=reason_code,
        grant_id=grant_id, timestamp=timestamp, issuer_root=issuer_root,
    )
    record["sig_scheme"] = SIG_SCHEME_ED25519
    record["key_id"] = (key_id or "").strip()
    try:
        record["signature"] = ed25519_sign(
            private_pem, _ed25519_signed_values(record), password=password
        )
    except RuntimeError as exc:
        raise GrantError(f"cannot Ed25519-sign grant {grant_id!r}: {exc}") from exc
    # Verify-before-append (the roster was already asserted in _prepare_grant_record).
    try:
        verified = verify_grant_signature(record, team_dir=team_dir)
    except GrantError as exc:
        verified, reason = False, str(exc)
    else:
        reason = "the signature does not match the public key stored under that key_id"
    if not verified:
        raise GrantError(
            f"refusing to append grant {grant_id!r}: it does not verify against the holder team "
            f"dir {team_dir} ({reason}). Install the matching public key as "
            f"references/authorized-verify-keys/<key_id>.pub.pem there first."
        )
    _append_grant_row(repo_root, record)
    return record


def _append_grant_row(repo_root: Path, record: dict[str, str]) -> None:
    """Append a grant row to the ledger, creating it with a header if absent.

    Always writes :data:`GRANT_LEDGER_COLUMNS`, so a pre-PR-E ledger gains the empty
    ``sig_scheme``/``key_id`` columns on its next append (their empty values are omitted from
    every payload, so existing signatures and the hash chain are unchanged).
    """
    log_path = repo_root / GRANT_LOG_REL
    existing = _read_grant_rows(repo_root) if log_path.exists() else []
    log_path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(GRANT_LEDGER_COLUMNS)
    rows = [{c: (r.get(c) or "") for c in columns} for r in (*existing, record)]
    if not log_path.exists():
        _atomic_write_text(log_path, ",".join(columns) + "\n")
    atomic_rewrite_csv_rows(log_path, rows, columns)


def verify_grants(
    output_dir: Path, *, team_dir: Path, now: datetime | None = None, key: str | None = None,
) -> list[str]:
    """Validate every grant row read-only; return a list of human-readable problems.

    Mirrors ``--verify-waivers``: consumes nothing, reports every invalid row, including each
    HMAC-signed ``write`` grant (with the ``--sign-grant`` migration message).

    Args:
        output_dir: Workspace root holding the ledger.
        team_dir: The holder TEAM dir (roster + verify-key store).
        now: Reference time for expiry.
        key: Override the HMAC signing key.

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
    if rows:
        warn_if_only_root_roster(output_dir, team_dir)
    for row in rows:
        try:
            validate_grant(row, team_dir=team_dir, now=now, key=key)
        except GrantError as exc:
            problems.append(str(exc))
    return problems


__all__ = [
    "GRANT_COLUMNS",
    "GRANT_KEY_ENV",
    "GRANT_LEDGER_COLUMNS",
    "GRANT_LOG_REL",
    "GRANT_PURPOSE_TAG",
    "GRANT_ROSTER_REL",
    "GrantError",
    "grant_covers",
    "granted_write_roots",
    "held_grants",
    "issue_grant",
    "payload_claims_grant_purpose",
    "sign_ed25519_grant",
    "sign_grant",
    "validate_grant",
    "verify_grant_signature",
    "verify_grants",
    "warn_if_only_root_roster",
]
