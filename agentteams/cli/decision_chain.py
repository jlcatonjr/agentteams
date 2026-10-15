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


@dataclass(frozen=True)
class ChainReport:
    """What :func:`verify_chain` found in an intact log."""

    chained: bool
    """The log carries a ``prev_digest`` column."""
    legacy_prefix: int = 0
    """Rows after the first whose link is empty (accepted as the unchained legacy prefix)."""
    halts_in_prefix: list[int] = field(default_factory=list)
    """1-based numbers of HALT rows inside the legacy prefix (their deletion would be undetectable)."""


def row_chain_digest(row: dict[str, str]) -> str:
    """Return the digest the row after *row* must carry as its ``prev_digest``."""
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
    if "prev_digest" not in {c.strip() for c in fieldnames}:
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
    halts = [i + 1 for i in range(prefix_rows + 1) if prefix_rows and i < len(rows)
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


__all__ = ["ChainReport", "chain_tip", "row_chain_digest", "verify_chain"]
