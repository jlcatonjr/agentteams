"""The machine-local project registry: which repositories exist on this machine, by origin, and at which level.

``<config dir>/projects.json`` (mode 600) is written only by the operator command ``vulnscan enroll``
(:mod:`.project_cli`); builds, ``--update`` and fleet runs never write it, and :func:`save` refuses on CI.
It holds machine facts (paths), so it never goes into a repository. The registry only narrows what is
scanned and proposed; it never authorizes a change (signed grants do). ``enroll``'s terminal check is
not authentication.

A repository is identified by its *origin* (:func:`identity`), read from git's config file as text: git is
never run here, because the repositories are untrusted. Clones and worktrees of one origin are checkouts
of the same identity; one of them, the *fix checkout* (:func:`pick_fix_checkout`, or the operator's choice),
is what the daily run scans and what a grant may cover.

API (also used by ``grant sign --from-registry``, the executor's origin check and PR-body scrubbing):

| Call | Returns |
|---|---|
| :func:`identity` ``(repo)`` | ``host/owner/name``, ``local:<common dir>``, ``file:<origin path>`` or ``dir:<path>`` |
| :func:`checked_identity` ``(repo)`` | ``(identity, problem)``: problem names a ``.git`` file, ``commondir`` or ``config`` that is not a small regular file |
| :func:`owner_of` ``(identity)`` | ``host/owner``, or the identity's kind (``local``, ``file``, ``dir``) |
| :func:`origin_identity` ``(url)`` | the normalised ``host/owner/name`` of a remote URL, or None |
| :func:`load` / :func:`save` | the :class:`Entry` list (``save``: operator only; ``load`` refuses a file that is a symlink, not yours or group/world-writable) |
| :func:`find` ``(entries, path)`` | the entry whose identity ``path`` has, or None |
| :func:`discover` ``(root)`` | ``{identity: [checkout paths]}`` for agentteams workspaces under ``root`` |
| :func:`pick_fix_checkout` ``(paths)`` | the default fix checkout |
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import stat
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agentteams.vulnscan import paths

#: Levels a registry entry can hold: a brief's answer, or ``unanswered`` (discovered, no answer yet).
LEVELS = ("off", "scan", "remediate", "unanswered")
#: Levels whose fix checkout the daily run scans.
SCANNED_LEVELS = ("scan", "remediate")
FORMAT_VERSION = 1
#: Largest ``.git`` file, ``commondir`` or ``config`` :func:`identity` reads (bytes).
MAX_GIT_FILE = 1 << 20
#: Identity prefixes of repositories without a host origin (their "owner" is the prefix).
_PATH_KINDS = ("local", "file", "dir")
_WT_SEGMENTS = re.compile(r"(-wt-|\.wt-|\.worktrees$|^worktrees$)")


@dataclass
class Entry:
    """One repository identity on this machine.

    Attributes:
        identity: :func:`identity` of the repository.
        fix_checkout: Absolute path of the checkout the daily run scans and a grant may cover.
        checkouts: Every known checkout of the identity (the fix checkout included), absolute paths.
        level: One of :data:`LEVELS`.
        owner: :func:`owner_of` the identity.
        enrolled: ISO timestamp of the first enrolment (kept on re-runs).
        brief: The brief the level was read from, or None.
    """

    identity: str
    fix_checkout: str
    checkouts: list[str] = field(default_factory=list)
    level: str = "unanswered"
    owner: str = ""
    enrolled: str = ""
    brief: str | None = None


def registry_path() -> Path:
    """Return the registry file path.

    Returns:
        ``<config dir>/projects.json`` (may not exist).
    """
    return paths.config_dir() / "projects.json"


def is_ci() -> bool:
    """Return whether this process runs on CI (``CI`` set to anything but empty, ``0``, ``false`` or ``no``).

    Returns:
        True on CI.
    """
    return os.environ.get("CI", "").strip().lower() not in ("", "0", "false", "no")


# --- identity -------------------------------------------------------------------------------------

class UnreadableIdentity(ValueError):
    """A file :func:`identity` reads is not a regular file, is too large, or cannot be read."""


def _read_small(path: Path) -> str | None:
    """Return a small regular file's text, or None when it does not exist.

    Never follows a final symlink and never opens a FIFO or device (``O_NOFOLLOW | O_NONBLOCK``, then
    ``fstat``); at most :data:`MAX_GIT_FILE` bytes.

    Raises:
        UnreadableIdentity: The path is not a regular file, is larger than the cap, or cannot be read.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise UnreadableIdentity(f"{path.name}: cannot be opened ({exc.strerror})") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise UnreadableIdentity(f"{path.name} is not a regular file")
        if st.st_size > MAX_GIT_FILE:
            raise UnreadableIdentity(f"{path.name} is larger than {MAX_GIT_FILE} bytes")
        data = os.read(fd, MAX_GIT_FILE + 1)
    except OSError as exc:
        raise UnreadableIdentity(f"{path.name}: cannot be read ({exc.strerror})") from exc
    finally:
        os.close(fd)
    if len(data) > MAX_GIT_FILE:
        raise UnreadableIdentity(f"{path.name} is larger than {MAX_GIT_FILE} bytes")
    return data.decode("utf-8", errors="replace")


