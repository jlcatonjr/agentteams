"""vfr-a A2: distro verdicts for OS-managed Python packages, against recorded OSV / updateinfo answers.

Rule 12: a verdict sits beside the upstream grade; no row, severity or key changes, and a finding owned by a
distro package keeps its exit-code treatment. Unowned and non-allowlisted origins get no verdict. A feed failure is a
feed error, never "not affected".
"""

from __future__ import annotations

import pytest

from agentteams.vulnscan import _http, distro_verdict, engine, osv
from agentteams.vulnscan.report import Report
from tests.test_vulnscan_core import Feed, _adv

DP = "/usr/lib/python3/dist-packages"
STD, PRO = "Ubuntu:22.04:LTS", "Ubuntu:Pro:22.04:LTS"


class DistroFeed(Feed):
    """:class:`Feed` that also answers name-only queries (``osv.package_ids``) from ``listed``."""

    def __init__(self) -> None:
        super().__init__()
        self.listed: dict[tuple[str, str], list[str]] = {}
        self.calls: list[str] = []
        self.queried: set[tuple[str, str]] = set()   # (ecosystem, name) of every querybatch query

    def __call__(self, url: str, payload: dict | None = None, **kw: object) -> object:
        self.calls.append(url)
        for q in (payload or {}).get("queries", []):
            self.queried.add((q["package"]["ecosystem"], q["package"]["name"]))
        if self.fail:
            raise _http.FeedError("simulated outage")
        if url.endswith("/querybatch") and payload and any("version" not in q for q in payload["queries"]):
            out = []
            for q in payload["queries"]:
                ids = self.listed.get((q["package"]["ecosystem"], q["package"]["name"]), [])
                out.append({"vulns": [{"id": i} for i in ids]} if ids else {})
            return {"results": out}
        return super().__call__(url, payload, **kw)


@pytest.fixture
def dfeed(monkeypatch: pytest.MonkeyPatch) -> DistroFeed:
    f = DistroFeed()
    monkeypatch.setattr(_http, "fetch_json", f)
    monkeypatch.setattr(engine, "_os_findings", lambda *_a: None)   # the OSREL end-of-life check is tested elsewhere
    osv._DETAILS.clear()
    return f


def _up(vid: str, name: str, fixed: str, cves: list[str], sev: str = "CRITICAL") -> dict:
    rec = _adv(vid, "PyPI", name, "0", fixed, sev)
    rec["aliases"] = cves
    return rec


def _ubuntu(cve: str, source: str, std_fix: str | None, pro_fix: str | None = None) -> dict:
    """A ``UBUNTU-CVE-*`` record: the release entry (fixed or not) and optionally a Pro entry."""
    def entry(eco: str, fix: str | None) -> dict:
        events = [{"introduced": "0"}] + ([{"fixed": fix}] if fix else [])
        return {"package": {"ecosystem": eco, "name": source}, "ranges": [{"type": "ECOSYSTEM", "events": events}]}
    aff = [entry(STD, std_fix)] + ([entry(PRO, pro_fix)] if pro_fix else [])
    return {"id": f"UBUNTU-{cve}", "upstream": [cve], "affected": aff}


VK_API = "\n".join([
    "OSREL\tubuntu\t22.04\tUbuntu 22.04.5 LTS",
    "ESM\tdisabled\tdisabled",
    f"INV\tPyPI\tPyJWT\t2.3.0\t{DP}",
    f"INV\tPyPI\tTwisted\t22.1.0\t{DP}",
    f"INV\tPyPI\tcertifi\t2020.6.20\t{DP}",
    f"INV\tPyPI\tBrotli\t1.0.9\t{DP}",
    f"DISTROPKG\t{DP}\tPyJWT\t2.3.0\tpython3-jwt\tpyjwt\t2.3.0-1ubuntu0.2\tpkg:python3-jwt",
    f"DISTROPKG\t{DP}\tTwisted\t22.1.0\tpython3-twisted\ttwisted\t22.1.0-2ubuntu2.4\tpkg:python3-twisted:universe",
    f"DISTROPKG\t{DP}\tcertifi\t2020.6.20\tpython3-certifi\tpython-certifi\t2020.6.20-1\tpkg:python3-certifi",
    f"DISTROPKG\t{DP}\tBrotli\t1.0.9\tpython3-brotli\tbrotli\t1.0.9-2build6\tpkg:python3-brotli",
])


