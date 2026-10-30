"""Index reliability (report v2: R1, R3, R5): severity from CVSS, missed-run catch-up, unsupported ecosystems.

No network: OSV goes through the canned ``Feed`` of ``test_vulnscan_core``; notifications and scans are
replaced where a test needs them.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

import pytest

from agentteams.vulnscan import coverage, cvss, daily, engine, grant, manifests, notify, osv, schedule
from agentteams.vulnscan.__main__ import main
from agentteams.vulnscan.report import EXIT_CLEAN, EXIT_FINDINGS, Finding, Report

from tests.test_vulnscan_core import Feed, _adv, feed  # noqa: F401  (fixture)
from tests.test_vulnscan_registry import _entry, _repo, xdg  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]
SECURITY = ROOT / "agentteams" / "templates" / "universal" / "security.template.md"


# ---------------------------------------------------------------- R1: CVSS scores

@pytest.mark.parametrize(("vector", "expected"), [
    ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 9.8),
    ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H", 10.0),     # scope changed, capped at 10
    ("CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:C/C:H/I:H/A:H", 9.9),      # scope changed: PR:L weighs 0.68
    ("CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N", 6.1),      # the classic reflected XSS
    ("CVSS:3.1/AV:N/AC:L/PR:H/UI:R/S:C/C:L/I:L/A:N", 4.8),      # scope changed: PR:H weighs 0.5
    ("CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H", 8.8),
    ("CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H", 7.8),
    ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H", 7.5),
    ("CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:N/A:N", 5.9),
    ("CVSS:3.1/AV:P/AC:H/PR:H/UI:R/S:U/C:L/I:N/A:N", 1.6),
    ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:N", 0.0),      # no impact
    ("CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 9.8),
    ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H/E:U/RL:O", 9.8),   # temporal metrics: base score only
])
def test_cvss_v3_base_scores_match_published_values(vector: str, expected: float) -> None:
    assert cvss.score(vector) == expected


def test_cvss_v31_roundup_is_integer_exact() -> None:
    assert cvss.roundup_v31(4.02) == 4.1
    assert cvss.roundup_v31(4.000000000000001) == 4.0   # a float ceiling would give 4.1
    assert cvss.roundup_v31(7.0) == 7.0


@pytest.mark.parametrize(("vector", "expected"), [
    ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N", 9.3),
    ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H", 10.0),
    ("CVSS:4.0/AV:L/AC:L/AT:N/PR:L/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N", 8.5),
    ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:P/VC:N/VI:N/VA:N/SC:L/SI:L/SA:N", 5.3),
    ("CVSS:4.0/AV:P/AC:H/AT:P/PR:H/UI:A/VC:L/VI:N/VA:N/SC:N/SI:N/SA:N", 1.0),
    ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:N/VA:N/SC:N/SI:N/SA:N", 0.0),
    ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/E:U", 8.1),   # threat metric honoured (FIRST calculator)
])
def test_cvss_v4_scores_follow_the_first_algorithm(vector: str, expected: float) -> None:
    assert cvss.score(vector) == expected


@pytest.mark.parametrize("vector", [
    "", "CVSS:2.0/AV:N", "AV:N/AC:L/Au:N/C:P/I:P/A:P", "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/C:H/I:H/A:H",   # no S
    "CVSS:3.1/AV:X/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", "CVSS:3.1/AV:N/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
    "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N", "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/"
    "VA:H/SC:N/SI:N/SA:N/E:Z"])
def test_malformed_or_other_versions_give_no_score(vector: str) -> None:
    assert cvss.score(vector) is None


@pytest.mark.parametrize(("value", "band"), [(10.0, "CRITICAL"), (9.0, "CRITICAL"), (8.9, "HIGH"), (7.0, "HIGH"),
                                             (6.9, "MODERATE"), (4.0, "MODERATE"), (3.9, "LOW"), (0.0, "LOW")])
def test_band_thresholds(value: float, band: str) -> None:
    assert cvss.band(value) == band


# ---------------------------------------------------------------- R1: advisory severity

def _with(record: dict, *vectors: tuple[str, str], label: str | None = None) -> dict:
    rec = {**record, "severity": [{"type": t, "score": s} for t, s in vectors]}
    rec["database_specific"] = {"severity": label} if label else {}
    return rec


def test_severity_is_the_higher_of_cvss_and_the_label() -> None:
    base = _adv("X-1", "PyPI", "a", "0", "1.1", severity="")
    critical = ("CVSS_V3", "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H")
    moderate = ("CVSS_V3", "CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:N/A:N")
    assert osv.severity(_with(base, critical)) == 4                       # CVSS alone
    assert osv.severity(_with(base, critical, label="MODERATE")) == 4     # a lower label never lowers it
    assert osv.severity(_with(base, moderate, label="HIGH")) == 3         # database_specific higher than CVSS wins
    assert osv.severity(_with(base, ("CVSS_V4", "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"))) == 4
    assert osv.severity(_with(base, ("CVSS_V2", "AV:N/AC:L/Au:N/C:C/I:C/A:C"))) == 0   # v2 is not used
    assert osv.severity(_with(base, ("CVSS_V3", "garbage"))) == 0
    per_affected = {**base, "affected": [{**base["affected"][0], "severity": [{"type": critical[0], "score": critical[1]}]}]}
    assert osv.cvss_rank(per_affected) == 4                               # affected[].severity counts too


def test_cvss_only_advisory_is_ranked_from_its_vector(tmp_path: Path, feed: Feed) -> None:  # noqa: F811
    (tmp_path / "requirements.txt").write_text("a==1.0\n")
    feed.vulns = {("PyPI", "a", "1.0"): ["PYSEC-9"]}
    feed.records = {"PYSEC-9": _with(_adv("PYSEC-9", "PyPI", "a", "0", "1.1", severity=""),
                                     ("CVSS_V3", "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"))}
    rep = engine.scan_path(tmp_path)
    f = rep.findings[0]
    assert (f.severity, f.rated, f.unscored) == (4, 4, 0) and not f.shows_unscored()
    assert "CRITICAL" in rep.markdown() and "UNSCORED" not in rep.markdown()


def test_a_scan_counts_malformed_records_and_hidden_fix_versions(tmp_path: Path, feed: Feed,  # noqa: F811
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """R8 end to end: engine passes osv.quirks to the finding explicitly; the report shows both counts."""
    monkeypatch.setattr(osv, "_QUIRKS", {})
    (tmp_path / "requirements.txt").write_text("a==1.0\n")
    feed.vulns = {("PyPI", "a", "1.0"): ["PYSEC-1", "PYSEC-2", "PYSEC-3"]}
    feed.records = {"PYSEC-1": _adv("PYSEC-1", "PyPI", "a", "0", "1.1"),
                    "PYSEC-2": _adv("PYSEC-2", "PyPI", "a", "0", "1.2 <b>see notes</b>"),
                    "PYSEC-3": {**_adv("PYSEC-3", "PyPI", "a", "0", "1.3"), "affected": "broken"}}
    rep = engine.scan_path(tmp_path)
    f = rep.findings[0]
    assert (f.fix, f.malformed, f.fix_hidden) == ("1.1", 1, 1)
    md = rep.markdown()
    assert "fix version not shown (unusual format)" in md and "see notes" not in md
    assert "1 advisory record(s) from OSV were malformed" in md


def test_missing_severity_is_unscored_counted_as_high_and_shown_separately(tmp_path: Path, feed: Feed) -> None:  # noqa: F811
    (tmp_path / "requirements.txt").write_text("a==1.0\nb==2.0\n")
    feed.vulns = {("PyPI", "a", "1.0"): ["PYSEC-1"], ("PyPI", "b", "2.0"): ["G-LOW", "PYSEC-2"]}
    feed.records = {"PYSEC-1": _adv("PYSEC-1", "PyPI", "a", "0", "1.1", severity=""),
                    "PYSEC-2": _adv("PYSEC-2", "PyPI", "b", "0", "2.1", severity=""),
                    "G-LOW": _adv("G-LOW", "PyPI", "b", "0", "2.1", severity="LOW")}
    rep = engine.scan_path(tmp_path)
    by = {f.name: f for f in rep.findings}
    assert by["a"].severity == 3 and by["a"].unscored == 1
    assert (by["b"].severity, by["b"].rated, by["b"].unscored) == (3, 1, 1)   # one rated LOW, one unscored
    assert rep.exit_code("high") == EXIT_FINDINGS
    md = rep.markdown()
    assert "2 finding(s) UNSCORED" in md and "UNSCORED (as HIGH)" in md
    assert "## Unscored advisories (no CVSS vector or severity label)" in md and "PYSEC-1" in md
    back = Report.from_dict(json.loads(json.dumps(rep.to_dict())))           # summary.json round trip
    assert back.unscored_as == "high" and len(back.unscored_findings()) == 2
    rep.apply_unscored("low")
    assert by["b"].severity == 1 and [f.name for f in rep.unscored_findings()] == ["a"]
    assert rep.exit_code("moderate") == EXIT_CLEAN
    with pytest.raises(ValueError, match="unscored_as"):
        rep.apply_unscored("none")


def test_malicious_stays_critical_and_is_never_unscored() -> None:
    rep = Report(target="t", findings=[Finding(kind="package", target="t", name="x", severity=4, malicious=True,
                                               unscored=1, ids=["MAL-1"])])
    rep.apply_unscored("low")
    assert rep.findings[0].severity == 4 and not rep.unscored_findings() and rep.exit_code() == 4


def test_unscored_as_is_a_validated_config_key_applied_by_daily(xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    assert daily.DEFAULT_CONFIG["unscored_as"] == "high"
    path = daily.paths.config_file()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"unscored_as": "medium"}))
    with pytest.raises(ValueError, match="unscored_as must be one of"):
        daily.load_config()
    unscored = Finding(kind="package", target="r", name="a", version="1", ecosystem="PyPI", severity=3, unscored=1,
                       ids=["PYSEC-1"])

    def collect(cfg: dict, today: str, run_dir: Path, total: Report, staged: list) -> None:
        total.findings.append(unscored)

    monkeypatch.setattr(daily, "_collect", collect)
    cfg = {**daily.DEFAULT_CONFIG, "notify": "none", "unscored_as": "moderate"}
    _rep, code, run_dir = daily.run_daily(cfg, today="2026-10-24")
    assert code == EXIT_CLEAN and unscored.severity == 2           # fail_on high; counted as moderate
    assert "counts as MODERATE" in (run_dir / "summary.md").read_text()
    assert json.loads((run_dir / "summary.json").read_text())["unscored_as"] == "moderate"


def test_scan_cli_takes_unscored_as(tmp_path: Path, feed: Feed, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
                                    capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_CONFIG_DIR", str(tmp_path / "cfg"))
    (tmp_path / "requirements.txt").write_text("a==1.0\n")
    feed.vulns = {("PyPI", "a", "1.0"): ["PYSEC-1"]}
    feed.records = {"PYSEC-1": _adv("PYSEC-1", "PyPI", "a", "0", "1.1", severity="")}
    assert main(["scan", str(tmp_path)]) == EXIT_FINDINGS
    assert main(["scan", str(tmp_path), "--unscored-as", "moderate"]) == EXIT_CLEAN
    assert "UNSCORED (as MODERATE)" in capsys.readouterr().out


# ---------------------------------------------------------------- R3: catch-up and staleness

def _beat(hours_ago: float | None) -> None:
    daily.heartbeat_path().parent.mkdir(parents=True, exist_ok=True)
    if hours_ago is not None:
        last = dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours_ago)
        daily.heartbeat_path().write_text(json.dumps({"last_run": last.isoformat(), "exit": 0}))


def _recorders(monkeypatch: pytest.MonkeyPatch) -> tuple[list, list]:
    sent: list[tuple[str, dict]] = []
    runs: list[dict] = []
    monkeypatch.setattr(notify, "send", lambda cfg, key, **f: sent.append((key, f)) or True)
    monkeypatch.setattr(daily, "run_daily", lambda cfg, **k: runs.append(cfg) or (Report(target="daily"), 0,
                                                                                   daily.paths.state_dir()))
    return sent, runs


@pytest.mark.parametrize(("hours_ago", "due", "message"), [
    (2, False, None), (25.5, False, None), (30, True, "run-stale"), (None, True, "run-missing")])
def test_catch_up_runs_only_when_stale(xdg: Path, monkeypatch: pytest.MonkeyPatch, hours_ago: float | None,  # noqa: F811
                                       due: bool, message: str | None) -> None:
    sent, runs = _recorders(monkeypatch)
    _beat(hours_ago)
    result = daily.catch_up({**daily.DEFAULT_CONFIG})
    assert (result is not None) is due and bool(runs) is due
    assert [k for k, _f in sent] == ([message] if message else [])
    if message == "run-stale":
        assert sent[0][1] == {"hours": 30}
        assert notify.render("run-stale", hours=30).startswith("No daily run has completed for 30h")


def test_catch_up_honours_stale_after_hours_and_an_unreadable_heartbeat(xdg: Path,  # noqa: F811
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    _recorders(monkeypatch)
    _beat(10)
    assert daily.catch_up_due({**daily.DEFAULT_CONFIG, "stale_after_hours": 8}) == (True, 10)
    daily.heartbeat_path().write_text("{broken")
    assert daily.catch_up_due({**daily.DEFAULT_CONFIG}) == (True, None)


def test_daily_catch_up_cli_is_quiet_when_fresh(xdg: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
                                                capsys: pytest.CaptureFixture[str]) -> None:
    sent, runs = _recorders(monkeypatch)
    daily.init_config()
    _beat(1)
    assert main(["daily", "--catch-up"]) == 0
    out = capsys.readouterr()
    assert out.out == "" and out.err == "" and not runs and not sent
    _beat(40)
    assert main(["daily", "--catch-up"]) == 0 and len(runs) == 1 and sent[0][0] == "run-stale"
    assert "last daily run was 40h ago" in capsys.readouterr().err   # daily prints the staleness warning


def test_schedule_status_prints_the_staleness_warning(xdg: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(schedule, "_mechanism", lambda: "launchd")
    monkeypatch.setenv("HOME", str(xdg / "home"))
    daily.init_config()
    assert main(["schedule", "status"]) == 0
    assert "no daily run has completed yet" in capsys.readouterr().err
    _beat(50)
    assert main(["schedule", "status"]) == 0
    out = capsys.readouterr()
    assert "last daily run was 50h ago" in out.err and "catch-up job installed: False" in out.out
    assert main(["schedule", "install"]) == 0                         # a dry run prints its plan only
    assert "ago" not in capsys.readouterr().err


def test_launchd_catch_up_plist_runs_at_login_with_catch_up(xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    monkeypatch.setattr(schedule, "_mechanism", lambda: "launchd")
    monkeypatch.setenv("HOME", str(xdg / "home"))
    plan = schedule.install(7, 30, apply=False)
    main_plist, catch = (plan.files[str(xdg / "home" / "Library" / "LaunchAgents" / f"{label}.plist")]
                         for label in (schedule.LABEL, schedule.CATCH_UP_LABEL))
    assert "StartCalendarInterval" in main_plist and "RunAtLoad" not in main_plist and "--catch-up" not in main_plist
    assert f"<string>{schedule.CATCH_UP_LABEL}</string>" in catch and "<key>RunAtLoad</key><true/>" in catch
    assert f"<integer>{schedule.CATCH_UP_INTERVAL}</integer>" in catch and "StartCalendarInterval" not in catch
    assert re.search(r"<string>daily</string>\s*<string>--catch-up</string>", catch)
    assert not (xdg / "home" / "Library").exists() and any("catch-up" in n for n in plan.notes)


# ---------------------------------------------------------------- R5: unsupported ecosystems

UNSUPPORTED_FILES = {"go.sum": "Go (go.sum without go.mod)", "pom.xml": "Maven",
                     "Dockerfile": "container images", "docker-compose.prod.yml": "container images",
                     "compose.yaml": "container images", "api.Dockerfile": "container images",
                     "Containerfile": "container images"}


@pytest.mark.parametrize(("name", "eco"), sorted(UNSUPPORTED_FILES.items()))
def test_unsupported_ecosystems_are_discovered_and_unscannable(tmp_path: Path, name: str, eco: str) -> None:
    (tmp_path / name).write_text("content\n")
    assert [p.name for p in manifests.discover(tmp_path)] == [name]
    m = manifests.parse(tmp_path / name)
    assert not m.deps and m.unscannable == f"unsupported ecosystem: {eco}"


def test_fallback_manifests_only_without_their_lockfile(tmp_path: Path) -> None:
    for d, files in {"go1": ["go.mod", "go.sum"], "go2": ["go.mod"], "gr1": ["build.gradle", "gradle.lockfile"],
                     "gr2": ["build.gradle.kts"], "nu1": ["App.csproj", "packages.lock.json"], "nu2": ["App.csproj"],
                     "pn": ["package.json", "pnpm-lock.yaml"]}.items():
        (tmp_path / d).mkdir()
        for f in files:
            (tmp_path / d / f).write_text("x\n")
    got = {p.relative_to(tmp_path).as_posix() for p in manifests.discover(tmp_path)}
    assert got == {"go1/go.mod", "go2/go.mod", "gr1/gradle.lockfile", "gr2/build.gradle.kts",
                   "nu1/packages.lock.json", "nu2/App.csproj", "pn/pnpm-lock.yaml"}
    assert manifests.parse(tmp_path / "nu2" / "App.csproj").unscannable == "unsupported ecosystem: NuGet"
    assert manifests.unsupported_ecosystem("requirements.txt") == ""


def test_coverage_state_unsupported_ecosystem_and_mixed_workspaces(xdg: Path) -> None:  # noqa: F811
    go_only = _repo(xdg / "goapp", "git@example.org:acme/goapp.git")
    (go_only / "go.sum").write_text("x\n")
    (go_only / "Dockerfile").write_text("FROM scratch\n")
    mixed = _repo(xdg / "mixed", "git@example.org:acme/mixed.git")
    (mixed / "requirements.txt").write_text("lib-a==1.0\n")
    (mixed / "deploy").mkdir()
    (mixed / "deploy" / "Dockerfile").write_text("FROM scratch\n")
    empty = _repo(xdg / "empty", "git@example.org:acme/empty.git")
    entries = [_entry("example.org/acme/goapp", go_only, "scan"), _entry("example.org/acme/mixed", mixed, "scan"),
               _entry("example.org/acme/empty", empty, "scan")]
    found: dict[str, list[str]] = {}
    rows = coverage.states(daily.DEFAULT_CONFIG, entries, [], found)
    assert {n: s for _o, s, n, _p in rows} == {"goapp": "unsupported ecosystem", "mixed": "scan-only",
                                                "empty": "nothing to scan"}
    assert "unsupported ecosystem" in coverage.STATES
    assert found == {"goapp": ["Dockerfile (container images)", "go.sum (Go (go.sum without go.mod))"],
                     "mixed": ["deploy/Dockerfile (container images)"]}
    md = coverage.markdown(rows, unsupported=found)
    assert "- unsupported ecosystem (1): goapp" in md and "a coverage gap" in md
    assert "- mixed: deploy/Dockerfile (container images)" in md and "Not scanned (unsupported ecosystem)" in md
    counts = coverage.markdown(rows, names=False, unsupported=found)
    assert "3 manifest(s) in 2 workspace(s)." in counts and "goapp" not in counts
    assert coverage.project_state(None, None, go_only, []) == "unsupported ecosystem"


def test_status_shows_unsupported_ecosystem_and_lists_the_files(xdg: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
                                                                capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(grant, "load_active", lambda: ([], []))
    proj = xdg / "javaapp"
    proj.mkdir()
    (proj / "pom.xml").write_text("<project/>\n")
    assert main(["status", "--project", str(proj), "--json"]) == 0
    st: dict[str, Any] = json.loads(capsys.readouterr().out)
    assert st["coverage_state"] == "unsupported ecosystem" and st["unsupported_manifests"] == ["pom.xml (Maven)"]
    assert main(["status", "--project", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "coverage     unsupported ecosystem (only manifests vulnscan cannot read: a coverage gap" in out
    assert "not scanned  pom.xml (Maven) — unsupported ecosystem" in out


def test_rule_s_vm_reports_unsupported_ecosystem_but_not_nothing_to_scan() -> None:
    text = SECURITY.read_text(encoding="utf-8")
    assert int(re.search(r"security_rules_invariant v=(\d+)", text).group(1)) >= 15
    rule = text[text.index("**Rule S-VM: Continuous"):text.index("<!-- AGENTTEAMS:END security_rules_invariant")]
    assert "*unsupported ecosystem*" in rule and "**is** a finding: a coverage gap" in rule
    assert "*nothing to scan* (the project has no dependency\n  manifest) is not a finding" in rule
    row = next(ln for ln in rule.splitlines() if ln.startswith("| Daily vulnerability scan missing"))
    assert "*unsupported ecosystem* (a coverage gap; never *nothing to scan*)" in row and "CONDITIONAL PASS" in row


@pytest.mark.parametrize(("name", "read"), [
    ("requirements.txt", True), ("requirements-dev.txt", True), ("requirements.lock", True),
    ("ci-requirements.txt", True), ("dev_requirements.txt", True), ("test.requirements.in", True),
    ("myrequirements.txt", False), ("requirements.md", False), ("ci-requirements.json", False),
])
def test_prefixed_requirements_files_are_scanned(name: str, read: bool) -> None:
    """A prefix ending in -, _ or . (ci-requirements.txt) used to make the scanner skip the file silently."""
    assert bool(manifests.REQ_SCAN_NAME.match(name)) is read
    if name.endswith(".txt"):
        assert bool(manifests.REQ_NAME.match(name)) is read


def test_a_ci_requirements_file_reaches_the_report(tmp_path: Path, feed: Feed) -> None:  # noqa: F811
    ci = tmp_path / ".github"
    ci.mkdir()
    (ci / "ci-requirements.txt").write_text("pytest==9.0.2\n")
    feed.vulns = {("PyPI", "pytest", "9.0.2"): ["GHSA-6w46-j5rx-g56g"]}
    feed.records = {"GHSA-6w46-j5rx-g56g": _adv("GHSA-6w46-j5rx-g56g", "PyPI", "pytest", "0", "9.0.3", "MODERATE")}
    assert ci / "ci-requirements.txt" in manifests.discover(tmp_path)
    rep = engine.scan_path(tmp_path)
    (f,) = [f for f in rep.findings if f.kind == "package"]
    assert (f.name, f.version, f.fix) == ("pytest", "9.0.2", "9.0.3")
