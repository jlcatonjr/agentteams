"""vfr-v: per-finding effectiveness (V2: version pairing, the move detector) and same-day verification (V1).

V2's reference is the 9f audit script ``vfu-m7-audit.py`` r2, whose two defects it fixed are pinned here: a renamed
tree read as remediated, and a package matched across its version lines. V1 re-scans, after the last auto-merge,
the roots whose scan mirror moved since the morning run, re-audits the hosts an executor op ran against, and
writes ``verify.json`` without staging anything.
"""

from __future__ import annotations

import fcntl
import json
import os
import plistlib
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agentteams.vulnscan import daily, effectiveness, scan_mirror, verify
from agentteams.vulnscan.__main__ import main
from agentteams.vulnscan.report import Finding, Report
from tests.test_vulnscan_core import Feed
from tests.test_vulnscan_daily import _cfg, feed, xdg  # noqa: F401  (pytest fixtures)
from tests.test_vulnscan_install_guard import _wrapper

DAY = "2026-11-02"
OLD_TREE, NEW_TREE = "/root/anaconda3", "/root/anaconda3.disabled-20261101"
_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "scripts" / "vulnscan-remediation"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``$HOME`` = ``tmp_path``: the refresh script's ancestors are checked up to it (``/private/tmp`` is 1777)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _f(name: str, version: str, *locs: str, target: str = "host-a", ids: tuple[str, ...] = ("G1",),
       eco: str = "PyPI", **kw: Any) -> Finding:
    return Finding(kind="package", target=target, name=name, version=version, ecosystem=eco, ids=list(ids),
                   locations=list(locs), severity=3, **kw)


def _status(cmp: effectiveness.Comparison) -> dict[tuple[str, str], tuple[str, str]]:
    return {(o.finding.name, o.finding.version): (o.status, o.note) for o in cmp.outcomes}


# ---------------------------------------------------------------- V2: per finding


def test_two_version_lines_of_one_package_never_match_each_other() -> None:
    lock = "frontend/package-lock.json"
    base = Report(target="d", findings=[_f("minimatch", "3.0.4", lock, eco="npm"),
                                        _f("minimatch", "9.0.1", lock, eco="npm")])
    # the 3.x line moved up (still vulnerable), the 9.x line is untouched
    later = Report(target="d", findings=[_f("minimatch", "3.1.1", lock, eco="npm"),
                                         _f("minimatch", "9.0.1", lock, eco="npm")])
    got = _status(effectiveness.compare(base, later))
    assert got[("minimatch", "3.0.4")] == (effectiveness.PARTLY, "moved to 3.1.1")
    assert got[("minimatch", "9.0.1")] == (effectiveness.OPEN, "still at 9.0.1")
    # the 3.x line is gone: it is remediated, and the 9.x finding is not mistaken for it
    got = _status(effectiveness.compare(base, Report(target="d", findings=[later.findings[1]])))
    assert got[("minimatch", "3.0.4")][0] == effectiveness.REMEDIATED
    assert got[("minimatch", "9.0.1")][0] == effectiveness.OPEN


def test_a_renamed_tree_is_moved_not_remediated() -> None:
    site = "/lib/python3.12/site-packages"
    base = Report(target="h", findings=[_f("Django", "5.1", OLD_TREE + site), _f("Brotli", "1.0.9", OLD_TREE + site),
                                        _f("idna", "3.4", OLD_TREE + site), _f("Brotli", "1.1.0", "/usr/lib/py")])
    later = Report(target="h", findings=[
        _f("Django", "5.1", NEW_TREE + site),            # renamed, untouched
        _f("Brotli", "1.0.10", NEW_TREE + site),         # renamed, and upgraded (still vulnerable)
        _f("Brotli", "1.1.0", "/usr/lib/py"),            # elsewhere, untouched
    ])                                                    # idna gone from the renamed tree: remediated
    cmp = effectiveness.compare(base, later)
    assert cmp.moves == {"host-a": {NEW_TREE: OLD_TREE}}
    got = _status(cmp)
    assert got[("Django", "5.1")] == (effectiveness.MOVED, f"moved to {NEW_TREE}; still at 5.1")
    assert got[("Brotli", "1.0.9")] == (effectiveness.PARTLY, f"moved to 1.0.10 (under {NEW_TREE})")
    assert got[("Brotli", "1.1.0")][0] == effectiveness.OPEN
    assert got[("idna", "3.4")][0] == effectiveness.REMEDIATED
    assert cmp.counts()[effectiveness.MOVED] == 1 and cmp.counts()[effectiveness.REMEDIATED] == 1
    # an explicit path alias says the same thing, as the audit script's --path-alias did: still open
    named = effectiveness.compare(base, later, path_aliases={NEW_TREE: OLD_TREE})
    assert _status(named)[("Django", "5.1")][0] == effectiveness.OPEN and named.moves == {}