def _git_dirs(repo: Path) -> tuple[Path, Path] | None:
    """Return (git dir, common dir) of ``repo`` from its ``.git`` entry, or None when it is not a git checkout.

    A main checkout has a ``.git`` directory. A linked worktree or a submodule has a ``.git`` file naming its
    git dir (``gitdir: <path>``); a linked worktree's git dir has a ``commondir`` file naming the shared one.

    Raises:
        UnreadableIdentity: ``.git`` exists but is neither a directory nor a regular file, or the ``.git``
            file or ``commondir`` is not a small regular file.
    """
    dotgit = repo / ".git"
    if dotgit.is_dir():
        return dotgit, dotgit
    if not os.path.lexists(dotgit):
        return None
    line = (_read_small(dotgit) or "").strip()
    if not line.startswith("gitdir:"):
        return None
    gitdir = Path(line[len("gitdir:"):].strip())
    gitdir = gitdir if gitdir.is_absolute() else repo / gitdir
    common = gitdir
    rel = (_read_small(gitdir / "commondir") or "").strip()
    if rel:
        common = Path(rel) if Path(rel).is_absolute() else gitdir / rel
    return gitdir, common


_SECTION = re.compile(r'^\s*\[\s*([A-Za-z0-9.-]+)(?:\s+"((?:[^"\\]|\\.)*)")?\s*\]\s*(.*)$')
_URL_KEY = re.compile(r"^\s*url\s*=\s*(.*?)\s*$", re.I)


def _origin_url(config_text: str) -> str:
    """Return the first ``url`` of ``[remote "origin"]`` in a git config file's text, or ""."""
    in_origin = False
    for line in config_text.splitlines():
        if line.lstrip().startswith(("#", ";")):
            continue
        sec = _SECTION.match(line)
        if sec:
            in_origin = sec.group(1).lower() == "remote" and sec.group(2) == "origin"
            line = sec.group(3)
        m = _URL_KEY.match(line) if in_origin else None
        if m and m.group(1):
            value = m.group(1)
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1]
            return value
    return ""


def origin_identity(url: str) -> str | None:
    """Normalise a remote URL to ``host/owner/name`` (lowercase; no scheme, user, port, ``.git`` or trailing ``/``).

    ``git@host:owner/name.git``, ``ssh://git@host:22/owner/name``, ``https://user@host/owner/name/`` and
    ``git://host/owner/name.git`` all give ``host/owner/name``. A nested group path keeps every segment
    after the host (``host/group/sub/name``).

    Args:
        url: A remote URL as written in git config.

    Returns:
        The identity, or None when ``url`` is a local path, a ``file://`` URL or not a host URL.
    """
    url = url.strip()
    m = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*)://(?:[^@/]*@)?([^/]+)(/.*)?$", url)
    if m:
        if m.group(1).lower() == "file":
            return None
        host, rest = m.group(2), m.group(3) or ""
    else:   # scp-like: [user@]host:path (a colon before any slash)
        m = re.match(r"^(?:[^@/:]+@)?([^/:]+):(?!//)(.*)$", url)
        if not m or len(m.group(1)) == 1:   # one letter: a Windows drive, not a host
            return None
        host, rest = m.group(1), m.group(2)
    host = re.sub(r":\d*$", "", host.split("@")[-1]).strip("[]").lower()
    rest = rest.strip().rstrip("/")
    rest = rest[:-4] if rest.lower().endswith(".git") else rest
    parts = [p for p in rest.lower().split("/") if p and p != "~"]
    if not host or len(parts) < 2:
        return None
    return "/".join([host, *parts])


