"""rc.4 stream C: the gateway files are masked from the agent (R6); malformed OSV records and fix versions (R8).

No Docker and no network: ``docker`` and the OSV feed are faked.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agentteams.vulnscan import agent_run, debrief, executor, osv
from agentteams.vulnscan.report import Finding, Report

MASKS = ["/work/run/gateway:ro,size=1k", "/work/run/debrief:ro,size=1k"]


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(tmp_path / "state"))
    run = tmp_path / "state" / "2026-10-22"
    run.mkdir(parents=True)
    (run / "summary.json").write_text(json.dumps({"findings": []}))
    team = tmp_path / "team"
    team.mkdir()
    key = tmp_path / "secrets" / "provider.env"
    key.parent.mkdir()
    key.write_text("OPENROUTER_API_KEY=placeholder\n")
    a = agent_run.agent_cfg({"agent": {"enabled": True, "team_dir": str(team), "key_file": str(key)}})
    return {"run": run, "a": a, "tmp": tmp_path}


def _masks(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, x in enumerate(argv) if x == "--tmpfs" and argv[i + 1].startswith("/work/run/")]


def _fake_docker(monkeypatch: pytest.MonkeyPatch, seen: list[tuple[list[str], dict[str, bool]]],
                 watch: list[Path], stdout: str = "") -> None:
    """Record every docker call with whether each watched dir existed at that moment."""
    def docker(argv: list[str], **kw: object) -> subprocess.CompletedProcess:
        seen.append((list(argv), {str(p): p.is_dir() for p in watch}))
        return subprocess.CompletedProcess(argv, 0, stdout=stdout if "-c" in argv else "sha256:img", stderr="")
    monkeypatch.setattr(agent_run, "_docker", docker)
    monkeypatch.setattr(agent_run.time, "sleep", lambda s: None)
    monkeypatch.setattr(agent_run.gateway_context, "_scutil", lambda key: "")


# ---------------------------------------------------------------- R6: argv

def test_run_argv_masks_the_gateway_and_debrief_dirs(env: dict[str, Any]) -> None:
    argv = agent_run.agent_argv(env["a"], "img:1", env["run"], "vulnscan-2026-10-22")
    assert _masks(argv) == MASKS
    run_mount = argv.index(f"{env['run']}:/work/run:ro")
    assert all(argv.index(m) > run_mount for m in MASKS)   # read as masks over the run mount
    assert _masks(agent_run.selftest_argv(env["a"], "img:1", env["run"])) == MASKS


def test_debrief_argv_masks_the_gateway_and_debrief_dirs(env: dict[str, Any]) -> None:
    argv = debrief.debrief_argv(env["a"], "img:1", env["run"], "vulnscan-2026-10-22")
    assert _masks(argv) == MASKS
    assert argv.index("-it") < argv.index("--tmpfs")


def test_the_masks_are_in_the_fingerprinted_flags() -> None:
    base = agent_run._base_argv(Path("/run"), Path("/s"), Path("/t"), Path("/sp"), name="n")
    assert _masks(base) == MASKS                            # flags_sha256 hashes this argv


# ---------------------------------------------------------------- R6: mountpoints exist before docker run

def test_run_agent_makes_the_mountpoints_before_docker_run(env: dict[str, Any],
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    run = env["run"]
    watch = [run / "gateway", run / "debrief"]
    seen: list[tuple[list[str], dict[str, bool]]] = []
    _fake_docker(monkeypatch, seen, watch)
    monkeypatch.setattr(agent_run.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(agent_run, "selftest_ok", lambda cfg: (True, "ok"))
    at_popen: list[dict[str, bool]] = []

    class Proc:
        def __init__(self, argv: list[str], **kw: object) -> None:
            at_popen.append({str(p): p.is_dir() for p in watch})
            assert _masks(argv) == MASKS

        def poll(self) -> int:
            return 0

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(agent_run.subprocess, "Popen", Proc)
    agent_run.run_agent({"agent": env["a"]}, run)
    assert at_popen and all(all(v.values()) for v in at_popen)
    runs = [flags for argv, flags in seen if argv[:2] == ["docker", "run"]]
    assert runs and all(all(v.values()) for v in runs)       # the gateway's docker run too


def test_selftest_makes_the_mountpoints_before_docker_run(env: dict[str, Any],
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    work = env["tmp"] / "state" / "selftest" / "run"
    watch = [work / "gateway", work / "debrief"]
    seen: list[tuple[list[str], dict[str, bool]]] = []
    _fake_docker(monkeypatch, seen, watch, stdout="SELFTEST " + json.dumps({"no_capabilities": True}))
    res = agent_run.selftest({"agent": env["a"]})
    assert res["passed"]
    probe = [(argv, flags) for argv, flags in seen if argv[:2] == ["docker", "run"] and "-c" in argv]
    assert len(probe) == 1 and _masks(probe[0][0]) == MASKS and all(probe[0][1].values())


def test_debrief_makes_the_mountpoints_before_docker_run(env: dict[str, Any],
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    run = env["run"]
    agent_run.sessions_dir(run).mkdir(parents=True)
    watch = [run / "gateway", run / "debrief"]
    seen: list[tuple[list[str], dict[str, bool]]] = []
    _fake_docker(monkeypatch, seen, watch)
    monkeypatch.setattr(agent_run, "selftest_ok", lambda c: (True, "ok"))
    monkeypatch.setattr(debrief.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(debrief, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(debrief.time, "sleep", lambda s: None)
    at_popen: list[dict[str, bool]] = []

    class Proc:
        def __init__(self, argv: list[str]) -> None:
            at_popen.append({str(p): p.is_dir() for p in watch})
            assert _masks(argv) == MASKS

        def wait(self, timeout: float | None = None) -> int:
            return 0

    monkeypatch.setattr(debrief.subprocess, "Popen", Proc)
    assert not (run / "gateway").exists()
    assert debrief.run_debrief({"agent": env["a"]}, run) == 0
    assert at_popen == [{str(p): True for p in watch}]
    assert (executor.spool_dir() / "requests").is_dir()


# ---------------------------------------------------------------- R8: malformed records

def _rec(events: object, *, name: object = "p", ranges: object = None) -> dict:
    rng = {"type": "ECOSYSTEM", "events": events} if ranges is None else None
    return {"id": "X", "affected": [{"package": {"ecosystem": "npm", "name": name},
                                     "ranges": [rng] if ranges is None else ranges}]}


@pytest.mark.parametrize("record", [
    None, [], "text", {"affected": "oops"}, {"affected": [None, 3, "x"]}, {"affected": [{"package": "p"}]},
    {"affected": [{"package": {"ecosystem": "npm", "name": 7}}]}, _rec(None, ranges="r"), _rec(None, ranges=[None, 1]),
    _rec("events"), _rec([None, 1, "e"]), _rec([{"introduced": 0}, {"fixed": "1.2.0"}]),
    _rec([{"introduced": "0"}, {"fixed": ["1.2.0"]}]), _rec([{"introduced": "0"}, {"fixed": None}]),
])
def test_fix_for_walks_malformed_records_without_crashing(record: object) -> None:
    assert osv.fix_for(record, "npm", "p", "1.0.0") is None   # type: ignore[arg-type]
    assert osv._walk_fix(record, "npm", "p", "1.0.0")[1] is True


def test_a_malformed_event_never_pairs_a_fix_with_an_earlier_start() -> None:
    rec = _rec([{"introduced": "0"}, {"introduced": {"v": 1}}, {"fixed": "1.2.0"}])
    assert osv._walk_fix(rec, "npm", "p", "1.0.0") == (None, True, False)


def test_a_good_range_still_fixes_beside_a_malformed_one() -> None:
    rec = _rec(None, ranges=[None, {"type": "SEMVER", "events": [{"introduced": "0"}, {"fixed": "1.2.0"}]}])
    assert osv._walk_fix(rec, "npm", "p", "1.0.0") == ("1.2.0", True, False)
    clean = _rec([{"introduced": "0"}, {"fixed": "1.2.0"}])
    assert osv._walk_fix(clean, "npm", "p", "1.0.0") == ("1.2.0", False, False)


# ---------------------------------------------------------------- R8: fix versions

@pytest.mark.parametrize("fixed,shown", [
    ("1.2.0", True), ("1:2.0~rc1+deb_1-2", True), ("9" * 64, True), ("9" * 65, False), ("1.2.0 <b>", False),
    ("1.2.0\n", False), ("1.2.0](https://x.example)", False), ("1.2.0é", False),
])
def test_only_plain_fix_versions_are_returned(fixed: str, shown: bool) -> None:
    rec = _rec([{"introduced": "0"}, {"fixed": fixed}])
    got = osv._walk_fix(rec, "npm", "p", "0.0.1")
    if shown:
        assert got == (fixed, False, False)
    else:
        assert got == (None, False, True) and osv.fix_for(rec, "npm", "p", "0.0.1") is None


@pytest.fixture
def feed(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    records: dict[str, object] = {}
    monkeypatch.setattr(osv, "_QUIRKS", {})
    monkeypatch.setattr(osv, "details", lambda vid: records[vid])
    return records


def test_quirks_reach_the_finding_and_the_report(feed: dict[str, object]) -> None:
    feed.update(A=_rec([{"introduced": "0"}, {"fixed": "2.0.0 (see notes)"}]), B=_rec([{"introduced": "0"}, {"fixed": "1.5.0"}]),
                C={"affected": "broken"}, D=_rec([{"introduced": "0"}]))
    fix, unfixed = osv.minimal_fix(["A", "B", "C", "D"], "npm", "p", "1.0.0")
    assert (fix, unfixed) == ("1.5.0", 3)
    assert osv.quirks(["A", "B", "C", "D"], "npm", "P", "1.0.0") == (1, 1)
    assert osv.quirks(["A", "C"], "npm", "p", "9.9.9") == (0, 0)   # another version: not seen
    f = Finding(kind="package", target="web", name="p", version="1.0.0", ecosystem="npm", severity=3,
                fix=fix or "", unfixed=unfixed, ids=["A", "B", "C", "D"])
    assert (f.malformed, f.fix_hidden) == (1, 1)
    rep = Report(target="t", findings=[f])
    md = rep.markdown()
    assert "1 advisory(ies): fix version not shown (unusual format)" in md
    assert "2 advisory(ies) without a fix" in md                     # the hidden one is not double-counted
    assert "1 advisory record(s) malformed" in md and "1 advisory record(s) from OSV were malformed" in md
    assert "see notes" not in md
    back = Report.from_dict(json.loads(json.dumps(rep.to_dict())))
    assert (back.findings[0].malformed, back.findings[0].fix_hidden) == (1, 1)


def test_an_older_report_and_other_kinds_count_nothing(feed: dict[str, object]) -> None:
    osv._QUIRKS[("A", "npm", "p", "1.0.0")] = (True, True)
    old = {"target": "t", "findings": [{"kind": "package", "target": "web", "name": "p", "version": "1.0.0",
                                        "ecosystem": "npm", "ids": ["A"], "unfixed": 1}]}
    f = Report.from_dict(old).findings[0]
    assert (f.malformed, f.fix_hidden) == (0, 0)                     # never re-derived from this process
    assert (Finding(kind="host", target="h", name="host", ids=["A"]).malformed) == 0
    assert "without a fix" in Report.from_dict(old).markdown()
