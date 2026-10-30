"""Turn dependencies, runtimes and host checks into a :class:`~agentteams.vulnscan.report.Report`.

The engine has three entry points over one analysis path:

- :func:`scan_path`: manifests under a directory (repositories);
- :func:`scan_inventory`: the tab-separated lines a server-kit audit prints;
- :func:`local_runtimes`: the :data:`runtimes.PRODUCTS` found on the machine running the scan.

With ``unpinned_ranges`` (off by default), :func:`scan_path` also checks the ranges of Python requirements that
have no exact pin (:func:`analyze_ranges`, :mod:`.specifiers`).

Server inventory line formats (one per line; anything else is ignored):

| Prefix | Fields (tab-separated) |
|---|---|
| ``INV`` | ecosystem, name, version, location |
| ``RUNTIME`` | kind (a ``runtimes.PRODUCTS`` key; any other is unscannable), version, path, manager |
| ``OSREL`` | os-release ``ID``, ``VERSION_ID``, ``PRETTY_NAME`` (an ``ID`` not in ``runtimes.OS_PRODUCTS`` is unscannable) |
| ``ESM`` | esm-infra, esm-apps: ``enabled``, ``disabled`` or ``unknown`` (Ubuntu Pro, from its status file) |
| ``HOSTCHECK`` | severity, message |
| ``IOC`` | message |

A ``RUNTIME`` manager of ``pkg:<name>`` (or ``pkg:<name>:universe``) is a package from an allowlisted
distribution archive; ``repo:<host>:<name>`` and ``repo:unknown:<name>`` come from anywhere else and are
judged against upstream versions (:func:`runtimes.evaluate`).

An end-of-life OS release is a HIGH ``host`` finding named ``os-release``. Ubuntu's endoflife.date ``eol``
is the end of *standard* support, so an Ubuntu release past it whose ESM has not ended is judged on the
``ESM`` line: esm-infra not enabled (or no line) is HIGH; esm-infra enabled is LOW, or MODERATE when a
universe package (``pkg:<name>:universe``) runtime is present without esm-apps. When whether its ESM still runs is
unknown (no extended-support data), it is HIGH, "ESM state unknown", whatever the ``ESM`` line says.
"""

from __future__ import annotations

import concurrent.futures as _cf
import os
import re
import shutil
import subprocess
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from agentteams.vulnscan import _http, images, osv, private_sources, project_policy, runtimes, specifiers
from agentteams.vulnscan.manifests import REQ_SCAN_NAME, Dep, Manifest, discover, parse
from agentteams.vulnscan.report import OS_RELEASE, PARTIAL_RANGE_CAP, Finding, Report
from agentteams.vulnscan.versions import version_key

#: Python package locations owned by the OS package manager (dpkg / rpm).
DISTRO_LOCATION = re.compile(r"^/usr/lib(64)?/python3[^/]*/(dist|site)-packages/?$|^/usr/lib/python3/dist-packages/?$")
#: Rank of an advisory with neither a CVSS vector nor a label until ``Report.apply_unscored`` (HIGH).
UNSCORED_DEFAULT = osv.UNSCORED_AS["high"]
#: Distinct unpinned requirement names one :func:`scan_path` sends to OSV; the rest are a coverage note.
UNPINNED_QUERY_LIMIT = 500
#: Advisory records one :func:`scan_path` fetches for unpinned ranges; the rest are "not judged".
UNPINNED_DETAIL_LIMIT = 2000
#: Coverage note of a manifest whose ranges were checked (it stays unscannable: nothing installed is known).
RANGES_NOTE = ("ranges checked against advisories: {n} direct requirement(s); the installed versions and transitive "
               "dependencies are not known, so pin with a lock for full coverage")


def repo_of(path: Path, root: Path) -> str:
    """Return the repository (nearest ancestor holding ``.git``) of ``path``, relative to ``root``.

    Args:
        path: A file under ``root``.
        root: Scan root.

    Returns:
        Relative repo path (``"."`` for the root itself), or the parent dir when no repo is found.
    """
    for parent in [path.parent, *path.parent.parents]:
        if (parent / ".git").exists():
            try:
                rel = parent.relative_to(root)
            except ValueError:
                return str(parent)
            return str(rel) or "."
        if parent == root:
            break
    try:
        return str(path.parent.relative_to(root)) or "."
    except ValueError:
        return str(path.parent)


