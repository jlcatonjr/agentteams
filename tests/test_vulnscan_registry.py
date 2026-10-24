"""agentteams.vulnscan.registry, coverage and project_cli: origin identity, the operator-only project registry,
effective scan roots, coverage states, and the ``enroll`` / ``roots`` / ``status`` commands.

Repositories are faked as text (``.git/config``, ``.git`` files, ``commondir``): identity never runs git.
No network; config and state live in tmp dirs.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from agentteams.vulnscan import coverage, daily, engine, grant, project_cli, project_policy, registry, review
from agentteams.vulnscan.__main__ import main
from agentteams.vulnscan.report import Finding, Report

ORIGIN = "example.org/acme/widget"


@pytest.fixture
def xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated vulnscan config, state and server-kit dirs; never on CI."""
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AGENTTEAMS_SERVER_KIT_DIR", str(tmp_path / "kit"))
    monkeypatch.delenv("CI", raising=False)
    return tmp_path


def _repo(path: Path, url: str | None = "git@example.org:acme/widget.git", *, workspace: bool = True) -> Path:
    """A main checkout: a ``.git`` directory whose config names ``url`` as origin."""
    (path / ".git").mkdir(parents=True)
    cfg = "[core]\n\tbare = false\n"
    if url is not None:
        cfg += f'[remote "upstream"]\n\turl = https://example.org/other/thing.git\n[remote "origin"]\n\turl = {url}\n'
    (path / ".git" / "config").write_text(cfg)
    if workspace:
        (path / ".github" / "agents").mkdir(parents=True)
    return path


def _worktree(main_repo: Path, path: Path, name: str = "wt1") -> Path:
    """A linked worktree of ``main_repo``: a ``.git`` file → gitdir with a ``commondir`` file."""
    gitdir = main_repo / ".git" / "worktrees" / name
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n")
    path.mkdir(parents=True)
    (path / ".git").write_text(f"gitdir: {gitdir}\n")
    (path / ".github" / "agents").mkdir(parents=True)
    return path


def _entry(ident: str, fix: Path, level: str, checkouts: list[Path] | None = None) -> registry.Entry:
    return registry.Entry(identity=ident, fix_checkout=str(fix), checkouts=[str(c) for c in (checkouts or [fix])],
                          level=level, owner=registry.owner_of(ident), enrolled="2026-01-01T00:00:00+00:00")


# ---------------------------------------------------------------- identity

@pytest.mark.parametrize("url", [
    "git@example.org:acme/widget.git", "git@example.org:acme/widget", "git@Example.org:Acme/Widget.git/",
    "ssh://git@example.org/acme/widget.git", "ssh://git@example.org:22/acme/widget",
    "https://example.org/acme/widget.git", "https://user@example.org/acme/widget/",
    "https://token:x@example.org:443/acme/widget.git", "git://example.org/acme/widget.git",
    "example.org:acme/widget.git",
])
def test_every_origin_form_normalises_to_one_identity(url: str) -> None:
    assert registry.origin_identity(url) == ORIGIN


@pytest.mark.parametrize("url", ["/srv/git/widget.git", "../widget", "file:///srv/git/widget.git", "C:/x/y",
                                 "https://example.org/onlyowner", ""])
def test_local_and_incomplete_urls_are_not_origin_identities(url: str) -> None:
    assert registry.origin_identity(url) is None


def test_nested_group_paths_keep_every_segment() -> None:
    assert registry.origin_identity("https://example.org/grp/sub/proj.git") == "example.org/grp/sub/proj"


def test_clones_and_linked_worktrees_share_an_identity(tmp_path: Path) -> None:
    main_repo = _repo(tmp_path / "widget")
    clone = _repo(tmp_path / "widget-wt-fix", "https://example.org/acme/widget")
    wt = _worktree(main_repo, tmp_path / "widget.worktrees" / "b1")
    assert registry.identity(main_repo) == registry.identity(clone) == registry.identity(wt) == ORIGIN


def test_identity_reads_only_the_origin_section(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "config").write_text('[remote "origin-old"]\n\turl = git@example.org:x/y.git\n'
                                          '[remote "origin"]\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
                                          '\turl = "git@example.org:acme/widget.git"\n')
    assert registry.identity(repo) == ORIGIN