def _vk_api(f: DistroFeed) -> None:
    f.vulns = {("PyPI", "PyJWT", "2.3.0"): ["GHSA-jwt"], ("PyPI", "Twisted", "22.1.0"): ["GHSA-tw"],
               ("PyPI", "certifi", "2020.6.20"): ["PYSEC-cert"], ("PyPI", "Brotli", "1.0.9"): ["GHSA-br"],
               # OSV's own dpkg ordering: which exact installed versions the Ubuntu records still affect
               (STD, "twisted", "22.1.0-2ubuntu2.4"): ["UBUNTU-CVE-2024-41671"],
               (PRO, "twisted", "22.1.0-2ubuntu2.4"): ["UBUNTU-CVE-2024-41671"],
               (STD, "python-certifi", "2020.6.20-1"): ["UBUNTU-CVE-2023-37920"],
               (STD, "brotli", "1.0.9-2build6"): ["UBUNTU-CVE-2020-8927"]}
    f.records = {"GHSA-jwt": _up("GHSA-jwt", "PyJWT", "2.4.0", ["CVE-2022-29217"]),
                 "GHSA-tw": _up("GHSA-tw", "Twisted", "24.7.0", ["CVE-2024-41671"]),
                 "PYSEC-cert": _up("PYSEC-cert", "certifi", "2023.7.22", ["CVE-2023-37920"]),
                 "GHSA-br": _up("GHSA-br", "Brotli", "1.0.9.1", ["CVE-2020-8927"], "MODERATE"),
                 "UBUNTU-CVE-2022-29217": _ubuntu("CVE-2022-29217", "pyjwt", "2.3.0-1ubuntu0.2"),
                 "UBUNTU-CVE-2024-41671": _ubuntu("CVE-2024-41671", "twisted", None, "22.1.0-2ubuntu2.6+esm1"),
                 "UBUNTU-CVE-2023-37920": _ubuntu("CVE-2023-37920", "python-certifi", "2020.6.20-1ubuntu0.1"),
                 "UBUNTU-CVE-2020-8927": _ubuntu("CVE-2020-8927", "brotli", None)}
    f.listed = {(STD, "pyjwt"): ["UBUNTU-CVE-2022-29217"], (STD, "twisted"): ["UBUNTU-CVE-2024-41671"],
                (PRO, "twisted"): ["UBUNTU-CVE-2024-41671"], (STD, "python-certifi"): ["UBUNTU-CVE-2023-37920"],
                (STD, "brotli"): ["UBUNTU-CVE-2020-8927"]}


def _by(rep: Report) -> dict:
    return {f.name: f for f in rep.findings if f.kind == "package"}


# ---------------------------------------------------------------- Ubuntu

def test_ubuntu_each_verdict_from_osv_by_source_package(dfeed: DistroFeed) -> None:
    _vk_api(dfeed)
    rep = engine.scan_inventory(VK_API, "vk-api")
    by = _by(rep)
    assert rep.feed_errors == []
    assert by["PyJWT"].distro_verdict == "fixed-in-installed"            # a known backport: 2.3.0 stays 2.3.0
    assert "CVE-2022-29217: not affected at 2.3.0-1ubuntu0.2" in by["PyJWT"].distro_note
    assert "Ubuntu:22.04:LTS pyjwt 2.3.0-1ubuntu0.2" in by["PyJWT"].distro_note
    assert by["Twisted"].distro_verdict == "esm-only"
    assert "fixed only in Ubuntu Pro esm-apps (22.1.0-2ubuntu2.6+esm1)" in by["Twisted"].distro_note
    assert by["certifi"].distro_verdict == "fix-available-not-installed"
    assert "fixed in 2020.6.20-1ubuntu0.1" in by["certifi"].distro_note
    assert by["Brotli"].distro_verdict == "no-distro-fix"
    assert "Ubuntu publishes no fix" in by["Brotli"].distro_note
    # the join used the SOURCE package names, never the binary ones
    assert {n for e, n in dfeed.queried if e.startswith("Ubuntu:")} == {"pyjwt", "twisted", "python-certifi", "brotli"}
    assert {e for e, _n in dfeed.queried if e.startswith("Ubuntu:")} == {STD, PRO}