def _details_or_none(vuln_id: str) -> dict | None:
    """Fetch one advisory; None (reported as unscanned by the caller) when it cannot be read."""
    try:
        return osv.details(vuln_id)
    except _http.FeedError:
        return None


def analyze(deps: list[tuple[str, Dep]], report: Report, *, distro_check: bool = False,
            dev_policy: dict[str, bool] | None = None) -> None:
    """Look up every (target, dep) in OSV and append findings to ``report``.

    Args:
        deps: (target label, dependency) pairs.
        report: Report to extend; feed failures land in ``report.feed_errors``. Advisory
            details that fail in the parallel fetch are retried once, sequentially.
        distro_check: Mark Python packages in OS-managed locations as ``distro``.
        dev_policy: Per target, whether its dev-only findings count toward the exit threshold
            (a project's ``include_dev``); targets not listed follow the caller's setting.
    """
    dev_policy = dev_policy or {}
    by_key: dict[osv.Key, list[tuple[str, Dep]]] = defaultdict(list)
    for target, d in deps:
        by_key[(d.ecosystem, d.name, d.version)].append((target, d))
    report.checked += len(by_key)
    try:
        hits = osv.vulnerable_ids(by_key)
    except _http.FeedError as exc:
        report.feed_errors.append(f"OSV query for {report.target}: {exc}")
        return
    fetched = _fetch_details(sorted({i for ids in hits.values() for i in ids}), report)
    for key, ids in hits.items():
        eco, name, ver = key
        records = [fetched[i] for i in ids if fetched.get(i) is not None]
        ranks = [osv.severity(r) for r in records]
        rated = max(ranks, default=0)
        # unscored: neither a CVSS vector nor a label, or the record could not be read (also a feed error)
        unscored = ranks.count(0) + len(ids) - len(records)
        sev = max(rated, UNSCORED_DEFAULT if unscored else 0)
        known = [i for i in ids if fetched.get(i) is not None]
        fix, unfixed = osv.minimal_fix(known, eco, name, ver) if known else (None, 0)
        malformed, hidden = osv.quirks(known, eco, name, ver)   # R8: counted in the report, never silent
        malicious = any(osv.is_malicious(i) for i in ids)
        per_target: dict[str, list[Dep]] = defaultdict(list)
        for target, d in by_key[key]:
            per_target[target].append(d)
        for target, ds in per_target.items():
            locs = sorted({d.source for d in ds})
            distro = distro_check and eco == "PyPI" and all(DISTRO_LOCATION.match(loc) for loc in locs)
            scope = "runtime" if any(d.scope == "runtime" for d in ds) else "dev"
            report.findings.append(Finding(
                kind="package", target=target, name=name, version=ver, ecosystem=eco, scope=scope,
                severity=4 if malicious else sev, fix=fix or "", unfixed=unfixed, ids=ids,
                locations=locs, malicious=malicious, distro=distro, rated=rated, unscored=unscored,
                malformed=malformed, fix_hidden=hidden,
                count_dev=dev_policy.get(target) if scope == "dev" else None,
                detail="MALICIOUS package version (OSV MAL-*)" if malicious else ""))


@dataclass
class RangeItem:
    """One unpinned direct requirement to check (:func:`analyze_ranges`)."""

    target: str                       # repository label, as for findings
    location: str                     # manifest path relative to the scan root
    req: specifiers.Requirement


def _fetch_details(ids: list[str], report: Report) -> dict[str, dict | None]:
    """Fetch advisories in parallel, retry failures once in order, and record the ones still missing."""
    with _cf.ThreadPoolExecutor(max_workers=8) as pool:
        fetched = dict(zip(ids, pool.map(_details_or_none, ids)))
    for vid in sorted(i for i, rec in fetched.items() if rec is None):
        fetched[vid] = _details_or_none(vid)   # one sequential retry: parallel failures are often transient
    missing = sorted(i for i, rec in fetched.items() if rec is None)
    if missing:
        report.feed_errors.append(f"OSV details unavailable for {len(missing)} advisory(ies): {', '.join(missing[:5])}")
    return fetched


