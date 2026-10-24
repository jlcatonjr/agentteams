"""Effective scan roots and the coverage state of every known workspace.

The daily run scans the explicit ``repo_roots`` of ``vulnscan.json`` plus the fix checkout of every registry
entry (:mod:`.registry`) at ``scan`` or ``remediate``, minus the ``exclude`` paths and duplicates
(:func:`effective_roots`; ``vulnscan roots`` prints it). The summary and ``review`` then show each known
workspace in exactly one coverage state (:func:`states`, :func:`markdown`), so nothing is left out silently.
"""

from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentteams.vulnscan import registry
from agentteams.vulnscan.manifests import discover, unsupported_ecosystem

#: Coverage states, in display order. ``not enrolled`` is only reported by ``vulnscan status --project``
#: (:func:`project_state`): the coverage section lists registry entries, which are enrolled by definition.
#: ``nothing to scan`` is a workspace with no dependency manifest at all (:func:`nothing_to_scan`); it is not a
#: coverage gap, so Rule S-VM does not report it. ``unsupported ecosystem`` is a workspace whose only manifests
#: are of ecosystems vulnscan does not read (Go, Maven, Gradle, RubyGems, NuGet, pnpm, containers, ...:
#: :func:`unsupported_only`): nothing in it is checked, so it IS a coverage gap and Rule S-VM reports it.
STATES = ("fixable", "scan-only", "not covered by the grant yet", "opted out", "excluded", "unanswered",
          "unsupported ecosystem", "nothing to scan", "duplicate checkout", "missing on disk", "not a git repository",
          "not enrolled")

#: Root sources that are scanned.
SCANNED = ("repo_roots", "registry (scan)", "registry (remediate)")


@dataclass(frozen=True)
class Root:
    """One candidate scan root and where it came from.

    Attributes:
        path: The root, ``~`` expanded (not resolved).
        label: How findings name it (the ``repo_roots`` string, or the fix checkout path).
        source: ``repo_roots``, ``registry (scan)``, ``registry (remediate)``, ``excluded``, ``opted out``,
            ``unanswered`` or ``duplicate``.
        detail: Why, for a root that is not scanned.
    """

    path: Path
    label: str
    source: str
    detail: str = ""

    @property
    def scanned(self) -> bool:
        """Whether the daily run scans this root."""
        return self.source in SCANNED


def _real(path: str | Path) -> Path:
    return Path(os.path.realpath(Path(path).expanduser()))


def within(path: str | Path, root: str | Path) -> bool:
    """Return whether ``path`` is ``root`` or inside it, after ``~`` expansion and symlink resolution.

    Args:
        path: A path.
        root: A directory.

    Returns:
        True when contained.
    """
    p, r = _real(path), _real(root)
    return p == r or r in p.parents


def exclusions(cfg: dict[str, Any]) -> list[str]:
    """Return the config's ``exclude`` paths (``~`` not yet expanded).

    Args:
        cfg: Daily config.

    Returns:
        The list (empty when unset).
    """
    return [str(x) for x in cfg.get("exclude") or []]


def effective_roots(cfg: dict[str, Any], entries: Iterable[registry.Entry]) -> list[Root]:
    """Return every candidate root with its source; :attr:`Root.scanned` ones are what the daily run scans.

    Explicit ``repo_roots`` come first, in config order. A registry fix checkout inside (or equal to) a
    scanned explicit root, or equal to an earlier registry root, is a ``duplicate``. A root inside an
    ``exclude`` path is ``excluded``. Registry entries at ``off`` or ``unanswered`` are listed, not scanned.

    Args:
        cfg: Daily config.
        entries: Registry entries.

    Returns:
        The roots.
    """
    excl = exclusions(cfg)
    out: list[Root] = []

    def excluded_by(path: Path) -> str:
        return next((e for e in excl if within(path, e)), "")

    for root_s in cfg.get("repo_roots", []):
        path = Path(root_s).expanduser()
        hit = excluded_by(path)
        out.append(Root(path, root_s, "excluded", f"inside exclude {hit}") if hit else Root(path, root_s, "repo_roots"))
    for e in sorted(entries, key=lambda x: (x.owner, x.identity)):
        path = Path(e.fix_checkout)
        if e.level == "off":
            out.append(Root(path, e.fix_checkout, "opted out", f"{e.identity}: level off"))
            continue
        if e.level not in registry.SCANNED_LEVELS:
            out.append(Root(path, e.fix_checkout, "unanswered", f"{e.identity}: no level yet (vulnscan enroll)"))
            continue
        hit = excluded_by(path)
        if hit:
            out.append(Root(path, e.fix_checkout, "excluded", f"{e.identity}: inside exclude {hit}"))
            continue
        dup = next((r for r in out if r.scanned and (within(path, r.path) if r.source == "repo_roots"
                                                     else _real(path) == _real(r.path))), None)
        if dup:
            out.append(Root(path, e.fix_checkout, "duplicate", f"{e.identity}: already scanned under {dup.label}"))
            continue
        out.append(Root(path, e.fix_checkout, f"registry ({e.level})"))
    return out