def identity(repo: Path) -> str:
    """Return the identity of the repository checked out at ``repo``. Git is not run.

    Args:
        repo: A checkout (main, linked worktree or separate clone) or any directory.

    Returns:
        ``host/owner/name`` from ``origin``; ``file:<realpath>`` when origin is a local path;
        ``local:<realpath of the git common dir>`` when there is no origin; ``dir:<realpath>``
        when ``repo`` is not a git checkout, or when its ``.git`` file, ``commondir`` or ``config``
        is not a small regular file (:func:`checked_identity` says why).
    """
    return checked_identity(repo)[0]


def checked_identity(repo: Path) -> tuple[str, str]:
    """Return (:func:`identity`, why it could not be read: "" when it could).

    The executor refuses repo operations when the reason is not empty: the fallback ``dir:`` identity
    must not be mistaken for "no origin".

    Args:
        repo: A checkout or any directory.

    Returns:
        ``(identity, problem)``.
    """
    real = Path(os.path.realpath(repo.expanduser()))
    try:
        return _identity(real), ""
    except UnreadableIdentity as exc:
        return f"dir:{real}", str(exc)


def _identity(real: Path) -> str:
    dirs = _git_dirs(real)
    if dirs is None:
        return f"dir:{real}"
    _gitdir, common = dirs
    text = _read_small(common / "config") or ""
    url = _origin_url(text)
    if url:
        ident = origin_identity(url)
        if ident:
            return ident
        local = url[len("file://"):] if url.lower().startswith("file://") else url
        return f"file:{os.path.realpath(real / Path(local).expanduser())}"
    return f"local:{os.path.realpath(common)}"


def owner_of(ident: str) -> str:
    """Return the owner part of an identity.

    Args:
        ident: An :func:`identity`.

    Returns:
        ``host/owner`` for an origin identity; ``local``, ``file`` or ``dir`` for the others.
    """
    kind = ident.split(":", 1)[0]
    return kind if kind in _PATH_KINDS and ":" in ident else "/".join(ident.split("/")[:2])


def short_name(ident: str) -> str:
    """Return a short display name: the last path segment of the identity.

    Args:
        ident: An :func:`identity`.

    Returns:
        The name.
    """
    if owner_of(ident) not in _PATH_KINDS:
        return ident.rsplit("/", 1)[-1]
    path = Path(ident.split(":", 1)[1])
    name = path.parent.name if path.name == ".git" else path.name
    return name.removesuffix(".git") or name


# --- registry file --------------------------------------------------------------------------------

def _entry_problem(i: int, raw: Any) -> str:
    if not isinstance(raw, dict):
        return f"projects[{i}] is not an object"
    for key in ("identity", "fix_checkout", "level", "owner", "enrolled"):
        if not isinstance(raw.get(key), str) or (key in ("identity", "fix_checkout") and not raw[key]):
            return f"projects[{i}].{key} must be a non-empty string" if key in ("identity", "fix_checkout") \
                else f"projects[{i}].{key} must be a string"
    if raw["level"] not in LEVELS:
        return f"projects[{i}].level must be one of {', '.join(LEVELS)} (got {raw['level']!r:.40})"
    if not isinstance(raw.get("checkouts", []), list) or not all(isinstance(c, str) for c in raw.get("checkouts", [])):
        return f"projects[{i}].checkouts must be a list of strings"
    if raw.get("brief") is not None and not isinstance(raw["brief"], str):
        return f"projects[{i}].brief must be a string or null"
    unknown = set(raw) - {f for f in Entry.__dataclass_fields__}
    return f"projects[{i}] has unknown keys: {', '.join(sorted(unknown))}" if unknown else ""