def analyze_ranges(items: list[RangeItem], report: Report, *, dev_policy: dict[str, bool] | None = None,
                   query_limit: int = UNPINNED_QUERY_LIMIT,
                   detail_limit: int = UNPINNED_DETAIL_LIMIT) -> dict[str, list[str]]:
    """Check unpinned requirement ranges against every advisory OSV holds for their names; extend ``report``.

    Each distinct name goes to OSV without a version (:func:`osv.package_ids`). Each advisory's affected ranges
    (:func:`osv.affected_intervals`) are intersected with the requirement's specifier set
    (:func:`specifiers.judge`). Findings are of kind ``range``, with ``version`` empty and ``spec`` set, one per
    requirement and class:

    - a ``MAL-*`` advisory the range overlaps: CRITICAL, ``malicious`` False (no install is confirmed), detail
      "range admits known-malicious versions; check what is installed";
    - advisories that affect every allowed version: at their severity;
    - advisories that affect part of the range: at their severity capped at MODERATE (``partial``), with
      ``fix`` the lower bound that clears them, when known.

    Args:
        items: The requirements to check.
        report: Report to extend: ``checked`` counts the names queried, a feed failure goes to ``feed_errors``
            (never clean).
        dev_policy: Per target, the project's ``include_dev``.
        query_limit: Most names queried (:data:`UNPINNED_QUERY_LIMIT`).
        detail_limit: Most advisory records fetched (:data:`UNPINNED_DETAIL_LIMIT`).

    Returns:
        Per manifest location, notes for its coverage entry (caps reached, advisories not judged, a feed failure).
    """
    dev_policy = dev_policy or {}
    notes: dict[str, list[str]] = defaultdict(list)
    names = sorted({("PyPI", it.req.name) for it in items})
    queried, over = names[:query_limit], set(names[query_limit:])
    for loc in sorted({it.location for it in items if ("PyPI", it.req.name) in over}):
        n = sum(1 for it in items if it.location == loc and ("PyPI", it.req.name) in over)
        notes[loc].append(f"partial: {n} requirement(s) over the query cap ({query_limit}), not checked")
    report.checked += len(queried)
    try:
        hits = osv.package_ids(queried)
    except _http.FeedError as exc:
        report.feed_errors.append(f"OSV range query for {report.target}: {exc}")
        for loc in sorted({it.location for it in items}):
            notes[loc].append("ranges not checked: the OSV query failed")
        return notes
    ids_all = sorted({i for ids in hits.values() for i in ids})
    fetched = _fetch_details(ids_all[:detail_limit], report)
    capped = set(ids_all[detail_limit:])
    for it in items:
        if ("PyPI", it.req.name) in over:
            continue
        ids = hits.get(("PyPI", it.req.name), [])
        mal: list[str] = []
        whole: list[tuple[str, dict]] = []
        part: list[tuple[str, dict, specifiers.Judgement]] = []
        not_judged = over_cap = 0
        for vid in ids:
            if vid in capped:
                over_cap += 1
                continue
            rec = fetched.get(vid)
            if rec is None:
                continue   # recorded as a feed error: the run is partially unscanned
            ivs = osv.affected_intervals(rec, "PyPI", it.req.name)
            if ivs is None:
                not_judged += 1
                continue
            j = specifiers.judge(it.req.spec, ivs)
            if j.verdict == "none":
                continue
            if osv.is_malicious(vid):
                mal.append(vid)
            elif j.verdict == "whole":
                whole.append((vid, rec))
            else:
                part.append((vid, rec, j))
        if not_judged:
            notes[it.location].append(f"{it.req.name}: {not_judged} advisory(ies) not judged (unknown range event, "
                                      "GIT-only or malformed range)")
        if over_cap:
            notes[it.location].append(f"partial: {it.req.name}: {over_cap} advisory(ies) over the detail cap "
                                      f"({detail_limit}), not judged")
        _range_findings(it, mal, whole, part, report, dev_policy)
    return notes


