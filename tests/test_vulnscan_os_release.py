"""test_vulnscan_os_release.py — the server kit's OS release and ESM check, and package origin (plan N5, 2026-W44).

`engine.scan_inventory` judges the `OSREL` line through `runtimes.evaluate_os`, reads Ubuntu's `ESM` line, and
passes the new `RUNTIME` kinds to `runtimes.evaluate`, where a `repo:` manager (a third-party or unknown package
origin) is judged upstream and only `pkg:` is left to distro updates. No test touches the network.
"""

from __future__ import annotations

import datetime as dt

import pytest

from agentteams.vulnscan import _http, engine, runtimes, server_fixplan

TODAY = dt.date.today()
PAST = "2020-01-01"
FUTURE = "2099-01-01"


def _c(cycle: str, latest: str, *, eol: str | bool = False, eoes: str | bool | None = None) -> dict:
    is_eol = eol is not False and str(eol) <= TODAY.isoformat()
    return {"cycle": cycle, "eol": eol, "is_eol": is_eol, "latest": latest, "eoes": eoes, "is_eoes": None}


CYCLES: dict[str, list[dict]] = {
    "ubuntu": [_c("24.04", "24.04.1", eoes=FUTURE), _c("22.04", "22.04.5", eoes=FUTURE),
               _c("20.04", "20.04.6", eol=PAST, eoes=FUTURE), _c("16.04", "16.04.7", eol=PAST, eoes=PAST)],
    "debian": [_c("12", "12.7"), _c("10", "10.13", eol=PAST)],
    "amazon-linux": [_c("2023", "2023.6.20241010"), _c("2", "2.0.20241001.0")],
    "rhel": [_c("9", "9.4")],
    "rocky-linux": [_c("9", "9.4")],
    "almalinux": [_c("9", "9.4")],
    "fedora": [_c("40", "40")],
    "centos": [_c("7", "7.9", eol=PAST)],
    "nginx": [_c("1.26", "1.26.2"), _c("1.18", "1.18.0", eol=PAST)],
    "redis": [_c("7.2", "7.2.6")],
}


