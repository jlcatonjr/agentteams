"""agentteams.vulnscan fix / daily / schedule / server kit. Network-free."""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams.vulnscan import _http, daily, executor, fix, osv, paths, runtimes, schedule
from agentteams.vulnscan.__main__ import export_server_kit, main

from tests.test_vulnscan_core import Feed, _adv

KIT = Path(__file__).resolve().parents[1] / "agentteams" / "data" / "server_kit"
VULNSCAN = Path(__file__).resolve().parents[1] / "agentteams" / "vulnscan"


@pytest.fixture
def feed(monkeypatch: pytest.MonkeyPatch) -> Feed:
    f = Feed()
    monkeypatch.setattr(_http, "fetch_json", f)
    osv._DETAILS.clear()
    runtimes._CACHE.clear()
    return f


@pytest.fixture
def xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AGENTTEAMS_SERVER_KIT_DIR", str(tmp_path / "kit"))
    return tmp_path


def _pypi(feed: Feed, name: str, releases: dict[str, int]) -> None:
    """releases: version -> age in days."""
    now = dt.datetime.now(dt.UTC)
    feed.urls[f"https://pypi.org/pypi/{name}/json"] = {"releases": {
        v: [{"upload_time_iso_8601": (now - dt.timedelta(days=age)).isoformat().replace("+00:00", "Z"), "yanked": False}]
        for v, age in releases.items()}}


# ---------------------------------------------------------------- fix

def test_choose_version_smallest_clean_and_cooldown(feed: Feed) -> None:
    _pypi(feed, "requests", {"2.31.0": 900, "2.32.0": 300, "2.33.0": 60, "2.34.0rc1": 30, "2.35.0": 2})
    feed.vulns = {("PyPI", "requests", "2.32.0"): ["G"]}
    assert fix.choose_version("requests", "2.31.0") == ("2.33.0", "")
    feed.vulns[("PyPI", "requests", "2.33.0")] = ["G"]
    v, why = fix.choose_version("requests", "2.31.0")
    assert v == "" and "2.35.0" in why and "cooldown" in why


def test_plan_and_apply_requirements_preserves_bytes(tmp_path: Path, feed: Feed) -> None:
    d = tmp_path / "dir with space"
    d.mkdir()
    req = d / "requirements.txt"
    req.write_bytes(b"# keep\r\nPyJWT==2.3.0  # auth\r\ncryptography==3.4.8\r\nlitellm==1.107.0\r\nok==1.0\r\n")
    feed.vulns = {("PyPI", "PyJWT", "2.3.0"): ["G1"], ("PyPI", "cryptography", "3.4.8"): ["G2"],
                  ("PyPI", "litellm", "1.107.0"): ["MAL-1"]}
    _pypi(feed, "PyJWT", {"2.15.0": 100})
    _pypi(feed, "cryptography", {"44.0.0": 100})
    changes = {c.name: c for c in fix.plan_requirements(req)}
    assert changes["PyJWT"].action == "fix" and changes["PyJWT"].new == "2.15.0"
    assert changes["cryptography"].action == "major-held"
    assert changes["litellm"].action == "malicious" and changes["litellm"].new == ""
    assert fix.apply_requirements(req, list(changes.values())) == 1
    assert req.read_bytes() == b"# keep\r\nPyJWT==2.15.0  # auth\r\ncryptography==3.4.8\r\nlitellm==1.107.0\r\nok==1.0\r\n"
    allow = fix.plan_requirements(req, allow_major=True)
    assert {c.name: c.action for c in allow}["cryptography"] == "fix"


def test_fix_path_dry_run_writes_nothing(tmp_path: Path, feed: Feed) -> None:
    req = tmp_path / "requirements.txt"
    req.write_text("PyJWT==2.3.0\n")
    feed.vulns = {("PyPI", "PyJWT", "2.3.0"): ["G1"]}
    _pypi(feed, "PyJWT", {"2.15.0": 100})
    res = fix.fix_path(tmp_path, npm=False)
    assert req.read_text() == "PyJWT==2.3.0\n" and res.changes[0].new == "2.15.0"
    assert "dry run" in res.markdown()
    fix.fix_path(tmp_path, apply=True, npm=False)
    assert req.read_text() == "PyJWT==2.15.0\n"


# ---------------------------------------------------------------- daily

