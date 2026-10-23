"""Find dependency manifests under a directory and read exact versions from them.

Supported formats:

| Ecosystem | Files |
|---|---|
| PyPI | ``requirements*.txt`` (``==`` pins), ``poetry.lock``, ``uv.lock``, ``Pipfile.lock`` |
| npm | ``package-lock.json`` and ``npm-shrinkwrap.json`` (v1–v3), ``yarn.lock`` (classic and berry) |
| Packagist | ``composer.lock`` |

A manifest that yields no exact versions is reported as *unscannable*, not skipped
silently. Examples are an unpinned ``requirements.txt``, a ``pyproject.toml`` with no
lockfile, and a ``pnpm-lock.yaml``.

Discovery skips vendored and generated trees (``node_modules``, virtualenvs, build
output). It also skips **linked git worktrees** by default, because they repeat their
main checkout's manifests.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS: frozenset[str] = frozenset({
    "node_modules", ".git", ".hg", ".svn", "__pycache__", ".tox", ".nox", ".mypy_cache",
    ".pytest_cache", "site-packages", "dist-packages", "bower_components", "vendor",
    ".venv", "venv", "env", ".direnv", "dist", "build", ".next", ".nuxt", ".cache",
})

REQ_NAME = re.compile(r"^requirements[\w.-]*\.txt$", re.I)
REQ_PIN = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*==\s*([^\s;#,]+)")
_REQ_ANY = re.compile(r"^\s*[A-Za-z0-9][A-Za-z0-9._-]*")
LOCKFILES = ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "composer.lock",
             "poetry.lock", "uv.lock", "Pipfile.lock")


@dataclass(frozen=True)
class Dep:
    """One exact dependency version found in a manifest or on a host."""

    ecosystem: str          # "PyPI" | "npm" | "Packagist"
    name: str
    version: str
    scope: str = "runtime"  # "runtime" | "dev"
    source: str = ""        # manifest path or install location


@dataclass
class Manifest:
    """Parse result for one manifest file."""

    path: Path
    deps: list[Dep] = field(default_factory=list)
    unscannable: str = ""   # reason, when no exact versions could be read
    unpinned: int = 0       # requirement lines without an exact pin


def is_linked_worktree(directory: Path) -> bool:
    """Return True when ``directory`` is a linked git worktree (not a main checkout or submodule).

    Args:
        directory: Directory to test.

    Returns:
        Whether its ``.git`` is a file pointing into another repository's ``worktrees/``.
    """
    marker = directory / ".git"
    if not marker.is_file():
        return False
    try:
        text = marker.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return text.startswith("gitdir:") and "/worktrees/" in text.replace("\\", "/")


def discover(root: Path, *, include_worktrees: bool = False) -> list[Path]:
    """List manifest files under ``root``.

    Args:
        root: A repository or a parent directory of repositories.
        include_worktrees: Also descend into linked git worktrees.

    Returns:
        Sorted manifest paths. ``package.json`` and ``pyproject.toml`` are listed only when
        their directory has no lockfile, so they can be reported as unscannable.
    """
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        keep = []
        for d in dirnames:
            if d in SKIP_DIRS or d.endswith(".egg-info"):
                continue
            if not include_worktrees and is_linked_worktree(here / d):
                continue
            keep.append(d)
        dirnames[:] = sorted(keep)
        names = set(filenames)
        for name in sorted(names):
            if name in LOCKFILES or REQ_NAME.match(name):
                found.append(here / name)
        has_lock = bool(names & set(LOCKFILES))
        if "pyproject.toml" in names and not has_lock and not any(REQ_NAME.match(n) for n in names):
            found.append(here / "pyproject.toml")
        if "package.json" in names and not has_lock:
            found.append(here / "package.json")
        if "pnpm-lock.yaml" in names:
            found.append(here / "pnpm-lock.yaml")
    return sorted(set(found))


def parse(path: Path) -> Manifest:
    """Read exact dependency versions from one manifest.

    Args:
        path: Manifest file.

    Returns:
        A :class:`Manifest`. A file that cannot be read or decoded is returned with
        ``unscannable`` set; this function does not raise for bad input.
    """
    name = path.name
    m = Manifest(path=path)
    try:
        if REQ_NAME.match(name):
            _requirements(path, m)
        elif name in ("package-lock.json", "npm-shrinkwrap.json"):
            _npm_lock(path, m)
        elif name == "yarn.lock":
            _yarn_lock(path, m)
        elif name == "composer.lock":
            _composer_lock(path, m)
        elif name in ("poetry.lock", "uv.lock"):
            _toml_lock(path, m)
        elif name == "Pipfile.lock":
            _pipfile_lock(path, m)
        elif name == "pnpm-lock.yaml":
            m.unscannable = "pnpm-lock.yaml is not parsed yet — run `pnpm audit` here"
        elif name == "pyproject.toml":
            m.unscannable = "pyproject.toml without a lockfile — pin with uv/poetry lock or requirements"
        elif name == "package.json":
            m.unscannable = "package.json without a lockfile — run `npm install --package-lock-only`"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, tomllib.TOMLDecodeError,
            KeyError, TypeError, AttributeError) as exc:
        m.deps.clear()
        m.unscannable = f"could not parse: {type(exc).__name__}: {exc}"
    if not m.deps and not m.unscannable:
        m.unscannable = ("no exact `==` pins" if m.unpinned else "no dependencies listed")
    return m


def _requirements(path: Path, m: Manifest) -> None:
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith(("#", "-", "git+", "http:", "https:")):
            continue
        hit = REQ_PIN.match(line)
        if hit:
            m.deps.append(Dep("PyPI", hit.group(1), hit.group(3), "runtime", str(path)))
        elif _REQ_ANY.match(line):
            m.unpinned += 1
    if "dev" in path.name.lower() or "test" in path.name.lower():
        m.deps[:] = [Dep(d.ecosystem, d.name, d.version, "dev", d.source) for d in m.deps]


def _npm_lock(path: Path, m: Manifest) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    packages = data.get("packages")
    if isinstance(packages, dict):  # lockfileVersion 2/3
        for key, info in packages.items():
            if not key or "version" not in info or info.get("link"):
                continue
            name = info.get("name") or key.rsplit("node_modules/", 1)[-1]
            scope = "dev" if info.get("dev") or info.get("devOptional") else "runtime"
            m.deps.append(Dep("npm", name, info["version"], scope, str(path)))
        return

    def walk(deps: dict) -> None:  # lockfileVersion 1
        for name, info in (deps or {}).items():
            if "version" in info and not str(info["version"]).startswith(("file:", "link:", "git")):
                m.deps.append(Dep("npm", name, info["version"], "dev" if info.get("dev") else "runtime", str(path)))
            walk(info.get("dependencies") or {})

    walk(data.get("dependencies") or {})


_YARN_HEAD = re.compile(r'^"?(@?[^@"\s]+)@')
_YARN_VER = re.compile(r'^\s+version:?\s+"?([^"\s]+)"?')


def _yarn_lock(path: Path, m: Manifest) -> None:
    current: str | None = None
    seen: set[tuple[str, str]] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line and not line.startswith((" ", "#")) and line.rstrip().endswith(":"):
            head = _YARN_HEAD.match(line.split(",")[0].strip())
            current = head.group(1) if head else None
            continue
        ver = _YARN_VER.match(line)
        if current and ver and (current, ver.group(1)) not in seen:
            seen.add((current, ver.group(1)))
            if current != "__metadata":
                m.deps.append(Dep("npm", current, ver.group(1), "runtime", str(path)))
            current = None


def _composer_lock(path: Path, m: Manifest) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    for key, scope in (("packages", "runtime"), ("packages-dev", "dev")):
        for pkg in data.get(key) or []:
            m.deps.append(Dep("Packagist", pkg["name"], str(pkg["version"]).lstrip("v"), scope, str(path)))


def _toml_lock(path: Path, m: Manifest) -> None:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    for pkg in data.get("package") or []:
        source = pkg.get("source") or {}
        if isinstance(source, dict) and (source.get("editable") or source.get("virtual")):
            continue  # the project itself, not a dependency
        scope = "dev" if pkg.get("category") == "dev" else "runtime"
        m.deps.append(Dep("PyPI", pkg["name"], str(pkg["version"]), scope, str(path)))


def _pipfile_lock(path: Path, m: Manifest) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    for key, scope in (("default", "runtime"), ("develop", "dev")):
        for name, info in (data.get(key) or {}).items():
            ver = str(info.get("version", ""))
            if ver.startswith("=="):
                m.deps.append(Dep("PyPI", name, ver[2:], scope, str(path)))