def operator_skips(cfg: dict[str, Any], entries: Iterable[registry.Entry]) -> tuple[list[Path], set[Path]]:
    """Return what the operator chose not to scan, for :func:`engine.scan_path`: never read, never queried.

    Args:
        cfg: Daily config.
        entries: Registry entries.

    Returns:
        ``(exclude paths, checkouts of entries enrolled at off)``, resolved.
    """
    off = {_real(c) for e in entries if e.level == "off" for c in (e.fix_checkout, *e.checkouts)}
    return [_real(x) for x in exclusions(cfg)], off


def excluded_inside(cfg: dict[str, Any], root: Path) -> list[Path]:
    """Return the ``exclude`` paths strictly inside a scanned ``root`` (their findings are dropped).

    Args:
        cfg: Daily config.
        root: A scanned root.

    Returns:
        The resolved paths.
    """
    real = _real(root)
    return [x for x in (_real(e) for e in exclusions(cfg)) if real in x.parents]


def _grant_covers(grants: list[dict[str, Any]], path: str) -> bool:
    from agentteams.vulnscan import grant   # lazy: grant pulls in the signing code

    return any(grant.covers(g, "stage-fix", str(_real(path))) for g in grants)


def nothing_to_scan(path: str | Path) -> bool:
    """Return whether a workspace directory holds no dependency manifest the scanner would list.

    Unsupported lockfiles (``Cargo.lock`` and the like) count as manifests: they are reported as
    unscannable, never as nothing to scan. Vendored and generated trees are skipped as in a scan.

    Args:
        path: The workspace directory (anything but an existing directory is not "nothing to scan").

    Returns:
        True when :func:`~agentteams.vulnscan.manifests.discover` finds nothing under it.
    """
    p = Path(path).expanduser()
    return p.is_dir() and not discover(p)


def unsupported_manifests(path: str | Path) -> tuple[list[str], bool]:
    """Return a workspace's manifests of unsupported ecosystems, and whether they are all it has.

    Args:
        path: The workspace directory.

    Returns:
        ``(["<path in workspace> (<ecosystem>)", ...], only)``: ``only`` is True when the workspace has
        manifests and every one is of an unsupported ecosystem (the ``unsupported ecosystem`` state).
    """
    p = Path(path).expanduser()
    if not p.is_dir():
        return [], False
    found = discover(p)
    labels = [f"{m.relative_to(p).as_posix()} ({eco})" for m in found if (eco := unsupported_ecosystem(m.name))]
    return labels, bool(found) and len(labels) == len(found)


def unsupported_only(path: str | Path) -> bool:
    """Return whether every manifest of a workspace is of an unsupported ecosystem (and it has some).

    Args:
        path: The workspace directory.

    Returns:
        True for the ``unsupported ecosystem`` coverage state.
    """
    return unsupported_manifests(path)[1]