def test_init_never_overwrites_and_stale_warning(xdg: Path) -> None:
    assert daily.stale_warning() == ""                     # nothing configured → silent
    p = daily.init_config(repo_roots=["~/code"])
    p.write_text(json.dumps({"repo_roots": ["mine"]}))
    daily.init_config(repo_roots=["other"])
    assert json.loads(p.read_text())["repo_roots"] == ["mine"]
    assert "no daily run" in daily.stale_warning()
    daily.heartbeat_path().parent.mkdir(parents=True, exist_ok=True)
    old = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=50)).isoformat()
    daily.heartbeat_path().write_text(json.dumps({"last_run": old}))
    assert "50h ago" in daily.stale_warning()


def test_run_daily_writes_summary_heartbeat_and_marks_new(xdg: Path, feed: Feed) -> None:
    root = xdg / "code"
    repo = root / "svc"
    (repo / ".git").mkdir(parents=True)
    (repo / "requirements.txt").write_text("PyJWT==2.3.0\n")
    feed.vulns = {("PyPI", "PyJWT", "2.3.0"): ["G1"]}
    feed.records = {"G1": _adv("G1", "PyPI", "PyJWT", "0", "2.15.0", "CRITICAL")}
    _pypi(feed, "PyJWT", {"2.15.0": 100})
    cfg = {**daily.DEFAULT_CONFIG, "repo_roots": [str(root)], "local_runtimes": False, "notify": "none"}
    rep, code, run_dir = daily.run_daily(cfg, today="2026-10-21")
    assert code == 1 and (run_dir / "summary.md").exists() and "**NEW**" in (run_dir / "summary.md").read_text()
    assert (run_dir / "fixplan-code.md").read_text().count("2.15.0") >= 1
    hb = json.loads(daily.heartbeat_path().read_text())
    assert hb["exit"] == 1 and hb["new"] == 1
    _, _, run2 = daily.run_daily(cfg, today="2026-10-22")
    assert "**NEW**" not in (run2 / "summary.md").read_text()


def test_daily_reports_unreachable_kit_as_unscanned(xdg: Path, feed: Feed) -> None:
    cfg = {**daily.DEFAULT_CONFIG, "servers": ["web1"], "local_runtimes": False, "notify": "none"}
    rep, code, _ = daily.run_daily(cfg, today="2026-10-22")
    assert code == 3 and "no hosts.conf in" in rep.feed_errors[0]


# ---------------------------------------------------------------- schedule

def test_schedule_definitions_run_this_interpreter(xdg: Path) -> None:
    plist = schedule.launchd_plist(7, 30)
    assert "io.agentteams.vulnscan" in plist and sys.executable in plist and "<integer>7</integer>" in plist
    service, timer = schedule.systemd_units(6, 5)
    assert "agentteams.vulnscan daily" in service and "OnCalendar=*-*-* 06:05:00" in timer and "Persistent=true" in timer
    assert schedule.cron_line(1, 2).startswith("2 1 * * * ") and schedule.CRON_TAG in schedule.cron_line(1, 2)