def test_a_rename_with_one_upgrade_is_still_a_move() -> None:
    """Rule 12: "the same package set" would let a rename plus one upgrade pass as remediating everything."""
    base = Report(target="h", findings=[_f(n, "1.0", f"{OLD_TREE}/lib") for n in ("a", "b", "c")])
    later = Report(target="h", findings=[_f("a", "1.0", f"{NEW_TREE}/lib"), _f("b", "1.5", f"{NEW_TREE}/lib"),
                                         _f("c", "1.0", f"{NEW_TREE}/lib")])
    got = _status(effectiveness.compare(base, later))
    assert got[("a", "1.0")][0] == got[("c", "1.0")][0] == effectiveness.MOVED
    assert got[("b", "1.0")][0] == effectiveness.PARTLY


@pytest.mark.parametrize(("old", "new"), [
    ("/usr/lib/python3.10/site-packages", "/usr/lib/python3.12/site-packages"),   # an upgrade, not a rename
    ("/root/anaconda3/lib", "/opt/anaconda3/lib"),                                 # two components differ
    ("/srv/app/lib", "/srv/application/lib"),                                      # no separator after the name
])
def test_what_is_not_a_rename(old: str, new: str) -> None:
    base = Report(target="h", findings=[_f("Django", "5.1", old)])
    later = Report(target="h", findings=[_f("Django", "5.1", new)])
    cmp = effectiveness.compare(base, later)
    assert cmp.moves == {}
    # not a move, and still not remediated: the package is present elsewhere, and reported
    assert _status(cmp)[("Django", "5.1")] == (effectiveness.ELSEWHERE, f"found at {new} (5.1)")
    assert [g.locations for g in cmp.new] == [[new]]


def test_a_package_present_elsewhere_is_never_remediated() -> None:
    """@security: a tree moved other than by an in-place rename must not read as a fix."""
    base = Report(target="h", findings=[_f("Django", "5.1", "/root/anaconda3/lib"), _f("idna", "3.4", "/usr/lib/py")])
    later = Report(target="h", findings=[_f("Django", "5.2", "/opt/anaconda3/lib"),       # moved and bumped
                                         _f("Brotli", "1.0", "/usr/lib/py")])             # a new key
    cmp = effectiveness.compare(base, later)
    got = _status(cmp)
    assert got[("Django", "5.1")] == (effectiveness.ELSEWHERE, "found at /opt/anaconda3/lib (5.2)")
    assert got[("idna", "3.4")][0] == effectiveness.REMEDIATED
    assert sorted(g.name for g in cmp.new) == ["Brotli", "Django"]
    assert cmp.counts()[effectiveness.ELSEWHERE] == 1


def test_a_rename_carrying_no_finding_unchanged_is_not_a_move() -> None:
    base = Report(target="h", findings=[_f("Django", "5.1", f"{OLD_TREE}/lib")])
    later = Report(target="h", findings=[_f("Django", "5.2", f"{NEW_TREE}/lib")])
    assert effectiveness.detect_moves(base.findings, later.findings) == {}


def test_a_renamed_host_and_an_unscanned_target() -> None:
    base = Report(target="d", findings=[_f("Django", "5.1", "/x", target="vk-prod"),
                                        _f("PyJWT", "2.3.0", "requirements.txt", target="/repo")])
    later = Report(target="d", findings=[_f("Django", "5.1", "/x", target="jlc-prod")])
    cmp = effectiveness.compare(base, later, aliases={"jlc-prod": "vk-prod"}, scanned={"vk-prod"})
    got = _status(cmp)
    assert got[("Django", "5.1")][0] == effectiveness.OPEN and not cmp.new
    assert got[("PyJWT", "2.3.0")] == (effectiveness.NOT_ASSESSED, "target has no scan record in the later run")
    # without the alias the rename reads as a remediation plus a new finding: why --alias exists
    plain = effectiveness.compare(base, later, scanned={"vk-prod", "jlc-prod"})
    assert _status(plain)[("Django", "5.1")][0] == effectiveness.REMEDIATED and len(plain.new) == 1


