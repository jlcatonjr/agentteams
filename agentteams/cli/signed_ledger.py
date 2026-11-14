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
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from agentteams.capability_hints import agentteams_install_command


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


#: Placeholder values seen standing in for a real HMAC secret (masterTeacher 4.1, rc.12).
_PLACEHOLDER_KEYS = frozenset({"...", "changeme", "change-me", "xxx", "secret", "test", "key", "password",
                               "placeholder", "todo", "none", "null"})
#: The shortest accepted HMAC secret: the SHA-256 output size (RFC 2104 §3 recommends at least that).
MIN_HMAC_KEY_BYTES = 32


def require_strong_hmac_key(
    key: str, what: str = "signing key", error: type[RuntimeError] = RuntimeError,
) -> str:
    """Refuse an HMAC secret that is short or an obvious placeholder; return it unchanged otherwise.

    A placeholder such as ``...`` once signed a decision that read as verified, and anyone who knows
    the placeholder can forge that signature (masterTeacher 4.1). Signing and verifying both refuse.

    Args:
        key: The secret, as read from the environment or passed in.
        what: The name used in the error (for example ``"decision signing key"``).
        error: The exception type to raise, so a module can keep its own (``GrantError``).

    Returns:
        *key*, unchanged.

    Raises:
        RuntimeError: (or *error*) The key is under :data:`MIN_HMAC_KEY_BYTES` bytes, a known
            placeholder, or one repeated character.
    """
    stripped = key.strip()
    if (len(key.encode("utf-8")) < MIN_HMAC_KEY_BYTES or stripped.lower() in _PLACEHOLDER_KEYS
            or len(set(stripped)) <= 1):
        raise error(
            f"{what} is too weak (under {MIN_HMAC_KEY_BYTES} bytes, a placeholder, or one repeated "
            "character); use a random secret, e.g. `python -c 'import secrets; print(secrets.token_hex(32))'`"
        )
    return key


#: The directory every emitted sandbox read-denies and write-denies (``frameworks/_sandbox_emit.SIGNING_KEY_DIR``).
HMAC_KEY_DIR = "~/.config/agentteams/keys"
#: The operator verify-key store, write-denied in every emitted sandbox; a key file is trusted only when its
#: fingerprint is recorded here (``_sandbox_emit.OPERATOR_VERIFY_KEYS_DIR``; @security M2).
HMAC_FINGERPRINT_DIR = "~/.config/agentteams/verify-keys"
#: Each HMAC key's environment variable and its key file in :data:`HMAC_KEY_DIR` (hmac-key-autoresolve).
HMAC_KEY_FILES: dict[str, str] = {
    "AGENTTEAMS_WAIVER_SIGNING_KEY": "waiver-hmac.key",
    "AGENTTEAMS_DECISION_SIGNING_KEY": "decision-hmac.key",
    "AGENTTEAMS_GRANT_SIGNING_KEY": "grant-hmac.key",
    "AGENTTEAMS_MANAGEMENT_SIGNING_KEY": "management-hmac.key",
}
#: The kind names of :data:`HMAC_KEY_FILES` (``waiver``, ``decision``, ``grant``, ``management``).
HMAC_KINDS: dict[str, str] = {name.split("-", 1)[0]: env for env, name in HMAC_KEY_FILES.items()}
_FINGERPRINT_LABEL = b"agentteams-hmac-fp-v1"
_MAX_HMAC_KEY_FILE_BYTES = 4096
_warned_both: set[str] = set()
_location_cache: dict[tuple[str, ...], list[str]] = {}


def hmac_key_dir() -> Path:
    """:data:`HMAC_KEY_DIR` expanded (a seam: the test suite points it at a temp dir)."""
    return Path(os.path.expanduser(HMAC_KEY_DIR))


def hmac_fingerprint_dir() -> Path:
    """:data:`HMAC_FINGERPRINT_DIR` expanded (a seam, as :func:`hmac_key_dir`)."""
    return Path(os.path.expanduser(HMAC_FINGERPRINT_DIR))


def hmac_key_fingerprint(key: str) -> str:
    """Return the public fingerprint of an HMAC key: ``HMAC-SHA256(key, "agentteams-hmac-fp-v1")`` in hex.

    It reveals nothing about the key, and binds a key file to the operator's intent (@security M2).

    Args:
        key: The HMAC secret.

    Returns:
        64 hex characters.
    """
    return hmac.new(key.encode("utf-8"), _FINGERPRINT_LABEL, hashlib.sha256).hexdigest()


def _project_roots(dirs: Sequence[Path]) -> list[Path]:
    """The cwd, plus for each of *dirs* the nearest ancestor holding ``.claude/settings.json`` (as Ed25519 does)."""
    roots = [Path.cwd().resolve()]
    for d in dirs:
        here = Path(d).resolve()
        for cand in (here, *here.parents):
            if (cand / ".claude" / "settings.json").is_file():
                if cand not in roots:
                    roots.append(cand)
                break
    return roots


