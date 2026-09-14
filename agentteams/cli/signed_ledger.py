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


#: Signature-scheme tokens carried in a ``sig_scheme`` field. Empty/absent means the legacy
#: symmetric default (``hmac``); ``ed25519`` is the asymmetric scheme required for a
#: constraint-relaxing authorization (Workstream B). These tokens are SIGNED fields at the caller
#: (so an attacker cannot relabel a relaxing row's scheme ed25519→hmac and forge a cheap HMAC).
SIG_SCHEME_HMAC = "hmac"
SIG_SCHEME_ED25519 = "ed25519"


def _require_cryptography():
    """Import the Ed25519 backend, or FAIL CLOSED (never silently degrade to HMAC).

    The ``cryptography`` import is local to the asymmetric functions so importing this module
    (done across the whole CLI) never requires the ``signing`` extra — only actually signing or
    verifying an Ed25519 row does.

    Returns:
        A tuple ``(Ed25519PrivateKey, Ed25519PublicKey, serialization, InvalidSignature)``.

    Raises:
        RuntimeError: The ``cryptography`` library is not installed. Raising — rather than
            returning a sentinel a caller might treat as "unverified, fall back to HMAC" — is the
            whole point (WS-B re-review R4): a relaxing authorization must be *unavailable* when the
            asymmetric backend is absent, never quietly downgraded.
    """
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
    except ImportError as exc:  # pragma: no cover - exercised only where the extra is absent
        raise RuntimeError(
            "Ed25519 signing/verification requires the 'signing' extra (cryptography); it is not "
            "installed. Refusing to proceed — a relaxing authorization is unavailable without the "
            "asymmetric backend and is NEVER downgraded to HMAC (install `agentteams[signing]`)."
        ) from exc
    return Ed25519PrivateKey, Ed25519PublicKey, serialization, InvalidSignature


def ed25519_sign(private_pem: str, fields: Sequence[str], *, password: bytes | None = None) -> str:
    """Return the hex Ed25519 signature of the canonical payload of ``fields``.

    Signs the IDENTICAL bytes :func:`hmac_sign` would (``canonical_payload``), so the payload
    stays scheme-agnostic and a row can be verified under whichever scheme it declares.

    Args:
        private_pem: The operator's Ed25519 private key in PEM form (read from an operator-only
            file outside the repo; never an agent-held secret).
        fields: Ordered field values to sign.
        password: Optional passphrase if the PEM is encrypted.

    Returns:
        The signature as lowercase hex (128 chars).

    Raises:
        RuntimeError: The ``cryptography`` extra is absent, or the PEM is not an Ed25519 private key.
    """
    Ed25519PrivateKey, _pub, serialization, _inv = _require_cryptography()
    try:
        key = serialization.load_pem_private_key(private_pem.encode("utf-8"), password=password)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f"could not load Ed25519 private key: {exc}") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise RuntimeError("the supplied private key is not an Ed25519 key")
    signature = key.sign(canonical_payload(fields).encode("utf-8"))
    return signature.hex()


def ed25519_verify(public_pem: str, fields: Sequence[str], signature: str) -> bool:
    """Return True iff ``signature`` is a valid Ed25519 signature of ``fields`` under ``public_pem``.

    A bad/forged/mismatched signature returns False. The library being absent, or the verify key
    being malformed, RAISES (fail-closed) — those are "cannot verify" states that must never read
    as "verified" or silently fall back to HMAC.

    Args:
        public_pem: The tracked Ed25519 public verify key in PEM form.
        fields: Ordered field values that were signed.
        signature: The stored hex signature to check.

    Returns:
        Whether the signature verifies.

    Raises:
        RuntimeError: The ``cryptography`` extra is absent or the verify key cannot be loaded.
    """
    _priv, Ed25519PublicKey, serialization, InvalidSignature = _require_cryptography()
    try:
        key = serialization.load_pem_public_key(public_pem.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f"could not load Ed25519 public key: {exc}") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise RuntimeError("the supplied verify key is not an Ed25519 public key")
    try:
        raw = bytes.fromhex((signature or "").strip())
    except ValueError:
        return False  # a non-hex signature is simply invalid, not a config error
    try:
        key.verify(raw, canonical_payload(fields).encode("utf-8"))
        return True
    except InvalidSignature:
        return False


def verify_by_scheme(
    scheme: str,
    fields: Sequence[str],
    signature: str,
    *,
    hmac_key: str | None = None,
    public_pem: str | None = None,
) -> bool:
    """Verify ``signature`` over ``fields`` under the DECLARED ``scheme`` (pure validity check).

    This answers *validity* only ("is the signature correct under the scheme the row declares?"),
    never *sufficiency* ("is that scheme allowed for this row's class?") — the sufficiency policy
    (a relaxing row must be ed25519) lives in the caller (``decision_log``/``security_gate``), so
    the split that prevents a downgrade stays explicit.

    Args:
        scheme: ``""``/``hmac`` (symmetric) or ``ed25519`` (asymmetric).
        fields: Ordered signed field values.
        signature: The stored signature.
        hmac_key: Required for the hmac scheme.
        public_pem: Required for the ed25519 scheme.

    Returns:
        Whether the signature is valid under the named scheme.

    Raises:
        ValueError: An unknown scheme, or the key material for the named scheme is missing.
        RuntimeError: The ed25519 backend is absent (from :func:`ed25519_verify`).
    """
    normalized = (scheme or SIG_SCHEME_HMAC).strip().lower()
    if normalized == SIG_SCHEME_HMAC:
        if hmac_key is None:
            raise ValueError("hmac scheme requires hmac_key")
        return hmac_verify(hmac_key, fields, signature)
    if normalized == SIG_SCHEME_ED25519:
        if public_pem is None:
            raise ValueError("ed25519 scheme requires public_pem")
        return ed25519_verify(public_pem, fields, signature)
    raise ValueError(f"unknown signature scheme {scheme!r} (expected hmac or ed25519)")


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
    "SIG_SCHEME_ED25519",
    "SIG_SCHEME_HMAC",
    "canonical_payload",
    "ed25519_sign",
    "ed25519_verify",
    "hmac_sign",
    "hmac_verify",
    "is_expired",
    "path_within",
    "verify_by_scheme",
]