def test_range_findings_are_left_out() -> None:
    rng = Finding(kind="range", target="/r", name="requests", ecosystem="PyPI", spec=">=2")
    cmp = effectiveness.compare(Report(target="d", findings=[rng]), Report(target="d"))
    assert cmp.outcomes == [] and cmp.counts() == dict.fromkeys(effectiveness.OUTCOMES, 0)


def test_scanned_targets_need_a_record_or_a_complete_audit(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    home = str(Path.home())
    (run / "repos-a.json").write_text(json.dumps({"target": f"{home}/code/a"}), encoding="utf-8")
    (run / "repos-m.json").write_text(json.dumps({"target": "/code/m.worktrees/vulnscan-mirror"}), encoding="utf-8")
    (run / "repos-bad.json").write_text("{", encoding="utf-8")
    (run / "server-good.log").write_text("...\nRESULT clean\n", encoding="utf-8")
    (run / "server-cut.log").write_text("ssh: connection reset\n", encoding="utf-8")
    got = effectiveness.scanned_targets(run)
    assert got == {"this-machine", f"{home}/code/a", "~/code/a", "/code/m", "good"}
    assert effectiveness.assessed({"/code/m/sub", "/code/mx", "good", "cut"}, got) == {"/code/m/sub", "good"}
    # a record an earlier run of the day left behind is not this run's scan
    old = (run / "server-good.log").stat().st_mtime - 3600
    os.utime(run / "server-good.log", (old, old))
    assert "good" not in effectiveness.scanned_targets(run, since=old + 60)


def test_the_daily_section_adds_per_finding_counts_and_names_nothing(xdg: Path) -> None:  # noqa: F811
    prev = Report(target="daily", findings=[_f("Django", "5.1", f"{OLD_TREE}/lib", target="secret-host"),
                                            _f("PyJWT", "2.3.0", "req.txt", target="/private/repo")])
    total = Report(target="daily", findings=[_f("Django", "5.1", f"{NEW_TREE}/lib", target="secret-host")])
    md, _ = effectiveness.section(total, prev, stats=effectiveness.PrStats(), scanned={"secret-host", "/private/repo"})
    assert "| Previous run's findings, per finding: moved (not remediated) | 1 |" in md
    assert "| Previous run's findings, per finding: remediated | 1 |" in md
    assert "secret-host" not in md and "/private/repo" not in md and "anaconda" not in md
    md, _ = effectiveness.section(total, prev, stats=effectiveness.PrStats())   # no scan record: not computed
    assert "per finding" not in md


def _run_dir(state: Path, name: str, findings: list[Finding], *, servers: tuple[str, ...] = ()) -> Path:
    d = state / name
    d.mkdir(parents=True)
    (d / "summary.json").write_text(json.dumps(Report(target="daily", findings=findings).to_dict()), encoding="utf-8")
    for h in servers:
        (d / f"server-{h}.log").write_text("RESULT action-needed\n", encoding="utf-8")
    return d


def test_the_effectiveness_command_writes_rows_and_markdown(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    base = _run_dir(tmp_path, "2026-10-31", [_f("Django", "5.1", f"{OLD_TREE}/lib", target="vk-prod")],
                    servers=("vk-prod",))
    later = _run_dir(tmp_path, "2026-11-02", [_f("Django", "5.1", f"{NEW_TREE}/lib", target="jlc-prod")],
                     servers=("jlc-prod",))
    rows, out = tmp_path / "rows.csv", tmp_path / "eff.md"
    assert main(["effectiveness", str(base), str(later), "--alias", "vk-prod=jlc-prod", "--csv", str(rows),
                 "--out", str(out)]) == 0
    assert "moved (not remediated) 1" in capsys.readouterr().out
    assert rows.read_text(encoding="utf-8").splitlines()[1].startswith("moved (not remediated),HIGH,vk-prod,")
    text = out.read_text(encoding="utf-8")
    assert f"`{OLD_TREE}` → `{NEW_TREE}`" in text and "target alias: `jlc-prod` = `vk-prod`" in text
    assert main(["effectiveness", str(base), str(tmp_path / "missing")]) == 2
    assert main(["effectiveness", str(base), str(later), "--alias", "nope"]) == 2


# ---------------------------------------------------------------- V1: same-day verification


def _repo(tmp_path: Path, pin: str) -> tuple[Path, Path]:
    root = tmp_path / "code" / "app"
    (root / ".git").mkdir(parents=True)
    (root / "requirements.txt").write_text(f"PyJWT=={pin}\n", encoding="utf-8")
    mirror = tmp_path / "code" / "app.worktrees" / "vulnscan-mirror"
    mirror.mkdir(parents=True)
    (mirror / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    (mirror / "requirements.txt").write_text(f"PyJWT=={pin}\n", encoding="utf-8")
    return root, mirror


def _stamp(root: Path, sha: str) -> None:
    p = scan_mirror.stamp_path(DAY)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({str(root): f"ok {sha}"}), encoding="utf-8")


def _refresher(tmp_path: Path, root: Path, mirror: Path, pin: str, sha: str) -> Path:
    """A stand-in for vauto-a-mirrors.sh refresh: moves the mirror to a fixed commit and records it."""
    script = tmp_path / "bin" / "refresh.sh"
    script.parent.mkdir(exist_ok=True)
    script.write_text(f'#!/bin/bash\n[[ "$1" == refresh ]] || exit 2\n'
                      f'printf "PyJWT=={pin}\\n" > "{mirror}/requirements.txt"\n'
                      f"printf '{json.dumps({str(root): f'ok {sha}'})}' > \"{scan_mirror.stamp_path(DAY)}\"\n"
                      'echo "refreshed: 1 ok, 0 failed"\n', encoding="utf-8")
    script.chmod(0o700)
    return script


def _cfg_m(root: Path, **kw: Any) -> dict[str, Any]:
    return _cfg(repo_roots=[str(root)], scan_mirror="vulnscan-mirror", **kw)


def test_a_fix_merged_after_the_morning_scan_is_measured_the_same_day(xdg: Path, feed: Feed,  # noqa: F811
                                                                       tmp_path: Path, home: Path) -> None:
    root, mirror = _repo(tmp_path, "2.3.0")
    _stamp(root, "a" * 40)
    cfg = _cfg_m(root)
    daily.run_daily(cfg, today=DAY)
    run_dir = daily.paths.state_dir() / DAY
    summary = (run_dir / "summary.json").read_bytes()
    plans = sorted(p.name for p in run_dir.iterdir())
    rec, code = verify.run_verify(cfg, today=DAY, mirror_refresh=_refresher(tmp_path, root, mirror, "2.15.0", "b" * 40))
    assert code == 0 and rec["state"] == "verified" and rec["mirror_refresh"] == "ok: refreshed: 1 ok, 0 failed"
    assert rec["rescanned"] == [{"root": str(root), "before": "a" * 40, "after": "b" * 40}]
    assert rec["counts"][effectiveness.REMEDIATED] == 1 and rec["findings"][0]["name"] == "PyJWT"
    on_disk = json.loads((run_dir / verify.VERIFY_FILE).read_text(encoding="utf-8"))
    assert on_disk["complete"] and on_disk["counts"] == rec["counts"]
    # the morning's files are untouched, nothing is staged or planned; only verify.json and verify/ are new
    assert (run_dir / "summary.json").read_bytes() == summary
    assert sorted(p.name for p in run_dir.iterdir()) == sorted([*plans, verify.VERIFY_FILE, verify.WORK_DIR])
    assert json.loads((run_dir / verify.WORK_DIR / verify.MORNING_MIRRORS).read_text())[str(root)] == "ok " + "a" * 40
    again, code = verify.run_verify(cfg, today=DAY)
    assert code == 0 and again["state"] == "already-verified"


def test_a_second_verification_compares_with_the_morning_commit(xdg: Path, feed: Feed,  # noqa: F811
                                                                 tmp_path: Path, home: Path) -> None:
    root, mirror = _repo(tmp_path, "2.3.0")
    _stamp(root, "a" * 40)
    cfg = _cfg_m(root)
    daily.run_daily(cfg, today=DAY)
    script = _refresher(tmp_path, root, mirror, "2.15.0", "b" * 40)
    rec, _ = verify.run_verify(cfg, today=DAY, mirror_refresh=script, force=True)
    rec, code = verify.run_verify(cfg, today=DAY, mirror_refresh=script, force=True)   # the record now says b
    assert code == 0 and rec["rescanned"][0]["before"] == "a" * 40 and rec["counts"][effectiveness.REMEDIATED] == 1


def test_a_root_whose_mirror_did_not_move_is_not_rescanned(xdg: Path, feed: Feed,  # noqa: F811
                                                           tmp_path: Path, home: Path) -> None:
    root, mirror = _repo(tmp_path, "2.3.0")
    _stamp(root, "a" * 40)
    cfg = _cfg_m(root)
    daily.run_daily(cfg, today=DAY)
    rec, code = verify.run_verify(cfg, today=DAY, mirror_refresh=_refresher(tmp_path, root, mirror, "2.3.0", "a" * 40))
    assert code == 0 and rec["rescanned"] == [] and rec["findings"] == [] and rec["assessed"] == []


def test_a_failed_refresh_or_scan_mirror_off_is_reported(xdg: Path, feed: Feed,  # noqa: F811
                                                         tmp_path: Path, home: Path) -> None:
    root, _mirror = _repo(tmp_path, "2.3.0")
    _stamp(root, "a" * 40)
    daily.run_daily(_cfg_m(root), today=DAY)
    bad = tmp_path / "bin" / "refresh.sh"
    bad.parent.mkdir()
    bad.write_text("#!/bin/bash\necho fetch failed\nexit 1\n", encoding="utf-8")
    bad.chmod(0o700)
    rec, code = verify.run_verify(_cfg_m(root), today=DAY, mirror_refresh=bad, force=True)
    assert code == 3 and rec["state"] == "partial" and rec["mirror_refresh"] == "failed: exit 1: fetch failed"
    rec, code = verify.run_verify(_cfg(repo_roots=[str(root)]), today=DAY, force=True)
    assert rec["not_rescanned"][-1]["reason"].startswith("scan_mirror is off") and rec["rescanned"] == []


def test_without_a_morning_run_or_while_a_lock_is_held_nothing_is_written(xdg: Path) -> None:  # noqa: F811
    rec, code = verify.run_verify(_cfg(), today=DAY)
    assert code == 3 and rec["state"] == "no-run"
    run_dir = _run_dir(daily.paths.state_dir(), DAY, [])
    for lock in (daily.remediation_lock_path(), daily.run_lock_path()):
        with open(lock, "a+", encoding="utf-8") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            rec, code = verify.run_verify(_cfg(), today=DAY)
            fcntl.flock(held, fcntl.LOCK_UN)
        assert code == 3 and rec["state"] == "busy" and str(lock) in rec["detail"]
        assert not (run_dir / verify.VERIFY_FILE).exists() and not (run_dir / verify.WORK_DIR).exists()


def test_refresh_problem_refuses_what_another_user_could_have_written(home: Path) -> None:
    sub = home / "bin"
    sub.mkdir(mode=0o700)
    script = sub / "r.sh"
    script.write_text("#!/bin/bash\n", encoding="utf-8")
    script.chmod(0o700)
    assert verify.refresh_problem(script) == ""
    script.chmod(0o720)
    assert "r.sh is writable by group or others" in verify.refresh_problem(script)
    script.chmod(0o700)
    sub.chmod(0o770)   # a writable parent: someone else could swap the script in
    assert f"directory {os.path.realpath(sub)} is writable by group or others" in verify.refresh_problem(script)
    sub.chmod(0o700)
    link = sub / "link.sh"
    link.symlink_to(script)
    assert "link.sh is a symlink" in verify.refresh_problem(link)
    assert "not an absolute path" in verify.refresh_problem(Path("r.sh"))
    assert verify.refresh_problem(sub / "missing.sh")
    (sub / "adir").mkdir(mode=0o700)
    assert "adir is not a regular file" in verify.refresh_problem(sub / "adir")


def test_outside_home_every_ancestor_is_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "elsewhere"))
    script = tmp_path / "r.sh"
    script.write_text("#!/bin/bash\n", encoding="utf-8")
    script.chmod(0o700)
    shared = [d for d in Path(os.path.realpath(tmp_path)).parents if os.stat(d).st_mode & 0o022]
    why = verify.refresh_problem(script)
    assert (f"directory {shared[0]} is writable by group or others" == why) if shared else why == ""


