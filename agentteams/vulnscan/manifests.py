"""Find dependency manifests under a directory and read exact versions from them.

Supported formats:

| Ecosystem | Files |
|---|---|
| PyPI | ``requirements*.txt``, ``requirements*.lock`` and ``requirements*.in``, also with a ``-``/``_``/``.``-ended prefix such as ``ci-requirements.txt`` (``==`` pins), ``poetry.lock``, ``uv.lock``, ``Pipfile.lock``; a ``pyproject.toml`` next to requirements files (PEP 621 ``==`` pins) |
| npm | ``package-lock.json`` and ``npm-shrinkwrap.json`` (v1–v3), ``yarn.lock`` (classic and berry) |
| Packagist | ``composer.lock`` |
| crates.io | ``Cargo.lock`` (v1–v4; crates from the crates.io index only, every one ``runtime``) |
| Go | ``go.mod`` (``require``, with ``replace`` applied; :mod:`~agentteams.vulnscan.lockfiles`) |
| npm | ``pnpm-lock.yaml`` (v6, v9), ``bun.lock`` (text) |
| RubyGems | ``Gemfile.lock`` (rubygems.org gems) |
| Maven | ``gradle.lockfile`` (``group:artifact``) |
| NuGet | ``packages.lock.json`` |

A manifest that yields no exact versions is reported as *unscannable*, not skipped
silently. Examples are an unpinned ``requirements.txt`` and a ``pyproject.toml`` with no
lockfile. Manifests of ecosystems the scanner does not read are discovered too and reported as
unscannable with the reason ``unsupported ecosystem: <name>`` (:func:`unsupported_ecosystem`), so a
workspace that holds only those is in the coverage state ``unsupported ecosystem``, never mistaken for
one with nothing to scan:

| Ecosystem | Files |
|---|---|
| Go | ``go.sum`` when there is no ``go.mod`` |
| Maven | ``pom.xml`` |
| Gradle | ``build.gradle`` / ``build.gradle.kts`` when there is no ``gradle.lockfile`` |
| NuGet | ``*.csproj`` when there is no ``packages.lock.json`` |
| conda, Lean | ``environment.yml`` / ``.yaml``, ``lake-manifest.json`` |
| SwiftPM, CocoaPods, Pub, Hex | ``Package.resolved``, ``Podfile.lock``, ``pubspec.lock``, ``mix.lock`` |
| Bun, Deno (npm packages) | ``bun.lock``, ``bun.lockb`` (binary), ``deno.lock`` |
| CRAN, Hackage | ``renv.lock``, ``stack.yaml.lock`` / ``cabal.project.freeze`` |
| Julia | ``Manifest.toml`` (only next to a ``Project.toml``) |
| RubyGems | ``Gemfile`` when there is no ``Gemfile.lock`` |
| Conan, vcpkg (C/C++) | ``conan.lock`` (``conanfile.txt`` / ``conanfile.py`` when there is no ``conan.lock``), ``vcpkg.json`` |
| container images | ``Dockerfile``, ``Dockerfile.*``, ``*.Dockerfile``, ``Containerfile``, ``compose.y(a)ml``, ``docker-compose*.y(a)ml`` |

``package.json``, ``composer.json`` and ``Cargo.toml`` without their lockfile are listed and reported unscannable
("without a lockfile", with the command that writes one), not as an unsupported ecosystem.

Only ``requirements*.txt`` (:data:`REQ_NAME`) is ever edited by a staged fix; the ``.lock``
and ``.in`` variants are read, never edited (:data:`REQ_SCAN_NAME`).

Discovery skips vendored and generated trees (``node_modules``, virtualenvs, build
output, Lean's ``.lake`` package cache). It also skips **linked git worktrees** by default, because they repeat their
main checkout's manifests.

:func:`check_pin_edit` and :func:`check_lock_edit` decide which edits of these files the
executor's ``validate_diff`` lets a staged fix make.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentteams.vulnscan import lockfiles
from agentteams.vulnscan.versions import is_final, version_key

SKIP_DIRS: frozenset[str] = frozenset({
    "node_modules", ".git", ".hg", ".svn", "__pycache__", ".tox", ".nox", ".mypy_cache",
    ".pytest_cache", "site-packages", "dist-packages", "bower_components", "vendor",
    ".venv", "venv", "env", ".direnv", "dist", "build", ".next", ".nuxt", ".cache",
    # Scratch space: a repository's "tmp" folder often holds throwaway clones of sibling repositories; scanning
    # them reports the same findings twice and invites duplicate fixes.
    "tmp",
    # Lean's Lake package cache (``.lake/packages``), vendored like node_modules. Skipping it also hides lockfiles
    # *inside* vendored Lean packages: their own tooling, never installed by the project.
    ".lake",
})

#: Requirements files a staged fix may edit (``executor.validate_diff`` checks them with :func:`check_pin_edit`):
#: ``requirements*.txt``, also with a prefix ending in ``-``, ``_`` or ``.`` (``ci-requirements.txt``,
#: ``dev_requirements.txt``), which a name test anchored at ``requirements`` used to skip silently.
REQ_NAME = re.compile(r"^([\w.-]*[-_.])?requirements[\w.-]*\.txt$", re.I)
#: Requirements files the scanner reads: :data:`REQ_NAME` plus pip-compile style ``.lock`` and ``.in`` files.
REQ_SCAN_NAME = re.compile(r"^([\w.-]*[-_.])?requirements[\w.-]*\.(txt|lock|in)$", re.I)
REQ_PIN = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*==\s*([^\s;#,]+)")
_REQ_ANY = re.compile(r"^\s*[A-Za-z0-9][A-Za-z0-9._-]*")
LOCKFILES = ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "composer.lock",
             "poetry.lock", "uv.lock", "Pipfile.lock")
#: Lockfiles discovered apart from :data:`LOCKFILES`: they do not hide a sibling ``package.json`` or
#: ``pyproject.toml`` (a wasm-pack or maturin directory holds both, and its npm/PyPI packages stay reported).
CRATE_LOCKS = ("Cargo.lock",)
#: Lockfiles whose fixes are plan-only (``stage-fix`` never edits them): ``Cargo.lock`` and every
#: :data:`lockfiles.FORMATS` file. Like :data:`CRATE_LOCKS` they are not in :data:`LOCKFILES`.
PLAN_ONLY_LOCKS: tuple[str, ...] = (*CRATE_LOCKS, *lockfiles.FORMATS)
#: Lockfiles beside a ``package.json`` that pin its packages (besides :data:`LOCKFILES`); ``bun.lockb`` is binary and
#: never read, so it does not count.
NPM_SIDE_LOCKS = ("pnpm-lock.yaml", "bun.lock")
#: ``source`` prefixes of crates.io packages in a ``Cargo.lock`` (git index, sparse index).
CRATES_IO_SOURCES = ("registry+https://github.com/rust-lang/crates.io-index", "sparse+https://index.crates.io/")
#: What a crates.io crate name and a Cargo.lock version may be (crates.io allows ASCII letters, digits, ``-`` and
#: ``_``, at most 64; versions are SemVer). Anything else makes the lockfile unscannable (:func:`_cargo_lock`).
CRATE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
CRATE_VERSION = re.compile(r"[0-9][0-9A-Za-z.+-]{0,63}")
#: Manifests of ecosystems that are not scanned (file name -> ecosystem): discovered, reported unscannable.
UNSUPPORTED: dict[str, str] = {
    "environment.yml": "conda",
    "environment.yaml": "conda",
    "lake-manifest.json": "Lean",
    "pom.xml": "Maven",
    "Containerfile": "container images",
    "compose.yml": "container images",
    "compose.yaml": "container images",
    "Package.resolved": "SwiftPM",
    "Podfile.lock": "CocoaPods",
    "pubspec.lock": "Pub",
    "mix.lock": "Hex",
    "bun.lockb": "Bun binary lockfile (npm packages)",
    "deno.lock": "Deno (npm/JSR)",
    "renv.lock": "CRAN",
    "stack.yaml.lock": "Hackage",
    "cabal.project.freeze": "Hackage",
    "conan.lock": "Conan (C/C++)",
    "vcpkg.json": "vcpkg (C/C++)",
}
#: Listed only when the directory has no lockfile of the same ecosystem (it would report the same packages).
_UNSUPPORTED_FALLBACK: dict[str, tuple[str, str]] = {
    "go.sum": ("Go (go.sum without go.mod)", "go.mod"),
    "build.gradle": ("Gradle", "gradle.lockfile"),
    "build.gradle.kts": ("Gradle", "gradle.lockfile"),
    "Gemfile": ("RubyGems", "Gemfile.lock"),
    "conanfile.txt": ("Conan (C/C++)", "conan.lock"),
    "conanfile.py": ("Conan (C/C++)", "conan.lock"),
}
#: Listed only when the directory **has** the sibling named (the file name alone is too generic to claim):
#: Julia's ``Manifest.toml`` next to its ``Project.toml``. :func:`unsupported_ecosystem` is name-only, so it labels
#: the name; :func:`discover` alone applies the sibling test.
_UNSUPPORTED_WITH_SIBLING: dict[str, tuple[str, str]] = {
    "Manifest.toml": ("Julia", "Project.toml"),
}
#: Manifests of scanned ecosystems that hold no exact versions themselves: listed (and reported unscannable with the
#: hint shown) only when no lockfile of theirs is beside them, or for ``Cargo.toml`` in a directory above them (a
#: Cargo workspace keeps one ``Cargo.lock`` at its root).
_NEEDS_LOCK: dict[str, tuple[str, str]] = {
    "composer.json": ("composer.lock", "composer.json without a lockfile — run `composer update --lock`"),
    "Cargo.toml": ("Cargo.lock", "Cargo.toml without a lockfile — run `cargo generate-lockfile`"),
}
_DOCKERFILE = re.compile(r"^(Dockerfile([.-][\w.-]+)?|[\w.-]+\.Dockerfile|docker-compose[\w.-]*\.ya?ml)$")
_CSPROJ = re.compile(r"^[\w.-]+\.csproj$")
#: Prefix of the unscannable reason of every unsupported-ecosystem manifest (coverage keys on it).
UNSUPPORTED_REASON = "unsupported ecosystem: "
_DEV_GROUP = re.compile(r"dev|test|lint|doc|type", re.I)
REGISTRY_HOSTS = frozenset({"registry.npmjs.org", "registry.yarnpkg.com"})
_LOCK_ROOT_KEYS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies", "scripts",
                   "workspaces")


@dataclass(frozen=True)
class Dep:
    """One exact dependency version found in a manifest or on a host."""

    ecosystem: str          # "PyPI" | "npm" | "Packagist" | "crates.io" | "Go" | "RubyGems" | "Maven" | "NuGet"
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
    covered: bool = False   # nothing of its own to report: requirements files alongside carry its pins


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


def unsupported_ecosystem(name: str) -> str:
    """Return the ecosystem of a manifest the scanner does not read, by file name.

    Args:
        name: File name.

    Returns:
        The ecosystem (``Go``, ``Maven``, ``container images``, ...), or "" for a scanned format.
    """
    if name in UNSUPPORTED:
        return UNSUPPORTED[name]
    if name in _UNSUPPORTED_FALLBACK:
        return _UNSUPPORTED_FALLBACK[name][0]
    if name in _UNSUPPORTED_WITH_SIBLING:
        return _UNSUPPORTED_WITH_SIBLING[name][0]
    if _CSPROJ.match(name):
        return "NuGet"
    if _DOCKERFILE.match(name):
        return "container images"
    return ""


def discover(root: Path, *, include_worktrees: bool = False) -> list[Path]:
    """List manifest files under ``root``.

    Args:
        root: A repository or a parent directory of repositories.
        include_worktrees: Also descend into linked git worktrees.

    Returns:
        Sorted manifest paths. ``package.json`` and ``pyproject.toml`` are listed only when
        their directory has no npm, Composer or PyPI lockfile (``pnpm-lock.yaml`` and ``bun.lock`` count for
        ``package.json`` only; a ``Cargo.lock`` does not count; a
        ``pyproject.toml`` next to requirements files is listed too: :func:`parse` reads its PEP 621 pins). Manifests of unsupported ecosystems
        (:func:`unsupported_ecosystem`) are listed so they can be reported as unscannable;
        ``go.sum`` (without a ``go.mod``), ``build.gradle*``, ``Gemfile``, ``conanfile.*`` and ``*.csproj`` only when their directory has no lockfile
        of the same ecosystem, ``Manifest.toml`` only next to a ``Project.toml`` (Julia). ``composer.json`` and
        ``Cargo.toml`` (:data:`_NEEDS_LOCK`) are listed only without their lockfile (for ``Cargo.toml``, none in
        its directory or one above it under ``root``).
    """
    found: list[Path] = []
    cargo_dirs: set[Path] = set()   # directories holding a Cargo.lock (os.walk is top-down: ancestors come first)
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
            if name in LOCKFILES or name in PLAN_ONLY_LOCKS or name in UNSUPPORTED or REQ_SCAN_NAME.match(name) \
                    or _DOCKERFILE.match(name):
                found.append(here / name)
            elif name in _UNSUPPORTED_FALLBACK and _UNSUPPORTED_FALLBACK[name][1] not in names:
                found.append(here / name)
            elif name in _UNSUPPORTED_WITH_SIBLING and _UNSUPPORTED_WITH_SIBLING[name][1] in names:
                found.append(here / name)
            elif _CSPROJ.match(name) and "packages.lock.json" not in names:
                found.append(here / name)
        if "Cargo.lock" in names:
            cargo_dirs.add(here)
        for name, (lock, _hint) in _NEEDS_LOCK.items():
            if name not in names or lock in names:
                continue
            if name == "Cargo.toml" and any(d in cargo_dirs for d in here.parents):
                continue   # a workspace member: the workspace root's Cargo.lock pins it
            found.append(here / name)
        has_lock = bool(names & set(LOCKFILES))
        if "pyproject.toml" in names and not has_lock:
            found.append(here / "pyproject.toml")
        if "package.json" in names and not has_lock and not names & set(NPM_SIDE_LOCKS):
            found.append(here / "package.json")
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
        if REQ_SCAN_NAME.match(name):
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
        elif name in CRATE_LOCKS:
            _cargo_lock(path, m)
        elif name in lockfiles.FORMATS:
            got = lockfiles.FORMATS[name].reader(path)
            m.deps += [Dep(eco, n, v, scope, str(path)) for eco, n, v, scope in got.deps]
            m.unscannable = "; ".join(got.notes)   # a partial read: the deps are scanned, the notes reported
        elif name == "pyproject.toml":
            _pyproject(path, m)
        elif name == "package.json":
            m.unscannable = "package.json without a lockfile — run `npm install --package-lock-only`"
        elif name in _NEEDS_LOCK:
            m.unscannable = _NEEDS_LOCK[name][1]
        elif eco := unsupported_ecosystem(name):
            m.unscannable = f"{UNSUPPORTED_REASON}{eco}"
    except lockfiles.Unreadable as exc:
        m.deps.clear()
        m.unscannable = str(exc)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, tomllib.TOMLDecodeError,
            KeyError, TypeError, AttributeError) as exc:
        m.deps.clear()
        m.unscannable = f"could not parse: {type(exc).__name__}: {exc}"
    if not m.deps and not m.unscannable and not m.covered:
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


def _pep503(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pyproject(path: Path, m: Manifest) -> None:
    """Read a ``pyproject.toml``: alone it is unscannable; next to requirements files, its PEP 621 ``==`` pins.

    ``[project].dependencies`` are runtime; ``[project.optional-dependencies]`` groups named like dev, test,
    lint, doc or type are dev, the rest runtime. A pin a requirements file alongside already lists (same
    PEP 503 name and version) is not counted twice. A dependency with no exact pin that no requirements file
    alongside pins makes the file *unscannable* for that part (its pins are still scanned).
    """
    siblings = sorted(p for p in path.parent.iterdir() if REQ_SCAN_NAME.match(p.name) and p.is_file())
    if not siblings:
        m.unscannable = "pyproject.toml without a lockfile — pin with uv/poetry lock or requirements"
        return
    pinned = {(_pep503(d.name), d.version) for s in siblings for d in parse(s).deps}
    pinned_names = {n for n, _v in pinned}
    project = tomllib.loads(path.read_text(encoding="utf-8")).get("project") or {}
    specs = [(s, "runtime") for s in project.get("dependencies") or []]
    for group, items in (project.get("optional-dependencies") or {}).items():
        specs += [(s, "dev" if _DEV_GROUP.search(group) else "runtime") for s in items or []]
    seen = set(pinned)
    for spec, scope in specs:
        line = spec.strip()   # a non-string entry raises AttributeError: parse reports it as unscannable
        hit = REQ_PIN.match(line)
        version = hit.group(3) if hit else ""
        if hit and not version.startswith("=") and "*" not in version:
            key = (_pep503(hit.group(1)), version)
            if key not in seen:
                seen.add(key)
                m.deps.append(Dep("PyPI", hit.group(1), version, scope, str(path)))
        elif (name := _REQ_ANY.match(line)) and _pep503(name.group(0).strip()) not in pinned_names:
            m.unpinned += 1
    if m.unpinned:
        m.unscannable = (f"{m.unpinned} [project] dependency(ies) without an exact `==` pin, not pinned in a "
                         "requirements file alongside")
    elif not m.deps:
        m.covered = True   # empty, or every dependency is pinned by the requirements files alongside


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


def _cargo_lock(path: Path, m: Manifest) -> None:
    """Read a ``Cargo.lock`` (v1–v4): every ``[[package]]`` from the crates.io index, as ``runtime``.

    Path and git packages (the workspace's own crates, or sources OSV does not know) and alternate registries are
    not crates.io packages and are skipped. Cargo.lock records no dev/build distinction, so every crate is
    ``runtime``: it over-reports, never hides.

    The lockfile is repository content: a crates.io entry whose name or version is not a plain string of crate-name
    / version characters makes the whole file unscannable (reported, never silently dropped), so no such text
    reaches a fix plan the agents read.
    """
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    for pkg in data.get("package") or []:
        if not isinstance(pkg, dict):
            raise TypeError("a [[package]] entry is not a table")
        source = pkg.get("source")
        if not (isinstance(source, str) and source.startswith(CRATES_IO_SOURCES)):
            continue
        name, version = pkg.get("name"), pkg.get("version")
        if not (isinstance(name, str) and isinstance(version, str)
                and CRATE_NAME.fullmatch(name) and CRATE_VERSION.fullmatch(version)):
            m.deps.clear()
            m.unscannable = "a crates.io entry with an unusual name or version: not read"
            return
        m.deps.append(Dep("crates.io", name, version, "runtime", str(path)))
    if not m.deps:
        m.unscannable = "no crates.io packages"


# ---------------------------------------------------------------- edit checks (executor.validate_diff)

def check_pin_edit(rel: str, old: bytes, new: bytes) -> None:
    """Refuse a requirements edit that is anything but raising ``==`` versions in place (B1, X1).

    Every byte outside a changed pin's version span must be unchanged: name, extras, marker,
    options, comment and line ending. The new version must be a final release above the old one
    (:mod:`agentteams.vulnscan.versions` ordering). A file holding ``--hash`` options is never
    edited, because the edit would leave a stale hash behind.

    Args:
        rel: Path for messages.
        old: File bytes at the base commit (``b""`` for a new file).
        new: File bytes now.

    Raises:
        ValueError: The edit is anything else.
    """
    if b"--hash" in old or b"--hash" in new:
        raise ValueError(f"{rel}: hash-pinned (--hash) requirements are never edited")
    a, b = old.splitlines(keepends=True), new.splitlines(keepends=True)
    if len(a) != len(b):
        raise ValueError(f"{rel}: line count changed")
    for x, y in zip(a, b):
        if x == y:
            continue
        sx, sy = x.decode("utf-8", "surrogateescape"), y.decode("utf-8", "surrogateescape")
        mx, my = REQ_PIN.match(sx), REQ_PIN.match(sy)
        if not (mx and my):
            raise ValueError(f"{rel}: a non-pin line changed")
        if sx[:mx.start(3)] != sy[:my.start(3)] or sx[mx.end(3):] != sy[my.end(3):]:
            raise ValueError(f"{rel}: {mx.group(1)}: a pin may change only its version")
        if not is_final(my.group(3)) or version_key(my.group(3)) <= version_key(mx.group(3)):
            raise ValueError(f"{rel}: {mx.group(1)} {mx.group(3)} -> {my.group(3)} is not an upgrade "
                             "to a final release")


def lock_entries(rel: str, text: str) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]]]:
    """Parse an npm lockfile type-safely (D3) and list every package entry it pins.

    Entries come from ``packages`` (v2/v3) and, recursively, ``dependencies`` (v1, and v2's legacy
    copy that npm 6 reads), so a ``resolved`` URL cannot hide in either.

    Args:
        rel: Path for messages.
        text: Lockfile text.

    Returns:
        (the root package entry ``packages[""]`` or ``{}``, [(name, entry), ...]).

    Raises:
        ValueError: Not JSON, or a level that must be an object is not one.
    """
    try:
        lock = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{rel}: not valid JSON: {exc}") from exc
    if not isinstance(lock, dict):
        raise ValueError(f"{rel}: malformed lockfile: the top level is not an object")
    entries: list[tuple[str, dict[str, Any]]] = []
    todo: list[tuple[str, str, Any]] = [("packages", "", lock.get("packages")),
                                        ("dependencies", "", lock.get("dependencies"))]
    while todo:
        section, prefix, sec = todo.pop()
        if sec is None:
            continue
        if not isinstance(sec, dict):
            raise ValueError(f"{rel}: malformed lockfile: {prefix or section} is not an object")
        for name, info in sec.items():
            if not isinstance(info, dict):
                raise ValueError(f"{rel}: malformed lockfile: {section} entry {prefix}{name!r} is not an object")
            entries.append((f"{prefix}{name}", info))
            if section == "dependencies":
                todo.append((section, f"{prefix}{name} > ", info.get("dependencies")))
    root = next((info for name, info in entries if name == ""), {})
    return root, entries


def _entry_key(name: str, info: dict[str, Any]) -> str:
    """Return a comparable form of one lockfile entry (its nested v1 ``dependencies`` are entries of their own)."""
    return name + "\0" + json.dumps({k: v for k, v in info.items() if k != "dependencies"}, sort_keys=True)


def _local_path(text: str) -> bool:
    """Return whether ``text`` is a relative path that stays inside the repository."""
    parts = text.replace("\\", "/").split("/")
    return bool(text) and not text.startswith(("/", "\\", "~")) and ":" not in text and ".." not in parts


def _kept_local(name: str, info: dict[str, Any], before: set[str]) -> bool:
    """Workspace policy (X4): a local link or ``file:`` entry passes only unchanged from the base and inside the repo.

    Such an entry fetches nothing; requiring it unchanged means the fix never introduced or retargeted it.
    """
    url, version = info.get("resolved"), info.get("version")
    if info.get("link") is True and isinstance(url, str):
        path = url
    elif not url and isinstance(version, str) and version.startswith("file:"):
        path = version[len("file:"):]
    else:
        return False
    return _local_path(path) and _entry_key(name, info) in before


def check_lock_edit(rel: str, old: str, new: str) -> None:
    """Refuse root-package edits and any entry that would fetch from off the registry allowlist (B1, A4, X4).

    Every entry must resolve to ``https://`` on :data:`REGISTRY_HOSTS`, or have no ``resolved`` and a
    plain version (a URL or git spec in ``version`` with no ``resolved`` is refused). The one exception
    is a workspace link or ``file:`` entry kept unchanged from the base lockfile, inside the repository.

    Args:
        rel: Path for messages.
        old: Lockfile text at the base commit (``"{}"`` for a new file).
        new: Lockfile text now.

    Raises:
        ValueError: Any of these is found, or a lockfile is malformed.
    """
    root_before, before = lock_entries(rel, old)
    root_after, entries = lock_entries(rel, new)
    for key in _LOCK_ROOT_KEYS:
        if root_before.get(key) != root_after.get(key):
            raise ValueError(f"{rel}: root package {key} changed")
    kept = {_entry_key(name, info) for name, info in before}
    for name, info in entries:
        if _kept_local(name, info, kept):
            continue
        url, version = info.get("resolved"), info.get("version")
        if url is None or url == "":
            if isinstance(version, str) and (":" in version or "/" in version):
                raise ValueError(f"{rel}: {name or '(root)'} has no resolved URL and its version is not a "
                                 f"registry version: {version[:120]}")
            continue
        host = re.sub(r"^https://([^/]+)/.*$", r"\1", url) if isinstance(url, str) else ""
        if not isinstance(url, str) or not url.startswith("https://") or host not in REGISTRY_HOSTS:
            raise ValueError(f"{rel}: {name or '(root)'} resolves outside the registry allowlist: {str(url)[:120]}")