@pytest.fixture
def eol(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fake endoflife.date; a slug not in CYCLES fails like an unreachable feed. Returns the slugs asked."""
    asked: list[str] = []

    def cycles(slug: str) -> list[dict]:
        asked.append(slug)
        if slug not in CYCLES:
            raise _http.FeedError(f"no fake data for {slug}")
        return CYCLES[slug]

    def no_network(url: str, *a: object, **kw: object) -> object:
        raise _http.FeedError(f"network refused in tests: {url}")

    runtimes._CACHE.clear()
    monkeypatch.setattr(runtimes, "_eol_cycles", cycles)
    monkeypatch.setattr(_http, "fetch_json", no_network)
    return asked


def _inv(*lines: str) -> engine.Report:
    return engine.scan_inventory("\n".join(lines) + "\n", "web1")


def _osrel(os_id: str, version: str, pretty: str) -> str:
    return f"OSREL\t{os_id}\t{version}\t{pretty}"


def _os_findings(rep: engine.Report) -> list:
    return [f for f in rep.findings if f.name == engine.OS_RELEASE]


# ---------------------------------------------------------------- OSREL: ID mapping, supported and end-of-life

@pytest.mark.parametrize(("os_id", "version", "slug"), [
    ("ubuntu", "22.04", "ubuntu"), ("debian", "12", "debian"), ("amzn", "2023", "amazon-linux"),
    ("amzn", "2", "amazon-linux"), ("rhel", "9.4", "rhel"), ("rocky", "9.4", "rocky-linux"),
    ("almalinux", "9.4", "almalinux"), ("fedora", "40", "fedora"),
])
def test_supported_release_is_checked_with_no_finding(eol: list[str], os_id: str, version: str, slug: str) -> None:
    rep = _inv(_osrel(os_id, version, f"{os_id} {version}"))
    assert eol == [slug]
    assert rep.checked == 1 and not rep.findings and not rep.unscannable and not rep.feed_errors


@pytest.mark.parametrize(("os_id", "version", "pretty", "fix"), [
    ("debian", "10", "Debian GNU/Linux 10 (buster)", "12.7"),
    ("centos", "7", "CentOS Linux 7 (Core)", ""),      # no supported CentOS line: no upgrade target
])
def test_end_of_life_release_is_high(eol: list[str], os_id: str, version: str, pretty: str, fix: str) -> None:
    (f,) = _os_findings(_inv(_osrel(os_id, version, pretty)))
    assert (f.kind, f.target, f.severity, f.version) == ("host", "web1", 3, version)
    assert f.detail == f"eol: OS release {pretty} is end-of-life: distro security updates have stopped"
    assert f.locations == ["/etc/os-release"] and f.fix == fix   # the newest supported release, for the plan


def test_unknown_os_id_is_unscannable_never_fine(eol: list[str]) -> None:
    rep = _inv(_osrel("linuxmint", "21.3", "Linux Mint 21.3"))
    assert rep.unscannable == [("os-release", "OS linuxmint not judged by this vulnscan version")]
    assert not rep.findings and rep.checked == 0 and eol == []
    rep = _inv(_osrel("unknown", "", "unknown"))      # L1 on a host without /etc/os-release
    assert rep.unscannable == [("os-release", "OS unknown not judged by this vulnscan version")]


def test_missing_version_or_unknown_line_is_unscannable_and_feed_failure_is_a_feed_error(eol: list[str]) -> None:
    rep = _inv(_osrel("debian", "", "Debian GNU/Linux trixie/sid"))
    assert rep.unscannable == [("os-release", "OS debian reports no VERSION_ID: its release was not judged")]
    rep = _inv(_osrel("debian", "9", "Debian 9"))
    assert rep.unscannable == [("os-release", "OS debian 9: release line 9 unknown to endoflife.date")]
    saved = CYCLES.pop("rhel")
    try:
        rep = _inv(_osrel("rhel", "9.4", "RHEL 9.4"))
    finally:
        CYCLES["rhel"] = saved
    assert rep.feed_errors and rep.feed_errors[0].startswith("web1: OS rhel 9.4: release data unavailable")
    assert not rep.findings and not rep.unscannable


def test_malformed_lines_are_ignored_and_old_kits_report_nothing(eol: list[str]) -> None:
    rep = _inv("OSREL\tdebian\t10", "OSREL\tdebian\t10\tx\textra", "ESM\tenabled")
    assert not rep.findings and not rep.unscannable and rep.checked == 0 and eol == []


# ---------------------------------------------------------------- Ubuntu: standard support ended, ESM table

STD_ENDED = "standard-support-ended: OS release Ubuntu 20.04.6 LTS: standard support ended"
FOCAL = _osrel("ubuntu", "20.04", "Ubuntu 20.04.6 LTS")
REDIS_UNIVERSE = "RUNTIME\tredis\t5.0.7\t/usr/bin/redis-server\tpkg:redis-server:universe"


@pytest.mark.parametrize(("esm", "extra", "sev", "tail"), [
    (None, [], 3, "; ESM/Pro required for security updates (esm-infra unknown)"),
    ("ESM\tdisabled\tdisabled", [], 3, "; ESM/Pro required for security updates (esm-infra disabled)"),
    ("ESM\tunknown\tenabled", [], 3, "; ESM/Pro required for security updates (esm-infra unknown)"),
    ("ESM\tbogus\tenabled", [], 3, "; ESM/Pro required for security updates (esm-infra unknown)"),
    ("ESM\tenabled\tdisabled", [], 1, "; on ESM (main only); universe packages need esm-apps"),
    ("ESM\tenabled\tunknown", [REDIS_UNIVERSE], 2,
     "; on ESM (main only); universe packages need esm-apps (universe runtime packages without esm-apps: redis-server)"),
    ("ESM\tenabled\tenabled", [REDIS_UNIVERSE], 1, "; on ESM (esm-infra and esm-apps enabled)"),
])
def test_ubuntu_past_standard_support_follows_the_esm_table(eol: list[str], esm: str | None, extra: list[str],
                                                           sev: int, tail: str) -> None:
    rep = _inv(FOCAL, *([esm] if esm else []), *extra)
    (f,) = _os_findings(rep)
    assert f.severity == sev and f.detail == STD_ENDED + tail, f.detail
    assert not any(r.name == "redis" for r in rep.findings)   # the universe redis itself is OS-packaged: distro


def test_ubuntu_whose_esm_has_ended_too_is_plain_end_of_life(eol: list[str]) -> None:
    (f,) = _os_findings(_inv(_osrel("ubuntu", "16.04", "Ubuntu 16.04.7 LTS"), "ESM\tenabled\tenabled"))
    assert f.severity == 3 and f.detail.startswith("eol: OS release Ubuntu 16.04.7 LTS is end-of-life")


def test_esm_line_is_ignored_off_ubuntu(eol: list[str]) -> None:
    (f,) = _os_findings(_inv(_osrel("debian", "10", "Debian 10"), "ESM\tenabled\tenabled"))
    assert f.severity == 3 and f.detail.startswith("eol:")


def test_os_extended_support_reads_both_api_shapes(eol: list[str]) -> None:
    assert runtimes.os_extended_support("ubuntu", "20.04") is True
    assert runtimes.os_extended_support("ubuntu", "focal") is True
    assert runtimes.os_extended_support("ubuntu", "16.04") is False
    assert runtimes.os_extended_support("linuxmint", "21") is None
    assert runtimes.os_extended_support("ubuntu", "14.04") is None       # line unknown
    assert runtimes._extended_active({"is_eoes": True}, TODAY) is False
    assert runtimes._extended_active({"is_eoes": None, "eoes": True}, TODAY) is True
    assert runtimes._extended_active({"is_eoes": None, "eoes": None}, TODAY) is None


# ---------------------------------------------------------------- package origin: pkg: vs repo:

@pytest.mark.parametrize("manager", ["pkg:nginx-core", "pkg:redis-server:universe"])
def test_pkg_origin_is_left_to_distro_updates(eol: list[str], manager: str) -> None:
    v = runtimes.evaluate("nginx", "1.18.0", "/usr/sbin/nginx", manager, today=TODAY)
    assert v.verdict == "distro" and v.severity == 0 and eol == []


@pytest.mark.parametrize("manager", ["repo:nginx.org:nginx", "repo:unknown:nginx"])
def test_repo_origin_is_judged_upstream(eol: list[str], manager: str) -> None:
    v = runtimes.evaluate("nginx", "1.18.0", "/usr/sbin/nginx", manager, today=TODAY)
    assert v.verdict == "eol" and v.severity == 3 and eol == ["nginx"]
    v = runtimes.evaluate("nginx", "1.26.1", "/usr/sbin/nginx", manager, today=TODAY)
    assert v.verdict == "behind-patch" and v.upgrade_to == "1.26.2"


def test_new_runtime_kinds_flow_through_the_inventory(eol: list[str]) -> None:
    rep = _inv(_osrel("ubuntu", "22.04", "Ubuntu 22.04.5 LTS"), "ESM\tdisabled\tdisabled",
               "RUNTIME\tnginx\t1.26.1\t/usr/sbin/nginx\trepo:nginx.org:nginx",
               "RUNTIME\tnginx\t1.18.0\t/usr/sbin/nginx.distro\tpkg:nginx-core",
               "RUNTIME\tredis\t7.2.6\t/usr/local/bin/redis-server\ttarball",
               "RUNTIME\tlibressl\t3.3.6\t/usr/bin/openssl\tpkg:os")
    (f,) = rep.findings
    assert (f.kind, f.name, f.version, f.severity, f.fix) == ("runtime", "nginx", "1.26.1", 2, "1.26.2")
    assert f.detail.endswith("[repo:nginx.org:nginx]")
    assert rep.unscannable == [("/usr/bin/openssl", "runtime kind libressl not judged by this vulnscan version")]
    assert rep.checked == 4   # three runtimes and the OS release


# ---------------------------------------------------------------- server fix plan

def test_fixplan_gives_the_os_release_its_own_line_and_non_node_runtimes_no_kit_lever(eol: list[str]) -> None:
    rep = _inv(_osrel("debian", "10", "Debian GNU/Linux 10 (buster)"),
               "RUNTIME\tnginx\t1.26.1\t/usr/sbin/nginx\trepo:nginx.org:nginx",
               "HOSTCHECK\tMODERATE\treboot required")
    rep.findings.append(engine.Finding(kind="runtime", target="web1", name="node", version="18.19.0",
                                       severity=3, detail="eol: end of life"))
    md = server_fixplan.markdown("web1", rep.findings, [], kit="KIT")
    lines = md.splitlines()
    assert lines.index("## OS release") < lines.index("## Commands")
    assert ("- **plan an OS release upgrade; no kit lever applies**: eol: OS release Debian GNU/Linux 10 (buster) "
            "is end-of-life: distro security updates have stopped (newest supported release: 12.7)") in lines
    assert any(ln.startswith("- nginx 1.26.1: behind-patch") and
               ln.endswith("— upgrade through the package manager or the vendor (no kit lever)") for ln in lines)
    assert "- node 18.19.0: eol: end of life" in lines and "--node-major" in md
    host_checks = md.split("## Host checks (by hand)", 1)[1]
    assert "reboot required" in host_checks and "os-release" not in host_checks and "end-of-life" not in host_checks
    assert "--apply" in md and "--apply --os" not in md        # no new kit lever
