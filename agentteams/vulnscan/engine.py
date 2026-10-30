"""Turn dependencies, runtimes and host checks into a :class:`~agentteams.vulnscan.report.Report`.

The engine has three entry points over one analysis path:

- :func:`scan_path`: manifests under a directory (repositories);
- :func:`scan_inventory`: the tab-separated lines a server-kit audit prints;
- :func:`local_runtimes`: the :data:`runtimes.PRODUCTS` found on the machine running the scan.

Server inventory line formats (one per line; anything else is ignored):

| Prefix | Fields (tab-separated) |
|---|---|
| ``INV`` | ecosystem, name, version, location |
| ``RUNTIME`` | kind (a ``runtimes.PRODUCTS`` key; any other is unscannable), version, path, manager |
| ``HOSTCHECK`` | severity, message |
| ``IOC`` | message |
"""

from __future__ import annotations

import concurrent.futures as _cf
import os
import re
import shutil
import subprocess
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from agentteams.vulnscan import _http, osv, project_policy, runtimes
from agentteams.vulnscan.manifests import Dep, discover, parse
from agentteams.vulnscan.report import Finding, Report

#: Python package locations owned by the OS package manager (dpkg / rpm).
DISTRO_LOCATION = re.compile(r"^/usr/lib(64)?/python3[^/]*/(dist|site)-packages/?$|^/usr/lib/python3/dist-packages/?$")
#: Rank of an advisory with neither a CVSS vector nor a label until ``Report.apply_unscored`` (HIGH).
UNSCORED_DEFAULT = osv.UNSCORED_AS["high"]


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
    ids_all = sorted({i for ids in hits.values() for i in ids})
    with _cf.ThreadPoolExecutor(max_workers=8) as pool:
        fetched = dict(zip(ids_all, pool.map(_details_or_none, ids_all)))
    for vid in sorted(i for i, rec in fetched.items() if rec is None):
        fetched[vid] = _details_or_none(vid)   # one sequential retry: parallel failures are often transient
    missing = sorted(i for i, rec in fetched.items() if rec is None)
    if missing:
        report.feed_errors.append(f"OSV details unavailable for {len(missing)} advisory(ies): {', '.join(missing[:5])}")
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


def scan_path(root: Path, *, include_worktrees: bool = False, exclude: Iterable[str | Path] = (),
              opted_out: Iterable[str | Path] = ()) -> Report:
    """Scan every manifest under ``root``: the operator decides what is checked, each brief narrows what is sent.

    The operator's choices come first and apply before any manifest is read: a manifest inside an
    ``exclude`` path is never read or queried (and not listed: the operator chose it), and a repository in
    ``opted_out`` (enrolled by the operator at ``off``) is skipped and listed under ``opted_out``.

    Each repository's ``vulnerability_scanning`` block (:mod:`.project_policy`) then narrows what leaves
    the machine, never what is checked: ``level: off`` does **not** skip a repository the operator asked to
    scan (it is scanned and listed under ``brief_off``, a mismatch); manifests matching ``exclude_paths``
    are skipped (``excluded``, ``"<repo>: <path in repo>"``); packages matching ``private_packages`` are never
    sent to OSV (``withheld``, ``"<repo>: <name> (<ecosystem>)"``); ``include_dev`` decides whether the
    repository's dev-only findings count.

    Args:
        root: Repository or parent directory.
        include_worktrees: Also scan linked worktrees (normally duplicates).
        exclude: The operator's ``exclude`` paths (``~`` allowed); symlinks are resolved on both sides.
        opted_out: Repository paths the operator enrolled at ``off``.

    Returns:
        The report; targets are repositories relative to ``root``.
    """
    root = root.resolve()
    report = Report(target=str(root))
    deps: list[tuple[str, Dep]] = []
    policies = project_policy.PolicyCache()
    dev_policy: dict[str, bool] = {}
    excl = [Path(os.path.realpath(Path(x).expanduser())) for x in exclude]
    off = {Path(os.path.realpath(Path(x).expanduser())) for x in opted_out}
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
        m = parse(path)
        if m.unscannable:
            report.unscannable.append((rel, m.unscannable))
        if pol.include_dev is not None:
            dev_policy[target] = pol.include_dev
        for d in m.deps:
            if pol.private_packages and project_policy.is_private(d.name, pol.private_packages, d.ecosystem):
                label = f"{target}: {d.name} ({d.ecosystem})"
                if label not in report.withheld:
                    report.withheld.append(label)
            else:
                deps.append((target, Dep(d.ecosystem, d.name, d.version, d.scope, rel)))
    analyze(deps, report, dev_policy=dev_policy)
    return report


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


def scan_inventory(text: str, target: str) -> Report:
    """Analyze the inventory lines a server-kit audit printed.

    Args:
        text: The audit log.
        target: Host label for the report.

    Returns:
        The report.
    """
    report = Report(target=target)
    deps: list[tuple[str, Dep]] = []
    rts: list[tuple[str, str, str, str]] = []
    for line in text.splitlines():
        parts = line.rstrip("\r").split("\t")
        tag = parts[0]
        if tag == "INV" and len(parts) == 5:
            _, eco, name, ver, loc = parts
            deps.append((target, Dep(eco, name, ver, "runtime", loc)))
        elif tag == "RUNTIME" and len(parts) == 5:
            rts.append((parts[1], parts[2], parts[3], parts[4]))
        elif tag == "HOSTCHECK" and len(parts) == 3:
            sev = osv.SEVERITY_RANK.get(parts[1].upper(), 2)
            report.findings.append(Finding(kind="host", target=target, name="host", severity=sev, detail=parts[2]))
        elif tag == "IOC" and len(parts) == 2:
            report.findings.append(Finding(kind="ioc", target=target, name="compromise indicator",
                                           severity=4, detail=parts[1]))
    analyze(deps, report, distro_check=True)
    _runtime_findings(target, rts, report)
    return report


def _version_of(binary: str, args: list[str]) -> str:
    try:
        out = subprocess.run([binary, *args], capture_output=True, text=True, timeout=20, check=False).stdout
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
        res = subprocess.run([path, *probe.argv[1:]], capture_output=True, text=True,
                             timeout=PROBE_TIMEOUT, check=False)
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
