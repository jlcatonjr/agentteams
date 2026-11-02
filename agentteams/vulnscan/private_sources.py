"""Private package sources a repository's own tooling declares, so their names never go to OSV (security review N1/N7).

A brief's ``private_packages`` names what must stay local; these signals come from the tooling itself and need no brief:

- **Go:** a module matching ``GOPRIVATE``, ``GONOPROXY`` or ``GONOSUMDB`` (the environment, then ``go env`` when a
  ``go`` binary exists: run from ``/`` with ``GOTOOLCHAIN=local`` and no ``GOFLAGS``, so it never fetches a
  toolchain), matched with Go's own prefix-glob rule (``module.MatchPrefixPatterns``);
- **NuGet:** a ``nuget.config`` in the repository, or a parent directory within the scan root, naming any package
  source other than nuget.org withholds every NuGet package of that repository; an unreadable one counts as naming
  a private source;
- **pnpm / Bun:** an ``.npmrc`` (same places) whose ``@scope:registry=`` is not registry.npmjs.org withholds that
  scope; a ``registry=`` other than it withholds every name those lockfiles list;
- **Gradle:** no such signal exists, so ``gradle.lockfile`` is read only on an opt-in (:data:`GRADLE_OPT_IN`).

Reading is local and bounded (:data:`MAX_BYTES`); nothing here contacts a network.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

#: Largest ``nuget.config`` / ``.npmrc`` read; a larger one counts as naming a private source.
MAX_BYTES = 1024 * 1024
#: The Go settings whose patterns mark a module private (``GOPRIVATE`` is the default of the other two).
GO_PRIVATE_VARS = ("GOPRIVATE", "GONOPROXY", "GONOSUMDB")
#: Seconds ``go env`` may run.
GO_ENV_TIMEOUT = 10
#: Hosts of the public NuGet gallery.
NUGET_PUBLIC = frozenset({"api.nuget.org", "www.nuget.org", "nuget.org"})
#: The public npm registry host.
NPM_PUBLIC = "registry.npmjs.org"
#: The lockfiles whose npm names follow ``.npmrc`` scope registries.
NPMRC_LOCKS = ("pnpm-lock.yaml", "bun.lock")

#: Why a ``gradle.lockfile`` is not read by default: no reliable signal tells a private Maven repository from Maven
#: Central, so its ``group:artifact`` names would go to OSV unchecked.
GRADLE_OPT_IN = ("gradle.lockfile not read (opt-in: `scan --gradle-lockfiles` / scan_path(gradle_lockfiles=True); "
                 "a private Maven repository cannot be told apart, so its names would go to OSV)")

_NPMRC_LINE = re.compile(r"^\s*(@[A-Za-z0-9._~-]+:)?registry\s*=\s*(\S+)\s*$")


def _glob_regex(glob: str) -> re.Pattern[str]:
    """Translate one Go ``path.Match`` pattern: ``*`` and ``?`` never match ``/``; ``[...]`` is a class."""
    out, i = [], 0
    while i < len(glob):
        c = glob[i]
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "\\" and i + 1 < len(glob):
            i += 1
            out.append(re.escape(glob[i]))
        elif c == "[":
            j = glob.find("]", i + 2)
            if j < 0:
                raise ValueError("unterminated class")
            body = glob[i + 1:j]
            neg = body[:1] in ("^", "!")
            body = body[1:] if neg else body
            out.append("[" + ("^/" if neg else "") + body.replace("\\", "\\\\") + "]")
            i = j
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out))


def go_private_match(globs: str, module: str) -> bool:
    """Return whether ``module`` matches a comma-separated ``GOPRIVATE``-style pattern list.

    Go's rule: each pattern with ``n`` slashes is matched against the module path's first ``n + 1`` elements, so
    ``example.com/team`` covers ``example.com/team/repo/v2``. A malformed pattern counts as matching (withheld).

    Args:
        globs: The comma-separated patterns.
        module: The module path.

    Returns:
        True when a pattern matches.
    """
    for glob in globs.split(","):
        glob = glob.strip().rstrip("/")
        if not glob:
            continue
        n = glob.count("/")
        parts = module.split("/")
        if len(parts) < n + 1:
            continue
        try:
            if _glob_regex(glob).fullmatch("/".join(parts[:n + 1])):
                return True
        except (ValueError, re.error):
            return True
    return False


def go_private_patterns(environ: dict[str, str] | None = None) -> str:
    """Return the ``GOPRIVATE`` / ``GONOPROXY`` / ``GONOSUMDB`` patterns of this machine, comma-joined.

    The environment is read first; ``go env`` adds the values set with ``go env -w`` when a ``go`` binary exists.
    A ``go env`` that fails adds nothing (the environment's values still apply).

    Args:
        environ: The environment (tests); defaults to ``os.environ``.

    Returns:
        The patterns ("" for none).
    """
    env = dict(os.environ if environ is None else environ)
    vals = [env.get(k, "") for k in GO_PRIVATE_VARS]
    go = shutil.which("go", path=env.get("PATH"))
    if go:
        run_env = {"PATH": env.get("PATH", ""), "HOME": env.get("HOME", "/"), "GOTOOLCHAIN": "local", "GOFLAGS": ""}
        try:
            res = subprocess.run([go, "env", *GO_PRIVATE_VARS], capture_output=True, text=True, cwd="/",
                                 env=run_env, timeout=GO_ENV_TIMEOUT, check=False)
        except (OSError, subprocess.TimeoutExpired):
            res = None
        if res is not None and res.returncode == 0:
            vals += res.stdout.splitlines()
    return ",".join(v.strip() for v in vals if v.strip())


def _small_text(path: Path) -> str | None:
    """Return a config file's text, or None when it is too large or unreadable (the caller fails closed)."""
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        return path.read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def nuget_names_private_source(path: Path) -> bool:
    """Return whether a ``nuget.config`` names a package source other than nuget.org (unreadable: True).

    Args:
        path: The ``nuget.config``.

    Returns:
        True when it names a non-nuget.org source, or cannot be read.
    """
    text = _small_text(path)
    if text is None or "<!ENTITY" in text or "<!DOCTYPE" in text:
        return True
    try:
        tree = ET.fromstring(text)
    except ET.ParseError:
        return True
    for sources in tree.iter():
        if sources.tag.lower() != "packagesources":
            continue
        for add in sources:
            if add.tag.lower() == "add" and _host(add.get("value", "")) not in NUGET_PUBLIC:
                return True
    return False


def npmrc_private(path: Path) -> tuple[bool, set[str]]:
    """Read an ``.npmrc``: (default registry is not npmjs, scopes whose registry is not npmjs).

    Args:
        path: The ``.npmrc``.

    Returns:
        (all private, private scopes such as ``"@acme"``); an unreadable file makes every name private.
    """
    text = _small_text(path)
    if text is None:
        return True, set()
    every, scopes = False, set()
    for line in text.splitlines():
        m = _NPMRC_LINE.match(line)
        if not m or _host(m.group(2)) == NPM_PUBLIC:
            continue
        if m.group(1):
            scopes.add(m.group(1)[:-1].lower())
        else:
            every = True
    return every, scopes


@dataclass
class RepoSources:
    """What one repository's tooling marks private."""

    nuget_private: bool = False
    npm_all: bool = False
    npm_scopes: set[str] = field(default_factory=set)


def _config_dirs(repo: Path, root: Path) -> list[Path]:
    """The repository directory and its parents up to (and including) the scan root."""
    out = [repo]
    if repo != root and root in repo.parents:
        for d in repo.parents:
            out.append(d)
            if d == root:
                break
    return out


def repo_sources(repo: Path, root: Path) -> RepoSources:
    """Return what ``repo``'s ``nuget.config`` and ``.npmrc`` files (it and its parents within ``root``) mark private.

    A directory that cannot be listed marks every NuGet and pnpm/Bun name private (it may hold either file).

    Args:
        repo: The repository directory.
        root: The scan root.

    Returns:
        The private markers.
    """
    rs = RepoSources()
    for d in _config_dirs(repo, root):
        try:
            names = {p.name.lower(): p for p in d.iterdir() if p.is_file()}
        except OSError:   # a directory that cannot be listed may hold a config: fail closed, withhold both
            rs.nuget_private = rs.npm_all = True
            break
        if "nuget.config" in names and nuget_names_private_source(names["nuget.config"]):
            rs.nuget_private = True
        if ".npmrc" in names:
            every, scopes = npmrc_private(names[".npmrc"])
            rs.npm_all = rs.npm_all or every
            rs.npm_scopes |= scopes
    return rs


class PrivateSources:
    """Per scan: decide whether a dependency is private by its repository's tooling (read once per repository)."""

    def __init__(self, root: Path, go_patterns: str | None = None) -> None:
        """Start for one scan.

        Args:
            root: The scan root.
            go_patterns: The Go patterns (tests); read with :func:`go_private_patterns` on first Go module.
        """
        self.root = root
        self._go = go_patterns
        self._repos: dict[Path, RepoSources] = {}

    def is_private(self, repo: Path, manifest: str, ecosystem: str, name: str) -> bool:
        """Return whether ``name`` must not be sent to OSV.

        Args:
            repo: The repository directory.
            manifest: The manifest's file name (``go.mod``, ``pnpm-lock.yaml``, ...).
            ecosystem: The OSV ecosystem.
            name: The package name.

        Returns:
            True when the repository's tooling marks it private.
        """
        if ecosystem == "Go":
            if self._go is None:
                self._go = go_private_patterns()
            return bool(self._go) and go_private_match(self._go, name)
        if ecosystem == "NuGet":
            return self._repo(repo).nuget_private
        if ecosystem == "npm" and manifest in NPMRC_LOCKS:
            rs = self._repo(repo)
            return rs.npm_all or (name.startswith("@") and name.split("/")[0].lower() in rs.npm_scopes)
        return False

    def _repo(self, repo: Path) -> RepoSources:
        if repo not in self._repos:
            self._repos[repo] = repo_sources(repo, self.root)
        return self._repos[repo]
