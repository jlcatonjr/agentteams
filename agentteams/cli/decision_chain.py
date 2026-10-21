"""decision_chain.py — the security-decision log's hash chain, in one place.

Signing a row proves who wrote it; chaining proves nobody removed one. Each row's ``prev_digest`` is
the sha256 of the previous row's signed payload (``decision_log._decision_signature_payload``), so
deleting, reordering or editing a row breaks the next row's link. ``prev_digest`` is itself a signed
field, so a signed row's link cannot be rewritten without breaking its signature.

**Legacy prefix.** Before 1.0, ``--sign-decision`` created chained logs but never filled
``prev_digest``, so every signed row carried an empty link and the second one broke the chain, which
blocks every gated action. When signing is active, a *leading* run of rows with an empty
``prev_digest`` is accepted as an unchained prefix. The running digest is kept through the prefix:
the first non-empty ``prev_digest`` must equal the digest of the row just before it, and from there
on every row must chain (an empty link after that point is a break). Deletions inside the prefix,
HALT rows included, are undetectable, exactly as in a log with no ``prev_digest`` column; the prefix
length is reported so the operator can see it. The chain is unkeyed, so rows cut from the tail are
not detected either.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ChainReport:
    """What :func:`verify_chain` found in an intact log."""

    chained: bool
    """The log carries a ``prev_digest`` column."""
    legacy_prefix: int = 0
    """Rows after the first whose link is empty (accepted as the unchained legacy prefix)."""
    halts_in_prefix: list[int] = field(default_factory=list)
    """1-based numbers of HALT rows inside the legacy prefix (their deletion would be undetectable)."""


def is_chained(fieldnames: list[str]) -> bool:
    """True when a decisions-log header carries the ``prev_digest`` column (the log is hash-chained).

    Args:
        fieldnames: The log's header.

    Returns:
        Whether rows of this log must chain.
    """
    return "prev_digest" in {c.strip() for c in fieldnames}


def row_chain_digest(row: dict[str, str]) -> str:
    """Return the digest the row after *row* must carry as its ``prev_digest``.

    Args:
        row: A decisions-log row.

    Returns:
        The sha256 hex digest of the row's signed payload.
    """
    # Local import: decision_log imports this module, so a top-level import would be a cycle.
    from agentteams.cli.decision_log import _decision_signature_payload

    return hashlib.sha256(_decision_signature_payload(row).encode("utf-8")).hexdigest()


def verify_chain(rows: list[dict[str, str]], fieldnames: list[str], *, signing_active: bool) -> ChainReport:
    """Verify the log's hash chain, accepting a leading legacy prefix when signing is active.

    Args:
        rows: The log's rows, in file order.
        fieldnames: The log's header.
        signing_active: Whether the log is signed (``decision_log._decision_signing_active``). Without
            signing a link is not authenticated, so no prefix is accepted and every link must hold.

    Returns:
        A :class:`ChainReport`; ``chained`` is False for a log with no ``prev_digest`` column.

    Raises:
        RuntimeError: A link is broken: a row was removed, reordered, or edited.
    """
    if not is_chained(fieldnames):
        return ChainReport(chained=False)
    previous = ""
    in_prefix = True
    prefix_rows = 0
    for index, row in enumerate(rows):
        declared = (row.get("prev_digest") or "").strip()
        if index > 0 and in_prefix and signing_active and declared == "":
            prefix_rows += 1
        elif declared != previous:
            raise RuntimeError(
                f"security decision log chain broken at row {index + 1}: prev_digest is "
                f"{declared!r}, expected {previous!r}. A row has been removed, reordered, or edited."
            )
        elif index > 0:
            in_prefix = False
        previous = row_chain_digest(row)
    # Rows 1..prefix_rows+1 form the unchained run (row 1's empty link is legitimate on its own).
    halts = [i + 1 for i in range(prefix_rows + 1) if prefix_rows
             and (rows[i].get("verdict") or rows[i].get("status") or "").strip().upper() == "HALT"]
    return ChainReport(chained=True, legacy_prefix=prefix_rows, halts_in_prefix=halts)


def chain_tip(rows: list[dict[str, str]], fieldnames: list[str], *, signing_active: bool) -> str:
    """Return the ``prev_digest`` the next appended row must carry, after verifying the chain.

    Never empty once the log has a row, so no writer can append an unchained row after the first.

    Args:
        rows: The log's rows, in file order.
        fieldnames: The log's header.
        signing_active: As for :func:`verify_chain`.

    Returns:
        The digest of the last row, or ``""`` for an empty log.

    Raises:
        RuntimeError: The existing chain is broken (refuse rather than extend it).
    """
    verify_chain(rows, fieldnames, signing_active=signing_active)
    return row_chain_digest(rows[-1]) if rows else ""


def describe_log_chain(team_dir: Path) -> tuple[list[str], bool]:
    """Report the decisions log's chain for ``--verify-integrity``.

    Args:
        team_dir: The team root (the log is ``references/security-decisions.log.csv``).

    Returns:
        ``(lines, broken)``: report lines, and True when the chain is broken (an integrity failure).
        A legacy prefix, and any HALT inside it, is reported but is not a failure.
    """
    # Local import: decision_log imports this module, so a top-level import would be a cycle.
    from agentteams.cli.decision_log import _decision_signing_active, _read_decision_rows

    try:
        read = _read_decision_rows(team_dir)
        if read is None:
            return [], False
        report = verify_chain(read[0], read[1], signing_active=_decision_signing_active(read[1]))
    except RuntimeError as exc:
        return [f"Decision log chain: BROKEN — {exc}"], True
    if not report.chained:
        return ["Decision log chain: none (no prev_digest column)"], False
    lines = ["Decision log chain: OK"]
    if report.legacy_prefix:
        lines.append(f"  legacy prefix: {report.legacy_prefix} row(s) after row 1 carry no link (pre-1.0 "
                     f"signer); a deletion within rows 1-{report.legacy_prefix + 1} is undetectable")
    if report.halts_in_prefix:
        lines.append(f"  HALT row(s) inside the prefix: {report.halts_in_prefix}; their deletion would be "
                     "undetectable, so confirm they are still present as recorded")
    return lines, False


def _describe_unauthenticated_rows(output_dir: Path) -> list[str]:
    """Advisory lines for ``--verify-integrity``: every authorizing or retraction row that fails authentication.

    The HALT scan skips a retraction that does not authenticate, and a later valid retraction then
    clears the HALT, so a forged row is never surfaced (VERIFY-INTEGRITY-NONAUTH-ROWS). This runs the
    gate's own check (``decision_log._assert_authorizing_row_is_authentic``, plus a retraction's evidence
    requirement) on every PASS / CONDITIONAL PASS / HALT-RETRACTED row and names each that fails.
    A row is checked against its first ``scope`` entry, or its ``action_reviewed`` when unscoped, so
    the question is whether it authenticates, not what it may clear. Rows that need the HMAC key when
    none is set are listed as not checked, never as forged. Advisory: it never changes the exit code.

    Args:
        output_dir: The team root (the log is ``references/security-decisions.log.csv``).

    Returns:
        Report lines; empty when the log is absent, unreadable (reported elsewhere) or all authentic.
    """
    # Local import: decision_log imports this module, so a top-level import would be a cycle.
    from agentteams.cli.decision_log import (
        _HALT_RETRACTION_VERDICT,
        _NO_SIGNING_KEY,
        _assert_authorizing_row_is_authentic,
        _decision_signing_active,
        _read_decision_rows,
    )

    try:
        read = _read_decision_rows(output_dir)
    except RuntimeError:
        return []  # describe_log_chain and check_logs report an unreadable log
    if read is None:
        return []
    rows, fieldnames = read
    signing_active = _decision_signing_active(fieldnames)
    failed: list[str] = []
    unchecked: list[int] = []
    for index, row in enumerate(rows):
        verdict = (row.get("verdict") or row.get("status") or "").strip().upper()
        if verdict not in {"PASS", "CONDITIONAL PASS", _HALT_RETRACTION_VERDICT}:
            continue
        reviewed = (row.get("action_reviewed") or row.get("decision") or "").strip()
        scoped = [part.strip() for part in (row.get("scope") or "").split(";") if part.strip()]
        try:
            _assert_authorizing_row_is_authentic(row, output_dir=output_dir, signing_active=signing_active,
                                                 action=scoped[0] if scoped else reviewed)
            evidence = (row.get("evidence") or row.get("conditions") or "").strip()
            if verdict == _HALT_RETRACTION_VERDICT and not evidence:
                raise RuntimeError("a retraction carries no evidence")
        except RuntimeError as exc:
            if str(exc) == _NO_SIGNING_KEY:
                unchecked.append(index + 1)
            else:
                failed.append(f"  row {index + 1} ({verdict} {reviewed!r}): {exc}")
    lines: list[str] = []
    if failed:
        lines.append(f"Decision log authentication: {len(failed)} authorizing or retraction row(s) do not "
                     "authenticate and clear nothing (advisory; the HALT scan skips such a row silently, so "
                     "review each as a possible forgery):")
        lines.extend(failed)
    if unchecked:
        lines.append(f"Decision log authentication: not checked for row(s) {unchecked}; "
                     "AGENTTEAMS_DECISION_SIGNING_KEY is not set")
    return lines


__all__ = ["ChainReport", "chain_tip", "describe_log_chain", "is_chained", "row_chain_digest", "verify_chain"]
