"""Which packages ``stage-fix`` must update together when ``npm audit fix`` leaves an in-range advisory unfixed.

``npm audit fix`` without ``--force`` stops short in two cases a lockfile shows by itself (2026-11-01: vitest and
``@vitest/mocker`` in data-quality-monitor; ``qs`` under ``express`` in two repositories):

* **Exact peer groups.** ``@vitest/coverage-v8`` declares ``peerDependencies: {"vitest": "4.1.5"}`` and vitest
  declares the same back, so neither can move alone: the whole group moves in one ``npm update a b c``.
* **A pin held by a direct parent.** ``express 4.22.1`` depends on ``qs ~6.14.0``, so ``qs`` cannot reach the fixed
  ``6.16.x`` until ``express`` itself moves (to a newer release inside the range ``package.json`` declares).

:meth:`LockGraph.moves` reads a lockfile (v2/v3 ``packages``, :func:`load`) and returns the ``npm update`` name
sets for one vulnerable package version; :func:`npm_moves` (called by ``fix.fix_path``) runs them lockfile-only,
never touching ``package.json``.
Groups are found from the lockfile's own ``peerDependencies`` (an exact version spec binds), never from a list of
known package names. The lockfile is repository content (C-4): a name that is not a plain npm package name is never
returned, so nothing read here can become an npm option. The graph side imports nothing from the rest of the package;
:func:`npm_moves` runs npm through ``fix.npm_fix`` (imported lazily: ``fix`` imports this module).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

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
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    packages = data.get("packages") if isinstance(data, dict) else None
    if not isinstance(packages, dict):
        return {}
    return {k: v for k, v in packages.items() if isinstance(k, str) and isinstance(v, dict)}


def semver_line(version: str) -> tuple[int, ...]:
    """Return what a caret range holds fixed: the major, or ``0.minor``, or ``0.0.patch``.

    Args:
        version: An installed or fixed version (``v`` prefix, pre-release and build parts ignored).

    Returns:
        ``(major,)``, ``(0, minor)`` or ``(0, 0, patch)``.
    """
    core = version.strip().lstrip("vV").split("-", 1)[0].split("+", 1)[0]
    nums = [int(n) for n in re.findall(r"\d+", core)[:3]]
    nums += [0] * (3 - len(nums))
    return (nums[0],) if nums[0] else (0, nums[1]) if nums[1] else (0, 0, nums[2])


def has_workspaces(lockfile: Path) -> bool:
    """Return whether the lockfile's root package, or the ``package.json`` beside it, declares workspaces.

    Args:
        lockfile: package-lock.json or npm-shrinkwrap.json.

    Returns:
        True when either declares a non-empty ``workspaces`` (``stage-fix`` and ``stage-major`` fail closed there).
    """
    for path, root_of in ((lockfile, lambda d: d["packages"].get("") if isinstance(d.get("packages"), dict) else None),
                          (lockfile.parent / "package.json", lambda d: d)):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):   # missing or malformed: npm reports that, not the plan
            data = None
        root = root_of(data) if isinstance(data, dict) else None
        if isinstance(root, dict) and root.get("workspaces"):
            return True
    return False


def _name(key: str, info: dict[str, Any]) -> str:
    name = info.get("name")
    return name if isinstance(name, str) and name else key.rsplit("node_modules/", 1)[-1]


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
        targets = {k for k, v in self.packages.items() if k and _name(k, v) == name and v.get("version") == version}
        found: set[str] = set()
        seen, todo = set(targets), list(targets)
        while todo:
            key = todo.pop()
            for user in self.users.get(key, ()):
                if user == "":
                    found.add(_name(key, self.packages[key]))
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
    """Name in each ``fix`` row's note the moves :func:`npm_moves` would try (``fix.plan_npm_lock``).

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


#: Most ``npm update`` moves :func:`npm_moves` tries on one lockfile (each is one npm run).
MAX_NPM_MOVES = 8