def _file_key_location_refusals(roots: Sequence[Path]) -> list[str]:
    """Install-location (a) refusals for reading a key file, cached per set of project roots."""
    key = tuple(sorted(str(r) for r in roots))
    if key not in _location_cache:
        from agentteams.cli.signer_location import verify_install_location

        _location_cache[key] = [f.message for f in verify_install_location(list(roots)) if f.refuse]
    return _location_cache[key]


def _read_custody_file(directory: Path, name: str, *, private: bool) -> bytes:
    """Read ``directory/name`` without following a symlink at either level, checking both by ``fstat``.

    The directory is opened ``O_DIRECTORY | O_NOFOLLOW`` and must be yours and not group/world-writable; the
    file is opened relative to it (``dir_fd``) with ``O_NOFOLLOW``, so swapping either for a link after the
    checks cannot redirect the read (@security M5).

    Args:
        directory: The custody directory.
        name: A bare file name in it.
        private: True for a secret (the file must be mode 0600); False for a public record (not writable by
            group or others).

    Returns:
        The file's bytes (at most :data:`_MAX_HMAC_KEY_FILE_BYTES`).

    Raises:
        OSError: Missing, a symlink, or unreadable.
        ValueError: A custody rule fails.
    """
    import stat as _stat

    dfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        dst = os.fstat(dfd)
        if dst.st_uid != os.getuid() or dst.st_mode & 0o022:
            raise ValueError(f"{directory} must be a directory you own, not writable by group or others")
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
        try:
            st = os.fstat(fd)
            if not _stat.S_ISREG(st.st_mode):
                raise ValueError("is not a regular file")
            if st.st_uid != os.getuid() or st.st_mode & (0o077 if private else 0o022):
                raise ValueError("must be a regular file you own, with mode 0600" if private
                                 else "must be a file you own, not writable by group or others")
            data = os.read(fd, _MAX_HMAC_KEY_FILE_BYTES + 1)
        finally:
            os.close(fd)
    finally:
        os.close(dfd)
    if len(data) > _MAX_HMAC_KEY_FILE_BYTES:
        raise ValueError(f"is larger than {_MAX_HMAC_KEY_FILE_BYTES} bytes")
    return data


def load_hmac_key(env_name: str, error: type[RuntimeError] = RuntimeError, *,
                  project_dirs: Sequence[Path] = ()) -> str:
    """Return an HMAC key: the environment variable when set, else its key file, else ``""``.

    The variable wins, so an existing setup behaves as before. When the key file also exists, a warning
    says whether the two match. Otherwise ``HMAC_KEY_DIR/<kind>-hmac.key`` is read, and trusted only when
    (1) it and its directory pass custody checks, (2) the running install is not one an agent could edit,
    and (3) its fingerprint (:func:`hmac_key_fingerprint`) equals the one recorded in
    ``HMAC_FINGERPRINT_DIR/<kind>-hmac.fp`` by :func:`record_hmac_fingerprint`. Every failure refuses, so a
    planted key file, or a read the sandbox denies, fails closed (@security ruling and review,
    references/plans/hmac-key-autoresolve.plan.md). The key is never printed.

    Args:
        env_name: One of :data:`HMAC_KEY_FILES`.
        error: The exception type to raise, so a module keeps its own (``GrantError``).
        project_dirs: Team or project directories whose ``.claude/settings.json`` write roots count as
            agent-writable for the install-location check (the cwd always counts).

    Returns:
        The key text, or ``""`` when neither the variable nor the file exists. Callers keep their own
        "not configured" errors, and run :func:`require_strong_hmac_key` as before.

    Raises:
        RuntimeError: (or *error*) The key file exists but fails any check above, or cannot be read.
    """
    value = os.environ.get(env_name, "")
    key_dir = hmac_key_dir()
    name = HMAC_KEY_FILES[env_name]
    path = key_dir / name
    if value:
        if os.path.lexists(path) and env_name not in _warned_both:
            _warned_both.add(env_name)
            try:
                same = hmac.compare_digest(_read_custody_file(key_dir, name, private=True).decode("utf-8").strip(),
                                           value)
                how = "the same key" if same else "a DIFFERENT key"
            except (OSError, ValueError, UnicodeDecodeError):
                how = "a key file that fails its checks"
            print(f"Warning: {env_name} is set and {path} exists ({how}); using the variable. If you did not "
                  "create that file, delete it. If you did, unset the variable, which sandboxed commands "
                  "inherit.", file=sys.stderr)
        return value
    if not os.path.lexists(path):
        return ""
    try:
        refusals = _file_key_location_refusals(_project_roots(project_dirs))
    except OSError as exc:
        raise error(f"{env_name}: cannot check where agentteams is installed ({exc}); set {env_name} for this "
                    "command instead") from exc
    if refusals:
        raise error(f"{env_name}: refusing to read {path} from an install an agent may be able to edit "
                    f"({refusals[0]}); set {env_name} for this command, or run from a trusted install "
                    "(SECURITY.md, \"Signing from a trusted install\")")
    try:
        data = _read_custody_file(key_dir, name, private=True).decode("utf-8").strip()
    except OSError as exc:
        raise error(f"{env_name}: key file {path} cannot be read (a symlink, or denied here): "
                    f"{exc.strerror or exc}") from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise error(f"{env_name}: key file {path} {exc}") from exc
    if not data:
        raise error(f"{env_name}: key file {path} is empty")
    fp_name = name.replace(".key", ".fp")
    fp_path = hmac_fingerprint_dir() / fp_name
    try:
        recorded = _read_custody_file(hmac_fingerprint_dir(), fp_name, private=False).decode("ascii").strip()
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        kind = name.split("-", 1)[0]
        raise error(f"{env_name}: key file {path} has no usable recorded fingerprint at {fp_path} ({exc}); if you "
                    f"created the key, record it with `agentteams --record-hmac-fingerprint {kind}`") from exc
    if not hmac.compare_digest(recorded, hmac_key_fingerprint(data)):
        raise error(f"{env_name}: key file {path} does not match the fingerprint recorded at {fp_path}; refusing "
                    "it (it may have been planted or replaced). If you replaced the key, re-record it")
    return data