def test_identity_fallbacks(tmp_path: Path) -> None:
    no_origin = _repo(tmp_path / "solo", None)
    assert registry.identity(no_origin) == f"local:{os.path.realpath(no_origin / '.git')}"
    plain = tmp_path / "plain"
    plain.mkdir()
    assert registry.identity(plain) == f"dir:{os.path.realpath(plain)}"
    local = _repo(tmp_path / "loc", "../bare/widget.git")
    assert registry.identity(local) == f"file:{os.path.realpath(tmp_path / 'bare' / 'widget.git')}"
    wt = _worktree(no_origin, tmp_path / "solo-wt")
    assert registry.identity(wt) == registry.identity(no_origin)


def test_identity_never_runs_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    def boom(*a: Any, **k: Any) -> None:
        raise AssertionError("git was run")
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    assert registry.identity(_repo(tmp_path / "w")) == ORIGIN


def test_owner_and_short_name() -> None:
    assert registry.owner_of(ORIGIN) == "example.org/acme"
    assert registry.owner_of("local:/x/y/.git") == "local"
    assert registry.owner_of("dir:/x/y") == "dir"
    assert registry.short_name(ORIGIN) == "widget"
    assert registry.short_name("local:/x/proj/.git") == "proj"
    assert registry.short_name("file:/srv/widget.git") == "widget"


# ---------------------------------------------------------------- registry file

def test_load_missing_is_empty_and_save_round_trips_with_mode_600(xdg: Path) -> None:
    assert registry.load() == []
    entries = [_entry(ORIGIN, xdg / "w", "scan"), _entry("dir:/p", Path("/p"), "unanswered")]
    path = registry.save(entries)
    assert path == registry.registry_path() == xdg / "cfg" / "projects.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert {e.identity for e in registry.load()} == {ORIGIN, "dir:/p"}
    assert not list(path.parent.glob(".projects.json.*"))


@pytest.mark.parametrize(("raw", "fragment"), [
    ("{nope", "not valid JSON"),
    ("[]", "projects"),
    ('{"projects": [1]}', "not an object"),
    ('{"projects": [{"identity": "", "fix_checkout": "/x", "level": "scan", "owner": "", "enrolled": ""}]}',
     "identity"),
    ('{"projects": [{"identity": "a/b/c", "fix_checkout": "/x", "level": "always", "owner": "", "enrolled": ""}]}',
     "level"),
    ('{"projects": [{"identity": "a/b/c", "fix_checkout": "/x", "level": "scan", "owner": "", "enrolled": "",'
     ' "extra": 1}]}', "unknown keys"),
    ('{"projects": [{"identity": "a/b/c", "fix_checkout": "/x", "level": "scan", "owner": "", "enrolled": ""},'
     ' {"identity": "a/b/c", "fix_checkout": "/y", "level": "scan", "owner": "", "enrolled": ""}]}', "more than once"),
])
def test_malformed_registry_is_a_clear_value_error(xdg: Path, raw: str, fragment: str) -> None:
    registry.registry_path().parent.mkdir(parents=True)
    registry.registry_path().write_text(raw)
    with pytest.raises(ValueError, match=fragment):
        registry.load()


@pytest.mark.parametrize("value", ["true", "1", "yes"])
def test_save_refuses_on_ci(xdg: Path, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("CI", value)
    with pytest.raises(PermissionError):
        registry.save([_entry(ORIGIN, xdg, "scan")])
    assert not registry.registry_path().exists()


def test_ci_false_is_not_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "false")
    assert not registry.is_ci()


# ---------------------------------------------------------------- discovery and fix checkout

def test_discover_groups_clones_and_worktrees_by_origin(tmp_path: Path) -> None:
    root = tmp_path / "code"
    main_repo = _repo(root / "widget")
    _repo(root / "widget-wt-old", "https://example.org/acme/widget.git")
    _worktree(main_repo, root / "widget-feature")
    _repo(root / "other", "git@example.org:acme/other.git")
    loose = root / "notes"
    (loose / ".github" / "agents").mkdir(parents=True)
    groups = registry.discover(root)
    real = Path(os.path.realpath(root))
    assert groups[ORIGIN] == sorted([real / "widget", real / "widget-feature", real / "widget-wt-old"])
    assert groups["example.org/acme/other"] == [real / "other"]
    assert groups[f"dir:{real / 'notes'}"] == [real / "notes"]
    assert registry.pick_fix_checkout(groups[ORIGIN]) == real / "widget"


