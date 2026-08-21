"""signed_ledger.py — neutral primitives for signed, expiring, path-scoped ledgers.

Small, dependency-free helpers shared by capability-grant enforcement (``grants.py``)
and available for the security-waiver gate to converge on later. Deliberately narrow:
a keyed HMAC over an ordered field list, an ISO-8601 expiry check, and a
symlink/``..``-safe path-containment test. No I/O, no CSV, no policy — those live in
the callers.

The signing is **symmetric** (HMAC-SHA256, one shared key). It defends a *keyless*
actor — an agent that cannot read the signing key cannot forge a row — not an actor
that holds the key. Cross-trust-boundary unforgeability (a holder who cannot forge the
issuer's rows) would require asymmetric signatures, which the Python standard library
does not provide; :func:`hmac_sign` / :func:`hmac_verify` are the swap point where an
asymmetric backend would later slot in.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


def canonical_payload(fields: Sequence[str]) -> str:
    """Return the canonical signing payload for an ordered field list.

    Args:
        fields: The field values to sign, in a fixed order. Each is stripped of
            surrounding whitespace and joined with ``|`` — the same serialization the
            security-waiver gate uses, so the two stay compatible.

    Returns:
        The ``|``-joined payload string.
    """
    return "|".join((f or "").strip() for f in fields)


def hmac_sign(key: str, fields: Sequence[str]) -> str:
    """Return the lowercase hex HMAC-SHA256 of the canonical payload of ``fields``.

    Args:
        key: The shared signing secret.
        fields: Ordered field values to sign.

    Returns:
        The hex digest (lowercase).
    """
    payload = canonical_payload(fields)
    return hmac.new(key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def hmac_verify(key: str, fields: Sequence[str], signature: str) -> bool:
    """Return True iff ``signature`` matches the HMAC of ``fields`` under ``key``.

    Constant-time comparison; the stored signature is lowercased and stripped first so
    a case/whitespace difference is not treated as a forgery.

    Args:
        key: The shared signing secret.
        fields: Ordered field values that were signed.
        signature: The stored signature to check.

    Returns:
        Whether the signature is valid.
    """
    expected = hmac_sign(key, fields)
    return hmac.compare_digest(expected, (signature or "").strip().lower())


def is_expired(expires_at: str, *, now: datetime | None = None) -> bool:
    """Return True iff the ISO-8601 ``expires_at`` is at or before ``now`` (UTC).

    Args:
        expires_at: ISO-8601 timestamp. A trailing ``Z`` is accepted.
        now: The reference time; defaults to ``datetime.now(timezone.utc)``.

    Returns:
        Whether the deadline has passed.

    Raises:
        ValueError: ``expires_at`` is not parseable as ISO-8601.
    """
    ref = now or datetime.now(timezone.utc)
    text = expires_at.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed <= ref


def path_within(candidate: str, root: str, *, base: Path | None = None) -> bool:
    """Return True iff ``candidate`` resolves inside (or equal to) ``root``.

    Both are canonicalized (``..`` collapsed, symlinks resolved) before comparison so a
    ``../escape`` or a symlinked path cannot slip a write outside the granted root.
    Relative inputs resolve against ``base`` (default: current working directory).

    Args:
        candidate: The path a write targets.
        root: The permitted root.
        base: Directory relative inputs resolve against; defaults to ``Path.cwd()``.

    Returns:
        Whether ``candidate`` is contained by ``root``.
    """
    anchor = base or Path.cwd()
    cand = (anchor / candidate).resolve() if not Path(candidate).is_absolute() else Path(candidate).resolve()
    top = (anchor / root).resolve() if not Path(root).is_absolute() else Path(root).resolve()
    return cand == top or top in cand.parents


__all__ = [
    "canonical_payload",
    "hmac_sign",
    "hmac_verify",
    "is_expired",
    "path_within",
]