def _range_findings(it: RangeItem, mal: list[str], whole: list[tuple[str, dict]],
                    part: list[tuple[str, dict, specifiers.Judgement]], report: Report,
                    dev_policy: dict[str, bool]) -> None:
    """Append the (at most three) range findings of one requirement: malicious, whole range, part of it."""
    req = it.req
    spec = req.spec.text or "any version"
    marker = "; environment marker ignored (judged for every environment)" if req.marker else ""
    base = {"kind": "range", "target": it.target, "name": req.name, "ecosystem": "PyPI", "scope": req.scope,
            "spec": req.spec.text, "locations": [it.location],
            "count_dev": dev_policy.get(it.target) if req.scope == "dev" else None}
    if mal:
        report.findings.append(Finding(**base, severity=4, rated=4, ids=sorted(mal),
                                       detail="range admits known-malicious versions; check what is installed" + marker))
    if whole:
        ranks = [osv.severity(rec) for _v, rec in whole]
        rated, unscored = max(ranks), ranks.count(0)
        report.findings.append(Finding(
            **base, severity=max(rated, UNSCORED_DEFAULT if unscored else 0), rated=rated, unscored=unscored,
            ids=sorted(v for v, _r in whole), detail=f"every version `{spec}` allows is affected" + marker))
    if part:
        ranks = [osv.severity(rec) for _v, rec, _j in part]
        rated, unscored = max(ranks), ranks.count(0)
        raises = [j.raise_to for _v, _r, j in part]
        fix = max(raises, key=version_key) if all(raises) else ""
        if fix and not osv.FIX_VERSION.fullmatch(fix):
            fix = ""
        overlap = " or ".join(_shown(iv) for iv in specifiers.merge(o for _v, _r, j in part for o in j.overlap))
        report.findings.append(Finding(
            **base, severity=min(max(rated, UNSCORED_DEFAULT if unscored else 0), PARTIAL_RANGE_CAP), rated=rated,
            unscored=unscored, partial=True, fix=fix, unfixed=sum(1 for r in raises if not r),
            ids=sorted(v for v, _r, _j in part),
            detail=f"`{spec}` allows vulnerable versions `{overlap}`; the installed version is unknown" + marker))


def _shown(iv: specifiers.Interval) -> str:
    """Return an interval for a report, or ``(unusual versions)`` when a bound from the feed is not plain version text."""
    plain = all(b is None or osv.FIX_VERSION.fullmatch(b) for b in (iv.lo, iv.hi))
    return iv.text() if plain else "(unusual versions)"


def _range_file(path: Path, m: Manifest, repo: Path) -> specifiers.RangeFile | None:
    """Return the unpinned requirements of an unscannable Python manifest, or None when it has none to check."""
    if REQ_SCAN_NAME.match(path.name) and not m.deps and m.unpinned:
        return specifiers.read_requirements(path, repo)
    if path.name == "pyproject.toml" and m.unscannable and not m.unscannable.startswith("could not parse"):
        siblings = [p for p in path.parent.iterdir() if REQ_SCAN_NAME.match(p.name) and p.is_file()]
        return specifiers.read_pyproject(path, [d.name for d in m.deps]
                                         + [d.name for s in siblings for d in parse(s).deps])
    return None


#: Unscannable reason of a ``gradle.lockfile`` read without the opt-in (re-exported).
GRADLE_OPT_IN = private_sources.GRADLE_OPT_IN