def states(cfg: dict[str, Any], entries: Iterable[registry.Entry], grants: list[dict[str, Any]],
           unsupported: dict[str, list[str]] | None = None) -> list[tuple[str, str, str, str]]:
    """Return the coverage state of every known workspace.

    Each registry entry gives one row for its fix checkout; each further checkout gives a
    ``duplicate checkout`` row. Precedence for the fix checkout: missing on disk, not a git repository,
    excluded, nothing to scan (no dependency manifest), unsupported ecosystem (only manifests vulnscan cannot
    read), then the level (remediate splits into fixable and not covered by the grant yet).

    Args:
        cfg: Daily config.
        entries: Registry entries.
        grants: Valid remediation grants (:func:`grant.load_active`).
        unsupported: When given, filled with ``{display name: unsupported manifests}`` for every fix
            checkout that has some (mixed workspaces keep their state; their unsupported files are listed).

    Returns:
        ``(owner, state, display name, path)`` rows.
    """
    rows: list[tuple[str, str, str, str]] = []
    excl = exclusions(cfg)
    for e in entries:
        found: tuple[list[str], bool] = ([], False)
        name = registry.short_name(e.identity)
        if not Path(e.fix_checkout).exists():
            state = "missing on disk"
        elif e.identity.startswith("dir:"):
            state = "not a git repository"
        elif any(within(e.fix_checkout, x) for x in excl):
            state = "excluded"
        elif nothing_to_scan(e.fix_checkout):
            state = "nothing to scan"
        elif (found := unsupported_manifests(e.fix_checkout))[1]:
            state = "unsupported ecosystem"
        elif e.level == "remediate":
            state = "fixable" if _grant_covers(grants, e.fix_checkout) else "not covered by the grant yet"
        else:
            state = {"scan": "scan-only", "off": "opted out"}.get(e.level, "unanswered")
        rows.append((e.owner, state, name, e.fix_checkout))
        if unsupported is not None and found[0]:
            unsupported[name] = found[0]
        for c in e.checkouts:
            if _real(c) != _real(e.fix_checkout):
                rows.append((e.owner, "missing on disk" if not Path(c).exists() else "duplicate checkout",
                             f"{name} ({Path(c).name})", c))
    return rows


def markdown(rows: list[tuple[str, str, str, str]], *, names: bool = True,
             unsupported: dict[str, list[str]] | None = None) -> str:
    """Render the coverage section: totals, then names per state grouped by owner.

    Args:
        rows: :func:`states` output.
        names: List names (False: totals only, for the published copy).
        unsupported: :func:`states`' ``unsupported`` map: the manifests no scan reads, per workspace.

    Returns:
        Markdown starting with ``## Coverage``.
    """
    out = ["## Coverage", ""]
    if not rows:
        return "\n".join(out + ["- no enrolled projects (enrol them: `python -m agentteams.vulnscan enroll "
                                "--discover <dir>`)", ""])
    counts: dict[str, int] = defaultdict(int)
    for _o, state, _n, _p in rows:
        counts[state] += 1
    out.append("- " + " · ".join(f"{s}: {counts[s]}" for s in STATES if counts[s]))
    if names:
        by_owner: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for owner, state, name, _p in rows:
            by_owner[owner][state].append(name)
        for owner in sorted(by_owner):
            out += ["", f"### {owner}", ""]
            out += [f"- {s} ({len(by_owner[owner][s])}): {', '.join(sorted(by_owner[owner][s]))}"
                    for s in STATES if by_owner[owner][s]]
    if counts["unanswered"]:
        out += ["", "Answer the unanswered ones: `python -m agentteams.vulnscan enroll --discover <dir>`."]
    if counts["unsupported ecosystem"]:
        out += ["", "Unsupported ecosystem: the workspace's only manifests are of ecosystems vulnscan does not read "
                "(a coverage gap: nothing in it is checked)."]
    if unsupported:
        n_files = sum(len(v) for v in unsupported.values())
        out += ["", f"Not scanned (unsupported ecosystem): {n_files} manifest(s) in {len(unsupported)} workspace(s)"
                + (":" if names else ".")]
        if names:
            out += [f"- {name}: {', '.join(files[:8])}{' …' if len(files) > 8 else ''}"
                    for name, files in sorted(unsupported.items())]
    if counts["nothing to scan"]:
        out += ["", "Nothing to scan: no dependency manifest in the workspace (not a coverage gap)."]
    if counts["not covered by the grant yet"]:
        out += ["", "Extend the grant to them (operator): `python -m agentteams.vulnscan grant sign ...`."]
    return "\n".join(out) + "\n"


