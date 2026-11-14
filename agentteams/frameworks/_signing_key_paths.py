"""_signing_key_paths.py: where the operator's keys live, and what every emitted sandbox must deny there.

Carved out of ``_sandbox_emit`` (hmac-key-autoresolve, @code-hygiene CH-07 ceiling). The Claude block, the goose
Seatbelt profile and ``confine-run.sh`` read-deny :data:`SIGNING_KEY_DIR`; the macOS Claude block and the goose
Seatbelt profile also write-deny it and :data:`OPERATOR_VERIFY_KEYS_DIR`. ``_write_roots.validate_write_roots``
refuses write roots at, inside or above ``~/.config/agentteams``. ``_sandbox_emit`` re-exports
every name here, so importers are unchanged.
"""

from __future__ import annotations

import os

#: The operator private-key directory (F-1). Every emitted sandbox (Claude block, goose Seatbelt
#: profile, ``confine-run.sh``) read-denies it whatever the privilege profile. Deliberately NOT
#: ``$XDG_CONFIG_HOME``-relative: a deny path must be static and identical across the emitters.
#: The shell literals in ``confine-run.sh`` and ``provision-operator-signing-key.sh`` are locked to
#: this value by a test. Not the whole ``~/.config/agentteams``: ``goose_config`` reads
#: ``goose-sources.json`` there and silently falls back to built-ins when it cannot.
SIGNING_KEY_DIR = "~/.config/agentteams/keys"
#: The operator public keys that verify direct-write grants (R5), and the recorded HMAC key fingerprints.
OPERATOR_VERIFY_KEYS_DIR = "~/.config/agentteams/verify-keys"

#: The pre-F-1 private-key location. Key files there are denied TRANSITIONALLY (@security ruling
#: (b), 2026-09-30); remove once a release has shipped the migration advisory. The glob form is
#: used ONLY in ``permissions.deny`` (gitignore syntax); the sandbox side lists exact paths.
LEGACY_SIGNING_KEY_DIR = "~/.config/agentteams"
LEGACY_SIGNING_KEY_GLOB = f"{LEGACY_SIGNING_KEY_DIR}/*.pem"


def _home_rel_to_abs(path: str) -> str:
    """Return ``path`` with a leading ``~`` expanded and made absolute."""
    return os.path.abspath(os.path.expanduser(path))


def legacy_signing_key_files() -> list[str]:
    """Return the ``~/``-relative paths of ``*.pem`` files in the legacy key location, sorted.

    Host-local by nature: it lists what exists on the GENERATING host (the sandbox side takes
    exact paths only). Unreadable or absent directory → ``[]``.

    Returns:
        E.g. ``["~/.config/agentteams/decision-signing-op-2026.pem"]``.
    """
    legacy = _home_rel_to_abs(LEGACY_SIGNING_KEY_DIR)
    try:
        names = sorted(os.listdir(legacy))
    except OSError:
        return []
    return [
        f"{LEGACY_SIGNING_KEY_DIR}/{n}"
        for n in names
        if n.endswith(".pem") and os.path.isfile(os.path.join(legacy, n))
    ]


def signing_key_deny_read(*, resolve_abspath: bool = False) -> list[str]:
    """Return the sandbox read-deny entries isolating the operator private key.

    Args:
        resolve_abspath: Expand ``~`` to the generating host's absolute home (the P3-3 opt-in).

    Returns:
        :data:`SIGNING_KEY_DIR`, then every legacy key file on this host (exact paths).
    """
    paths = [SIGNING_KEY_DIR, *legacy_signing_key_files()]
    return [_home_rel_to_abs(p) for p in paths] if resolve_abspath else paths