def scan_path(root: Path, *, include_worktrees: bool = False, exclude: Iterable[str | Path] = (),
              opted_out: Iterable[str | Path] = (), gradle_lockfiles: bool = False,
              unpinned_ranges: bool = False) -> Report:
    """Scan every manifest under ``root``: the operator decides what is checked, each brief narrows what is sent.

    The operator's choices come first and apply before any manifest is read: a manifest inside an
    ``exclude`` path is never read or queried (and not listed: the operator chose it), and a repository in
    ``opted_out`` (enrolled by the operator at ``off``) is skipped and listed under ``opted_out``.

    A container manifest (Dockerfile, compose file) has its base images judged (:mod:`.images`); it stays
    listed unscannable, because the image contents are not scanned.

    Each repository's ``vulnerability_scanning`` block (:mod:`.project_policy`) then narrows what leaves
    the machine, never what is checked: ``level: off`` does **not** skip a repository the operator asked to
    scan (it is scanned and listed under ``brief_off``, a mismatch); manifests matching ``exclude_paths``
    are skipped (``excluded``, ``"<repo>: <path in repo>"``); packages matching ``private_packages`` are never
    sent to OSV (``withheld``, ``"<repo>: <name> (<ecosystem>)"``); ``include_dev`` decides whether the
    repository's dev-only findings count.

    The repository's own tooling withholds names too (:mod:`.private_sources`): Go modules matching
    ``GOPRIVATE`` / ``GONOPROXY`` / ``GONOSUMDB``, every NuGet package of a repository whose ``nuget.config`` names
    a non-nuget.org source, and pnpm/Bun names in an ``.npmrc`` scope with a non-npmjs registry. A
    ``gradle.lockfile`` is read only with ``gradle_lockfiles`` (otherwise unscannable, :data:`GRADLE_OPT_IN`).

    With ``unpinned_ranges``, a Python manifest without exact pins (a ``requirements*`` file of ranges only, a
    ``pyproject.toml`` with no lock) also has its direct requirements' ranges checked (:func:`analyze_ranges`).
    It stays *unscannable*, with :data:`RANGES_NOTE` as its reason. The same brief rules apply, and more is
    withheld: a whole file that names a package index (``withheld``, ``"<repo>: <path in repo> (private
    index)"``, :mod:`.specifiers`), and each ``private_packages`` name.

    Args:
        root: Repository or parent directory.
        include_worktrees: Also scan linked worktrees (normally duplicates).
        exclude: The operator's ``exclude`` paths (``~`` allowed); symlinks are resolved on both sides.
        opted_out: Repository paths the operator enrolled at ``off``.
        gradle_lockfiles: Read ``gradle.lockfile`` files (off by default).
        unpinned_ranges: Check unpinned Python requirement ranges (default False; sends their names to OSV).

    Returns:
        The report; targets are repositories relative to ``root``.
    """
    root = root.resolve()
    report = Report(target=str(root))
    deps: list[tuple[str, Dep]] = []
    policies = project_policy.PolicyCache()
    dev_policy: dict[str, bool] = {}
    ranges: list[RangeItem] = []
    range_files: dict[str, list[int]] = {}   # location -> [unscannable index, requirements kept, unchecked, unread]
    excl = [Path(os.path.realpath(Path(x).expanduser())) for x in exclude]
    off = {Path(os.path.realpath(Path(x).expanduser())) for x in opted_out}
    sources = private_sources.PrivateSources(root)
    for path in discover(root, include_worktrees=include_worktrees):
        real = Path(os.path.realpath(path))
        if any(x == real or x in real.parents for x in excl):
            continue   # the operator's exclusion: never read, never queried
        rel = str(path.relative_to(root))
        target = repo_of(path, root)
        repo = project_policy.repo_dir(path, root)
        if Path(os.path.realpath(repo)) in off:
            if target not in report.opted_out:
                report.opted_out.append(target)
            continue
        pol = policies.get(repo)
        if pol.level == "off" and target not in report.brief_off:
            report.brief_off.append(target)   # a brief cannot switch off what the operator scans
        in_repo = path.relative_to(repo).as_posix()
        if pol.exclude_paths and project_policy.is_excluded(in_repo, pol.exclude_paths):
            report.excluded.append(f"{target}: {in_repo}")
            continue
        if path.name == "gradle.lockfile" and not gradle_lockfiles:
            report.unscannable.append((rel, GRADLE_OPT_IN))
            continue
        m = parse(path)
        if images.is_container_manifest(path.name):   # base images judged; the layer contents stay unscanned
            images.scan_manifest(path, rel, target, report)
        elif m.unscannable:
            report.unscannable.append((rel, m.unscannable))
        if pol.include_dev is not None:
            dev_policy[target] = pol.include_dev
        if unpinned_ranges and m.unscannable:
            _collect_ranges(path, m, repo, rel, f"{target}: {in_repo}", target, pol.private_packages, ranges,
                            range_files, report)
        for d in m.deps:
            if (pol.private_packages and project_policy.is_private(d.name, pol.private_packages, d.ecosystem)) \
                    or sources.is_private(repo, path.name, d.ecosystem, d.name):
                label = f"{target}: {d.name} ({d.ecosystem})"
                if label not in report.withheld:
                    report.withheld.append(label)
            else:
                deps.append((target, Dep(d.ecosystem, d.name, d.version, d.scope, rel)))
    analyze(deps, report, dev_policy=dev_policy)
    if ranges:
        notes = analyze_ranges(ranges, report, dev_policy=dev_policy)
        _note_ranges(report, range_files, notes)
    return report