MISMATCH_OFF = "brief says off; scanned by operator enrolment"


def brief_lines(entries: Iterable[registry.Entry], report: Any = None, *, names: bool = True) -> list[str]:
    """Return the project-brief lines of the coverage section: ``off`` mismatches and per-repository counts.

    A brief that says ``off`` for a repository the operator enrols (or lists in ``repo_roots``) is scanned
    anyway (:func:`engine.scan_path`); that mismatch is listed. Then, from the run's report, how many
    manifests each repository's brief excluded and how many private packages it withheld: counts only,
    never package names (this text reaches the agent).

    Args:
        entries: Registry entries (their briefs are read for the mismatch).
        report: The run's :class:`~agentteams.vulnscan.report.Report`, or None (``review``).
        names: List repositories (False: totals only, for the published copy).

    Returns:
        Markdown lines (empty when there is nothing to say).
    """
    from agentteams.vulnscan import project_policy

    off = {e.fix_checkout for e in entries if e.level in registry.SCANNED_LEVELS
           and project_policy.read_policy(Path(e.fix_checkout)).level == "off"}
    off |= set(getattr(report, "brief_off", []) or [])
    counts = report.policy_counts() if report is not None else {}
    if not off and not counts:
        return []
    out = ["", "### Project briefs", ""]
    if not names:
        n_ex, n_wh = sum(c[0] for c in counts.values()), sum(c[1] for c in counts.values())
        return out + [f"- {MISMATCH_OFF}: {len(off)} · manifests excluded: {n_ex} · private packages withheld: {n_wh}"]
    out += [f"- `{t}`: {MISMATCH_OFF}" for t in sorted(off)]
    out += [f"- `{t}`: {ex} manifest(s) excluded, {wh} private package(s) withheld (not checked)"
            for t, (ex, wh) in counts.items()]
    return out


def section(cfg: dict[str, Any], *, names: bool = True, report: Any = None) -> str:
    """Return the coverage section for the summary or review; never raises on a bad registry or grant dir.

    Args:
        cfg: Daily config.
        names: List names (False for a published copy).
        report: The run's report (adds the per-repository brief counts), or None.

    Returns:
        Markdown.
    """
    from agentteams.vulnscan import grant

    try:
        entries = registry.load()
    except (OSError, ValueError) as exc:
        return f"## Coverage\n\n- the project registry is unreadable: {exc}\n"
    grants, _problems = grant.load_active()
    extra = brief_lines(entries, report, names=names)
    unsupported: dict[str, list[str]] = {}
    rows = states(cfg, entries, grants, unsupported)
    return markdown(rows, names=names, unsupported=unsupported) + ("\n".join(extra) + "\n" if extra else "")


def project_state(cfg: dict[str, Any] | None, entry: registry.Entry | None, path: Path,
                  grants: list[dict[str, Any]]) -> str:
    """Return one project's coverage state in the :data:`STATES` vocabulary (``vulnscan status --project``).

    Args:
        cfg: Daily config, or None when there is none.
        entry: The project's registry entry, or None.
        path: The project directory.
        grants: Valid remediation grants.

    Returns:
        The enrolled entry's :func:`states` row state; for a project the registry does not know, ``nothing to
        scan`` when it holds no dependency manifest, ``unsupported ecosystem`` when it holds only manifests
        vulnscan cannot read, ``fixable`` or ``scan-only`` when an explicit ``repo_roots``
        entry scans it (the grant decides), else ``not enrolled``.
    """
    if entry is not None:
        return states(cfg or {}, [entry], grants)[0][1]
    if nothing_to_scan(path):
        return "nothing to scan"
    if unsupported_only(path):
        return "unsupported ecosystem"
    roots = [r for r in effective_roots(cfg or {}, []) if r.scanned]
    if not any(within(path, r.path) for r in roots):
        return "not enrolled"
    return "fixable" if _grant_covers(grants, str(path)) else "scan-only"
