"""OSV.dev client: which exact versions are vulnerable, how badly, and what fixes them.

``vulnerable_ids`` asks ``/v1/querybatch`` about each (ecosystem, name, version). It
works in chunks of 100 and follows ``next_page_token`` (at most ``MAX_PAGES`` pages, no
repeated token). A reply that does not answer every query raises ``FeedError``. ``details`` hydrates one
advisory from ``/v1/vulns/{id}``, cached per process. ``fix_for`` returns the fixed
version of the affected range that contains the installed version.
``minimal_fix`` takes the highest of those across all advisories, which is the smallest
upgrade that clears every one of them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from agentteams.vulnscan import _http
from agentteams.vulnscan.versions import version_key

OSV_API = "https://api.osv.dev/v1"
BATCH = 100
MAX_PAGES = 50   # querybatch pages per query; more (or a repeated token) is a feed fault
SEVERITY_RANK = {"CRITICAL": 4, "HIGH": 3, "MODERATE": 2, "MEDIUM": 2, "LOW": 1}
SEVERITY_NAME = {4: "CRITICAL", 3: "HIGH", 2: "MODERATE", 1: "LOW", 0: "UNRATED"}

Key = tuple[str, str, str]   # (ecosystem, name, version)
_DETAILS: dict[str, dict] = {}


def normalize_name(ecosystem: str, name: str) -> str:
    """Return the comparison form of a package name.

    Args:
        ecosystem: Package ecosystem.
        name: Name as written in a manifest or advisory.

    Returns:
        PEP 503 form for PyPI (``Foo_Bar`` -> ``foo-bar``); lowercased otherwise.
    """
    if ecosystem == "PyPI":
        return re.sub(r"[-_.]+", "-", name).lower()
    return name.lower()


def vulnerable_ids(items: Iterable[Key]) -> dict[Key, list[str]]:
    """Return advisory IDs for every item that OSV reports as affected.

    Args:
        items: (ecosystem, name, version) triples; duplicates are queried once.

    Returns:
        Mapping of affected items to their advisory IDs (unaffected items are absent).

    Raises:
        FeedError: OSV could not be queried, answered fewer or malformed results, or
            paginated without end; nothing can be concluded about the items.
    """
    todo = sorted(set(items))
    found: dict[Key, list[str]] = {}
    for start in range(0, len(todo), BATCH):
        chunk = todo[start:start + BATCH]
        pending: dict[int, str | None] = {i: None for i in range(len(chunk))}   # index -> page token
        pages: dict[int, int] = {}          # index -> pages requested so far
        tokens: dict[int, set[str]] = {}    # index -> page tokens already followed
        while pending:
            queries = []
            for i, token in pending.items():
                eco, name, ver = chunk[i]
                q: dict = {"package": {"ecosystem": eco, "name": name}, "version": ver}
                if token:
                    q["page_token"] = token
                queries.append(q)
            res = _http.fetch_json(f"{OSV_API}/querybatch", {"queries": queries})
            results = res.get("results") if isinstance(res, dict) else None
            if not isinstance(results, list) or len(results) != len(pending):
                # Fail closed: a package without an answer is unknown, never clean.
                raise _http.FeedError(f"OSV querybatch: malformed reply for {len(pending)} queries")
            nxt: dict[int, str] = {}
            for i, result in zip(list(pending), results):
                vulns = result.get("vulns", []) if isinstance(result, dict) else None
                if not isinstance(vulns, list):
                    raise _http.FeedError(f"OSV querybatch: malformed result for {chunk[i][1]}")
                ids = [v["id"] for v in vulns if isinstance(v, dict) and "id" in v]
                if ids:
                    found.setdefault(chunk[i], []).extend(ids)
                token = result.get("next_page_token")
                if token:
                    pages[i] = pages.get(i, 1) + 1
                    if pages[i] > MAX_PAGES or token in tokens.setdefault(i, set()):
                        raise _http.FeedError(f"OSV querybatch: pagination for {chunk[i][1]} did not end")
                    tokens[i].add(token)
                    nxt[i] = token
            pending = nxt
    return {k: sorted(set(v)) for k, v in found.items()}


def details(vuln_id: str) -> dict:
    """Return the full OSV record for ``vuln_id`` (cached).

    Args:
        vuln_id: OSV / GHSA / PYSEC / MAL identifier.

    Returns:
        The advisory JSON object.

    Raises:
        FeedError: The record could not be fetched.
    """
    if vuln_id not in _DETAILS:
        rec = _http.fetch_json(f"{OSV_API}/vulns/{vuln_id}")
        _DETAILS[vuln_id] = rec if isinstance(rec, dict) else {}
    return _DETAILS[vuln_id]


def severity(record: dict) -> int:
    """Return the advisory's severity rank (0 = unrated .. 4 = critical).

    Args:
        record: OSV advisory.

    Returns:
        Rank from the GHSA-style ``database_specific.severity`` label, when present.
    """
    label = str((record.get("database_specific") or {}).get("severity") or "").upper()
    return SEVERITY_RANK.get(label, 0)


def is_malicious(vuln_id: str) -> bool:
    """Return True for OSV malicious-package records (``MAL-*``).

    Args:
        vuln_id: Advisory identifier.

    Returns:
        Whether the record reports a malicious package version.
    """
    return vuln_id.startswith("MAL-")


def fix_for(record: dict, ecosystem: str, name: str, version: str) -> str | None:
    """Return the fixed version for the affected range containing ``version``.

    Args:
        record: OSV advisory.
        ecosystem: Package ecosystem.
        name: Package name (matched after :func:`normalize_name`).
        version: Installed version.

    Returns:
        The fixing version, or None when the containing range has no fix.
    """
    cur = version_key(version)
    for aff in record.get("affected", []):
        pkg = aff.get("package") or {}
        if pkg.get("ecosystem") != ecosystem or \
                normalize_name(ecosystem, str(pkg.get("name", ""))) != normalize_name(ecosystem, name):
            continue
        for rng in aff.get("ranges", []):
            if rng.get("type") == "GIT":
                continue
            intro: str | None = None
            for ev in rng.get("events", []):
                if "introduced" in ev:
                    intro = ev["introduced"]
                elif "fixed" in ev and intro is not None:
                    inside = (intro == "0" or version_key(intro) <= cur) and cur < version_key(ev["fixed"])
                    if inside:
                        return ev["fixed"]
                    intro = None
    return None


def minimal_fix(ids: list[str], ecosystem: str, name: str, version: str) -> tuple[str | None, int]:
    """Return (smallest version fixing every advisory, number of advisories without a fix).

    Args:
        ids: Advisory IDs affecting the version.
        ecosystem: Package ecosystem.
        name: Package name.
        version: Installed version.

    Returns:
        The upgrade target (None if no advisory has a fix) and the unfixable count.

    Raises:
        FeedError: An advisory could not be fetched.
    """
    fixes: list[str] = []
    unfixed = 0
    for vid in ids:
        f = fix_for(details(vid), ecosystem, name, version)
        if f is None:
            unfixed += 1
        else:
            fixes.append(f)
    return (max(fixes, key=version_key) if fixes else None), unfixed