def test_schedule_install_dry_run_writes_nothing(xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(schedule.Path, "home", classmethod(lambda cls: xdg / "home"))
    plan = schedule.install(7, 30, apply=False)
    assert plan.files and not any(Path(p).exists() for p in plan.files if p != "crontab")
    assert not (xdg / "state").exists()


# ---------------------------------------------------------------- server kit

def test_export_server_kit_keeps_user_config(xdg: Path) -> None:
    dest = xdg / "kit"
    names = export_server_kit(dest)
    assert {"L1-audit.sh", "remote.sh", "run-host.sh", "hosts.conf", "apps.conf"} <= set(names)
    assert (dest / "remote.sh").stat().st_mode & 0o111
    (dest / "hosts.conf").write_text("mine | me@host | private\n")
    export_server_kit(dest)
    assert (dest / "hosts.conf").read_text() == "mine | me@host | private\n"


def test_cli_server_kit_and_init(xdg: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["server-kit"]) == 0 and (paths.server_kit_dir() / "hosts.conf").exists()
    assert main(["init", "--repo-root", "~/code"]) == 0 and paths.config_file().exists()


@pytest.mark.skipif(not shutil.which("bash"), reason="bash not available")
@pytest.mark.parametrize("script", sorted(p.name for p in KIT.glob("*.sh")))
def test_server_kit_scripts_parse(script: str) -> None:
    proc = subprocess.run(["bash", "-n", str(KIT / script)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


# ---------------------------------------------------------------- leak guard

_IP = re.compile(rb"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])")
_EMAIL = re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_HOME = re.compile(rb"/(?:Users|home)/([A-Za-z0-9._-]+)")
_DOC_NETS = [ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "127.0.0.0/8", "0.0.0.0/32")]


def _shipped_files() -> list[Path]:
    return sorted([*KIT.iterdir(), *VULNSCAN.glob("*.py")])


def test_shipped_kit_and_engine_carry_no_operator_specific_data() -> None:
    """The kit and engine ship to every user: no real IPs, emails, home dirs or live configs."""
    assert not (KIT / "hosts.conf").exists() and not (KIT / "apps.conf").exists()
    offenders: list[str] = []
    for f in _shipped_files():
        if not f.is_file():
            continue
        data = f.read_bytes()
        for m in _IP.finditer(data):
            text = m.group(1).decode()
            if any(int(octet) > 255 for octet in text.split(".")):
                continue   # a dotted version string, not an address
            ip = ipaddress.ip_address(text)
            if not any(ip in n for n in _DOC_NETS):
                offenders.append(f"{f.name}: IP {ip}")
        for m in _EMAIL.finditer(data):
            if not m.group(0).lower().endswith((b"example.com", b"example.org", b"example.net",
                                                 b".invalid", b".example", b".test")):   # RFC 2606 reserved
                offenders.append(f"{f.name}: address {m.group(0).decode()}")
        for m in _HOME.finditer(data):
            offenders.append(f"{f.name}: home path {m.group(0).decode()}")
    assert not offenders, offenders


# ---------------------------------------------------------------- review regressions (2026-10-22)

def test_fix_exit_codes(tmp_path: Path, feed: Feed) -> None:
    req = tmp_path / "requirements.txt"
    req.write_text("litellm==1.107.0\n")
    feed.vulns = {("PyPI", "litellm", "1.107.0"): ["MAL-1"]}
    assert fix.fix_path(tmp_path, npm=False).exit_code() == 4
    req.write_text("cryptography==3.4.8\n")
    feed.vulns = {("PyPI", "cryptography", "3.4.8"): ["G"]}
    _pypi(feed, "cryptography", {"44.0.0": 100})
    assert fix.fix_path(tmp_path, npm=False).exit_code() == 1          # major held
    feed.fail = True
    assert fix.fix_path(tmp_path, npm=False).exit_code() == 3


def test_server_audit_needs_the_final_result_line(xdg: Path, feed: Feed, monkeypatch: pytest.MonkeyPatch) -> None:
    kit = xdg / "kit"
    kit.mkdir()
    (kit / "hosts.conf").write_text("web1 | deploy@web1.example.com\n")
    (kit / "remote.sh").write_text("#!/bin/sh\necho planted\n")      # a user-writable copy is never run

    class Proc:
        returncode = 0
        stdout = "== RESULT\n"     # header only: the audit died before its verdict
        stderr = ""

    seen: list[tuple[list[str], dict]] = []
    monkeypatch.setenv("AGENTTEAMS_SERVER_KIT_EXTRA", "dropped")
    monkeypatch.setattr(daily.subprocess, "run", lambda argv, **k: seen.append((argv, k["env"])) or Proc())
    rep = daily._scan_server("web1", kit, xdg)
    assert rep.feed_errors and "did not complete" in rep.feed_errors[0]
    argv, env = seen[0]
    assert argv == ["bash", str(executor.kit_dir() / "remote.sh"), "web1", "L1-audit.sh"]   # P12: packaged kit
    assert env["AGENTTEAMS_SERVER_KIT_CONFIG"] == str(kit) and env["AGENTTEAMS_SERVER_KIT_LOGS"] == str(xdg)
    assert env["AGENTTEAMS_VULNSCAN_NO_REPORT"] == "1" and "AGENTTEAMS_SERVER_KIT_EXTRA" not in env
    assert "AGENTTEAMS_SERVER_KIT_DIR" not in env
    Proc.stdout = "== RESULT\nRESULT clean (package/runtime verdicts come from the workstation report)\n"
    assert not daily._scan_server("web1", kit, xdg).feed_errors


@pytest.mark.skipif(not shutil.which("bash"), reason="bash not available")
def test_remote_sh_embeds_packaged_and_config_ioc_data(tmp_path: Path) -> None:
    conf = tmp_path / "conf"
    conf.mkdir()
    (conf / "hosts.conf").write_text("web1 | -p 2222 deploy@web1.example.com\n")
    (conf / "ioc-local.txt").write_text("name operator-added-indicator")        # no final newline
    (conf / next(KIT.glob("ioc-*.txt")).name).write_text("name same-basename-as-packaged\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "ssh").write_text(f'#!/bin/sh\ncat > "{tmp_path}/payload"\necho "RESULT clean"\n')
    (bindir / "ssh").chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "AGENTTEAMS_SERVER_KIT_CONFIG": str(conf),
           "AGENTTEAMS_SERVER_KIT_LOGS": str(tmp_path / "logs"), "AGENTTEAMS_VULNSCAN_NO_REPORT": "1"}
    proc = subprocess.run(["bash", str(KIT / "remote.sh"), "web1", "L1-audit.sh"], capture_output=True, text=True,
                          env=env, check=False)
    assert proc.returncode == 0, proc.stderr
    payload = (tmp_path / "payload").read_text()
    data = payload.split("__AGENTTEAMS_IOC_DATA_END__")[1]
    for f in KIT.glob("ioc-*.txt"):                                              # packaged rules first
        assert f.read_text().strip().splitlines()[-1] in data
    assert "name operator-added-indicator\n" in data and "name same-basename-as-packaged" in data
    assert data.index("operator-added") > data.index(next(KIT.glob("ioc-*.txt")).read_text().strip().splitlines()[-1])
    (conf / "ioc-evil.txt").write_text("__AGENTTEAMS_IOC_DATA_END__\nrm -rf /\n")
    (tmp_path / "payload").unlink()
    bad = subprocess.run(["bash", str(KIT / "remote.sh"), "web1", "L1-audit.sh"], capture_output=True, text=True,
                         env=env, check=False)
    assert "refusing" in bad.stderr and "contains the data terminator" in bad.stderr
    assert bad.returncode == 2 and not (tmp_path / "payload").exists()      # V1-3: ssh was never started


def test_daily_records_heartbeat_even_when_collection_fails(xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise OSError("disk gone")
    monkeypatch.setattr(daily, "_collect", boom)
    _, code, _ = daily.run_daily({**daily.DEFAULT_CONFIG, "notify": "none"}, today="2026-10-22")
    daily.init_config()
    assert code == 3 and json.loads(daily.heartbeat_path().read_text())["exit"] == 3
    assert "partially scanned" in daily.stale_warning()


def test_schedule_bakes_environment_and_quotes(xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_CONFIG_DIR", str(xdg / "my cfg%dir"))
    plist = schedule.launchd_plist(7, 30)
    assert "AGENTTEAMS_VULNSCAN_CONFIG_DIR" in plist and "my cfg%dir" in plist
    service, _ = schedule.systemd_units(7, 30)
    assert '"AGENTTEAMS_VULNSCAN_CONFIG_DIR=' in service and "cfg%%dir" in service
    assert "AGENTTEAMS_VULNSCAN_CONFIG_DIR=" in schedule.cron_line(7, 30)


def test_exported_hosts_conf_has_no_live_example_entries(xdg: Path) -> None:
    export_server_kit(xdg / "kit2")
    live = [ln for ln in (xdg / "kit2" / "hosts.conf").read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    assert live == []


BAD_PINS = ["--index-url=https://evil.example.com", "-e " + "g" + "it+https://x", "pkg>=1", "pkg==1.0;rm -rf /"]


@pytest.mark.skipif(not shutil.which("bash"), reason="bash not available")
@pytest.mark.parametrize("pin", BAD_PINS)
def test_l4_rejects_pins_that_are_not_name_equals_version(pin: str) -> None:
    proc = subprocess.run(["bash", str(KIT / "L4-python-app.sh"), "--unit", "x.service", "--dir", "/nonexistent",
                           "--pin", pin], capture_output=True, text=True, check=False)
    assert proc.returncode == 1 and "invalid --pin" in proc.stdout


def test_ioc_rules_are_data_files_not_scripts() -> None:
    assert not list(KIT.glob("ioc-*.sh")) and list(KIT.glob("ioc-*.txt"))
    for f in KIT.glob("ioc-*.txt"):
        for ln in f.read_text().splitlines():
            if ln.strip() and not ln.startswith("#"):
                assert ln.split()[0] in {"file", "name", "env", "grep"}, ln