def test_rule12_verdicts_change_no_row_grade_key_or_exit_code(dfeed: DistroFeed) -> None:
    _vk_api(dfeed)
    judged = engine.scan_inventory(VK_API, "vk-api")
    plain = engine.scan_inventory("\n".join(ln for ln in VK_API.splitlines() if not ln.startswith("DISTROPKG")),
                                  "vk-api")
    assert [(f.name, f.severity, f.fix, f.locations, f.key(), f.distro) for f in judged.findings] == \
        [(f.name, f.severity, f.fix, f.locations, f.key(), f.distro) for f in plain.findings]
    for fail_on in ("critical", "high", "moderate", "low", "any"):
        assert judged.exit_code(fail_on) == plain.exit_code(fail_on)
    assert all(not f.distro_verdict and not f.distro_note for f in plain.findings)   # an older kit: nothing


def test_esm_enabled_makes_an_esm_fix_installable(dfeed: DistroFeed) -> None:   # plan step A5
    _vk_api(dfeed)
    rep = engine.scan_inventory(VK_API.replace("ESM\tdisabled\tdisabled", "ESM\tenabled\tenabled"), "vk-api")
    tw = _by(rep)["Twisted"]
    assert tw.distro_verdict == "fix-available-not-installed" and "(esm-apps, enabled)" in tw.distro_note
    # esm-infra alone does not cover a universe package
    rep = engine.scan_inventory(VK_API.replace("ESM\tdisabled\tdisabled", "ESM\tenabled\tdisabled"), "vk-api")
    assert _by(rep)["Twisted"].distro_verdict == "esm-only"


def test_an_esm_installed_version_is_fixed(dfeed: DistroFeed) -> None:
    _vk_api(dfeed)
    inv = VK_API.replace("22.1.0-2ubuntu2.4", "22.1.0-2ubuntu2.6+esm1")
    dfeed.vulns[(STD, "twisted", "22.1.0-2ubuntu2.6+esm1")] = ["UBUNTU-CVE-2024-41671"]   # no release fix
    rep = engine.scan_inventory(inv, "vk-api")
    assert _by(rep)["Twisted"].distro_verdict == "fixed-in-installed"   # the Pro entry no longer affects it


def test_summary_reports_each_verdict_and_esm_only_per_host(dfeed: DistroFeed) -> None:
    _vk_api(dfeed)
    rep = engine.scan_inventory(VK_API, "vk-api")
    other = engine.scan_inventory(VK_API.replace("pkg:python3-twisted:universe", "pkg:python3-twisted"), "jlc-prod")
    rep.merge(other)
    assert rep.distro_verdict_counts() == {
        "jlc-prod": {"fixed-in-installed": 1, "fix-available-not-installed": 1, "esm-only": 1, "no-distro-fix": 1,
                     "none": 0},
        "vk-api": {"fixed-in-installed": 1, "fix-available-not-installed": 1, "esm-only": 1, "no-distro-fix": 1,
                   "none": 0}}
    md = rep.markdown()
    assert "## Distro verdicts (OS-managed Python packages; upstream grade unchanged)" in md
    assert "| `vk-api` | 1 | 1 | 1 | 1 | 0 |" in md
    assert "**esm-only per host** (the only fix is Ubuntu Pro ESM, not enabled; operator decision OD1): " \
           "`jlc-prod` 1, `vk-api` 1" in md
    assert "distro: esm-only (`Ubuntu:22.04:LTS twisted 22.1.0-2ubuntu2.4: GHSA-tw CVE-2024-41671" in md
    assert md.count("| Twisted (PyPI) |") == 2 and "| CRITICAL | package | Twisted (PyPI) |" in md   # Rule 12
    back = Report.from_dict(rep.to_dict())
    assert back.distro_verdict_counts() == rep.distro_verdict_counts()
    assert Report.from_dict({"target": "h", "findings": [{"kind": "package", "target": "h", "name": "a"}]}) \
        .findings[0].distro_verdict == ""                                  # an older report still loads