def signing_key_deny_write(*, resolve_abspath: bool = False) -> list[str]:
    """Return the sandbox write-deny entries for the operator's key and verify-key directories.

    An HMAC key file there is trusted only against a fingerprint recorded beside the operator verify keys,
    so neither directory may be writable by an agent, whatever its write roots (hmac-key-autoresolve,
    @security M1). ``denyWrite`` wins over ``allowWrite``.

    Args:
        resolve_abspath: Expand ``~`` to the generating host's absolute home (the P3-3 opt-in).

    Returns:
        :data:`SIGNING_KEY_DIR` and :data:`OPERATOR_VERIFY_KEYS_DIR`.
    """
    paths = [SIGNING_KEY_DIR, OPERATOR_VERIFY_KEYS_DIR]
    return [_home_rel_to_abs(p) for p in paths] if resolve_abspath else paths


def assert_roots_clear_of_signing_keys(roots: list[str] | None) -> None:
    """Raise if a write root sits at or inside :data:`SIGNING_KEY_DIR`.

    Claude Code resolves overlapping read rules narrower-wins, so a write root (re-opened by
    ``allowRead``, and writable) at or inside the key directory would undo its deny. Only
    ``~``-relative and absolute roots are checked: a relative root is relative to the project,
    which is never inside the key directory.

    Args:
        roots: The workspace write roots (``None`` = the default ``["."]``).

    Raises:
        ValueError: A root resolves at or inside the key directory.
    """
    keys = _home_rel_to_abs(SIGNING_KEY_DIR)
    for root in roots or []:
        if not root or not (root.startswith("~") or os.path.isabs(root)):
            continue
        resolved = _home_rel_to_abs(root)
        if resolved == keys or resolved.startswith(keys + os.sep):
            raise ValueError(
                f"workspace write root {root!r} is at or inside the operator signing-key "
                f"directory {SIGNING_KEY_DIR}; that would re-open the private key to the "
                "sandboxed agent. Remove it from workspace_write_roots (or the grant)."
            )


def signing_keyfile_warnings(keyfile: str) -> list[str]:
    """Return operator warnings about where a private signing key file sits (never refuses).

    The sandboxes read-deny only :data:`SIGNING_KEY_DIR` (plus legacy key files transitionally),
    so a key elsewhere, behind a symlink, or with a mode wider than 600 is a visibility signal
    for the operator (Rule 12), not a hard stop: ``--sign-decision`` still signs.

    Args:
        keyfile: The path named by ``AGENTTEAMS_DECISION_ED25519_KEYFILE``.

    Returns:
        Zero or more warning sentences. A missing file yields a pointer to the migrated copy
        when one exists under :data:`SIGNING_KEY_DIR`, else nothing (the read error reports it).
    """
    keys = os.path.realpath(_home_rel_to_abs(SIGNING_KEY_DIR))
    legacy = os.path.realpath(_home_rel_to_abs(LEGACY_SIGNING_KEY_DIR))
    path = os.path.abspath(os.path.expanduser(keyfile))
    real = os.path.realpath(path)
    if not os.path.exists(path):
        moved = os.path.join(keys, os.path.basename(path))
        if os.path.dirname(real) == legacy and os.path.isfile(moved):
            return [
                f"{keyfile} does not exist, but {moved} does: the key was migrated into "
                f"{SIGNING_KEY_DIR}. Unset AGENTTEAMS_DECISION_ED25519_KEYFILE so the signer finds it by key_id, "
                f"or set it to \"{moved}\" on the signing command only (never export it)."
            ]
        return []
    out: list[str] = []
    if os.path.islink(path):
        out.append(f"{keyfile} is a symlink (to {real}); sandbox read-denies match the resolved "
                   "path, so keep the key itself in the key directory.")
    if os.path.dirname(real) == legacy:
        out.append(f"{keyfile} is in the pre-F-1 location {LEGACY_SIGNING_KEY_DIR}/. Move it into "
                   f"{SIGNING_KEY_DIR} with provision-operator-signing-key.sh --migrate.")
    elif not real.startswith(keys + os.sep):
        out.append(f"{keyfile} is outside {SIGNING_KEY_DIR}: no emitted sandbox read-denies it, "
                   "so a sandboxed agent may be able to read it.")
    try:
        mode = os.stat(real).st_mode & 0o777
    except OSError:
        mode = 0
    if mode & 0o077:
        out.append(f"{keyfile} has mode {mode:o}; a private key should be 600 (chmod 600).")
    return out
