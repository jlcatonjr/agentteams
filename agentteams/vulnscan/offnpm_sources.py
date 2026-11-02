"""Allowlisted non-npm package sources, and how an install from one is graded on the npm advisories' ranges (vfr-e.E3).

Some publishers ship npm packages from their own host instead of the npm registry. SheetJS publishes ``xlsx``
releases after 0.18.5 only on ``cdn.sheetjs.com``, so a lockfile entry for ``xlsx`` 0.20.3 resolves there. The
GHSA records for npm ``xlsx`` name no fixed npm version (the fix was never published to npm), so an exact-version
OSV query marks every CDN release affected, the fixed ones included.

For an entry whose ``resolved`` URL is on an allowlisted source (:data:`SOURCES`, matched by :func:`match`), the
engine asks OSV for every advisory of the package and grades the installed version against each advisory's
affected ranges (:func:`judge`), the same as for an npm package:

| Grade | When |
|---|---|
| ``fixed`` | no advisory's affected range holds the version |
| ``vulnerable`` | a closed range holds it, or an open-ended range does and nothing bounds what is known affected |
| ``unknown`` | an open-ended range holds it, but ``last_known_affected_version_range`` ends below it; or the record cannot be judged |

``unknown`` is never clean: the finding keeps the advisory's severity. Only a publisher's signed known entry
(vfr-k, not this module) settles it. ``fixed`` leaves no finding, but the report lists the entry and its grade
(``Report.sources``), so it is never silently clean.

The allowlist is data: one :class:`Source` row per host. A row is the evidence that the host is the package's
publisher; there is **no general CDN trust**. An entry whose URL does not match a row exactly (another host, a
port, user info, a query, another path, a name or version that differs from the lockfile's) is judged as an npm
package, as before.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from agentteams.vulnscan import osv, specifiers
from agentteams.vulnscan.versions import version_key

#: Grades of an install from an allowlisted source (module docstring).
GRADES = ("fixed", "vulnerable", "unknown")
#: Version text a source URL may carry (the same shape as :data:`osv.FIX_VERSION`, starting with a digit).
_VERSION = r"[0-9][0-9A-Za-z.+-]{0,63}"
#: A ``last_known_affected_version_range`` upper bound (GHSA ``database_specific``): ``< X`` or ``<= X``.
_LAST_KNOWN = re.compile(r"(<=?)\s*(" + _VERSION + r")")


@dataclass(frozen=True)
class Source:
    """One allowlisted non-npm source: a publisher's own host for the npm packages it publishes.

    Attributes:
        host: The ``https://`` host, lowercase, no port.
        packages: The npm names this host publishes.
        path: Tarball path pattern; it must hold the groups ``name`` and ``version``.
        publisher: Who runs the host.
        evidence: Why the host is the package's publisher (shown in the report).
    """

    host: str
    packages: tuple[str, ...]
    path: re.Pattern[str]
    publisher: str
    evidence: str


#: The allowlist. A new source is a new row, reviewed like code; nothing here trusts a host it does not name.
SOURCES: tuple[Source, ...] = (
    Source(host="cdn.sheetjs.com", packages=("xlsx",),
           path=re.compile(r"/(?P<name>xlsx)-(?P<version>" + _VERSION + r")/(?P=name)-(?P=version)\.tgz"),
           publisher="SheetJS",
           evidence="SheetJS publishes xlsx releases after 0.18.5 only on its own CDN, not on the npm registry"),
)


def match(name: str, version: str, resolved: str, sources: tuple[Source, ...] = SOURCES) -> Source | None:
    """Return the allowlisted source a lockfile entry resolves from, or None.

    The URL must be ``https://`` on a row's host exactly (no user info, port, query or fragment), its path must
    match the row's pattern in full, and the path's name and version must equal the entry's.

    Args:
        name: The entry's npm name.
        version: The entry's version.
        resolved: The entry's ``resolved`` URL ("" when it has none).
        sources: The allowlist (:data:`SOURCES`).

    Returns:
        The matching row, or None (the entry is judged as an npm package).
    """
    if not resolved.startswith("https://"):
        return None
    try:
        url = urlsplit(resolved)
        port = url.port
    except ValueError:
        return None
    if url.username is not None or url.password is not None or port is not None or url.query or url.fragment:
        return None
    host = (url.hostname or "").lower()
    for src in sources:
        if host != src.host or name not in src.packages:
            continue
        m = src.path.fullmatch(url.path)
        if m and m.group("name") == name and m.group("version") == version:
            return src
    return None


def _holds(iv: specifiers.Interval, version: str) -> bool:
    """Return whether ``version`` lies in ``iv``."""
    return not specifiers.intersect(iv, specifiers.Interval(version, True, version, True)).empty()


def _beyond_last_known(entry: dict, version: str) -> str:
    """Return the entry's ``last_known_affected_version_range`` when ``version`` lies above it, else "".

    Only a plain ``< X`` / ``<= X`` bound is read; anything else is ignored (the open range then stands).
    """
    spec = entry.get("database_specific")
    text = spec.get("last_known_affected_version_range") if isinstance(spec, dict) else None
    m = _LAST_KNOWN.fullmatch(text.strip()) if isinstance(text, str) else None
    if not m:
        return ""
    op, bound = m.groups()
    cur, top = version_key(version), version_key(bound)
    above = cur > top if op == "<=" else cur >= top
    return f"{op} {bound}" if above else ""


def _unreadable(iv: specifiers.Interval) -> bool:
    """Return whether an interval cannot be trusted: a bound that is not version text, or a start above its end.

    :func:`versions.version_key` never fails and sorts text below every release, so such an interval would read as
    empty (clean) rather than as unknown (@security review of E3).
    """
    if any(b is not None and not b[:1].isdigit() for b in (iv.lo, iv.hi)):
        return True
    return iv.lo is not None and iv.hi is not None and version_key(iv.lo) > version_key(iv.hi)


def judge(record: object, name: str, version: str) -> tuple[str, str]:
    """Grade one npm advisory against a version installed from an allowlisted source.

    Each affected entry for ``name`` is read with :func:`osv.affected_intervals`. A closed interval holding the
    version is affected (its ``fixed`` bound is the fix). An open-ended interval holding it is affected unless the
    entry's ``last_known_affected_version_range`` ends below the version, which makes it unknown. A range with a
    bound that is not version text (``fixed: "next"``) or a start above its end is unknown too, never clean
    (:func:`_unreadable`). Affected wins over unknown across entries.

    Args:
        record: The OSV advisory (untrusted JSON).
        name: The npm name.
        version: The installed version.

    Returns:
        ``("affected", fix or "")``, ``("unknown", why)`` or ``("not affected", "")``.
    """
    if osv.affected_intervals(record, "npm", name) is None:
        return "unknown", "not judged (unknown range event, GIT-only or malformed range)"
    want = osv.normalize_name("npm", name)
    fixes: list[str] = []
    unfixed = False
    beyond: list[str] = []
    unreadable = False
    for aff in record.get("affected", []):   # affected_intervals checked the shape
        pkg = aff.get("package") or {}
        if pkg.get("ecosystem") != "npm" or osv.normalize_name("npm", pkg.get("name", "")) != want:
            continue
        ivs = osv.affected_intervals({"affected": [aff]}, "npm", name) or []
        unreadable = unreadable or any(_unreadable(iv) for iv in ivs)
        ivs = [iv for iv in ivs if not _unreadable(iv)]
        holding = [iv for iv in ivs if _holds(iv, version)]
        closed = [iv for iv in holding if iv.hi is not None]
        for iv in closed:
            if iv.fixed and osv.FIX_VERSION.fullmatch(iv.hi):
                fixes.append(iv.hi)
            else:
                unfixed = True
        if holding and not closed:
            bound = _beyond_last_known(aff, version)
            if bound:
                beyond.append(bound)
            else:
                unfixed = True   # open-ended with nothing bounding it: every version is affected
    if fixes or unfixed:
        return "affected", "" if unfixed else max(fixes, key=version_key)
    if unreadable:
        return "unknown", "not judged (a range bound is not a version, or a range starts above its end)"
    if beyond:
        return "unknown", (f"names no fixed version; last known affected {', '.join(sorted(set(beyond)))}, "
                           f"and {version} is above it")
    return "not affected", ""
