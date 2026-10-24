"""Whether a directory may be mounted into the agent container as the team dir (``agent.team_dir``).

Split out of :mod:`agentteams.vulnscan.agent_run` (CH-07), which re-exports :func:`team_dir_problem` and
:data:`TEAM_DIR_PROTECTED`. Used before every mount: agent runs, the self-test and a debrief.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from agentteams.frameworks._write_roots import HOME_PROTECTED
from agentteams.vulnscan import paths
from agentteams.vulnscan.agent_upstreams import SECRETS_DIR

#: Paths a team dir may not be at, inside or above (besides home and its ancestors): the write-root
#: protected home paths, which include credentials, shell persistence and the whole operator dir
#: ``~/.config/agentteams``. ``team_dir_problem`` also refuses the configured vulnscan config and
#: state dirs and judges the resolved path, which ``root_problem`` does not; hence its own check.
TEAM_DIR_PROTECTED: tuple[str, ...] = HOME_PROTECTED
#: Further paths a team dir may not be at, inside or above: the provider key dir
#: ``~/.config/agentteams-secrets`` (a sibling of ``~/.config/agentteams``, so not covered by it), whose
#: key files the gateway alone mounts.
TEAM_DIR_PROTECTED_EXTRA: tuple[str, ...] = (SECRETS_DIR,)


def _at_or_inside(path: str, base: str) -> bool:
    return path == base or path.startswith(base.rstrip("/") + "/")


def _my_uid() -> int:
    """Return the uid a team dir must be owned by (the operator running vulnscan)."""
    return os.getuid()


def _dir_owner_problem(real: Path) -> str | None:
    """Return why the directory ``real`` (already resolved) is not safely the operator's, or None (X6).

    The final component itself is judged: it is opened with ``O_NOFOLLOW`` and ``fstat``-ed, so a
    swap to a symlink after resolution is refused rather than followed.
    """
    try:
        fd = os.open(real, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        return f"agent.team_dir {real} cannot be opened as a directory ({exc.strerror})"
    try:
        st = os.fstat(fd)
    finally:
        os.close(fd)
    if st.st_uid != _my_uid():
        return f"agent.team_dir {real} is not owned by you (uid {st.st_uid})"
    if st.st_mode & 0o022:
        return f"agent.team_dir {real} is group- or world-writable (mode {st.st_mode & 0o777:o}); chmod go-w it"
    return None


def team_dir_problem(path: str | Path | None, secrets: tuple[str | Path, ...] = ()) -> str | None:
    """Return why ``path`` may not be mounted as the team dir, or None when it may (R3-3, X6).

    It must be an absolute (or ``~/``) path to an existing directory, free of docker-mount syntax
    characters, that is not the home directory or an ancestor of it, and not at, inside or above
    any :data:`TEAM_DIR_PROTECTED` or :data:`TEAM_DIR_PROTECTED_EXTRA` path or the configured vulnscan config or state dir. Both the
    path as written and its resolved target are compared with both forms of each protected path
    (so ``/var`` and ``/private/var`` match). The directory itself must be owned by the operator
    and not group- or world-writable. Used before every mount (runs, self-test, debrief).

    Args:
        path: ``agent.team_dir`` (or the fallback the caller mounts).
        secrets: Files that must stay outside the mount (``agent.key_file``). The operator signing key
            named by ``AGENTTEAMS_DECISION_ED25519_KEYFILE`` is always added (V2-3).

    Returns:
        A reason, or None.
    """
    raw = str(path or "").strip()
    if not raw:
        return "agent.team_dir is not set"
    p = Path(raw).expanduser()
    if not p.is_absolute():
        return f"agent.team_dir {raw!r} must be an absolute path"
    real = p.resolve()
    if any(c in s for s in (raw, str(real)) for c in ":,") or any(ord(c) < 0x20 for c in raw + str(real)):
        return f"agent.team_dir {raw!r} contains ':', ',' or a control character (docker mount syntax)"
    if not real.is_dir():
        return f"agent.team_dir {real} is not an existing directory"
    fold = str.lower if sys.platform == "darwin" else str
    forms = {fold(os.path.normpath(str(p))), fold(str(real))}
    home = Path(os.path.expanduser("~"))
    homes = {fold(os.path.normpath(str(home))), fold(str(home.resolve()))}
    if any(_at_or_inside(h, f) for h in homes for f in forms):
        return f"agent.team_dir {real} is the home directory or an ancestor of it"
    for prot in (*TEAM_DIR_PROTECTED, *TEAM_DIR_PROTECTED_EXTRA, str(paths.config_dir()), str(paths.state_dir())):
        q = Path(os.path.expanduser(prot))
        for b in {fold(os.path.normpath(str(q))), fold(str(q.resolve()))}:
            if any(_at_or_inside(f, b) or _at_or_inside(b, f) for f in forms):
                return f"agent.team_dir {real} is at, inside or above a protected path ({prot})"
    keyfile = os.environ.get("AGENTTEAMS_DECISION_ED25519_KEYFILE", "")
    for secret in (*secrets, *((keyfile,) if keyfile else ())):
        if not str(secret).strip():
            continue
        s = Path(str(secret)).expanduser()
        for b in {fold(os.path.normpath(str(s))), fold(str(s.resolve()))}:
            if any(_at_or_inside(b, f) for f in forms):
                return f"agent.team_dir {real} contains a key file ({secret}); it would be mounted into the container"
    return _dir_owner_problem(real)
