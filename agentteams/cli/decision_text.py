"""decision_text.py — bind a signed decision row's free-text ``conditions`` and ``evidence``.

The decision signature covers the fixed fields and the optional axes (``decision_log``), but the
free-text ``conditions`` and ``evidence`` columns sit outside it. A signed row's stated limits ("only
these repos, expires 2027-01-16") could therefore be rewritten by a keyless writer while the HMAC or
Ed25519 signature, and the hash chain, still verified. That matters most on a Rule 15
constraint-relaxing row, whose conditions are often the only thing narrowing it.

The fix follows the optional-axis pattern. ``text_digest`` is the last entry of
``decision_log._OPTIONAL_SIGNED_AXES``, so it is signed (and chained) only when non-empty and every
existing signature verifies byte-for-byte. Its value is ``sha256:`` + the hex digest of
``conditions.strip() + "\\x1f" + evidence.strip()``. Signing the digest binds the text; verification
recomputes it, and a row whose ``text_digest`` no longer matches its text fails exactly like a bad
signature. A row with no ``text_digest`` behaves as before (its text stays unbound, and
``--verify-integrity`` counts it).

Carved into its own module because ``decision_log`` sits near the CH-07 ceiling, the way
``decision_chain`` and ``action_ids`` were. It is integrity-pinned and in the signing closure.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

#: The optional signed column that binds a row's ``conditions`` and ``evidence`` text.
TEXT_DIGEST_FIELD = "text_digest"

#: The digest's scheme prefix; a value without it never matches.
_PREFIX = "sha256:"

#: Unit separator between the two texts, so moving text from one column to the other changes the digest.
_SEPARATOR = "\x1f"


def text_digest_for(row: dict[str, str]) -> str:
    """Return the ``text_digest`` value a row's current ``conditions`` and ``evidence`` produce.

    Args:
        row: A decision row (either schema; a legacy row has no ``evidence``, read as empty).

    Returns:
        ``sha256:`` followed by the hex digest of the UTF-8 of
        ``conditions.strip() + "\\x1f" + evidence.strip()``.
    """
    conditions = (row.get("conditions") or "").strip()
    evidence = (row.get("evidence") or "").strip()
    blob = f"{conditions}{_SEPARATOR}{evidence}".encode("utf-8")
    return _PREFIX + hashlib.sha256(blob).hexdigest()


def text_is_bound(row: dict[str, str]) -> bool:
    """True when a row carries a non-empty ``text_digest`` (its text is inside the signature).

    Args:
        row: A decision row.

    Returns:
        Whether the row binds its text.
    """
    return bool((row.get(TEXT_DIGEST_FIELD) or "").strip())


def text_digest_mismatch(row: dict[str, str]) -> bool:
    """True when a row's ``text_digest`` is set but does not match its current text.

    Args:
        row: A decision row.

    Returns:
        False for a row without ``text_digest`` (nothing is claimed) or one whose digest matches.
    """
    declared = (row.get(TEXT_DIGEST_FIELD) or "").strip()
    return bool(declared) and declared != text_digest_for(row)


def assert_text_bound(row: dict[str, str], *, action: str) -> None:
    """Refuse a signed row whose ``text_digest`` no longer matches its ``conditions``/``evidence``.

    Called right after a row's signature verifies, so a mismatch fails exactly like a bad signature.
    A row without ``text_digest`` passes unchanged (the legacy behaviour).

    Args:
        row: The authorizing row, whose signature has just verified.
        action: The action being authorized (for the message).

    Raises:
        RuntimeError: The row's ``conditions`` or ``evidence`` was edited after signing.
    """
    if text_digest_mismatch(row):
        raise RuntimeError(
            f"decision authorizing '{action}' failed signature verification: its signed "
            f"{TEXT_DIGEST_FIELD} does not match its current conditions/evidence, which were edited "
            "after signing (fail-closed)"
        )


def describe_text_binding(team_dir: Path) -> list[str]:
    """Report, for ``--verify-integrity``, how many signed rows bind their conditions and evidence.

    Advisory only: the caller prints the lines and does not change its exit code.

    Args:
        team_dir: The team root (the log is ``references/security-decisions.log.csv``).

    Returns:
        Report lines; empty when the log is absent or unreadable (the chain report covers that), or
        holds no signed row.
    """
    # Local import: decision_log imports this module, so a top-level import would be a cycle.
    from agentteams.cli.decision_log import _read_decision_rows

    try:
        read = _read_decision_rows(team_dir)
    except RuntimeError:
        return []
    if read is None:
        return []
    rows, fieldnames = read
    signed = [(i + 1, r) for i, r in enumerate(rows) if (r.get("signature") or "").strip()]
    if not signed:
        return []
    bound = [(n, r) for n, r in signed if text_is_bound(r)]
    lines = [f"Decision log text binding: {len(bound)} of {len(signed)} signed row(s) bind their "
             f"conditions/evidence ({TEXT_DIGEST_FIELD}); {len(signed) - len(bound)} do not"]
    if TEXT_DIGEST_FIELD not in {c.strip() for c in fieldnames}:
        lines.append(f"  the log has no {TEXT_DIGEST_FIELD} column: new signed rows will not bind their "
                     "text until `agentteams --migrate-decision-log --output <team dir> --yes` adds it")
    bad = [n for n, r in bound if text_digest_mismatch(r)]
    if bad:
        lines.append(f"  {TEXT_DIGEST_FIELD} MISMATCH on row(s) {bad}: conditions/evidence edited after "
                     "signing; those rows no longer clear")
    return lines


__all__ = [
    "TEXT_DIGEST_FIELD",
    "assert_text_bound",
    "describe_text_binding",
    "text_digest_for",
    "text_digest_mismatch",
    "text_is_bound",
]