def test_pick_fix_checkout_prefers_a_plain_main_checkout(tmp_path: Path) -> None:
    main_repo = _repo(tmp_path / "a" / "long-named-widget")
    clone = _repo(tmp_path / "w-wt-1")
    wt = _worktree(main_repo, tmp_path / "x")
    in_wt_dir = _repo(tmp_path / "worktrees" / "y")
    assert registry.pick_fix_checkout([clone, wt, in_wt_dir, main_repo]) == main_repo
    assert registry.pick_fix_checkout([clone, wt]) == clone          # worktree-like main beats a linked worktree
    assert registry.pick_fix_checkout([wt]) == wt
    with pytest.raises(ValueError):
        registry.pick_fix_checkout([])


# ---------------------------------------------------------------- effective roots

def test_effective_roots_dedupe_exclude_and_skip_unanswered(tmp_path: Path) -> None:
    code = tmp_path / "code"
    inside = _repo(code / "inner")
    alone = _repo(tmp_path / "alone", "git@example.org:acme/alone.git")
    gone = tmp_path / "skipme" / "x"
    cfg = {**daily.DEFAULT_CONFIG, "repo_roots": [str(code), str(tmp_path / "old")], "exclude": [str(tmp_path / "old"),
                                                                                                  str(tmp_path / "skipme")]}
    entries = [_entry("example.org/acme/inner", inside, "remediate"), _entry("example.org/acme/alone", alone, "scan"),
               _entry("example.org/acme/twin", alone, "scan"), _entry("example.org/acme/no", tmp_path / "n", "off"),
               _entry("example.org/acme/q", tmp_path / "q", "unanswered"), _entry("example.org/acme/g", gone, "scan")]
    roots = {(r.label, r.source) for r in coverage.effective_roots(cfg, entries)}
    assert roots == {(str(code), "repo_roots"), (str(tmp_path / "old"), "excluded"), (str(inside), "duplicate"),
                     (str(alone), "registry (scan)"), (str(alone), "duplicate"), (str(tmp_path / "n"), "opted out"),
                     (str(tmp_path / "q"), "unanswered"), (str(gone), "excluded")}
    scanned = [r.label for r in coverage.effective_roots(cfg, entries) if r.scanned]
    assert scanned == [str(code), str(alone)]


@pytest.mark.parametrize("bad", ["~/x", [1], {"a": 1}])
def test_exclude_must_be_a_list_of_strings(xdg: Path, bad: Any) -> None:
    daily.paths.config_file().parent.mkdir(parents=True)
    daily.paths.config_file().write_text(json.dumps({"exclude": bad}))
    with pytest.raises(ValueError, match="exclude"):
        daily.load_config()