def npm_moves(lockfile: Path, *, cooldown_days: int = 0, private: tuple[str, ...] = ()) -> list[str]:
    """Retry what ``npm audit fix`` left vulnerable inside its range by moving exact-peer groups or direct parents.

    Run by :func:`.fix.fix_path` with ``apply``, after the audit fix. The lockfile is looked up again in OSV; for each
    package still vulnerable whose fixed version is inside the installed caret range (a ``fix`` row of
    :func:`.fix.plan_npm_lock`, never ``major-held`` or ``malicious``),
    :meth:`LockGraph.moves` names the packages to update together: the row's exact-peer group (vitest with
    ``@vitest/coverage-v8``, each pinning the other's exact version) or the direct parent whose own pin holds it
    (``express`` for ``qs``), with that parent's group. Each set runs as ``npm update --no-save <names>
    --package-lock-only --ignore-scripts`` (:func:`.fix.npm_fix` with ``update``; the audit fix's isolation, registry,
    cooldown and confinement), so npm stays inside the ranges ``package.json`` declares and never rewrites it (a
    range change is ``stage-major``'s). A move is kept only when OSV counts at least one advisory occurrence fewer
    and none new (:func:`.fix.advisory_counts`); otherwise, or on an npm error, the lockfile is restored byte for byte.
    ``executor.validate_diff`` still judges the result (:func:`manifests.check_lock_edit`).

    Args:
        lockfile: package-lock.json or npm-shrinkwrap.json (never a symlink here; workspaces are skipped).
        cooldown_days: Passed to npm (``--before``), as for the audit fix.
        private: The project's ``private_packages`` patterns (never looked up, never named to npm here).

    Returns:
        One summary per move tried (``npm update <names>: kept`` or ``...: reverted (why)``), then npm's output
        indented (so a reverted move's ``npm error`` line never reads as the audit fix's own).

    Raises:
        FeedError: OSV could not be read (the lockfile is restored first).
    """
    from agentteams.vulnscan import (  # lazy: fix imports this module
        _http,
        fix,
        osv,
        project_policy,
    )
    from agentteams.vulnscan.manifests import parse

    if has_workspaces(lockfile) or (m := parse(lockfile)).unscannable:
        return []
    graph = LockGraph(load(lockfile))
    movable = {(d.name, d.version): found for d in m.deps
               if not project_policy.is_private(d.name, private, "npm") and (found := graph.moves(d.name, d.version))}
    hits = osv.vulnerable_ids(("npm", n, v) for n, v in movable) if movable else {}   # none movable: OSV not asked
    todo: dict[tuple[str, ...], set[str]] = {}   # names to update -> the vulnerable packages they are for
    for (name, ver), found in sorted(movable.items()):
        ids = hits.get(("npm", name, ver))
        if not ids or any(osv.is_malicious(i) for i in ids):
            continue
        new, _unfixed = osv.minimal_fix(ids, "npm", name, ver)
        if new and semver_line(new) == semver_line(ver):   # plan_npm_lock's "fix", never "major-held"
            for names, _why in found:
                todo.setdefault(names, set()).add(name)
    out: list[str] = []
    for names, targets in list(todo.items())[:MAX_NPM_MOVES]:
        before_bytes = lockfile.read_bytes()
        before = fix.advisory_counts(lockfile, private)
        if not before or not targets & {n for _e, n, _i in before}:
            continue   # what this move was for is fixed already (an earlier move took it)
        npm_out = fix.npm_fix(lockfile.parent, cooldown_days=cooldown_days, apply=True, update=names)
        failed = next((ln for ln in npm_out.splitlines() if ln.startswith(("npm error", "npm ERR!"))), "")
        why = failed[:200] if failed else "" if lockfile.read_bytes() != before_bytes else "npm changed nothing"
        if not why:
            try:
                after = fix.advisory_counts(lockfile, private)
            except _http.FeedError:
                lockfile.write_bytes(before_bytes)
                raise
            if after is None:
                why = "the lockfile has no exact versions after it"
            elif after - before:
                why = "it adds advisories: " + ", ".join(sorted({n for _e, n, _i in after - before}))[:200]
            elif not before - after:
                why = "it resolves no advisory"
        if why and lockfile.read_bytes() != before_bytes:
            lockfile.write_bytes(before_bytes)
        # indented, so a reverted move's npm error never reads as the audit fix's own (executor.stage_fix)
        shown = "\n".join("  " + ln for ln in npm_out.splitlines())
        out.append(f"npm update {' '.join(names)}: {'reverted (' + why + ')' if why else 'kept'}\n{shown}")
    return out
