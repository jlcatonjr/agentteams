"""Turn dependencies, runtimes and host checks into a :class:`~agentteams.vulnscan.report.Report`.

The engine has three entry points over one analysis path:

- :func:`scan_path`: manifests under a directory (repositories);
- :func:`scan_inventory`: the tab-separated lines a server-kit audit prints;
- :func:`local_runtimes`: Node/PHP/Python found on the machine running the scan.

Server inventory line formats (one per line; anything else is ignored):

| Prefix | Fields (tab-separated) |
|---|---|
| ``INV`` | ecosystem, name, version, location |
| ``RUNTIME`` | kind, version, path, manager |
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
from pathlib import Path

from agentteams.vulnscan import _http, osv, runtimes
from agentteams.vulnscan.manifests import Dep, discover, parse
from agentteams.vulnscan.report import Finding, Report

#: Python package locations owned by the OS package manager (dpkg / rpm).
DISTRO_LOCATION = re.compile(r"^/usr/lib(64)?/python3[^/]*/(dist|site)-packages/?$|^/usr/lib/python3/dist-packages/?$")
UNRATED_AS = 2   # advisories without a severity label count as MODERATE, never as harmless


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


def analyze(deps: list[tuple[str, Dep]], report: Report, *, distro_check: bool = False) -> None:
    """Look up every (target, dep) in OSV and append findings to ``report``.

    Args:
        deps: (target label, dependency) pairs.
        report: Report to extend; feed failures land in ``report.feed_errors``. Advisory
            details that fail in the parallel fetch are retried once, sequentially.
        distro_check: Mark Python packages in OS-managed locations as ``distro``.
    """
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
        sev = max((osv.severity(r) or UNRATED_AS for r in records), default=UNRATED_AS)
        known = [i for i in ids if fetched.get(i) is not None]
        fix, unfixed = osv.minimal_fix(known, eco, name, ver) if known else (None, 0)
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
                locations=locs, malicious=malicious, distro=distro,
                detail="MALICIOUS package version (OSV MAL-*)" if malicious else ""))


def scan_path(root: Path, *, include_worktrees: bool = False) -> Report:
    """Scan every manifest under ``root``.

    Args:
        root: Repository or parent directory.
        include_worktrees: Also scan linked git worktrees (normally duplicates).

    Returns:
        The report; targets are repositories relative to ``root``.
    """
    root = root.resolve()
    report = Report(target=str(root))
    deps: list[tuple[str, Dep]] = []
    for path in discover(root, include_worktrees=include_worktrees):
        m = parse(path)
        rel = str(path.relative_to(root))
        if m.unscannable:
            report.unscannable.append((rel, m.unscannable))
        target = repo_of(path, root)
        deps += [(target, Dep(d.ecosystem, d.name, d.version, d.scope, rel)) for d in m.deps]
    analyze(deps, report)
    return report


def _runtime_findings(target: str, items: list[tuple[str, str, str, str]], report: Report) -> None:
    for kind, version, path, manager in items:
        if kind not in runtimes.EOL_PRODUCT:
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


def local_runtimes(target: str = "localhost") -> Report:
    """Evaluate Node.js, PHP and Python found on PATH of the machine running this.

    Args:
        target: Label for the report.

    Returns:
        The report: runtime findings, and a runtime whose version cannot be read as unscannable.
    """
    report = Report(target=target)
    items: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()
    for kind, names, args in (("node", ("node",), ["-v"]), ("php", ("php",), ["-v"]),
                              ("python", ("python3",), ["--version"])):
        for name in names:
            path = shutil.which(name)
            if not path or os.path.realpath(path) in seen:
                continue
            seen.add(os.path.realpath(path))
            ver = _version_of(path, args)
            if ver:
                items.append((kind, ver, path, _manager_of(path)))
            else:
                report.unscannable.append((path, f"could not read the {kind} version"))
    _runtime_findings(target, items, report)
    return report
