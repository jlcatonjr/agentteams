"""Which packages ``stage-fix`` must update together when ``npm audit fix`` leaves an in-range advisory unfixed.

``npm audit fix`` without ``--force`` stops short in two cases a lockfile shows by itself (2026-11-01: vitest and
``@vitest/mocker`` in data-quality-monitor; ``qs`` under ``express`` in two repositories):

* **Exact peer groups.** ``@vitest/coverage-v8`` declares ``peerDependencies: {"vitest": "4.1.5"}`` and vitest
  declares the same back, so neither can move alone: the whole group moves in one ``npm update a b c``.
* **A pin held by a direct parent.** ``express 4.22.1`` depends on ``qs ~6.14.0``, so ``qs`` cannot reach the fixed
  ``6.16.x`` until ``express`` itself moves (to a newer release inside the range ``package.json`` declares).

:meth:`LockGraph.moves` reads a lockfile (v2/v3 ``packages``, :func:`load`) and returns the ``npm update`` name
sets for one vulnerable package version; :func:`.npm_ops.npm_moves` (called by ``fix.fix_path``) runs them
lockfile-only, never touching ``package.json``.
Groups are found from the lockfile's own ``peerDependencies`` (an exact version spec binds), never from a list of
known package names. The lockfile is repository content (C-4): a name that is not a plain npm package name is never
returned, so nothing read here can become an npm option. This module never runs npm or looks anything up; it imports
only :mod:`.manifests` (the lockfile's ``packages`` walk).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agentteams.vulnscan.manifests import npm_entry_name, npm_packages

#: A package name that may be passed to ``npm update`` (scoped or not; never starts with ``-`` or holds ``:``).
NPM_NAME = re.compile(r"(@[A-Za-z0-9][A-Za-z0-9._~-]{0,213}/)?[A-Za-z0-9][A-Za-z0-9._~-]{0,213}")
#: A peer spec that pins one exact version (``4.1.5``, ``=4.1.5``, ``4.1.5-beta.1``): such a peer cannot move alone.
_EXACT = re.compile(r"=?v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?")
#: Most packages one ``npm update`` names; a larger group is left alone (a person's change).
MAX_GROUP = 20
_DEP_KEYS = ("dependencies", "optionalDependencies")   # a peer binds through peer_group, not as a parent
_ROOT_KEYS = ("dependencies", "devDependencies", "optionalDependencies")


def load(path: Path) -> dict[str, dict[str, Any]]:
    """Return a lockfile's ``packages`` map (v2/v3), or ``{}`` when it is unreadable, v1 or malformed.

    Args:
        path: package-lock.json or npm-shrinkwrap.json.

    Returns:
        ``{key: entry}`` with only object entries kept (``""`` is the root package).
    """
    try:
        packages = npm_packages(json.loads(path.read_text(encoding="utf-8"))) or {}
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in packages.items() if isinstance(k, str) and isinstance(v, dict)}


def _deps(info: dict[str, Any], keys: tuple[str, ...]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for k in keys:
        sec = info.get(k)
        if isinstance(sec, dict):
            out += [(n, s) for n, s in sec.items() if isinstance(n, str) and isinstance(s, str)]
    return out


def _resolve(packages: dict[str, dict[str, Any]], key: str, dep: str) -> str | None:
    """Return the key node resolution finds for ``dep`` required from ``key`` (nearest ``node_modules`` up)."""
    base = key
    while True:
        cand = f"{base}/node_modules/{dep}" if base else f"node_modules/{dep}"
        if cand in packages and not packages[cand].get("link"):
            return cand
        if not base:
            return None
        cut = base.rfind("/node_modules/")
        base = base[:cut] if cut >= 0 else ""


class LockGraph:
    """The dependency and exact-peer relations of one lockfile, built once (:func:`load`'s map)."""

    def __init__(self, packages: dict[str, dict[str, Any]]) -> None:
        """Index ``packages``: who requires each entry (node resolution), which top-level entries peer-pin each other.

        Args:
            packages: :func:`load`'s map.
        """
        self.packages = packages
        self.users: dict[str, set[str]] = {}
        for key, info in packages.items():
            for dep, _spec in _deps(info, _ROOT_KEYS if key == "" else _DEP_KEYS):
                hit = _resolve(packages, key, dep)
                if hit is not None:
                    self.users.setdefault(hit, set()).add(key)
        top = {k[len("node_modules/"):]: v for k, v in packages.items()
               if k.startswith("node_modules/") and "/node_modules/" not in k[len("node_modules/"):]
               and not v.get("link")}
        self.peers: dict[str, set[str]] = {}
        for a, info in top.items():
            for b, spec in _deps(info, ("peerDependencies",)):
                if b in top and b != a and _EXACT.fullmatch(spec.strip()):
                    self.peers.setdefault(a, set()).add(b)
                    self.peers.setdefault(b, set()).add(a)

    def peer_group(self, name: str) -> list[str]:
        """Return the installed top-level packages bound to ``name`` by exact peer specs, ``name`` included, sorted.

        Two packages are bound when either declares the other in ``peerDependencies`` with an exact version and the
        other is installed at the top level; the relation is followed transitively.

        Args:
            name: A package installed at ``node_modules/<name>``.

        Returns:
            The group (``[name]`` alone when nothing binds it); ``[]`` when it exceeds :data:`MAX_GROUP` or a
            member's name is not a plain package name.
        """
        group, todo = {name}, [name]
        while todo:
            for nxt in self.peers.get(todo.pop(), ()):
                if nxt not in group:
                    group.add(nxt)
                    todo.append(nxt)
        if len(group) > MAX_GROUP or not all(NPM_NAME.fullmatch(n) for n in group):
            return []
        return sorted(group)

    def direct_parents(self, name: str, version: str) -> list[str]:
        """Return the root package's direct dependencies through which ``name@version`` is installed, sorted.

        Args:
            name: The vulnerable package.
            version: Its installed version.

        Returns:
            The direct dependencies (the root's ``dependencies``, ``devDependencies``, ``optionalDependencies``)
            whose dependency tree resolves to that copy, the package itself when it is direct; ``[]`` when none is.
        """
        targets = {k for k, v in self.packages.items()
                   if k and npm_entry_name(k, v) == name and v.get("version") == version}
        found: set[str] = set()
        seen, todo = set(targets), list(targets)
        while todo:
            key = todo.pop()
            for user in self.users.get(key, ()):
                if user == "":
                    found.add(npm_entry_name(key, self.packages[key]))
                elif user not in seen:
                    seen.add(user)
                    todo.append(user)
        return sorted(n for n in found if NPM_NAME.fullmatch(n))

    def moves(self, name: str, version: str) -> list[tuple[tuple[str, ...], str]]:
        """Return the ``npm update`` name sets that may fix ``name@version`` when ``npm audit fix`` did not, with why.

        A direct dependency bound by exact peers moves with its group. A transitive one moves through each direct
        parent (with that parent's peer group). A direct dependency with no exact peers gets no move: ``npm audit
        fix`` already had everything it needed.

        Args:
            name: The vulnerable package.
            version: Its installed version.

        Returns:
            ``[(sorted names, reason)]``, each name a plain package name, no set repeated.
        """
        out: list[tuple[tuple[str, ...], str]] = []
        for parent in sorted(self.direct_parents(name, version), key=lambda p: p != name):   # itself first
            group = tuple(self.peer_group(parent))
            if len(group) < (2 if parent == name else 1) or any(group == g for g, _ in out):
                continue
            if parent == name:
                out.append((group, "exact-peer group"))
            elif len(group) > 1:
                out.append((group, f"via direct parent {parent} and its exact-peer group"))
            else:
                out.append((group, f"via direct parent {parent}"))
        return out


#: Appended to an npm section's note in a fix plan when a row names a move (:func:`annotate_plan`).
PLAN_NOTE = (" A row the audit fix leaves vulnerable is then retried with the `npm update` its note names "
             "(`--no-save --package-lock-only --ignore-scripts`: its exact-peer group or direct parent, within "
             "the ranges `package.json` declares, never changing them), kept only when it removes an advisory "
             "and adds none.")


def annotate_plan(lockfile: Path, rows: list[Any]) -> str:
    """Name in each ``fix`` row's note the moves :func:`.npm_ops.npm_moves` would try (``fix.plan_npm_lock``).

    The dry-run plan is what ``retry_guard`` keys a "nothing fixable" hold on, so a move appearing here changes the
    plan's digest and lifts the hold once.

    Args:
        lockfile: The planned lockfile.
        rows: Its ``fix.PinChange`` rows (``note`` is extended in place).

    Returns:
        :data:`PLAN_NOTE` when any row names a move, else "".
    """
    graph, named = LockGraph(load(lockfile)), False
    for c in rows:
        if c.action == "fix" and (found := graph.moves(c.name, c.old)):
            named = True
            c.note += "; if the audit fix leaves it: " + "; ".join(
                f"`npm update {' '.join(names)}` ({why})" for names, why in found)
    return PLAN_NOTE if named else ""