def record_hmac_fingerprint(kind: str) -> Path:
    """Record the fingerprint of the ``<kind>-hmac.key`` file in :data:`HMAC_FINGERPRINT_DIR` (operator-only).

    Reads the key file under the same custody checks as :func:`load_hmac_key` (not the fingerprint), and
    writes ``<kind>-hmac.fp`` (mode 0644) in a directory created mode 0700 if absent. Every emitted sandbox
    write-denies that directory, so an agent cannot record a planted key.

    Args:
        kind: One of :data:`HMAC_KINDS`.

    Returns:
        The fingerprint file written.

    Raises:
        ValueError: An unknown kind.
        RuntimeError: The running install is one an agent could edit, or the key file is missing or fails
            a check.
    """
    if kind not in HMAC_KINDS:
        raise ValueError(f"unknown HMAC key kind {kind!r}; one of {', '.join(sorted(HMAC_KINDS))}")
    refusals = _file_key_location_refusals(_project_roots(()))
    if refusals:
        raise RuntimeError(f"refusing to record a key fingerprint from an install an agent may be able to edit "
                           f"({refusals[0]}); run from a trusted install (SECURITY.md, \"Signing from a trusted "
                           "install\")")
    name = HMAC_KEY_FILES[HMAC_KINDS[kind]]
    try:
        key = _read_custody_file(hmac_key_dir(), name, private=True).decode("utf-8").strip()
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"cannot read {hmac_key_dir() / name}: {exc}") from exc
    require_strong_hmac_key(key, f"{kind} HMAC key")
    out_dir = hmac_fingerprint_dir()
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = out_dir / name.replace(".key", ".fp")
    from agentteams.atomicio import _atomic_write_text

    _atomic_write_text(target, hmac_key_fingerprint(key) + "\n")
    target.chmod(0o644)
    return target


def hmac_sign(key: str, fields: Sequence[str]) -> str:
    """Return the lowercase hex HMAC-SHA256 of the canonical payload of ``fields``.

    Args:
        key: The shared signing secret.
        fields: Ordered field values to sign.

    Returns:
        The hex digest (lowercase).
    """
    payload = canonical_payload(fields)
    key = require_strong_hmac_key(key)
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


#: The pinned ``cryptography`` floor (``references/dependency-pins.json``, the ``[signing]`` extra;
#: locked by ``tests/test_supply_chain_pins.py``). Below it, signing still works but warns once.
CRYPTOGRAPHY_MIN_VERSION = (50, 0, 2)
_CRYPTO_WARNED: list[bool] = []


def _warn_if_old_cryptography(version: str) -> None:
    """Warn once on stderr when ``version`` is below :data:`CRYPTOGRAPHY_MIN_VERSION` (#20)."""
    parts = tuple(int(x) for x in re.findall(r"\d+", version)[:3])
    if _CRYPTO_WARNED or not parts or parts >= CRYPTOGRAPHY_MIN_VERSION:
        return
    _CRYPTO_WARNED.append(True)
    floor = ".".join(map(str, CRYPTOGRAPHY_MIN_VERSION))
    print(f"Warning: cryptography {version} is below the pinned {floor} (the [signing] extra; "
          "known CVEs). Install agentteams[signing] in its own venv rather than relying on a "
          "base-environment copy.", file=sys.stderr)


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
            "asymmetric backend and is NEVER downgraded to HMAC (install the extra from the git source: "
            f"`{agentteams_install_command('signing')}`)."
        ) from exc
    import cryptography

    _warn_if_old_cryptography(getattr(cryptography, "__version__", ""))
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
    "MIN_HMAC_KEY_BYTES",
    "HMAC_FINGERPRINT_DIR",
    "HMAC_KEY_DIR",
    "HMAC_KEY_FILES",
    "HMAC_KINDS",
    "hmac_fingerprint_dir",
    "hmac_key_dir",
    "hmac_key_fingerprint",
    "record_hmac_fingerprint",
    "hmac_sign",
    "load_hmac_key",
    "require_strong_hmac_key",
    "hmac_verify",
    "is_expired",
    "path_within",
    "verify_by_scheme",
]
