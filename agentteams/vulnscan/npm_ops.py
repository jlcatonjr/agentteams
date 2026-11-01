"""Run ``stage-fix``'s npm follow-up moves, and the npm helpers ``fix`` and these moves share.

:func:`npm_moves` updates, lockfile-only, the exact-peer groups and direct parents :mod:`.npm_groups` selects for what
``npm audit fix`` left vulnerable inside its range, and keeps a move only when it resolves an advisory and adds none.
It runs npm and counts advisories through the callables ``fix.fix_path`` passes (``fix.npm_fix``,
``fix.advisory_counts``), so the dependency is one way: ``fix`` imports this module, never the reverse.
:func:`semver_line` and :func:`has_workspaces` are the caret-line and workspaces checks both use.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from agentteams.vulnscan import _http, npm_groups, osv, project_policy, subproc_confine
from agentteams.vulnscan.manifests import npm_off_registry, parse

#: What ``counts`` returns: (ecosystem, package, advisory) occurrences, or None when not measurable.
Counted = Counter[tuple[str, str, str]] | None


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


#: Most ``npm update`` moves :func:`npm_moves` tries on one lockfile (each is one npm run).
MAX_NPM_MOVES = 8


def npm_moves(lockfile: Path, *, run: Callable[..., str], counts: Callable[..., Counted],
              base_lock: bytes | None = None, cooldown_days: int = 0, private: tuple[str, ...] = ()) -> list[str]:
    """Retry what ``npm audit fix`` left vulnerable inside its range by moving exact-peer groups or direct parents.

    Run by :func:`.fix.fix_path` with ``apply``, after the audit fix, which passes its own runner and counter (``run``,
    ``counts``), so this module never imports ``fix``. The lockfile is looked up again in OSV; for each
    package still vulnerable whose fixed version is inside the installed caret range (a ``fix`` row of
    :func:`.fix.plan_npm_lock`, never ``major-held`` or ``malicious``),
    :meth:`LockGraph.moves` names the packages to update together: the row's exact-peer group (vitest with
    ``@vitest/coverage-v8``, each pinning the other's exact version) or the direct parent whose own pin holds it
    (``express`` for ``qs``), with that parent's group. Each set runs as ``npm update --no-save <names>
    --package-lock-only --ignore-scripts`` (``run``, i.e. :func:`.fix.npm_fix` with ``update``: the audit fix's
    isolation, registry and cooldown), so npm stays inside the ranges ``package.json`` declares and never rewrites it (a
    range change is ``stage-major``'s). A move is kept only when OSV counts at least one advisory occurrence fewer
    and none new (``counts``, i.e. :func:`.fix.advisory_counts`); otherwise, or on an npm error, the lockfile is
    restored byte for byte. ``executor.validate_diff`` still judges the result (:func:`manifests.check_lock_edit`).

    Fail closed, as ``stage-major``'s ``npm install`` (security review): ``npm update <parent>`` re-resolves that
    parent's ``package.json`` spec, so no move runs unless the tools container is active
    (:func:`subproc_confine.active`) and the ``package.json`` and lockfile (the base one, ``base_lock``, and the
    current one) name only registry sources (:func:`manifests.npm_off_registry`). A refusal is one summary line and
    leaves the lockfile as the audit fix left it.

    Args:
        lockfile: package-lock.json or npm-shrinkwrap.json (never a symlink here; workspaces are skipped).
        run: Runs npm on the lockfile's folder (``fix.npm_fix``; called with ``apply=True, update=names``).
        counts: Counts a manifest's advisory occurrences (``fix.advisory_counts(path, private)``).
        base_lock: The lockfile before the audit fix (checked for off-registry sources too); None: not checked.
        cooldown_days: Passed to npm (``--before``), as for the audit fix.
        private: The project's ``private_packages`` patterns (never looked up, never named to npm here).

    Returns:
        One summary per move tried (``npm update <names>: kept`` or ``...: reverted (why)``), then npm's output
        indented (so a reverted move's ``npm error`` line never reads as the audit fix's own); or one
        ``npm update moves not run: ...`` line when the fail-closed checks refuse.

    Raises:
        FeedError: OSV could not be read (the lockfile is restored first).
    """
    if has_workspaces(lockfile) or (m := parse(lockfile)).unscannable:
        return []
    graph = npm_groups.LockGraph(npm_groups.load(lockfile))
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
    if not todo:
        return []
    if why_not := _refusal(lockfile, base_lock):
        return [f"npm update moves not run: {why_not} ({len(todo)} move(s) left; fix by hand)"]
    out: list[str] = []
    for names, targets in list(todo.items())[:MAX_NPM_MOVES]:
        before_bytes = lockfile.read_bytes()
        before = counts(lockfile, private)
        if not before or not targets & {n for _e, n, _i in before}:
            continue   # what this move was for is fixed already (an earlier move took it)
        npm_out = run(lockfile.parent, cooldown_days=cooldown_days, apply=True, update=names)
        failed = next((ln for ln in npm_out.splitlines() if ln.startswith(("npm error", "npm ERR!"))), "")
        why = failed[:200] if failed else "" if lockfile.read_bytes() != before_bytes else "npm changed nothing"
        if not why:
            try:
                after = counts(lockfile, private)
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


def _container_active() -> bool:
    """Return whether a confinement scope is active (npm then runs in the tools container)."""
    return subproc_confine.active() is not None


def _refusal(lockfile: Path, base_lock: bytes | None) -> str:
    """Return why no ``npm update`` move may run on ``lockfile`` (unconfined, or an off-registry source), or ""."""
    if not _container_active():   # S1: npm update re-resolves package.json specs; only in the container
        return "refused outside the tools container (confinement is off or opted out)"
    try:
        manifest = (lockfile.parent / "package.json").read_text(encoding="utf-8")
        locks = [lockfile.read_text(encoding="utf-8")] + ([base_lock.decode("utf-8", "replace")] if base_lock else [])
    except OSError as exc:
        return f"package.json or the lockfile cannot be read ({type(exc).__name__})"
    return next((p for text in locks if (p := npm_off_registry(lockfile.name, manifest, text))), "")
