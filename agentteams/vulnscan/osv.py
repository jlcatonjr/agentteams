"""OSV.dev client: which exact versions are vulnerable, how badly, and what fixes them.

``vulnerable_ids`` asks ``/v1/querybatch`` about each (ecosystem, name, version). It
works in chunks of 100 and follows ``next_page_token`` (at most ``MAX_PAGES`` pages, no
repeated token). A reply that does not answer every query raises ``FeedError``. ``details`` hydrates one
advisory from ``/v1/vulns/{id}``, cached per process. ``severity`` ranks it from its CVSS vectors
(:mod:`.cvss`) and its ``database_specific.severity`` label, whichever is higher. ``fix_for`` returns the fixed
version of the affected range that contains the installed version; it skips malformed parts of a record and
never returns a fix version of unusual format (``quirks`` counts both for the report).
``minimal_fix`` takes the highest of those across all advisories, which is the smallest
upgrade that clears every one of them.

For unpinned requirement ranges (:mod:`.specifiers`), ``package_ids`` asks for a package's advisories by
name alone (the query has no ``version`` key) and ``affected_intervals`` reads an advisory's affected
versions as intervals, or says it cannot be judged.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

from agentteams.vulnscan import _http, cvss
from agentteams.vulnscan.specifiers import Interval
from agentteams.vulnscan.versions import version_key

OSV_API = "https://api.osv.dev/v1"
BATCH = 100
MAX_PAGES = 50   # querybatch pages per query; more (or a repeated token) is a feed fault
SEVERITY_RANK = {"CRITICAL": 4, "HIGH": 3, "MODERATE": 2, "MEDIUM": 2, "LOW": 1}
SEVERITY_NAME = {4: "CRITICAL", 3: "HIGH", 2: "MODERATE", 1: "LOW", 0: "UNSCORED"}
#: ``unscored_as`` values: the rank an advisory without any severity counts as (default HIGH).
UNSCORED_AS = {"critical": 4, "high": 3, "moderate": 2, "low": 1}

Key = tuple[str, str, str]   # (ecosystem, name, version)
_DETAILS: dict[str, dict] = {}
#: (advisory id, ecosystem, normalized name, version) -> (malformed record, fix version not shown): :func:`quirks`.
_QUIRKS: dict[tuple[str, str, str, str], tuple[bool, bool]] = {}
#: A fix version a report may show; any other is "fix version not shown (unusual format)" (R8).
FIX_VERSION = re.compile(r"[A-Za-z0-9.+~:_-]{1,64}")


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
    return _query_batches(sorted(set(items)),
                          lambda k: {"package": {"ecosystem": k[0], "name": k[1]}, "version": k[2]})


def package_ids(items: Iterable[tuple[str, str]]) -> dict[tuple[str, str], list[str]]:
    """Return every advisory ID OSV holds for each (ecosystem, name), whatever the version.

    The query names the package only: it **omits** the ``version`` key (never sends ``""``), so OSV answers
    with every advisory for the package. Unpinned requirement ranges use it (:mod:`.specifiers`); pagination
    is bounded as in :func:`vulnerable_ids`.

    Args:
        items: (ecosystem, name) pairs; duplicates are queried once.

    Returns:
        Mapping of packages with advisories to their IDs (packages without any are absent).

    Raises:
        FeedError: OSV could not be queried, answered fewer or malformed results, or paginated without end.
    """
    return _query_batches(sorted(set(items)), lambda k: {"package": {"ecosystem": k[0], "name": k[1]}})


def _query_batches(todo: list, query: Callable[[Any], dict]) -> dict:
    """Run ``querybatch`` over ``todo`` (one query each, built by ``query``) and collect advisory IDs per item.

    Raises:
        FeedError: As :func:`vulnerable_ids`.
    """
    found: dict = {}
    for start in range(0, len(todo), BATCH):
        chunk = todo[start:start + BATCH]
        pending: dict[int, str | None] = {i: None for i in range(len(chunk))}   # index -> page token
        pages: dict[int, int] = {}          # index -> pages requested so far
        tokens: dict[int, set[str]] = {}    # index -> page tokens already followed
        while pending:
            queries = []
            for i, token in pending.items():
                q = query(chunk[i])
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
                ids = [v["id"] for v in vulns if isinstance(v, dict) and isinstance(v.get("id"), str)]   # untrusted
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


def cvss_rank(record: dict) -> int:
    """Return the highest severity rank among the advisory's CVSS v3.x / v4.0 vectors (0 = none usable).

    Args:
        record: OSV advisory; vectors come from ``severity[]`` and every ``affected[].severity[]``
            (``CVSS_V3`` / ``CVSS_V4`` entries; other types, such as ``CVSS_V2``, are ignored).

    Returns:
        Rank of the highest :func:`cvss.band` (1 LOW .. 4 CRITICAL), or 0 when no vector scores.
    """
    entries = _as_list(record.get("severity"))   # a scalar, object or null is no vector list: unscored, never a crash
    for aff in _as_list(record.get("affected")):
        if isinstance(aff, dict):
            entries += _as_list(aff.get("severity"))
    best = 0
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("type") not in ("CVSS_V3", "CVSS_V4"):
            continue
        value = cvss.score(str(entry.get("score") or ""))
        if value is not None:
            best = max(best, SEVERITY_RANK[cvss.band(value)])
    return best


def severity(record: dict) -> int:
    """Return the advisory's severity rank (0 = unscored .. 4 = critical).

    The higher of the CVSS-derived rank (:func:`cvss_rank`) and the GHSA-style
    ``database_specific.severity`` label: a label never lowers what a vector says, nor the reverse.

    Args:
        record: OSV advisory.

    Returns:
        The rank; 0 when the advisory has neither a usable CVSS vector nor a label (*unscored*: the
        engine counts it as the configured ``unscored_as``, never as harmless).
    """
    if not isinstance(record, dict):   # a malformed advisory is unscored (@security rc.4 Low 7)
        return 0
    spec = record.get("database_specific")
    label = spec.get("severity") if isinstance(spec, dict) else None
    label = label.upper() if isinstance(label, str) else ""
    return max(SEVERITY_RANK.get(label, 0), cvss_rank(record))


def _as_list(value: object) -> list:
    """Return ``value`` when it is a list, else ``[]`` (OSV fields from the feed are untrusted JSON)."""
    return list(value) if isinstance(value, list) else []


def is_malicious(vuln_id: str) -> bool:
    """Return True for OSV malicious-package records (``MAL-*``).

    Args:
        vuln_id: Advisory identifier.

    Returns:
        Whether the record reports a malicious package version.
    """
    return vuln_id.startswith("MAL-")


def _walk_fix(record: object, ecosystem: str, name: str, version: str) -> tuple[str | None, bool, bool]:
    """Return ``(fix, malformed, hidden)`` for the affected range containing ``version`` (R8).

    Every field comes from the feed and is untrusted JSON, so no type is assumed. An entry of the wrong
    type (``affected``, an affected entry, its package or package name, ``ranges``, a range, its ``events``,
    an event, or a non-string version in one) is skipped and sets ``malformed``. A fix version outside
    :data:`FIX_VERSION` is never returned: ``hidden`` is set instead.
    """
    affected = record.get("affected", []) if isinstance(record, dict) else None
    if not isinstance(affected, list):
        return None, True, False
    cur, want, malformed = version_key(version), normalize_name(ecosystem, name), False
    for aff in affected:
        pkg = aff.get("package") if isinstance(aff, dict) else None
        if not isinstance(pkg, dict) or not isinstance(pkg.get("name", ""), str):
            malformed = True
            continue
        if pkg.get("ecosystem") != ecosystem or normalize_name(ecosystem, pkg.get("name", "")) != want:
            continue
        ranges = aff.get("ranges", [])
        malformed = malformed or not isinstance(ranges, list)
        for rng in ranges if isinstance(ranges, list) else []:
            events = rng.get("events", []) if isinstance(rng, dict) else None
            if not isinstance(events, list):
                malformed = True
                continue
            if rng.get("type") == "GIT":
                continue
            intro: str | None = None
            for ev in events:
                if not isinstance(ev, dict) or any(not isinstance(ev[k], str) for k in ("introduced", "fixed") if k in ev):
                    malformed, intro = True, None   # never pair a fix with a range start it cannot read
                elif "introduced" in ev:
                    intro = ev["introduced"]
                elif "fixed" in ev and intro is not None:
                    if (intro == "0" or version_key(intro) <= cur) and cur < version_key(ev["fixed"]):
                        hidden = not FIX_VERSION.fullmatch(ev["fixed"])
                        return (None if hidden else ev["fixed"]), malformed, hidden
                    intro = None
    return None, malformed, False


#: Range event kinds :func:`affected_intervals` reads; any other makes the advisory "not judged".
RANGE_EVENTS = ("introduced", "fixed", "last_affected", "limit")


def affected_intervals(record: object, ecosystem: str, name: str) -> list[Interval] | None:
    """Return the versions an advisory affects for one package, as intervals (unpinned ranges, :mod:`.specifiers`).

    Each ``ECOSYSTEM`` / ``SEMVER`` range's events are read in order: ``introduced`` opens an interval
    (``"0"``: unbounded below), ``fixed`` closes it before that version (and marks the interval ``fixed``: a
    version to raise a lower bound to), ``last_affected`` closes it at that version, ``limit`` closes it before
    that version; an ``introduced`` that nothing closes is open-ended. Each entry of an explicit ``versions[]``
    list is one single-version interval. :func:`_walk_fix` (pinned versions) is separate and unchanged.

    The record is untrusted. The advisory is **not judged** (None, a coverage note, never clean) when the
    record is malformed; when an affected entry of this package has only ``GIT`` ranges (commit hashes, no
    release versions) or a range of another unknown type; when an event is not exactly one known kind
    (:data:`RANGE_EVENTS`) with a string version; or when no affected entry of this package yields an
    interval. A ``GIT`` range next to an ``ECOSYSTEM`` range or a ``versions[]`` list in the same entry (common
    in ``PYSEC-*`` records) is skipped, as :func:`_walk_fix` skips it: the release ranges describe the same
    vulnerability.

    Args:
        record: OSV advisory.
        ecosystem: Package ecosystem.
        name: Package name (matched after :func:`normalize_name`).

    Returns:
        The intervals, or None when the advisory cannot be judged.
    """
    affected = record.get("affected") if isinstance(record, dict) else None
    if not isinstance(affected, list):
        return None
    want, out = normalize_name(ecosystem, name), []
    for aff in affected:
        pkg = aff.get("package") if isinstance(aff, dict) else None
        if not isinstance(pkg, dict) or not isinstance(pkg.get("name", ""), str):
            return None
        if pkg.get("ecosystem") != ecosystem or normalize_name(ecosystem, pkg.get("name", "")) != want:
            continue
        ranges, versions = aff.get("ranges", []), aff.get("versions", [])
        if not isinstance(ranges, list) or not isinstance(versions, list):
            return None
        for v in versions:
            if not isinstance(v, str) or not v.strip():
                return None
            out.append(Interval(v, True, v, True))
        if any(not isinstance(r, dict) or r.get("type") not in ("ECOSYSTEM", "SEMVER", "GIT") for r in ranges):
            return None   # a range of a type no version interval can be read from
        versioned = [r for r in ranges if r.get("type") != "GIT"]
        if not versioned and not versions and ranges:
            return None   # GIT ranges only (commit hashes): nothing says which releases are affected
        for rng in versioned:
            events = rng.get("events")
            if not isinstance(events, list):
                return None
            start: str | None = None
            is_open = False
            for ev in events:
                if not isinstance(ev, dict) or len(ev) != 1:
                    return None
                (kind, value), = ev.items()
                if kind not in RANGE_EVENTS or not isinstance(value, str) or not value.strip():
                    return None
                if kind == "introduced":
                    if not is_open:
                        start, is_open = (None if value == "0" else value), True
                elif is_open:
                    out.append(Interval(start, True, value, kind == "last_affected", kind == "fixed"))
                    is_open = False
            if is_open:
                out.append(Interval(start, True, None, False))
    return out or None


def fix_for(record: dict, ecosystem: str, name: str, version: str) -> str | None:
    """Return the fixed version for the affected range containing ``version``.

    Malformed parts of the record are skipped, and a fix version of unusual format (outside
    :data:`FIX_VERSION`) is never returned (:func:`_walk_fix`).

    Args:
        record: OSV advisory.
        ecosystem: Package ecosystem.
        name: Package name (matched after :func:`normalize_name`).
        version: Installed version.

    Returns:
        The fixing version, or None when the containing range has no fix, or none that may be shown.
    """
    return _walk_fix(record, ecosystem, name, version)[0]


def quirks(ids: Iterable[str], ecosystem: str, name: str, version: str) -> tuple[int, int]:
    """Return how many of ``ids`` :func:`minimal_fix` found malformed, or with a fix version not shown (R8).

    Args:
        ids: Advisory IDs of one finding.
        ecosystem: Package ecosystem.
        name: Package name.
        version: Installed version.

    Returns:
        ``(malformed, fix_hidden)``; ``(0, 0)`` for advisories :func:`minimal_fix` has not seen in this process.
    """
    found = [_QUIRKS.get((vid, ecosystem, normalize_name(ecosystem, name), version), (False, False)) for vid in ids]
    return sum(m for m, _ in found), sum(h for _, h in found)


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
        f, malformed, hidden = _walk_fix(details(vid), ecosystem, name, version)
        if malformed or hidden:   # engine passes quirks() to each Finding: counted in the report, never silent
            _QUIRKS[(vid, ecosystem, normalize_name(ecosystem, name), version)] = (malformed, hidden)
        if f is None:
            unfixed += 1
        else:
            fixes.append(f)
    return (max(fixes, key=version_key) if fixes else None), unfixed