def test_daily_scans_registry_roots_once_and_drops_excluded_findings(xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code = xdg / "code"
    inner = _repo(code / "inner")
    enrolled = _repo(xdg / "enrolled", "git@example.org:acme/enrolled.git")
    registry.save([_entry("example.org/acme/inner", inner, "scan"),
                   _entry("example.org/acme/enrolled", enrolled, "remediate"),
                   _entry("example.org/acme/off", xdg / "off", "off")])
    scanned: list[Path] = []
    passed: list[dict] = []

    def fake_scan(root: Path, **kw: Any) -> Report:
        scanned.append(root)
        passed.append(kw)
        rep = Report(target=str(root))
        if root == code:
            rep.findings = [Finding("package", "inner", "a", "1", fix="", locations=["inner/requirements.txt"]),
                            Finding("package", "inner/vendor", "b", "1", locations=["inner/vendor/requirements.txt"])]
        return rep
    monkeypatch.setattr(engine, "scan_path", fake_scan)
    cfg = {**daily.DEFAULT_CONFIG, "repo_roots": [str(code)], "exclude": [str(inner / "vendor")],
           "local_runtimes": False, "notify": "none"}
    total = Report(target="daily")
    run_dir = xdg / "run"
    run_dir.mkdir()
    daily._collect(cfg, "2026-01-01", run_dir, total, [])
    assert scanned == [code, enrolled]
    assert [f.name for f in total.findings] == ["a"]
    assert (run_dir / f"repos-{daily.plan_slug(enrolled.resolve())}.json").exists()
    # S6: the operator's exclusions and off enrolments go into the scan itself (never read), not only dropped after
    assert passed[0]["exclude"] == [Path(os.path.realpath(inner / "vendor"))]
    assert Path(os.path.realpath(xdg / "off")) in passed[0]["opted_out"]


def test_daily_with_an_unreadable_registry_scans_explicit_roots_and_says_so(xdg: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    registry.registry_path().parent.mkdir(parents=True)
    registry.registry_path().write_text("{broken")
    monkeypatch.setattr(engine, "scan_path", lambda root, **k: Report(target=str(root)))
    total = Report(target="daily")
    (xdg / "run").mkdir()
    daily._collect({**daily.DEFAULT_CONFIG, "local_runtimes": False}, "2026-01-01", xdg / "run", total, [])
    assert any("project registry unreadable" in e for e in total.feed_errors)


# ---------------------------------------------------------------- coverage

def test_every_workspace_gets_exactly_one_coverage_state(xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def repo(name: str) -> Path:
        return _repo(xdg / name, f"git@example.org:acme/{name}.git")
    fixable, nogrant, scan_only, off, unans, excl, dup_main = (repo(n) for n in
                                                               ("fx", "ng", "so", "of", "un", "ex", "dm"))
    dup_clone = repo("dm-wt-2")
    plain = xdg / "plain"
    plain.mkdir()
    entries = [_entry("example.org/acme/fx", fixable, "remediate"), _entry("example.org/acme/ng", nogrant, "remediate"),
               _entry("example.org/acme/so", scan_only, "scan"), _entry("example.org/acme/of", off, "off"),
               _entry("example.org/acme/un", unans, "unanswered"), _entry("example.org/acme/ex", excl, "scan"),
               _entry("example.org/acme/dm", dup_main, "scan", [dup_main, dup_clone]),
               _entry("example.org/acme/gone", xdg / "gone", "scan"), _entry(f"dir:{plain}", plain, "scan")]
    grants = [{"ops": ["stage-fix"], "repo_roots": [str(fixable)], "hosts": []}]
    rows = coverage.states({**daily.DEFAULT_CONFIG, "exclude": [str(excl)]}, entries, grants)
    got = {name: state for _o, state, name, _p in rows}
    assert got == {"fx": "fixable", "ng": "not covered by the grant yet", "so": "scan-only", "of": "opted out",
                   "un": "unanswered", "ex": "excluded", "dm": "scan-only", "dm (dm-wt-2)": "duplicate checkout",
                   "gone": "missing on disk", "plain": "not a git repository"}
    md = coverage.markdown(rows)
    assert "### example.org/acme" in md and "fixable: 1" in md and "- unanswered (1): un" in md
    assert "enroll --discover" in md
    counts_only = coverage.markdown(rows, names=False)
    assert "### " not in counts_only and "fx" not in counts_only


def test_summary_and_review_carry_the_coverage_section(xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = _repo(xdg / "w")
    registry.save([_entry(ORIGIN, w, "scan")])
    monkeypatch.setattr(daily, "_collect", lambda *a, **k: None)
    _rep, _code, run_dir = daily.run_daily({**daily.DEFAULT_CONFIG, "local_runtimes": False, "notify": "none"},
                                           today="2026-01-02")
    assert "## Coverage" in (run_dir / "summary.md").read_text() and "scan-only (1): widget" in \
        (run_dir / "summary.md").read_text()
    assert "scan-only (1): widget" in review.build(since="2026-01-01")
    published = review.build(since="2026-01-01", for_publish=True)
    assert "scan-only: 1" in published and "widget" not in published.split("## Coverage")[1].split("##")[0]


# ---------------------------------------------------------------- enroll

def _args(**kw: Any) -> argparse.Namespace:
    return argparse.Namespace(dirs=kw.get("dirs", []), discover=kw.get("discover", []), level=kw.get("level"))


def _answers(*seq: str):
    it = iter(seq)
    return lambda _prompt: next(it)


def test_enroll_refuses_without_a_terminal_and_on_ci(xdg: Path, monkeypatch: pytest.MonkeyPatch,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    assert project_cli.cmd_enroll(_args(dirs=[xdg]), interactive=lambda: False) == 2
    monkeypatch.setenv("CI", "1")
    assert project_cli.cmd_enroll(_args(dirs=[xdg]), interactive=lambda: True) == 2
    assert "operator command" in capsys.readouterr().err
    assert not registry.registry_path().exists()


def test_main_enroll_refuses_when_stdin_is_not_a_terminal(xdg: Path) -> None:
    assert main(["enroll", str(xdg)]) == 2


def _policy_stub(monkeypatch: pytest.MonkeyPatch, levels: dict[Path, str | None]) -> list[tuple[Path, str]]:
    written: list[tuple[Path, str]] = []

    def read_policy(repo: Path) -> project_policy.ProjectPolicy:
        return project_policy.ProjectPolicy(level=levels.get(Path(repo)), brief_path=Path(repo) / "brief.json")

    def set_level(repo: Path, level: str, *, confirm: Any) -> bool:
        if not confirm(f"--- a/brief.json\n+++ b/brief.json\n+ level {level}"):
            return False
        levels[Path(repo)] = level
        written.append((Path(repo), level))
        return True
    monkeypatch.setattr(project_policy, "read_policy", read_policy)
    monkeypatch.setattr(project_policy, "set_level", set_level)
    return written


def test_enroll_groups_by_owner_sets_levels_and_is_idempotent(xdg: Path, monkeypatch: pytest.MonkeyPatch,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    root = xdg / "code"
    a = _repo(root / "alpha", "git@example.org:acme/alpha.git")
    a_clone = _repo(root / "alpha-wt-x", "https://example.org/acme/alpha")
    b = _repo(root / "beta", "git@example.org:acme/beta.git")
    c = _repo(root / "gamma", "git@example.org:acme/gamma.git")
    o = _repo(root / "other", "git@example.org:else/other.git")
    real = Path(os.path.realpath(root))
    levels: dict[Path, str | None] = {real / "alpha": "remediate", real / "beta": None, real / "gamma": None}
    written = _policy_stub(monkeypatch, levels)
    # owner example.org/acme: set a level for unanswered ones (scan), first diff "a" = all; owner else: skip
    rc = project_cli.cmd_enroll(_args(discover=[root]), ask=_answers("s", "scan", "a", "k"), interactive=lambda: True)
    assert rc == 0
    assert sorted(written) == [(real / "beta", "scan"), (real / "gamma", "scan")]
    got = {e.identity: e for e in registry.load()}
    assert got["example.org/acme/alpha"].level == "remediate"
    assert got["example.org/acme/alpha"].fix_checkout == str(real / "alpha")
    assert set(got["example.org/acme/alpha"].checkouts) == {str(real / "alpha"), str(real / "alpha-wt-x")}
    assert got["example.org/acme/beta"].level == got["example.org/acme/gamma"].level == "scan"
    assert got["example.org/else/other"].level == "unanswered"           # skipped, but recorded as discovered
    first = got["example.org/acme/alpha"].enrolled
    out = capsys.readouterr().out
    assert "example.org/acme (3 project(s))" in out and "registry" in out
    # re-run: enrol at brief levels; the first enrolment date is kept; choose the clone as fix checkout
    rc = project_cli.cmd_enroll(_args(discover=[root]), ask=_answers("c", "2", "e", "e"), interactive=lambda: True)
    assert rc == 0
    again = {e.identity: e for e in registry.load()}
    assert again["example.org/acme/alpha"].enrolled == first
    assert again["example.org/acme/alpha"].fix_checkout == str(real / "alpha-wt-x")
    assert len(again) == 4
    del a, a_clone, b, c, o


def test_enroll_with_level_flag_and_a_declined_diff_stays_unanswered(xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = _repo(xdg / "w")
    _policy_stub(monkeypatch, {})
    rc = project_cli.cmd_enroll(_args(dirs=[w], level="scan"), ask=_answers("s", "n"), interactive=lambda: True)
    assert rc == 0
    assert [e.level for e in registry.load()] == ["unanswered"]


def test_enroll_refuses_a_malformed_registry(xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry.registry_path().parent.mkdir(parents=True)
    registry.registry_path().write_text("[")
    assert project_cli.cmd_enroll(_args(dirs=[xdg]), interactive=lambda: True) == 2


# ---------------------------------------------------------------- roots and status

def test_roots_command_lists_sources(xdg: Path, capsys: pytest.CaptureFixture[str]) -> None:
    w = _repo(xdg / "w")
    registry.save([_entry(ORIGIN, w, "scan"), _entry("example.org/acme/x", xdg / "x", "off")])
    assert main(["roots"]) == 0
    out = capsys.readouterr().out
    assert f"SCAN  {w}  [registry (scan)]" in out and "[opted out]" in out and "1 of 2 roots scanned" in out


def test_status_is_read_only_and_reports_findings_fixes_and_grant(xdg: Path, monkeypatch: pytest.MonkeyPatch,
                                                                 capsys: pytest.CaptureFixture[str]) -> None:
    w = _repo(xdg / "w")
    wt = _worktree(w, xdg / "w.worktrees" / "f")
    registry.save([_entry(ORIGIN, w, "remediate", [w, wt])])
    run = xdg / "state" / "2026-01-03"
    run.mkdir(parents=True)
    rep = Report(target="daily", findings=[
        Finding("package", f"{w}/sub", "a", "1", severity=4), Finding("package", str(w), "b", "1", severity=3),
        Finding("package", "relative/target", "c", "1", severity=4), Finding("package", str(xdg / "elsewhere"), "d", "1")])
    (run / "summary.json").write_text(json.dumps(rep.to_dict()))
    rows = [{"kind": "done", "op": "open-pr", "target": str(w), "ts": "2026-01-03T00:00:00",
             "detail": json.dumps({"branch": "vulnscan/2026-01-03-abc", "pr": "https://example.org/pr/1"})},
            {"kind": "done", "op": "stage-fix", "target": str(xdg / "elsewhere"), "ts": "t", "detail": "{}"}]
    from agentteams.vulnscan import ledger
    monkeypatch.setattr(ledger, "read", lambda verify=True: rows)
    monkeypatch.setattr(grant, "load_active", lambda: ([{"ops": ["stage-fix"], "repo_roots": [str(w)], "hosts": []}], []))
    before = sorted(p for p in xdg.rglob("*"))
    assert main(["status", "--project", str(wt), "--json"]) == 0
    st = json.loads(capsys.readouterr().out)
    assert sorted(p for p in xdg.rglob("*")) == before
    assert st["identity"] == ORIGIN and st["level"] == "remediate" and st["fix_checkout"] == str(w)
    assert st["open_findings"] == {"CRITICAL": 1, "HIGH": 1}
    assert st["recorded_fixes"] == [{"ts": "2026-01-03T00:00:00", "op": "open-pr", "branch": "vulnscan/2026-01-03-abc",
                                     "pr": "https://example.org/pr/1"}]
    assert st["grant_covers_fix_checkout"] is True
    assert main(["status", "--project", str(w)]) == 0
    assert "covers the fix checkout" in capsys.readouterr().out


def test_status_of_an_unenrolled_project(xdg: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = xdg / "p"
    p.mkdir()
    assert main(["status", "--project", str(p), "--json"]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["level"] == "not enrolled" and st["identity"].startswith("dir:") and st["last_scan"] is None
    assert main(["status", "--project", str(xdg / "missing")]) == 2


# ---------------------------------------------------------------- review findings (S1, S3, S5, C1, C4, C7)

def test_load_refuses_a_registry_others_can_write_or_a_symlink(xdg: Path) -> None:
    path = registry.save([_entry(ORIGIN, _repo(xdg / "w"), "scan")])
    os.chmod(path, 0o666)
    with pytest.raises(ValueError, match="group/world-writable"):
        registry.load()
    os.chmod(path, 0o600)
    moved = path.with_name("real.json")
    path.rename(moved)
    path.symlink_to(moved)
    with pytest.raises(ValueError, match="not a regular file"):
        registry.load()


def test_identity_refuses_special_or_oversized_repository_files(tmp_path: Path) -> None:
    fifo_repo = tmp_path / "fifo"
    (fifo_repo / ".git").mkdir(parents=True)
    os.mkfifo(fifo_repo / ".git" / "config")             # a FIFO would block a plain read forever
    ident, problem = registry.checked_identity(fifo_repo)
    assert ident == f"dir:{os.path.realpath(fifo_repo)}" and "not a regular file" in problem
    assert registry.identity(fifo_repo) == ident
    big = tmp_path / "big"
    big.mkdir()
    (big / ".git").write_text("gitdir: " + "x" * (registry.MAX_GIT_FILE + 1))
    assert "larger than" in registry.checked_identity(big)[1]
    main_repo = _repo(tmp_path / "main")
    wt = _worktree(main_repo, tmp_path / "wt")
    common = main_repo / ".git" / "worktrees" / "wt1" / "commondir"
    common.unlink()
    common.symlink_to(main_repo / ".git" / "config")      # a symlinked commondir is not followed
    assert "cannot be opened" in registry.checked_identity(wt)[1]
    assert registry.checked_identity(main_repo) == (ORIGIN, "")


def test_daily_prefixes_brief_lists_with_the_root_and_drops_operator_exclusions(xdg: Path,
                                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    code = xdg / "code"
    inner = _repo(code / "inner")
    (inner / "vendor").mkdir()

    def fake_scan(root: Path, **_kw: Any) -> Report:
        rep = Report(target=str(root))
        rep.opted_out, rep.brief_off = ["inner/vendor"], ["inner", "."]
        rep.excluded = ["inner: examples/requirements.txt", "inner/vendor: requirements.txt"]
        rep.withheld = ["inner: acme-core (PyPI)", "inner/vendor: acme-x (PyPI)"]
        return rep
    monkeypatch.setattr(engine, "scan_path", fake_scan)
    cfg = {**daily.DEFAULT_CONFIG, "repo_roots": [str(code)], "exclude": [str(inner / "vendor")],
           "local_runtimes": False, "notify": "none"}
    total = Report(target="daily")
    (xdg / "run").mkdir()
    daily._collect(cfg, "2026-01-01", xdg / "run", total, [])
    assert total.opted_out == []                                            # inside the operator's exclude
    assert total.brief_off == [f"{code}/inner", str(code)]
    assert total.excluded == [f"{code}/inner: examples/requirements.txt"]
    assert total.withheld == [f"{code}/inner: acme-core (PyPI)"]


def _brief_off(repo: Path) -> None:
    (repo / ".agentteams").mkdir(parents=True, exist_ok=True)
    (repo / ".agentteams" / "brief.json").write_text(json.dumps({project_policy.POLICY_KEY: {"level": "off"}}))


def test_coverage_reports_brief_off_mismatch_and_per_repo_counts_without_names(xdg: Path) -> None:
    w = _repo(xdg / "w")
    _brief_off(w)
    registry.save([_entry(ORIGIN, w, "scan")])
    rep = Report(target="daily", excluded=[f"{w}: examples/requirements.txt"],
                 withheld=[f"{w}: acme-core (PyPI)", f"{w}: acme-ui (npm)"])
    md = coverage.section(dict(daily.DEFAULT_CONFIG), report=rep)
    assert f"`{w}`: {coverage.MISMATCH_OFF}" in md
    assert f"`{w}`: 1 manifest(s) excluded, 2 private package(s) withheld" in md
    assert "acme-core" not in md and "acme-ui" not in md and "\n\n### Project briefs\n\n" in md
    published = coverage.section(dict(daily.DEFAULT_CONFIG), names=False, report=rep)
    assert f"{coverage.MISMATCH_OFF}: 1" in published and "private packages withheld: 2" in published
    assert str(w) not in published


def test_registry_off_skips_but_brief_off_is_scanned_by_daily(xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentteams.vulnscan import _http, osv

    sent: list[str] = []

    def fetch(url: str, payload: dict | None = None, **_: Any) -> dict:
        sent.extend(q["package"]["name"] for q in payload["queries"])
        return {"results": [{} for _ in payload["queries"]]}
    monkeypatch.setattr(_http, "fetch_json", fetch)
    osv._DETAILS.clear()
    code = xdg / "code"
    brief_off = _repo(code / "briefoff", "git@example.org:acme/briefoff.git")
    _brief_off(brief_off)
    (brief_off / "requirements.txt").write_text("lib-a==1.0\n")
    op_off = _repo(code / "opoff", "git@example.org:acme/opoff.git")
    (op_off / "requirements.txt").write_text("lib-b==1.0\n")
    registry.save([_entry("example.org/acme/opoff", op_off, "off")])
    cfg = {**daily.DEFAULT_CONFIG, "repo_roots": [str(code)], "local_runtimes": False, "notify": "none"}
    total = Report(target="daily")
    (xdg / "run").mkdir()
    daily._collect(cfg, "2026-01-01", xdg / "run", total, [])
    assert sent == ["lib-a"]
    assert total.brief_off == [f"{code}/briefoff"] and total.opted_out == [f"{code}/opoff"]


def test_summary_and_mcp_never_name_private_packages(xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentteams.vulnscan import mcp

    def collect(cfg: dict, today: str, run_dir: Path, total: Report, staged: list) -> None:
        rep = Report(target="/r", withheld=["/r/app: acme-secret-lib (PyPI)"],
                     findings=[Finding("package", "/r/app", "requests", "1", severity=3)])
        (run_dir / "repos-r.json").write_text(json.dumps(rep.to_dict()))   # machine-local copy keeps the name
        total.merge(rep)
    monkeypatch.setattr(daily, "_collect", collect)
    _rep, _code, run_dir = daily.run_daily({**daily.DEFAULT_CONFIG, "local_runtimes": False, "notify": "none"},
                                           today="2026-01-04")
    assert "acme-secret-lib" in (run_dir / "repos-r.json").read_text()
    for name in ("summary.md", "summary.json"):
        assert "acme-secret-lib" not in (run_dir / name).read_text(), name
    assert "`/r/app`: 1 package(s)" in (run_dir / "summary.md").read_text()
    server = mcp.Server(run_dir, xdg / "spool", xdg / "results")
    served = json.dumps([server.call("get_summary", {}), server.call("list_findings", {}),
                         server.call("get_fix_plans", {})])
    assert "acme-secret-lib" not in served and "repos-r.json" not in served


def test_status_reports_coverage_state_and_a_partial_last_scan(xdg: Path, monkeypatch: pytest.MonkeyPatch,
                                                               capsys: pytest.CaptureFixture[str]) -> None:
    w = _repo(xdg / "w")
    registry.save([_entry(ORIGIN, w, "remediate")])
    run = xdg / "state" / "2026-01-05"
    run.mkdir(parents=True)
    (run / "summary.json").write_text(json.dumps(Report(target="daily", feed_errors=["OSV down"]).to_dict()))
    monkeypatch.setattr(grant, "load_active", lambda: ([], []))
    assert main(["status", "--project", str(w), "--json"]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["coverage_state"] == "not covered by the grant yet" and st["coverage_state"] in coverage.STATES
    assert st["last_scan_partial"] is True and st["last_scan_unscanned"] == 1
    assert main(["status", "--project", str(w)]) == 0
    out = capsys.readouterr().out
    assert "coverage     not covered by the grant yet" in out and "PARTIAL: 1 part(s) unscanned" in out
    other = xdg / "other"
    other.mkdir()
    assert main(["status", "--project", str(other), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["coverage_state"] == "not enrolled"


def test_status_flags_a_brief_off_mismatch(xdg: Path, monkeypatch: pytest.MonkeyPatch,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    w = _repo(xdg / "w")
    _brief_off(w)
    registry.save([_entry(ORIGIN, w, "scan")])
    monkeypatch.setattr(grant, "load_active", lambda: ([], []))
    assert main(["status", "--project", str(w), "--json"]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["brief_level"] == "off" and f"mismatch: {coverage.MISMATCH_OFF}" in st["problems"]


def test_enroll_skips_a_brief_with_an_invalid_block_and_says_why(xdg: Path, monkeypatch: pytest.MonkeyPatch,
                                                                 capsys: pytest.CaptureFixture[str]) -> None:
    bad = _repo(xdg / "bad", "git@example.org:acme/bad.git")
    good = _repo(xdg / "good", "git@example.org:acme/good.git")
    (bad / ".agentteams").mkdir()
    (bad / ".agentteams" / "brief.json").write_text(json.dumps({project_policy.POLICY_KEY: {"level": "everything"}}))
    calls: list[Path] = []
    monkeypatch.setattr(project_policy, "set_level", lambda repo, level, **k: calls.append(Path(repo)) or False)
    rc = project_cli.cmd_enroll(_args(dirs=[bad, good], level="scan"), ask=_answers("s", "n"),
                                interactive=lambda: True)
    assert rc == 0
    assert calls == [Path(os.path.realpath(good))]                    # the invalid brief is never answered over
    out = capsys.readouterr().out
    assert "skipped example.org/acme/bad: fix its brief first (level must be one of" in out
    assert [e.identity for e in registry.load()] == ["example.org/acme/good"]
