"""signer_location.py — is the running agentteams install one an agent could have edited? (#10)

The signing minters read the operator's private key in THIS process, so the code running them must
not live where a confined agent can write. ``verify_install_location`` checks where the running
package, interpreter and prefix live:

* **(a) refuse** (unless ``--allow-checkout-signing``): the package dir, ``sys.prefix`` or
  ``sys.executable`` is inside the current directory, a git work tree, or a sandbox ``allowWrite``
  root of the project (``.claude/settings.json``; ``.`` is the project).
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
        res = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
             "-c", "core.untrackedCache=false", "-C", str(probe), "rev-parse",
             "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=10, check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return res.returncode == 0 and res.stdout.strip() == "true"


def _settings_allow_write(project: Path) -> list:
    """The raw ``sandbox.filesystem.allowWrite`` list of a project's live settings (else [])."""
    try:
        data = json.loads((project / ".claude" / "settings.json").read_text(encoding="utf-8"))
        allow = data["sandbox"]["filesystem"]["allowWrite"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    return allow if isinstance(allow, list) else []


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _allow_write_roots(project_roots: list[Path]) -> list[Path]:
    """Absolute ``allowWrite`` roots from each project's live ``.claude/settings.json``."""
    roots: list[Path] = []
    for project in project_roots:
        for entry in _settings_allow_write(project):
            if not isinstance(entry, str) or not entry:
                continue
            p = Path(os.path.expanduser(entry)) if entry.startswith("~") else Path(entry)
            roots.append((p if p.is_absolute() else project / p).resolve())
    return roots


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
    targets = {"the agentteams package": pkg, "sys.prefix": Path(sys.prefix).resolve(),
               "sys.executable": Path(sys.executable).resolve()}
    writable = [*(p.resolve() for p in project_roots), *_allow_write_roots(project_roots)]
    out: list[LocationFinding] = []
    for label, path in targets.items():
        where = ("the current directory" if _inside(path, here)
                 else "a git work tree" if _git_work_tree(path)
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