def test_unowned_and_third_party_origins_get_no_verdict_and_are_judged_upstream(dfeed: DistroFeed) -> None:
    _vk_api(dfeed)
    inv = VK_API.replace("python3-certifi\tpython-certifi\t2020.6.20-1\tpkg:python3-certifi", "-\t-\t-\tunowned") \
        .replace("pkg:python3-brotli", "repo:ppa.launchpadcontent.net:python3-brotli")
    rep = engine.scan_inventory(inv, "vk-api")
    by = _by(rep)
    assert by["certifi"].distro_verdict == "" and "origin unowned" in by["certifi"].distro_note
    assert by["Brotli"].distro_verdict == "" and "repo:ppa.launchpadcontent.net" in by["Brotli"].distro_note
    assert not by["certifi"].distro and not by["Brotli"].distro          # not OS-managed: graded upstream
    assert by["certifi"].severity == 4 and rep.exit_code("high") == 1    # now counts toward the threshold
    assert rep.distro_verdict_counts()["vk-api"]["none"] == 2
    assert not any("python-certifi" in c or "brotli" in c for c in dfeed.calls)


def test_one_unowned_copy_is_enough_to_judge_upstream(dfeed: DistroFeed) -> None:
    _vk_api(dfeed)
    dp2 = "/usr/lib/python3.10/dist-packages"
    inv = VK_API + f"\nINV\tPyPI\tPyJWT\t2.3.0\t{dp2}\nDISTROPKG\t{dp2}\tPyJWT\t2.3.0\t-\t-\t-\tunowned"
    jwt = _by(engine.scan_inventory(inv, "vk-api"))["PyJWT"]
    assert jwt.distro_verdict == "" and not jwt.distro and "origin unowned" in jwt.distro_note


def test_status_only_certifi_newer_than_any_package_gets_no_verdict(dfeed: DistroFeed) -> None:
    """vk-api certifi 2026.6.17 sat in dist-packages; no 22.04 package carries it (plan A2's example)."""
    _vk_api(dfeed)
    dfeed.vulns[("PyPI", "certifi", "2026.6.17")] = ["PYSEC-cert"]
    inv = VK_API.replace("certifi\t2020.6.20\t", "certifi\t2026.6.17\t")   # the PyPI fields, not the deb version
    rep = engine.scan_inventory(inv, "vk-api")
    c = _by(rep)["certifi"]
    assert c.distro_verdict == "" and "is not a build of certifi 2026.6.17" in c.distro_note
    assert c.distro                                                       # still dpkg-owned: unchanged


def test_ubuntu_feed_failure_is_a_feed_error_never_fixed(dfeed: DistroFeed, monkeypatch: pytest.MonkeyPatch) -> None:
    _vk_api(dfeed)

    def broken(_items: object) -> dict:
        raise _http.FeedError("simulated outage")
    monkeypatch.setattr(osv, "package_ids", broken)
    rep = engine.scan_inventory(VK_API, "vk-api")
    assert any("Ubuntu distro verdicts (Ubuntu:22.04:LTS)" in e for e in rep.feed_errors), rep.feed_errors
    assert all(f.distro_verdict == "" for f in rep.findings)
    assert rep.exit_code("high") == 3


def test_an_unreadable_ubuntu_record_is_a_feed_error(dfeed: DistroFeed, monkeypatch: pytest.MonkeyPatch) -> None:
    _vk_api(dfeed)
    real = osv.details

    def details(vid: str) -> dict:
        if vid == "UBUNTU-CVE-2023-37920":
            raise _http.FeedError("HTTP 503")
        return real(vid)
    monkeypatch.setattr(osv, "details", details)
    rep = engine.scan_inventory(VK_API, "vk-api")
    c = _by(rep)["certifi"]
    assert c.distro_verdict == "" and "feed error" in c.distro_note
    assert any("Ubuntu record for certifi" in e for e in rep.feed_errors)
    assert _by(rep)["PyJWT"].distro_verdict == "fixed-in-installed"       # the others are still judged