def _collect_ranges(path: Path, m: Manifest, repo: Path, rel: str, label: str, target: str,
                    private: tuple[str, ...], ranges: list[RangeItem], range_files: dict[str, list[int]],
                    report: Report) -> None:
    """Read one unscannable manifest's unpinned requirements into ``ranges``, withholding what must stay local."""
    try:
        rf = _range_file(path, m, repo)
    except (OSError, UnicodeDecodeError, ValueError, TypeError, AttributeError) as exc:   # TOMLDecodeError too
        report.unscannable[-1] = (rel, f"{m.unscannable}; ranges not read: {type(exc).__name__}")
        return
    if rf is None:
        return
    if rf.private_index:
        report.withheld.append(f"{label} (private index)")
        report.unscannable[-1] = (rel, f"{m.unscannable}; ranges not checked: it names a package index, so its "
                                       "names stay local")
        return
    kept = 0
    for req in rf.reqs:
        if private and project_policy.is_private(req.name, private, "PyPI"):
            name = f"{target}: {req.name} (PyPI)"
            if name not in report.withheld:
                report.withheld.append(name)
            continue
        ranges.append(RangeItem(target, rel, req))
        kept += 1
    range_files[rel] = [len(report.unscannable) - 1, kept, rf.unchecked, rf.unreadable]


def _note_ranges(report: Report, range_files: dict[str, list[int]], notes: dict[str, list[str]]) -> None:
    """Rewrite each checked manifest's unscannable reason to :data:`RANGES_NOTE` and its coverage notes."""
    for rel, (index, kept, unchecked, unreadable) in range_files.items():
        extra = list(notes.get(rel, []))
        if unchecked:
            extra.append(f"{unchecked} requirement(s) not checked (URL, VCS, editable or ===)")
        if unreadable:
            extra.append(f"{unreadable} requirement(s) not judged (specifier not read)")
        if any(e.startswith("ranges not checked") for e in extra):
            report.unscannable[index] = (rel, "; ".join([report.unscannable[index][1], *extra]))
        else:
            report.unscannable[index] = (rel, "; ".join([RANGES_NOTE.format(n=kept), *extra]))


def _runtime_findings(target: str, items: list[tuple[str, str, str, str]], report: Report) -> None:
    for kind, version, path, manager in items:
        if kind not in runtimes.PRODUCTS:
            # Reported, never dropped: a kind the server kit emits but this version cannot judge (Rule 12).
            report.unscannable.append((path, f"runtime kind {kind} not judged by this vulnscan version"))
            continue
        v = runtimes.evaluate(kind, version, path, manager)
        report.checked += 1
        if v.verdict == "unchecked":
            report.feed_errors.append(f"{target}: {kind} {version} at {path}: {v.detail}")
        elif v.verdict not in ("ok", "distro"):
            report.findings.append(Finding(kind="runtime", target=target, name=kind, version=v.version,
                                           severity=v.severity, fix=v.upgrade_to, locations=[path],
                                           detail=f"{v.verdict}: {v.detail} [{manager or 'unknown install'}]"))


#: ``ESM`` line values (anything else reads as ``unknown``).
ESM_STATES = ("enabled", "disabled", "unknown")