def load(path: Path | None = None) -> list[Entry]:
    """Read the registry.

    Args:
        path: Registry file; defaults to :func:`registry_path`.

    Returns:
        The entries; [] when the file does not exist.

    Raises:
        ValueError: The file is not valid JSON, not the expected shape, or names one identity twice
            (the message names the file and the problem).
        OSError: The file exists but cannot be read.
    """
    from agentteams.vulnscan import grant   # lazy: grant imports this module lazily too

    path = path or registry_path()
    if not os.path.lexists(path):
        return []
    unsafe = grant.file_problem(path)   # as for grant files: a file others can write could steer scan roots
    if unsafe:
        raise ValueError(f"{path}: {unsafe}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"{path}: not valid JSON: {exc} (re-run `python -m agentteams.vulnscan enroll`)") from exc
    if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
        raise ValueError(f"{path}: expected an object with a \"projects\" list")
    entries: list[Entry] = []
    for i, raw in enumerate(data["projects"]):
        problem = _entry_problem(i, raw)
        if problem:
            raise ValueError(f"{path}: {problem}")
        entries.append(Entry(**{**raw, "checkouts": list(raw.get("checkouts", []))}))
    seen = [e.identity for e in entries]
    dup = sorted({s for s in seen if seen.count(s) > 1})
    if dup:
        raise ValueError(f"{path}: identity listed more than once: {', '.join(dup)}")
    return entries


def save(entries: Iterable[Entry], path: Path | None = None) -> Path:
    """Write the registry atomically with mode 600. Called only by ``vulnscan enroll`` (an operator command).

    Args:
        entries: The complete entry list (it replaces the file).
        path: Registry file; defaults to :func:`registry_path`.

    Returns:
        The path written.

    Raises:
        PermissionError: Running on CI (``CI`` is set): the registry is the operator's, never a build's.
        ValueError: An entry is malformed or an identity is listed twice.
        OSError: The write failed (the file is then unchanged).
    """
    if is_ci():
        raise PermissionError("the project registry is written only by the operator, never on CI (CI is set)")
    path = path or registry_path()
    rows = [asdict(e) for e in sorted(entries, key=lambda e: (e.owner, e.identity))]
    for i, raw in enumerate(rows):
        problem = _entry_problem(i, raw)
        if problem:
            raise ValueError(problem)
    if len({r["identity"] for r in rows}) != len(rows):
        raise ValueError("an identity is listed more than once")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"version": FORMAT_VERSION, "projects": rows}, indent=2) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def find(entries: Iterable[Entry], path: Path) -> Entry | None:
    """Return the entry for the repository at ``path`` (matched by identity), or None.

    Args:
        entries: Registry entries.
        path: Any checkout of the repository.

    Returns:
        The entry, or None when the identity is not registered.
    """
    ident = identity(path)
    return next((e for e in entries if e.identity == ident), None)


def now_iso() -> str:
    """Return the current UTC time as an ISO timestamp (seconds).

    Returns:
        The timestamp.
    """
    return _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat()


# --- discovery ------------------------------------------------------------------------------------

def discover(root: Path) -> dict[str, list[Path]]:
    """Find the agentteams workspaces under ``root`` and group them by identity.

    Discovery is :func:`agentteams.fleet.discover_workspaces` with every framework. Linked worktrees, which
    fleet skips as workspaces, are kept here as further checkouts of their identity.

    Args:
        root: Directory to search.

    Returns:
        ``{identity: [resolved checkout paths, sorted]}``.
    """
    from agentteams.fleet import discover_workspaces   # lazy: fleet is large and not needed by the daily run

    skipped: list[Path] = []
    found = discover_workspaces(root.expanduser(), "all", skipped_worktrees=skipped)
    groups: dict[str, set[Path]] = defaultdict(set)
    for ws in [*found, *skipped]:
        real = Path(os.path.realpath(ws))
        groups[identity(real)].add(real)
    return {k: sorted(v) for k, v in sorted(groups.items())}


def _worktree_like(path: Path) -> bool:
    return any(_WT_SEGMENTS.search(part) for part in path.parts)


def pick_fix_checkout(checkouts: Iterable[Path]) -> Path:
    """Choose the default fix checkout among an identity's checkouts.

    Prefers a main checkout (a ``.git`` directory, not a linked worktree's ``.git`` file) whose path has no
    ``-wt-``, ``.wt-``, ``worktrees`` or ``*.worktrees`` segment; then any main checkout; then any path. The
    shortest path wins (ties: alphabetical). The operator can choose another in ``vulnscan enroll``.

    Args:
        checkouts: At least one path.

    Returns:
        The chosen path.

    Raises:
        ValueError: ``checkouts`` is empty.
    """
    pool = list(checkouts)
    if not pool:
        raise ValueError("no checkouts to choose from")
    mains = [p for p in pool if (p / ".git").is_dir()]
    best = [p for p in mains if not _worktree_like(p)] or mains or pool
    return min(best, key=lambda p: (len(str(p)), str(p)))