def test_no_ubuntu_record_or_no_cve_is_no_distro_fix(dfeed: DistroFeed) -> None:
    _vk_api(dfeed)
    dfeed.listed.pop((STD, "pyjwt"))
    dfeed.records["GHSA-br"]["aliases"] = []
    rep = engine.scan_inventory(VK_API, "vk-api")
    by = _by(rep)
    assert by["PyJWT"].distro_verdict == "no-distro-fix" and "no Ubuntu record for pyjwt" in by["PyJWT"].distro_note
    assert by["Brotli"].distro_verdict == "no-distro-fix" and "GHSA-br: no CVE id to join" in by["Brotli"].distro_note


def test_aliases_take_the_best_and_advisories_the_worst(dfeed: DistroFeed) -> None:
    _vk_api(dfeed)
    dfeed.records["GHSA-jwt"]["aliases"] = ["CVE-2022-29217", "CVE-2099-0001"]   # second alias: no record
    dfeed.vulns[("PyPI", "PyJWT", "2.3.0")] = ["GHSA-jwt", "GHSA-jwt2"]
    dfeed.records["GHSA-jwt2"] = _up("GHSA-jwt2", "PyJWT", "2.4.0", ["CVE-2023-0002"], "LOW")
    dfeed.records["UBUNTU-CVE-2023-0002"] = _ubuntu("CVE-2023-0002", "pyjwt", "2.3.0-1ubuntu0.3")
    dfeed.listed[(STD, "pyjwt")] = ["UBUNTU-CVE-2022-29217", "UBUNTU-CVE-2023-0002"]
    dfeed.vulns[(STD, "pyjwt", "2.3.0-1ubuntu0.2")] = ["UBUNTU-CVE-2023-0002"]
    rep = engine.scan_inventory(VK_API, "vk-api")
    jwt = _by(rep)["PyJWT"]
    assert jwt.distro_verdict == "fix-available-not-installed"
    assert "GHSA-jwt CVE-2022-29217: not affected" in jwt.distro_note
    assert "GHSA-jwt2 CVE-2023-0002: fixed in 2.3.0-1ubuntu0.3" in jwt.distro_note


def test_a_finding_at_two_locations_is_fixed_only_when_both_are(dfeed: DistroFeed) -> None:
    _vk_api(dfeed)
    dp2 = "/usr/lib/python3.10/dist-packages"
    inv = VK_API + "\n" + "\n".join([
        f"INV\tPyPI\tPyJWT\t2.3.0\t{dp2}",
        f"DISTROPKG\t{dp2}\tPyJWT\t2.3.0\tpython3-jwt-alt\tpyjwt\t2.3.0-1\tpkg:python3-jwt-alt"])
    dfeed.vulns[(STD, "pyjwt", "2.3.0-1")] = ["UBUNTU-CVE-2022-29217"]
    rep = engine.scan_inventory(inv, "vk-api")
    jwt = _by(rep)["PyJWT"]
    assert jwt.locations == sorted([DP, dp2])
    assert jwt.distro_verdict == "fix-available-not-installed", jwt.distro_note


def test_a_location_without_a_distropkg_line_gets_no_verdict(dfeed: DistroFeed) -> None:
    _vk_api(dfeed)
    inv = "\n".join(ln for ln in VK_API.splitlines() if not ln.startswith("DISTROPKG\t" + DP + "\tPyJWT"))
    rep = engine.scan_inventory(inv, "vk-api")
    assert _by(rep)["PyJWT"].distro_verdict == "" and "no DISTROPKG line" in _by(rep)["PyJWT"].distro_note


def test_a_release_without_a_distro_feed_gets_a_note(dfeed: DistroFeed) -> None:
    _vk_api(dfeed)
    rep = engine.scan_inventory(VK_API.replace("OSREL\tubuntu\t22.04", "OSREL\tdebian\t12"), "h")
    assert {f.distro_note for f in _by(rep).values()} == {"no distro verdict: no distro feed for debian 12"}
    assert rep.feed_errors == []


# ---------------------------------------------------------------- Amazon Linux 2023

