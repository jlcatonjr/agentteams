"""signer_location.py — is the running agentteams install one an agent could have edited? (#10)

The signing minters read the operator's private key in THIS process, so the code running them must
not live where a confined agent can write. ``verify_install_location`` checks where the running
package, interpreter and prefix live:

* **(a) refuse** (unless ``--allow-checkout-signing``): the package dir, ``sys.prefix`` or
  ``sys.executable`` is inside the current directory, a git work tree, or a sandbox ``allowWrite``
  root of the project (``.claude/settings.json``; ``.`` is the project). The base interpreter
  (``sys.executable``, and ``sys.prefix`` outside a venv) counts as in a work tree only when that
  work tree does not git-ignore it (Homebrew's ``Cellar/``; see ``_base_interpreter_in_work_tree``).
* **(b)–(e) warn**: (b) the package dir or its parent is owned by someone other than you or root,
  or is group/world-writable; (c) the dist-info ``RECORD`` hash of an installed file differs;
  (d) a ``.pth`` file in a site dir runs ``import`` lines; (e) the current directory is on
  ``sys.path`` (``python -m agentteams`` from a project root imports the checkout).

HONEST LIMITS: every check runs inside the process it judges, so tampered code can skip it, and
``RECORD`` lives in the same writable site dir as the files it hashes. These are speed bumps for an
honest operator. The durable control is signing from a verified install outside every write root
(``SECURITY.md``, "Signing from a trusted install").
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import site
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from agentteams.git_exec import run_git


@dataclass(frozen=True)
class LocationFinding:
    """One install-location finding."""

    criterion: str  # "a".."e"
    refuse: bool
    message: str


def _git_work_tree(path: Path) -> bool:
    """True when ``path`` is inside a git work tree (hardened: repo config cannot run code)."""
    probe = path if path.is_dir() else path.parent
    try:
        res = run_git(probe, "rev-parse", "--is-inside-work-tree", hardened=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return res.returncode == 0 and res.stdout.strip() == "true"


def _git_ignored(path: Path) -> bool:
    """True only when git positively reports ``path`` as ignored by its work tree.

    Tracked paths, untracked-but-not-ignored paths and any git error count as NOT ignored, so a
    caller that relaxes a refusal on this answer fails closed.
    """
    probe = path if path.is_dir() else path.parent
    # Only the work tree's own ignore rules count: inherited GIT_* variables (GIT_DIR, GIT_CONFIG_COUNT/
    # KEY/VALUE…) and global/system config (core.excludesFile) could otherwise mark any path "ignored".
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
    try:
        res = run_git(probe, "check-ignore", "-q", "--", str(path), hardened=True, env=env, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return res.returncode == 0


def _base_interpreter_in_work_tree(path: Path) -> bool:
    """Work-tree test for the base interpreter: inside a git work tree AND not git-ignored there.

    Why the base interpreter is judged differently (#10, Apple-silicon Homebrew): Homebrew's prefix
    (``/opt/homebrew``) is itself a git work tree (Homebrew's own repo) whose ``.gitignore`` ignores
    everything but ``Library/``, so every Homebrew python, and every venv/pipx install built on it,
    resolves into an ignored ``Cellar/`` path inside a work tree. The threat this criterion addresses
    is code an agent could edit as repository content: an editable or dev checkout of agentteams, or
    a venv kept inside a project. Those are caught by the package and venv ``sys.prefix`` tests,
    which stay strict (ignored or not). A base interpreter that the repository itself ignores is
    not repository content; it is still refused when it is under the current directory or an
    agent-writable ``allowWrite`` root, and still refused when it is tracked (or merely untracked)
    content of a work tree.
    """
    return _git_work_tree(path) and not _git_ignored(path)


def _settings_allow_write(project: Path) -> list | None:
    """The raw ``sandbox.filesystem.allowWrite`` list of a project's live settings.

    Returns:
        ``[]`` when there is no settings file or it sets no ``allowWrite``; ``None`` when the file exists
        but cannot be read or parsed, or ``allowWrite`` is not a list (the agent-writable roots are then
        unknown, and the caller refuses).
    """
    path = project / ".claude" / "settings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        return None
    node: object = data
    for key in ("sandbox", "filesystem"):
        if not isinstance(node, dict):
            return None
        node = node.get(key, {})
    if not isinstance(node, dict):
        return None
    allow = node.get("allowWrite", [])
    return allow if isinstance(allow, list) else None


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _allow_write_roots(project_roots: list[Path]) -> tuple[list[Path], list[Path]]:
    """Absolute ``allowWrite`` roots from each project's live ``.claude/settings.json``.

    Returns:
        (the roots, the projects whose settings could not be read or parsed).
    """
    roots: list[Path] = []
    unreadable: list[Path] = []
    for project in project_roots:
        allow = _settings_allow_write(project)
        if allow is None:
            unreadable.append(project)
            continue
        for entry in allow:
            if not isinstance(entry, str) or not entry:
                continue
            p = Path(os.path.expanduser(entry)) if entry.startswith("~") else Path(entry)
            roots.append((p if p.is_absolute() else project / p).resolve())
    return roots, unreadable


def _inside(path: Path, base: Path) -> bool:
    return path == base or path.is_relative_to(base)


def _record_mismatches(pkg: Path) -> list[str]:
    """Installed files whose dist-info RECORD sha256 differs (skips entries without a hash)."""
    from importlib import metadata

    try:
        dist = metadata.distribution("agentteams")
    except metadata.PackageNotFoundError:
        return []
    bad: list[str] = []
    for f in dist.files or []:
        if not f.hash or f.hash.mode != "sha256" or not str(f).startswith("agentteams/"):
            continue
        data = _read_bytes(Path(f.locate()))
        if data is None:
            continue
        digest = hashlib.sha256(data).digest()
        if base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != f.hash.value:
            bad.append(str(f))
    return bad


def _pth_imports() -> list[str]:
    """``.pth`` files in the site dirs that contain executable ``import`` lines."""
    dirs = [*getattr(site, "getsitepackages", lambda: [])(), site.getusersitepackages()]
    found: list[str] = []
    for d in dirs:
        for pth in sorted(Path(d).glob("*.pth")) if Path(d).is_dir() else []:
            data = _read_bytes(pth)
            lines = data.decode("utf-8", "replace").splitlines() if data is not None else []
            if any(line.lstrip().startswith(("import ", "import\t")) for line in lines):
                found.append(str(pth))
    return found


def verify_install_location(project_roots: list[Path], cwd: Path | None = None) -> list[LocationFinding]:
    """Return the install-location findings for the running agentteams.

    Args:
        project_roots: Projects whose ``.claude/settings.json`` ``allowWrite`` roots count as
            agent-writable (``.`` = the project itself).
        cwd: The current directory (default ``Path.cwd()``).

    Returns:
        Findings; those with ``refuse=True`` block signing unless overridden.
    """
    import agentteams

    here = (cwd or Path.cwd()).resolve()
    pkg = Path(agentteams.__file__).resolve().parent
    prefix = Path(sys.prefix).resolve()
    in_venv = prefix != Path(sys.base_prefix).resolve()
    # Each target maps to its work-tree test: the package and a venv's prefix are strict; the base
    # interpreter (sys.executable, and sys.prefix outside a venv) may sit in a git-ignored path.
    targets = {"the agentteams package": (pkg, _git_work_tree),
               "sys.prefix": (prefix, _git_work_tree if in_venv else _base_interpreter_in_work_tree),
               "sys.executable": (Path(sys.executable).resolve(), _base_interpreter_in_work_tree)}
    extra, unreadable = _allow_write_roots(project_roots)
    writable = [*(p.resolve() for p in project_roots), *extra]
    out: list[LocationFinding] = [
        LocationFinding("a", True, f"{p / '.claude' / 'settings.json'} cannot be read or parsed, so the agent-"
                        "writable roots it grants are unknown") for p in unreadable]
    for label, (path, in_work_tree) in targets.items():
        where = ("the current directory" if _inside(path, here)
                 else "a git work tree" if in_work_tree(path)
                 else next((f"the agent-writable root {w}" for w in writable if _inside(path, w)), None))
        if where:
            out.append(LocationFinding("a", True, f"{label} ({path}) is inside {where}"))
    euid = os.geteuid() if hasattr(os, "geteuid") else None
    for path in (pkg, pkg.parent):
        st = path.stat() if path.exists() else None
        if st is None:
            continue
        if euid is not None and st.st_uid not in (euid, 0):
            out.append(LocationFinding("b", False, f"{path} is owned by uid {st.st_uid}, not you or root"))
        if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            out.append(LocationFinding("b", False, f"{path} is group- or world-writable"))
    mismatched = _record_mismatches(pkg)
    if mismatched:
        out.append(LocationFinding("c", False, "installed files differ from the dist-info RECORD: "
                                   + ", ".join(mismatched[:5]) + ("…" if len(mismatched) > 5 else "")))
    for pth in _pth_imports():
        out.append(LocationFinding("d", False, f"{pth} runs import lines at interpreter start"))
    if not getattr(sys.flags, "safe_path", False) and any(
            p in ("", ".") or Path(p).resolve() == here for p in sys.path[:1]):
        out.append(LocationFinding("e", False, "the current directory is first on sys.path "
                                   "(`python -m agentteams` from a project imports its checkout)"))
    return out


__all__ = ["LocationFinding", "verify_install_location"]
