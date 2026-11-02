"""Distro verdicts for Python packages the OS package manager owns (plan vfr-a A2).

A package in an OS-managed path (:data:`engine.DISTRO_LOCATION`) is graded on upstream PyPI advisories, but Ubuntu
and Amazon Linux backport fixes without changing the upstream version. This module adds, beside that grade, what
the distribution says about each of the finding's advisories. The upstream row, severity, key and exit code stay as
they were (Rule 12); a verdict is :attr:`Finding.distro_verdict` and :attr:`Finding.distro_note`.

Verdicts (:data:`report.DISTRO_VERDICTS`), per advisory and then the worst across a finding's advisories:

| Verdict | Meaning |
|---|---|
| ``fixed-in-installed`` | the installed distro package already carries the fix |
| ``fix-available-not-installed`` | a fix is published in the archive this host can install from |
| ``esm-only`` | the only fix is in Ubuntu Pro ESM, which this host has not enabled (operator decision OD1) |
| ``no-distro-fix`` | the distribution publishes no fix, or no record joins the advisory to the package |

The join is advisory -> its CVE ids (the id itself, or the ``aliases`` / ``upstream`` of its OSV record) -> the
distribution's status for that CVE and the owning package from the ``DISTROPKG`` line:

- **Ubuntu** (``OSREL`` ``ubuntu``): OSV's ``Ubuntu:<release>[:LTS]`` and ``Ubuntu:Pro:<release>[:LTS]`` ecosystems,
  by **source** package and the installed package version. OSV says whether that exact version is affected
  (:func:`osv.vulnerable_ids`, its own dpkg ordering); a ``UBUNTU-<CVE>`` record that lists the package but does not
  affect the version is ``fixed-in-installed``. An affected one is ``fix-available-not-installed`` when the release
  entry has a fix, else ``esm-only`` when only a Pro entry (or an ``esm`` version) has one, else ``no-distro-fix``.
  An ESM fix counts as available when the ``ESM`` line shows the matching service enabled (esm-apps for a universe
  package, esm-infra otherwise): plan step A5.
- **Amazon Linux 2023** (``OSREL`` ``amzn`` ``2023``): the host's own updateinfo (ALAS), which only the host can
  read. ``UPDATEINFO`` lines (``installed`` or ``available`` pass, advisory, CVE, package NEVRA) join on the owning
  rpm's name: ``available`` is ``fix-available-not-installed``, ``installed`` is ``fixed-in-installed``, neither is
  ``no-distro-fix``. An ``UPDATEINFOREAD`` line per pass says whether dnf read its cache. A pass not ``ok`` gives
  no verdict and a LOW ``host`` finding named :data:`report.UPDATEINFO_CHECK` (not a feed error: the listing's
  format is unverified, and a mismatch must not fail the host's scan daily). Without any ``UPDATEINFOREAD`` line (an
  older kit, or a host without dnf) there is no AL2023 verdict.

No verdict (and a note saying why) when the owning package's origin is not an allowlisted distro archive
(``unowned``, ``repo:<host>:…``: the vlc-n5 Revision 3 rule), when the PyPI version is not the package's own
upstream version (the file is not the package's), when no ``DISTROPKG`` line covers a location, for a release
neither feed covers, or when a feed could not be read. An OSV failure (an Ubuntu query or record, or an advisory
record the CVE join needs, on either distro) is a feed error (``Report.feed_errors``, exit 3), never "not affected". A finding with any location ``unowned`` or ``repo:`` is not (only) OS-managed: it loses
``distro`` and is judged upstream like any other install, the stricter direction.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from agentteams.vulnscan import _http, osv
from agentteams.vulnscan.report import DISTRO_VERDICTS, UPDATEINFO_CHECK, Finding, Report, _code
from agentteams.vulnscan.versions import version_key

#: Rank of each verdict (report.DISTRO_VERDICTS, best first): a finding takes the worst of its advisories'.
_RANK = {v: i for i, v in enumerate(DISTRO_VERDICTS)}
#: ``UPDATEINFO`` passes: ``dnf -C updateinfo list --security --installed`` and ``--available``.
UPDATEINFO_PASSES = ("installed", "available")
_CVE = re.compile(r"^CVE-\d{4}-\d{4,}$")
_LTS = re.compile(r"^(\d\d)\.04$")


@dataclass(frozen=True)
class DistroPkg:
    """One ``DISTROPKG`` line: the OS package owning one PyPI package in an OS-managed path."""

    location: str
    name: str            # PyPI name, as installed
    version: str         # PyPI version, as installed
    binary: str          # owning dpkg package, or rpm NEVRA; "-" when unowned
    source: str          # source package (dpkg ``source:Package``; the SRPM name); "-" when unowned
    pkgversion: str      # installed version of the owning package; "-" when unowned
    origin: str          # ``pkg:<binary>[:universe]``, ``repo:<host|unknown>:<binary>`` or ``unowned``

    @property
    def allowlisted(self) -> bool:
        """Whether the package came from an allowlisted distro archive (``pkg:``): only then is there a verdict."""
        return self.origin.startswith("pkg:") and self.binary != "-" and self.source != "-" and self.pkgversion != "-"

    @property
    def universe(self) -> bool:
        """Whether the package is from Ubuntu universe/multiverse (its ESM fixes need esm-apps)."""
        return self.origin.endswith(":universe")


@dataclass(frozen=True)
class UpdateInfo:
    """One ``UPDATEINFO`` line: one CVE of one advisory for one package, from one dnf updateinfo pass."""

    state: str           # :data:`UPDATEINFO_PASSES`
    advisory: str        # ALAS id, or "-"
    cve: str
    nevra: str


def parse_distropkg(parts: list[str]) -> DistroPkg | None:
    """Read one tab-split ``DISTROPKG`` line.

    Args:
        parts: The line split on tabs, tag first.

    Returns:
        The record, or None when the line does not have the eight fields.
    """
    if len(parts) != 8 or parts[0] != "DISTROPKG":
        return None
    return DistroPkg(*parts[1:8])


def parse_updateinfo(parts: list[str]) -> UpdateInfo | None:
    """Read one tab-split ``UPDATEINFO`` line (``state``, advisory, CVE, NEVRA).

    Args:
        parts: The line split on tabs, tag first.

    Returns:
        The record, or None when the line is malformed (an unknown pass, or a CVE field that is not a CVE id).
    """
    if len(parts) != 5 or parts[0] != "UPDATEINFO" or parts[1] not in UPDATEINFO_PASSES:
        return None
    if not _CVE.match(parts[3]) or not parts[4]:
        return None
    return UpdateInfo(parts[1], parts[2], parts[3], parts[4])


def ubuntu_ecosystems(version_id: str) -> tuple[str, str] | None:
    """Return OSV's Ubuntu ecosystems for one release: the archive's and Ubuntu Pro's.

    Args:
        version_id: os-release ``VERSION_ID`` (``22.04``).

    Returns:
        ``("Ubuntu:22.04:LTS", "Ubuntu:Pro:22.04:LTS")`` for an LTS (an even year's ``.04``),
        ``("Ubuntu:24.10", "Ubuntu:Pro:24.10")`` otherwise; None for a version that is not ``YY.MM``.
    """
    if not re.fullmatch(r"\d\d\.\d\d", version_id):
        return None
    m = _LTS.match(version_id)
    suffix = ":LTS" if m and int(m.group(1)) % 2 == 0 else ""
    return f"Ubuntu:{version_id}{suffix}", f"Ubuntu:Pro:{version_id}{suffix}"


def upstream_matches(pypi_version: str, pkgversion: str) -> bool:
    """Return whether a distro package version is the build of this PyPI version (the file is the package's).

    The epoch (``N:``) and the packaging revision (after the last ``-``) are dropped, then any ``+dfsg`` style suffix;
    ``~`` (Debian's pre-release marker) is removed. The rest must equal the PyPI version, or order equal to it.

    Args:
        pypi_version: Version from the dist-info / egg-info.
        pkgversion: Installed version of the owning dpkg / rpm package.

    Returns:
        True when they agree.
    """
    up = re.sub(r"^\d+:", "", pkgversion)
    if "-" in up:
        up = up.rsplit("-", 1)[0]
    up = re.split(r"[+]", up, maxsplit=1)[0].replace("~", "")
    if not up:
        return False
    return up.lower() == pypi_version.lower() or version_key(up) == version_key(pypi_version)


def rpm_name(binary: str, pkgversion: str) -> str:
    """Return the rpm name from the NEVRA ``rpm -qf`` prints (``python3-urllib3-1.25.10-5.amzn2023.noarch``).

    Args:
        binary: The ``DISTROPKG`` binary field (a NEVRA, or already a name).
        pkgversion: The package's ``version-release``.

    Returns:
        The name part.
    """
    marker = f"-{pkgversion}"
    if pkgversion and marker in binary:
        return binary[:binary.index(marker)]
    parts = binary.rsplit("-", 2)
    return parts[0] if len(parts) == 3 else binary


def _cves(advisory: str) -> list[str]:
    """Return the CVE ids of one upstream advisory: the id itself, or its OSV record's ``aliases`` and ``upstream``.

    Raises:
        _http.FeedError: The advisory record could not be read.
    """
    if _CVE.match(advisory):
        return [advisory]
    rec = osv.details(advisory)
    found = []
    for field in ("aliases", "upstream"):
        vals = rec.get(field) if isinstance(rec, dict) else None
        found += [v for v in (vals if isinstance(vals, list) else []) if isinstance(v, str) and _CVE.match(v)]
    return sorted(set(found))


def _entry_fixes(record: object, ecosystem: str, source: str) -> list[tuple[str, bool]]:
    """Return ``(fixed version, ESM)`` for every fix an advisory record gives ``source`` in ``ecosystem``.

    A fix is ESM when its ecosystem is Ubuntu Pro, its version carries ``esm`` (Ubuntu's ESM version suffix), or the
    entry's ``ecosystem_specific.availability`` names Ubuntu Pro. The record is untrusted: anything of the wrong
    type is skipped (it can only remove a fix, never add one).
    """
    out: list[tuple[str, bool]] = []
    affected = record.get("affected") if isinstance(record, dict) else None
    for aff in affected if isinstance(affected, list) else []:
        pkg = aff.get("package") if isinstance(aff, dict) else None
        if not isinstance(pkg, dict) or pkg.get("ecosystem") != ecosystem or pkg.get("name") != source:
            continue
        spec = aff.get("ecosystem_specific")
        avail = str(spec.get("availability", "")) if isinstance(spec, dict) else ""
        pro = ":Pro:" in ecosystem or "ubuntu pro" in avail.lower()
        ranges = aff.get("ranges")
        for rng in ranges if isinstance(ranges, list) else []:
            events = rng.get("events") if isinstance(rng, dict) else None
            for ev in events if isinstance(events, list) else []:
                fixed = ev.get("fixed") if isinstance(ev, dict) else None
                if isinstance(fixed, str) and fixed:
                    out.append((fixed, pro or "esm" in fixed.lower()))
    return out


def _name(f: Finding) -> str:
    """A finding's package name for feed-error text, which the summary renders as is: capped, as a safe code span."""
    return _code(f.name[:80])


def _versions(vals: list[str]) -> str:
    """Up to two fix versions as text (record order; dpkg/rpm versions are not ordered here, OSV ordered them)."""
    uniq = list(dict.fromkeys(vals))
    return ", ".join(uniq[:2]) + (" …" if len(uniq) > 2 else "")


def _worst(verdicts: Iterable[str]) -> str:
    return max(verdicts, key=lambda v: _RANK[v], default="no-distro-fix")


@dataclass
class _Candidate:
    """A finding to judge, and the ``DISTROPKG`` records of its locations."""

    finding: Finding
    pkgs: list[DistroPkg]


def _candidates(target: str, records: list[DistroPkg], report: Report) -> list[_Candidate]:
    """Pick the host's OS-managed PyPI findings, apply the origin rule, and note why a finding gets no verdict."""
    by_loc = {(d.location.rstrip("/"), osv.normalize_name("PyPI", d.name), d.version): d for d in records}
    out: list[_Candidate] = []
    for f in report.findings:
        if f.kind != "package" or f.ecosystem != "PyPI" or not f.distro or f.target != target:
            continue
        pkgs = [by_loc.get((loc.rstrip("/"), osv.normalize_name("PyPI", f.name), f.version)) for loc in f.locations]
        if any(p is None for p in pkgs):
            f.distro_note = "no distro verdict: no DISTROPKG line for this location (kit without vfr-a A1)"
            continue
        owned = [p for p in pkgs if p is not None]
        if f.malicious:
            f.distro_note = "no distro verdict: malicious package version"
            continue
        off = [p for p in owned if not p.allowlisted]
        if off:
            label = ", ".join(sorted({p.origin.rsplit(":", 1)[0] if p.origin.startswith("repo:") else p.origin
                                      for p in off}))
            f.distro_note = f"no distro verdict: origin {label}, not an allowlisted distro archive (graded upstream)"
            f.distro = False   # a copy not OS-managed is judged like any other install (vlc-n5 Revision 3)
            continue
        bad = [p for p in owned if not upstream_matches(p.version, p.pkgversion)]
        if bad:
            p = bad[0]
            f.distro_note = (f"no distro verdict: {p.binary} {p.pkgversion} is not a build of {p.name} {p.version} "
                             "(the file is not the package's)")
            continue
        out.append(_Candidate(f, owned))
    return out


def _ubuntu(target: str, version_id: str, esm: tuple[str, str] | None, cands: list[_Candidate],
            report: Report) -> None:
    """Ubuntu verdicts from OSV (see the module docstring)."""
    ecos = ubuntu_ecosystems(version_id)
    if ecos is None:
        for c in cands:
            c.finding.distro_note = f"no distro verdict: Ubuntu VERSION_ID {version_id[:20]!r} unreadable"
        return
    std, pro = ecos
    names = sorted({(e, p.source) for c in cands for p in c.pkgs for e in (std, pro)})
    exact = sorted({(e, p.source, p.pkgversion) for c in cands for p in c.pkgs for e in (std, pro)})
    try:
        listed = osv.package_ids(names)
        affected = osv.vulnerable_ids(exact)
    except _http.FeedError as exc:
        report.feed_errors.append(f"{target}: Ubuntu distro verdicts ({std}): {exc}")
        for c in cands:
            c.finding.distro_note = "no distro verdict: the Ubuntu feed could not be read (feed error)"
        return
    infra, apps = esm or ("unknown", "unknown")

    def one(cve: str, p: DistroPkg) -> tuple[str, str] | None:
        rid = f"UBUNTU-{cve}"
        in_std, in_pro = rid in listed.get((std, p.source), []), rid in listed.get((pro, p.source), [])
        if not (in_std or in_pro):
            return None                                     # no Ubuntu record joins this CVE to the package
        hit_std = rid in affected.get((std, p.source, p.pkgversion), [])
        hit_pro = rid in affected.get((pro, p.source, p.pkgversion), [])
        if (in_std and not hit_std) or (in_pro and not hit_pro):
            return "fixed-in-installed", f"{cve}: not affected at {p.pkgversion}"
        rec = osv.details(rid)
        fixes = _entry_fixes(rec, std, p.source) + _entry_fixes(rec, pro, p.source)
        plain = [v for v, is_esm in fixes if not is_esm]
        esm_fix = [v for v, is_esm in fixes if is_esm]
        if plain:
            return "fix-available-not-installed", f"{cve}: fixed in {_versions(plain)}"
        if esm_fix:
            on = (apps if p.universe else infra) == "enabled"
            service = "esm-apps" if p.universe else "esm-infra"
            fix = _versions(esm_fix)
            if on:
                return "fix-available-not-installed", f"{cve}: fixed in {fix} ({service}, enabled)"
            return "esm-only", f"{cve}: fixed only in Ubuntu Pro {service} ({fix})"
        return "no-distro-fix", f"{cve}: Ubuntu publishes no fix"

    for c in cands:
        where = "; ".join(sorted({f"{std} {p.source} {p.pkgversion}" for p in c.pkgs}))
        try:
            verdict, notes = _judge(c, one, "Ubuntu")
        except _http.FeedError as exc:
            report.feed_errors.append(f"{target}: Ubuntu record for {_name(c.finding)}: {exc}")
            c.finding.distro_note = "no distro verdict: an advisory record could not be read (feed error)"
            continue
        c.finding.distro_verdict = verdict
        c.finding.distro_note = f"{where}: {notes}"


def _al2023(target: str, ups: list[UpdateInfo], reads: dict[str, str], age: str, cands: list[_Candidate],
            report: Report) -> None:
    """Amazon Linux 2023 verdicts from the host's updateinfo lines (see the module docstring)."""
    if not reads:
        for c in cands:
            c.finding.distro_note = "no distro verdict: L1 printed no UPDATEINFO lines (AL2023 updateinfo not read)"
        return
    unread = [p for p in UPDATEINFO_PASSES if reads.get(p) != "ok"]
    if unread:
        # Host-side, and its --with-cve format is unverified: no verdict (never "not affected"; the upstream grade
        # stands), a LOW host check so it is not silent, and a note in the summary. Not a feed error: a format
        # mismatch would otherwise make the host's scan exit 3 every day.
        passes = ", ".join(unread)
        report.findings.append(Finding(
            kind="host", target=target, name=UPDATEINFO_CHECK, severity=1,
            detail=(f"dnf updateinfo pass(es) unreadable or unparseable: {passes}; AL2023 distro verdicts not given "
                    "(upstream grades stand)")))
        for c in cands:
            c.finding.distro_note = f"no distro verdict: the host's dnf updateinfo ({passes}) was not read"
        return
    seen: dict[str, set[tuple[str, str]]] = {s: set() for s in UPDATEINFO_PASSES}
    for u in ups:
        seen[u.state].add((u.cve, rpm_name(u.nevra, "")))

    def one(cve: str, p: DistroPkg) -> tuple[str, str] | None:
        name = rpm_name(p.binary, p.pkgversion)
        if (cve, name) in seen["available"]:
            return "fix-available-not-installed", f"{cve}: an ALAS update for {name} is available"
        if (cve, name) in seen["installed"]:
            return "fixed-in-installed", f"{cve}: the ALAS update for {name} is installed"
        return None

    for c in cands:
        try:
            verdict, notes = _judge(c, one, "ALAS")   # an advisory's CVE ids may need its OSV record
        except _http.FeedError as exc:
            report.feed_errors.append(f"{target}: advisory record for {_name(c.finding)} (AL2023): {exc}")
            c.finding.distro_note = "no distro verdict: an advisory record could not be read (feed error)"
            continue
        c.finding.distro_verdict = verdict
        c.finding.distro_note = f"AL2023 updateinfo (cache {age} h old): {notes}"


def _judge(c: _Candidate, one: Callable[[str, DistroPkg], tuple[str, str] | None], feed: str) -> tuple[str, str]:
    """Roll per-CVE verdicts up into the finding's verdict and its notes.

    Per advisory and owning package, the best verdict among the advisory's CVE ids that a record joins (they are
    aliases of one vulnerability); no joined CVE is ``no-distro-fix``. The finding takes the worst across packages
    (a finding at two locations is fixed only when both are) and across advisories.

    Raises:
        _http.FeedError: An advisory record could not be read.
    """
    per_adv: list[str] = []
    notes: list[str] = []
    for adv in sorted(c.finding.ids):
        cves = _cves(adv)
        if not cves:
            per_adv.append("no-distro-fix")
            notes.append(f"{adv}: no CVE id to join to {feed}")
            continue
        per_pkg: list[tuple[str, str]] = []
        for p in c.pkgs:
            results = [r for r in (one(cve, p) for cve in cves) if r is not None]
            if results:
                per_pkg.append(min(results, key=lambda r: _RANK[r[0]]))
            else:
                per_pkg.append(("no-distro-fix", f"{adv} ({', '.join(cves[:3])}): no {feed} record for {p.source}"))
        verdict, note = max(per_pkg, key=lambda r: _RANK[r[0]])
        per_adv.append(verdict)
        notes.append(note if note.startswith(adv) else f"{adv} {note}")
    return _worst(per_adv), "; ".join(notes)


def apply(target: str, osrel: tuple[str, str, str] | None, esm: tuple[str, str] | None, records: list[DistroPkg],
          updateinfo: list[UpdateInfo], updateinfo_read: dict[str, str], autoupd: dict[tuple[str, str], str],
          report: Report) -> None:
    """Give each OS-managed PyPI finding of ``target`` its distro verdict, beside its upstream grade (Rule 12).

    Nothing happens without ``DISTROPKG`` lines (an older kit). Severities, rows and keys never change; a finding
    with any location unowned or from a non-allowlisted origin loses ``distro`` (it is judged upstream).

    Args:
        target: Host label.
        osrel: The ``OSREL`` record (ID, VERSION_ID, PRETTY_NAME), if any.
        esm: The ``ESM`` record (esm-infra, esm-apps), if any.
        records: ``DISTROPKG`` records.
        updateinfo: ``UPDATEINFO`` records (AL2023).
        updateinfo_read: Per pass, the ``UPDATEINFOREAD`` status (``ok`` or anything else: failed).
        autoupd: ``AUTOUPD`` values by (manager, key), for the metadata age shown with AL2023 verdicts.
        report: The host's report, with the package findings; extended in place (notes, ``feed_errors``).
    """
    if not records:
        return
    cands = _candidates(target, records, report)
    if not cands:
        return
    os_id, version_id = (osrel[0], osrel[1]) if osrel else ("unknown", "")
    if os_id == "ubuntu":
        _ubuntu(target, version_id, esm, cands, report)
    elif os_id == "amzn" and version_id == "2023":
        _al2023(target, updateinfo, updateinfo_read, autoupd.get(("dnf", "metadata-age-hours"), "unknown"), cands,
                report)
    else:
        for c in cands:
            c.finding.distro_note = f"no distro verdict: no distro feed for {os_id[:20]} {version_id[:20]}".rstrip()