SP9 = "/usr/lib/python3.9/site-packages"
BASTION = "\n".join([
    "OSREL\tamzn\t2023\tAmazon Linux 2023.6.20250303",
    "AUTOUPD\tdnf\tmetadata-age-hours\t20",
    f"INV\tPyPI\turllib3\t1.25.10\t{SP9}",
    f"INV\tPyPI\trequests\t2.25.1\t{SP9}",
    f"INV\tPyPI\tidna\t2.10\t{SP9}",
    f"DISTROPKG\t{SP9}\turllib3\t1.25.10\tpython3-urllib3-1.25.10-5.amzn2023.0.3.noarch\tpython-urllib3"
    "\t1.25.10-5.amzn2023.0.3\tpkg:python3-urllib3-1.25.10-5.amzn2023.0.3.noarch",
    f"DISTROPKG\t{SP9}\trequests\t2.25.1\tpython3-requests-2.25.1-1.amzn2023.0.4.noarch\tpython-requests"
    "\t2.25.1-1.amzn2023.0.4\tpkg:python3-requests-2.25.1-1.amzn2023.0.4.noarch",
    f"DISTROPKG\t{SP9}\tidna\t2.10\tpython3-idna-2.10-3.amzn2023.0.2.noarch\tpython-idna\t2.10-3.amzn2023.0.2"
    "\tpkg:python3-idna-2.10-3.amzn2023.0.2.noarch",
    "UPDATEINFOREAD\tinstalled\tok",
    "UPDATEINFOREAD\tavailable\tok",
    "UPDATEINFO\tinstalled\tALAS2023-2024-500\tCVE-2023-43804\tpython3-urllib3-1.25.10-5.amzn2023.0.3.noarch",
    "UPDATEINFO\tavailable\tALAS2023-2025-900\tCVE-2024-35195\tpython3-requests-2.25.1-1.amzn2023.0.5.noarch",
])


def _bastion(f: DistroFeed) -> None:
    f.vulns = {("PyPI", "urllib3", "1.25.10"): ["GHSA-u"], ("PyPI", "requests", "2.25.1"): ["GHSA-r"],
               ("PyPI", "idna", "2.10"): ["GHSA-i"]}
    f.records = {"GHSA-u": _up("GHSA-u", "urllib3", "1.26.18", ["CVE-2023-43804"], "HIGH"),
                 "GHSA-r": _up("GHSA-r", "requests", "2.32.0", ["CVE-2024-35195"], "MODERATE"),
                 "GHSA-i": _up("GHSA-i", "idna", "3.7", ["CVE-2024-3651"], "MODERATE")}


def test_al2023_verdicts_from_the_hosts_updateinfo(dfeed: DistroFeed) -> None:
    _bastion(dfeed)
    rep = engine.scan_inventory(BASTION, "vk-bastion")
    by = _by(rep)
    assert rep.feed_errors == []
    assert by["urllib3"].distro_verdict == "fixed-in-installed"
    assert by["requests"].distro_verdict == "fix-available-not-installed"
    assert by["idna"].distro_verdict == "no-distro-fix" and "no ALAS record for python-idna" in by["idna"].distro_note
    assert by["urllib3"].distro_note.startswith("AL2023 updateinfo (cache 20 h old): ")
    assert not any("Ubuntu" in c for c in dfeed.calls)                   # no network source for ALAS
    assert rep.distro_verdict_counts()["vk-bastion"]["esm-only"] == 0


def test_al2023_without_updateinfo_lines_gets_no_verdict_and_no_feed_error(dfeed: DistroFeed) -> None:
    _bastion(dfeed)
    rep = engine.scan_inventory("\n".join(ln for ln in BASTION.splitlines() if not ln.startswith("UPDATEINFO")),
                                "vk-bastion")
    assert rep.feed_errors == [] and all(f.distro_verdict == "" for f in rep.findings)
    assert "L1 printed no UPDATEINFO lines" in _by(rep)["urllib3"].distro_note


@pytest.mark.parametrize("bad", ["UPDATEINFOREAD\tavailable\tfailed", "UPDATEINFOREAD\tavailable\tweird"])
def test_al2023_an_unread_updateinfo_pass_is_a_feed_error(dfeed: DistroFeed, bad: str) -> None:
    _bastion(dfeed)
    rep = engine.scan_inventory(BASTION + "\n" + bad, "vk-bastion")   # a later failed line wins over "ok"
    assert any("dnf updateinfo (available) could not be read" in e for e in rep.feed_errors), rep.feed_errors
    assert all(f.distro_verdict == "" for f in rep.findings) and rep.exit_code("high") == 3