def _os_findings(target: str, osrel: tuple[str, str, str] | None, esm: tuple[str, str] | None,
                 managers: list[str], report: Report) -> None:
    """Judge the ``OSREL`` line (plan N5): end-of-life, Ubuntu past standard support (per ``ESM``), or fine."""
    if osrel is None:
        return   # an audit from a kit older than OSREL: nothing to judge, and not a gap of this scan
    os_id, version_id, pretty = (x.strip() for x in osrel)
    key = os_id.lower()
    if key not in runtimes.OS_PRODUCTS:
        report.unscannable.append((OS_RELEASE, f"OS {os_id or 'unknown'} not judged by this vulnscan version"))
        return
    if not version_id:
        report.unscannable.append((OS_RELEASE, f"OS {os_id} reports no VERSION_ID: its release was not judged"))
        return
    v = runtimes.evaluate_os(key, version_id)
    if v.verdict == "unchecked":
        if v.detail.startswith("release data unavailable"):
            report.feed_errors.append(f"{target}: OS {os_id} {version_id}: {v.detail}")
        else:
            report.unscannable.append((OS_RELEASE, f"OS {os_id} {version_id}: {v.detail}"))
        return
    report.checked += 1
    if v.verdict != "eol":
        return
    label = pretty or f"{os_id} {version_id}"
    extended = runtimes.os_extended_support(key, version_id) if key == "ubuntu" else False
    if extended is None:   # whether ESM still runs is unknown: never graded as covered (fail closed)
        sev, verdict = 3, "standard-support-ended"
        detail = f"OS release {label}: standard support ended; ESM state unknown (extended-support data unavailable)"
    elif extended:
        infra, apps = esm or ("unknown", "unknown")
        verdict = "standard-support-ended"
        if infra != "enabled":
            sev = 3
            detail = (f"OS release {label}: standard support ended; ESM/Pro required for security updates "
                      f"(esm-infra {infra})")
        elif apps == "enabled":
            sev, detail = 1, f"OS release {label}: standard support ended; on ESM (esm-infra and esm-apps enabled)"
        else:
            universe = sorted({m.split(":")[1] for m in managers if m.startswith("pkg:") and m.endswith(":universe")})
            sev = 2 if universe else 1
            detail = f"OS release {label}: standard support ended; on ESM (main only); universe packages need esm-apps"
            if universe:
                detail += f" (universe runtime packages without esm-apps: {', '.join(universe)})"
    else:
        sev, verdict = 3, "eol"
        detail = f"OS release {label} is end-of-life: distro security updates have stopped"
    report.findings.append(Finding(kind="host", target=target, name=OS_RELEASE, version=v.version, severity=sev,
                                   fix=v.upgrade_to, locations=["/etc/os-release"], detail=f"{verdict}: {detail}"))


def scan_inventory(text: str, target: str) -> Report:
    """Analyze the inventory lines a server-kit audit printed.

    Args:
        text: The audit log (line formats in this module's docstring).
        target: Host label for the report.

    Returns:
        The report: package, runtime, host (including the ``os-release`` check) and indicator findings.
    """
    report = Report(target=target)
    deps: list[tuple[str, Dep]] = []
    rts: list[tuple[str, str, str, str]] = []
    osrel: tuple[str, str, str] | None = None
    esm: tuple[str, str] | None = None
    for line in text.splitlines():
        parts = line.rstrip("\r").split("\t")
        tag = parts[0]
        if tag == "INV" and len(parts) == 5:
            _, eco, name, ver, loc = parts
            deps.append((target, Dep(eco, name, ver, "runtime", loc)))
        elif tag == "RUNTIME" and len(parts) == 5:
            rts.append((parts[1], parts[2], parts[3], parts[4]))
        elif tag == "OSREL" and len(parts) == 4:
            osrel = (parts[1], parts[2], parts[3])
        elif tag == "ESM" and len(parts) == 3:
            esm = tuple(p if p in ESM_STATES else "unknown" for p in parts[1:3])  # type: ignore[assignment]
        elif tag == "HOSTCHECK" and len(parts) == 3:
            sev = osv.SEVERITY_RANK.get(parts[1].upper(), 2)
            report.findings.append(Finding(kind="host", target=target, name="host", severity=sev, detail=parts[2]))
        elif tag == "IOC" and len(parts) == 2:
            report.findings.append(Finding(kind="ioc", target=target, name="compromise indicator",
                                           severity=4, detail=parts[1]))
    analyze(deps, report, distro_check=True)
    _runtime_findings(target, rts, report)
    _os_findings(target, osrel, esm, [m for _k, _v, _p, m in rts], report)
    return report


#: Environment pinned for every local version probe (security review R-s1, as ``L1-audit.sh`` does on a host):
#: ``go`` never fetches a toolchain or reads the scan's ``GOFLAGS``; ``docker`` reads the local socket only.
PROBE_ENV_PIN = {"GOTOOLCHAIN": "local", "GOFLAGS": "", "DOCKER_HOST": "unix:///var/run/docker.sock"}
#: Dropped from the probe environment: it would point ``docker`` at another (possibly remote) daemon.
PROBE_ENV_DROP = ("DOCKER_CONTEXT",)


def _probe_env() -> dict[str, str]:
    """Return the probe environment: this one (PATH kept), :data:`PROBE_ENV_PIN` set, :data:`PROBE_ENV_DROP` gone."""
    env = {k: v for k, v in os.environ.items() if k not in PROBE_ENV_DROP}
    env.update(PROBE_ENV_PIN)
    return env


