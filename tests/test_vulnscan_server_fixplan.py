"""Plan D1: ``server-fixplan-<host>.md`` from a host's L1 audit findings (no new authority: a plan for a person)."""

from __future__ import annotations

from pathlib import Path

from agentteams.vulnscan import server_fixplan
from agentteams.vulnscan.report import Finding, Report

APPS = """# host | unit | app dir | python | flags | health | pins
vk-api | api.service | ~svc/api | /usr/bin/python3 | --user-site --as svc | - | --pin requests==2.33.0
vk-api | root.service | ~root/tool | /root/tool/.venv/bin/python | | - |
web1 | other.service | /srv/other | /srv/other/.venv/bin/python | | - |
not a line
"""


def _pkg(name: str, version: str, fix: str, where: str, *, eco: str = "PyPI", distro: bool = False,
         sev: int = 3, host: str = "vk-api") -> Finding:
    return Finding(kind="package", target=host, name=name, version=version, ecosystem=eco, fix=fix, severity=sev,
                   ids=["GHSA-x"], locations=[where], distro=distro)


def test_apps_conf_and_app_dirs() -> None:
    apps = server_fixplan.parse_apps(APPS)
    assert [(a.host, a.unit) for a in apps] == [("vk-api", "api.service"), ("vk-api", "root.service"),
                                                 ("web1", "other.service")]
    api, root, other = apps
    home_api = "/".join(("", "home", "svc", "api"))
    assert api.holds(home_api + "/.venv/lib/python3.12/site-packages") and not api.holds(home_api + "x/lib")
    assert root.holds("/root/tool/.venv/lib/python3.12/site-packages")
    assert other.holds("/srv/other") and not other.holds("/srv/otherwise")


def test_a_plan_sorts_each_finding_to_the_kit_level_that_fixes_it() -> None:
    home_api = "/".join(("", "home", "svc", "api"))
    findings = [
        _pkg("cryptography", "41.0.7", "49.0.0", "/usr/lib/python3/dist-packages", distro=True, sev=3),
        _pkg("PyJWT", "2.8.0", "2.15.0", home_api + "/.venv/lib/python3.12/site-packages", sev=4),
        _pkg("Django", "5.1", "5.2.17", "/root/anaconda3/lib/python3.12/site-packages", sev=4),
        _pkg("basic-ftp", "5.1.0", "6.2.1", "/var/www/site/app", eco="npm", sev=4),
        Finding(kind="runtime", target="vk-api", name="node", version="18.19.0", detail="end of life"),
        Finding(kind="host", target="vk-api", name="host", detail="secret-looking files under web root /var/www/site"),
        _pkg("requests", "2.31.0", "2.33.0", "/usr/lib/python3/dist-packages", distro=True, host="vk-prod"),
    ]
    md = server_fixplan.markdown("vk-api", [f for f in findings if f.target == "vk-api"],
                                 server_fixplan.parse_apps(APPS), kit="KIT")
    assert "`KIT/run-host.sh vk-api`" in md and "`KIT/run-host.sh vk-api --apply`" in md
    assert "## L2 — OS security updates (1 OS-managed package(s))" in md and "| cryptography | 41.0.7 | 49.0.0 |" in md
    assert "## L4 — api.service (~svc/api)" in md and "`--pin PyJWT==2.15.0`" in md
    assert "apps.conf pins now: `--pin requests==2.33.0`" in md
    assert "## Python packages with no apps.conf entry" in md and "/root/anaconda3/lib/python3.12/site-packages" in md
    assert "## Node app dependencies" in md and "basic-ftp" in md
    assert "--node-major" in md and "secret-looking files" in md
    assert "STOP" not in md and "requests | 2.31.0" not in md   # another host's finding is not in this plan


def test_a_node_runtime_missing_a_security_release_gets_the_s10_carve_out() -> None:
    """Rule S-10's remediation carve-out: the plan's commands let L3 take a fix inside the 14-day cooldown."""
    vuln = Finding(kind="runtime", target="vk-api", name="node", version="24.10.0", severity=3,
                   detail="security-release-missing: missing security release(s) 24.11.1 [tarball]")
    md = server_fixplan.markdown("vk-api", [vuln], [], kit="KIT")
    assert "`KIT/run-host.sh vk-api --apply --allow-fresh`" in md and "`KIT/run-host.sh vk-api --allow-fresh`" in md
    assert "remediation carve-out" in md
    for detail, sev in (("eol: node 18 is end-of-life — no more security fixes [tarball]", 3),
                        ("behind-patch: behind latest 24.x patch [tarball]", 2)):
        other = Finding(kind="runtime", target="vk-api", name="node", version="24.10.0", severity=sev, detail=detail)
        md = server_fixplan.markdown("vk-api", [other], [], kit="KIT")
        assert "--allow-fresh" not in md and "`KIT/run-host.sh vk-api --apply`" in md
    assumed = Finding(kind="runtime", target="vk-api", name="node", version="24.10.0", severity=3,
                      detail="behind-patch: behind latest 24.x patch; security-release data unavailable, so assume "
                             "the newer patch is a security release [tarball]")
    assert "--apply --allow-fresh" in server_fixplan.markdown("vk-api", [assumed], [], kit="KIT")


def test_compromise_indicators_come_first() -> None:
    md = server_fixplan.markdown("vk-api", [Finding(kind="ioc", target="vk-api", name="litellm", version="1.107.0",
                                                    detail="known-compromised release")], [])
    assert md.index("## STOP: compromise indicators") < md.index("## Commands")


def test_write_replaces_the_days_plans(tmp_path: Path) -> None:
    out = tmp_path / "server-plans" / "2026-10-27"
    out.mkdir(parents=True)
    (out / "server-fixplan-gone.md").write_text("stale", encoding="utf-8")
    (out / "notes.md").write_text("operator notes", encoding="utf-8")
    rep = Report(target="daily")
    rep.findings = [_pkg("idna", "3.6", "3.15", "/usr/lib/python3/dist-packages", distro=True)]
    written = server_fixplan.write(out, rep, ["vk-api", "vk-bastion"], tmp_path / "missing-apps.conf")
    assert [p.name for p in written] == ["server-fixplan-vk-api.md"]
    assert not (out / "server-fixplan-gone.md").exists() and (out / "notes.md").exists()


def test_the_daily_run_writes_plans_outside_the_run_directory(tmp_path: Path, monkeypatch) -> None:
    """The agent container mounts the run directory whole: host and path details must not be in it."""
    from agentteams.vulnscan import daily
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AGENTTEAMS_SERVER_KIT_DIR", str(tmp_path / "kit"))
    run = tmp_path / "state" / "2026-10-27"
    run.mkdir(parents=True)
    rep = Report(target="daily")
    rep.findings = [_pkg("idna", "3.6", "3.15", "/usr/lib/python3/dist-packages", distro=True)]
    md = daily._server_plans({"servers": ["vk-api"]}, run, rep)
    assert "server-fixplan-vk-api.md" in md and str(tmp_path) not in md
    assert not list(run.glob("*fixplan*")) and (tmp_path / "state" / "server-plans" / "2026-10-27" /
                                                 "server-fixplan-vk-api.md").is_file()