# ---------------------------------------------------------------- helpers

@pytest.mark.parametrize(("vid", "want"), [
    ("22.04", (STD, PRO)), ("24.04", ("Ubuntu:24.04:LTS", "Ubuntu:Pro:24.04:LTS")),
    ("24.10", ("Ubuntu:24.10", "Ubuntu:Pro:24.10")), ("23.04", ("Ubuntu:23.04", "Ubuntu:Pro:23.04")),
    ("jammy", None), ("", None)])
def test_ubuntu_ecosystems(vid: str, want: tuple[str, str] | None) -> None:
    assert distro_verdict.ubuntu_ecosystems(vid) == want


@pytest.mark.parametrize(("pypi", "pkg", "ok"), [
    ("2.3.0", "2.3.0-1ubuntu0.2", True), ("1.0.9", "1.0.9-2build6", True), ("22.1.0", "22.1.0-2ubuntu2.6+esm1", True),
    ("1.26.5", "1.26.5+dfsg-1", True), ("0.9.0rc1", "1:0.9.0~rc1-1", True), ("1.25.10", "1.25.10-5.amzn2023.0.3", True),
    ("2026.6.17", "2020.6.20-1", False), ("2.3.0", "-", False), ("2.3", "2.30-1", False)])
def test_upstream_matches(pypi: str, pkg: str, ok: bool) -> None:
    assert distro_verdict.upstream_matches(pypi, pkg) is ok


def test_rpm_name_and_malformed_lines() -> None:
    assert distro_verdict.rpm_name("python3-urllib3-1.25.10-5.amzn2023.0.3.noarch", "1.25.10-5.amzn2023.0.3") == \
        "python3-urllib3"
    assert distro_verdict.rpm_name("python3-requests-2.25.1-1.amzn2023.0.5.noarch", "") == "python3-requests"
    assert distro_verdict.parse_updateinfo(["UPDATEINFO", "installed", "-", "not-a-cve", "x-1-1.noarch"]) is None
    assert distro_verdict.parse_updateinfo(["UPDATEINFO", "maybe", "-", "CVE-2024-1234", "x-1-1.noarch"]) is None
    assert distro_verdict.parse_distropkg(["DISTROPKG", DP, "a", "1"]) is None


# ---------------------------------------------------------------- L1-audit.sh: the AL2023 updateinfo emitter

from tests.test_vulnscan_server_kit import Kit, _host, kit  # noqa: E402, F401  (kit: fixture)
from tests.test_vulnscan_server_kit_vfr import _dist, _run  # noqa: E402


def _fake_dnf(installed: str, available: str, *, fail: str = "", log: str = "/dev/null") -> str:
    """A dnf answering the two cache-only ``--with-cve`` passes, logging each call to ``log``; ``fail`` exits 1."""
    return f"""#!/bin/bash
echo "$*" >> '{log}'
case "$*" in
  *"-C -q updateinfo list --security --installed --with-cve"*) [ "{fail}" = installed ] && exit 1; printf '%b' '{installed}';;
  *"-C -q updateinfo list --security --available --with-cve"*) [ "{fail}" = available ] && exit 1; printf '%b' '{available}';;
esac
exit 0
"""


INSTALLED = r"CVE-2023-43804 Important/Sec. python3-urllib3-1.25.10-5.amzn2023.0.3.noarch\n"
AVAILABLE = (r"  CVE-2024-35195 Moderate/Sec.  python3-requests-2.25.1-1.amzn2023.0.5.noarch \n\n"
             r"CVE-2024-6345 Important/Sec. python3-setuptools-1:59.6.0-2.amzn2023.0.6.noarch\n")


def _updateinfo(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln.startswith(("UPDATEINFO\t", "UPDATEINFOREAD\t"))]