def test_the_checked_file_is_what_runs_even_if_swapped_after_the_check(home: Path,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    script = home / "r.sh"
    script.write_text('#!/bin/bash\necho "checked $1"\n', encoding="utf-8")
    script.chmod(0o700)
    real = subprocess.run

    def swap_then_run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess:
        evil = home / "evil.sh"
        evil.write_text('#!/bin/bash\necho "swapped $1"\n', encoding="utf-8")
        os.replace(evil, script)                       # after every check, before bash starts
        assert argv[1].startswith("/dev/fd/") and kw["pass_fds"]
        return real(argv, **kw)

    monkeypatch.setattr(verify.subprocess, "run", swap_then_run)
    assert verify._refresh(script) == "ok: checked refresh"
    assert script.read_text(encoding="utf-8").startswith('#!/bin/bash\necho "swapped')


def test_audited_hosts_are_todays_server_ops() -> None:
    rows = [{"op": "server-dryrun", "kind": "done", "target": "web", "ts": f"{DAY}T15:00:00+00:00"},
            {"op": "server-audit", "kind": "failed", "target": "db", "ts": f"{DAY}T16:00:00+00:00"},
            {"op": "server-dryrun", "kind": "begun", "target": "begun-only", "ts": f"{DAY}T15:00:00+00:00"},
            {"op": "open-pr", "kind": "done", "target": "/repo", "ts": f"{DAY}T15:00:00+00:00"},
            {"op": "server-dryrun", "kind": "done", "target": "old", "ts": "2026-10-01T15:00:00+00:00"}]
    assert verify.audited_hosts(rows, DAY) == ["db", "web"]


def test_hosts_an_executor_op_ran_against_are_re_audited(xdg: Path,  # noqa: F811
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    before = [_f("openssl", "3.0.2", target="web", eco="Ubuntu"), _f("zstd", "1.4", target="db", eco="Ubuntu")]
    run_dir = _run_dir(daily.paths.state_dir(), DAY, before)
    monkeypatch.setattr(verify.ledger, "read", lambda: [
        {"op": "server-dryrun", "kind": "done", "target": h, "ts": f"{DAY}T15:00:00+00:00"} for h in ("web", "other")])
    seen: list[tuple[str, Path]] = []

    def audit(host: str, kit: Path, out_dir: Path) -> Report:
        seen.append((host, out_dir))
        return Report(target=host)   # openssl is gone

    monkeypatch.setattr(daily, "scan_server", audit)
    rec, code = verify.run_verify(_cfg(servers=["web", "db"]), today=DAY)
    assert code == 0 and seen == [("web", run_dir / verify.WORK_DIR)]
    assert rec["hosts"] == ["web"] and rec["hosts_skipped"] == ["other"] and rec["assessed"] == ["web"]
    assert [(r["name"], r["status"]) for r in rec["findings"]] == [("openssl", effectiveness.REMEDIATED)]


def test_the_verify_command(xdg: Path, capsys: pytest.CaptureFixture) -> None:  # noqa: F811
    daily.paths.config_file().parent.mkdir(parents=True, exist_ok=True)
    daily.paths.config_file().write_text(json.dumps({"notify": "none"}), encoding="utf-8")
    assert main(["verify", "--date", "2026-13-01"]) == 2
    assert main(["verify", "--date", DAY]) == 3
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["state"] == "no-run"


# ---------------------------------------------------------------- V1: the scheduled job


def test_the_verify_job_is_guarded_and_installed_by_the_remediation_installer() -> None:
    wrapped = (_SCRIPTS / "vulnscan-daily-wrapped.sh").read_text(encoding="utf-8")
    auto = (_SCRIPTS / "vulnscan-remediate-auto.sh").read_text(encoding="utf-8")
    case = wrapped.split("  --verify)", 1)[1].split(";;", 1)[0]
    assert '"${VS[@]}" "${args[@]}"' in case and "args+=(--mirror-refresh" in case
    # the guard runs for every mode but --ensure-only, before the case, and under the wrapper lock
    assert wrapped.index('install_guard "daily${1:+ $1}"') < wrapped.index("  --verify)")
    assert wrapped.index('exec /usr/bin/lockf -t 1800 "$STATE/wrapper.lock"') < wrapped.index("  --verify)")
    install = auto.split("install_job() {", 1)[1].split("\n}\n", 1)[0]
    assert '"${VS[@]}" verify --help' in install and 'write_plist "$VERIFY_PLIST" "$VERIFY_LABEL"' in install
    assert '"<key>StartCalendarInterval</key><array>$when</array>" "$INSTALL_DIR/vulnscan-daily-wrapped.sh" --verify' \
        in install
    assert "notify() { :; }" in case   # verification never pops a notification
    assert 'rm -f "$VERIFY_PLIST"' in auto.split("--uninstall)", 1)[1].split(";;", 1)[0]


def test_the_verify_job_refuses_the_wrong_install(tmp_path: Path) -> None:
    out = _wrapper(tmp_path, _SCRIPTS / "vulnscan-daily-wrapped.sh", "--verify")
    assert out.returncode == 4 and "install-guard: REFUSED daily --verify" in out.stderr


def test_the_installer_rejects_a_bad_verify_time(tmp_path: Path) -> None:
    """VULNSCAN_VERIFY_TIMES is checked before anything is replaced (the times go into the plist)."""
    body = (_SCRIPTS / "vulnscan-remediate-auto.sh").read_text(encoding="utf-8")
    install = body.split("install_job() {", 1)[1].split("\n}\n", 1)[0]
    assert install.index("VULNSCAN_VERIFY_TIMES: not HH:MM") < install.index('bak="$CFG_DIR')
    loop = install.split('for t in $VERIFY_TIMES; do', 1)[1].split("done", 1)[0]

    def times(value: str) -> subprocess.CompletedProcess:
        return subprocess.run(["/bin/bash", "-c", f'bad() {{ echo "$*"; }}; VERIFY_TIMES="{value}"; when=""; '
                               f"f() {{ local t; for t in $VERIFY_TIMES; do{loop}done; echo \"$when\"; }}; f"],
                              capture_output=True, text=True, timeout=30, env={"PATH": "/usr/bin:/bin"})
    ok = times("17:15 08:05")
    assert ok.returncode == 0 and "<integer>17</integer><key>Minute</key><integer>15</integer>" in ok.stdout
    assert "<integer>8</integer><key>Minute</key><integer>5</integer>" in ok.stdout
    for bad in ("24:00", "7:5", "17:15;x", "noon"):
        assert times(bad).returncode == 2, bad


def test_write_plist_writes_both_jobs_as_valid_plists(tmp_path: Path) -> None:
    """The installer's one plist writer: run with launchctl stubbed, parse what it wrote."""
    body = (_SCRIPTS / "vulnscan-remediate-auto.sh").read_text(encoding="utf-8")
    esc = next(ln for ln in body.splitlines() if ln.startswith("xml_esc()"))
    fn = "write_plist() {" + body.split("write_plist() {", 1)[1].split("\n}\n", 1)[0] + "\n}\n"
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "launchctl").write_text(f'#!/bin/bash\necho "$*" >> "{tmp_path}/launchctl.log"\n', encoding="utf-8")
    (stub / "launchctl").chmod(0o755)
    script = (f"{esc}\n{fn}\npath=/usr/bin:/bin\n"
              f'write_plist "{tmp_path}/v.plist" io.x.verify "{tmp_path}/v&.log" '
              '"<key>StartCalendarInterval</key><array><dict><key>Hour</key><integer>17</integer></dict></array>" '
              f'"{tmp_path}/daily wrapped.sh" --verify\n')
    proc = subprocess.run(["/bin/bash", "-c", script], capture_output=True, text=True, timeout=30,
                          env={"PATH": f"{stub}:/usr/bin:/bin", "HOME": str(tmp_path)})
    assert proc.returncode == 0, proc.stderr
    plist = plistlib.loads((tmp_path / "v.plist").read_bytes())
    assert plist["Label"] == "io.x.verify" and plist["StartCalendarInterval"] == [{"Hour": 17}]
    assert plist["ProgramArguments"] == ["/bin/bash", f"{tmp_path}/daily wrapped.sh", "--verify"]
    assert plist["StandardOutPath"] == f"{tmp_path}/v&.log" and plist["EnvironmentVariables"]["PATH"] == "/usr/bin:/bin"
    assert "bootstrap" in (tmp_path / "launchctl.log").read_text()