def _version_of(binary: str, args: list[str]) -> str:
    try:
        out = subprocess.run([binary, *args], capture_output=True, text=True, timeout=20, check=False,
                             cwd="/", env=_probe_env()).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    m = re.search(r"\d+\.\d+\.\d+", out)
    return m.group(0) if m else ""


#: Seconds one version probe may run (``docker version`` waits on the daemon socket).
PROBE_TIMEOUT = 20
#: :func:`_run_probe` problem meaning "this binary belongs to another product's probe": skip it silently.
_OTHER_PRODUCT = "other product"


def _run_probe(path: str, probe: runtimes.Probe) -> tuple[list[str], str]:
    """Run one :class:`runtimes.Probe` with ``path`` as its binary.

    Args:
        path: The binary found on PATH for ``probe.argv[0]``.
        probe: The probe: arguments, stream to read and version regex.

    Returns:
        (versions, problem): the versions read (several only for an ``each`` probe), and ``""``, or
        :data:`_OTHER_PRODUCT`, or why the version could not be judged (versions then empty).
    """
    try:
        res = subprocess.run([path, *probe.argv[1:]], capture_output=True, text=True,   # from /: no repo config
                             timeout=PROBE_TIMEOUT, check=False, cwd="/", env=_probe_env())
    except (OSError, subprocess.TimeoutExpired):
        return [], ""
    out = {"stdout": res.stdout, "stderr": res.stderr}.get(probe.stream, f"{res.stdout}\n{res.stderr}")
    if (probe.reject and probe.reject in out) or (probe.only_if and probe.only_if not in out):
        return [], _OTHER_PRODUCT
    if probe.require and probe.require not in out:
        first = out.strip().splitlines()[0][:80] if out.strip() else "no output"
        return [], f"not {probe.require} ({first}): not judged by this vulnscan version"
    found = [m.group(1) for m in re.finditer(probe.regex, out, re.M)]
    versions = list(dict.fromkeys(found if probe.each else found[:1]))
    return versions, ""


def _manager_of(path: str) -> str:
    real = os.path.realpath(path)
    if "/.nvm/" in real:
        return "nvm"
    if "/Cellar/" in real or "/linuxbrew/" in real or real.startswith("/opt/homebrew/"):
        return "brew"
    if real.startswith(("/usr/bin/", "/bin/")) and shutil.which("dpkg"):
        out = subprocess.run(["dpkg", "-S", real], capture_output=True, text=True, check=False).stdout
        if out:
            return "pkg:" + out.split(":", 1)[0]
    if real.startswith(("/usr/bin/", "/bin/")) and shutil.which("rpm"):
        out = subprocess.run(["rpm", "-qf", real], capture_output=True, text=True, check=False).stdout.strip()
        if out and "not owned" not in out:
            return "pkg:" + out
    if real.startswith(("/usr/bin/", "/System/", "/Library/Developer/")):
        return "pkg:os"
    return "tarball"


#: Target of findings about runtimes on the machine running vulnscan. Not its hostname: the agent took a
#: hostname for a server and requested server-audit on it (first live run), and the hostname would end up in
#: the ledger and reports. Server operations only ever take hosts.conf names.
LOCAL_TARGET = "this-machine"


def local_runtimes(target: str = LOCAL_TARGET) -> Report:
    """Evaluate every :data:`runtimes.PRODUCTS` probe's binary found on PATH of the machine running this.

    A binary that is not on PATH is skipped; one found but whose version cannot be read (or that is not
    the product, such as LibreSSL's ``openssl``) is unscannable. Each probe runs for at most
    :data:`PROBE_TIMEOUT` seconds; the same binary is probed once per product.

    Args:
        target: Label for the report.

    Returns:
        The report: runtime findings, and a runtime whose version cannot be read as unscannable.
    """
    report = Report(target=target)
    items: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for kind, product in runtimes.PRODUCTS.items():
        for probe in product.probes:
            path = shutil.which(probe.argv[0])
            if not path or (kind, os.path.realpath(path)) in seen:
                continue
            seen.add((kind, os.path.realpath(path)))
            versions, problem = _run_probe(path, probe)
            if problem == _OTHER_PRODUCT:
                continue
            if not versions:
                report.unscannable.append((path, problem or f"could not read the {kind} version"))
                continue
            manager = _manager_of(path)
            items += [(kind, ver, path, manager) for ver in versions]
    _runtime_findings(target, items, report)
    return report