def test_l1_prints_updateinfo_records_from_both_cache_only_passes(kit: Kit) -> None:
    log = kit.root / "dnf-calls.txt"
    hb = _host(kit, extra={"dnf": _fake_dnf(INSTALLED, AVAILABLE, log=str(log))})
    out = _run(hb, kit.root / "hostroot").stdout
    assert _updateinfo(out) == [
        "UPDATEINFO\tinstalled\t-\tCVE-2023-43804\tpython3-urllib3-1.25.10-5.amzn2023.0.3.noarch",
        "UPDATEINFOREAD\tinstalled\tok",
        "UPDATEINFO\tavailable\t-\tCVE-2024-35195\tpython3-requests-2.25.1-1.amzn2023.0.5.noarch",
        "UPDATEINFO\tavailable\t-\tCVE-2024-6345\tpython3-setuptools-1:59.6.0-2.amzn2023.0.6.noarch",
        "UPDATEINFOREAD\tavailable\tok"], out
    calls = log.read_text().splitlines()
    assert sum("--with-cve" in c for c in calls) == 2, calls
    assert all(" -C " in f" {c} " or "--cacheonly" in c for c in calls), calls   # never the network
    assert not any("--refresh" in c or "upgrade" in c.split() or "install" in c.split() for c in calls)


def test_l1_an_empty_pass_is_read_ok_with_no_records(kit: Kit) -> None:
    hb = _host(kit, extra={"dnf": _fake_dnf("", "")})
    assert _updateinfo(_run(hb, kit.root / "hostroot").stdout) == ["UPDATEINFOREAD\tinstalled\tok",
                                                                   "UPDATEINFOREAD\tavailable\tok"]


@pytest.mark.parametrize("bad", [
    r"ALAS2023-2024-500 Important/Sec. python3-urllib3-1.25.10-5.amzn2023.0.3.noarch\n",   # no CVE column
    r"CVE-2023-43804 Important python3-urllib3-1.25.10-5.amzn2023.0.3.noarch\n",           # not a /Sec. severity
    r"CVE-2023-43804 Important/Sec.\n",                                                     # no NEVRA
    r"CVE-2023-43804 Important/Sec. python3-urllib3 extra\n",                               # extra column
    r"Last metadata expiration check: 0:10:00 ago\n",                                       # chatter
    r"CVE-2023-43804 Important/Sec. ../../etc/passwd\n"])                                   # not a NEVRA
def test_l1_an_unparseable_line_fails_the_pass_and_prints_no_records(kit: Kit, bad: str) -> None:
    hb = _host(kit, extra={"dnf": _fake_dnf(INSTALLED + bad, AVAILABLE)})
    got = _updateinfo(_run(hb, kit.root / "hostroot").stdout)
    assert "UPDATEINFOREAD\tinstalled\tfailed" in got and "UPDATEINFOREAD\tavailable\tok" in got
    assert not any(ln.startswith("UPDATEINFO\tinstalled\t") for ln in got), got


def test_l1_a_failing_dnf_pass_is_failed(kit: Kit) -> None:
    hb = _host(kit, extra={"dnf": _fake_dnf(INSTALLED, AVAILABLE, fail="available")})
    got = _updateinfo(_run(hb, kit.root / "hostroot").stdout)
    assert "UPDATEINFOREAD\tavailable\tfailed" in got and not any("\tavailable\t-\t" in ln for ln in got)
    assert "UPDATEINFOREAD\tinstalled\tok" in got


def test_l1_no_dnf_prints_no_updateinfo(kit: Kit) -> None:
    hb = _host(kit, extra={"yum": "#!/bin/sh\nexit 0\n"})
    assert _updateinfo(_run(hb, kit.root / "hostroot").stdout) == []


def test_l1_updateinfo_feeds_the_al2023_verdict_end_to_end(kit: Kit, dfeed: DistroFeed) -> None:
    _bastion(dfeed)
    hb = _host(kit, extra={"dnf": _fake_dnf(INSTALLED, AVAILABLE)})
    lines = _updateinfo(_run(hb, kit.root / "hostroot").stdout)
    inv = "\n".join([ln for ln in BASTION.splitlines() if not ln.startswith("UPDATEINFO")] + lines)
    rep = engine.scan_inventory(inv, "vk-bastion")
    by = _by(rep)
    assert rep.feed_errors == []
    assert (by["urllib3"].distro_verdict, by["requests"].distro_verdict, by["idna"].distro_verdict) == \
        ("fixed-in-installed", "fix-available-not-installed", "no-distro-fix")
