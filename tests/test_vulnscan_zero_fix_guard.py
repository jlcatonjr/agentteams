"""stage-fix and a change that resolves no advisory (2026-10-28).

npm audit fix moved data-quality-monitor's qs 6.14.2 -> 6.14.3, inside express's ``~6.14.0`` range. All four qs
advisories need 6.16.1, so the change fixed nothing, yet it was staged, approved by the repository's orchestrator
and opened as PR #19. ``fix_path`` now counts the advisory occurrences an applied change removes
(:attr:`fix.FixResult.resolved`) and ``stage-fix`` refuses a change that removes none, as "nothing fixable".
Network-free: OSV and npm are stubbed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
from agentteams.vulnscan import _http, executor, fix, ledger, osv, retry_guard  # noqa: E402
from tests.test_vulnscan_executor import (  # noqa: E402
    GIT,
    _assert_refused_cleanly,
    _install,
    _repo_with_remote,
    env,  # noqa: F401  (the shared fixture)
)

QS_IDS = ["GHSA-4mjr-xmp4-gh2g", "GHSA-6pq8-jhm2-2vcw", "GHSA-q8mj-m7cp-5q26", "GHSA-x5fp-wj9c-mxmx"]


def _lock(**versions: str) -> str:
    """A lockfileVersion 3 package-lock.json with one registry entry per ``node_modules`` path."""
    packages: dict = {"": {"name": "app", "dependencies": {"express": "^4"}}}
    for key, version in versions.items():
        name = key.split("__")[-1]
        packages[f"node_modules/{key.replace('__', '/node_modules/')}"] = {
            "version": version, "resolved": f"https://registry.npmjs.org/{name}/-/{name}-{version}.tgz"}
    return json.dumps({"name": "app", "lockfileVersion": 3, "packages": packages}, indent=2) + "\n"


def _osv(monkeypatch: pytest.MonkeyPatch, seen: list | None = None) -> None:
    """qs below 6.16.1 is affected by all four advisories, ms 2.0.0 by one; nothing else is."""
    def vulnerable_ids(items: object) -> dict:
        items = sorted(set(items))   # type: ignore[arg-type]
        if seen is not None:
            seen.extend(items)
        hits = {k: QS_IDS for k in items if k[1] == "qs" and tuple(map(int, k[2].split("."))) < (6, 16, 1)}
        return hits | {k: ["GHSA-ms"] for k in items if k[1:] == ("ms", "2.0.0")}
    monkeypatch.setattr(osv, "vulnerable_ids", vulnerable_ids)


def _npm_writes(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    def npm_fix(directory: Path, **kw: object) -> str:
        (directory / "package-lock.json").write_text(text)
        return "changed 1 package"
    monkeypatch.setattr(fix, "npm_fix", npm_fix)


def _tree(tmp: Path, lock: str) -> Path:
    root = tmp / "tree"
    root.mkdir()
    (root / "package.json").write_text('{"name": "app", "dependencies": {"express": "^4"}}\n')
    (root / "package-lock.json").write_text(lock)
    return root


# ---------------------------------------------------------------- the measure (fix_path)

def test_a_bump_inside_the_affected_range_resolves_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _osv(monkeypatch)
    root = _tree(tmp_path, _lock(qs="6.14.2", express__qs="6.15.1"))
    _npm_writes(monkeypatch, _lock(qs="6.14.3", express__qs="6.15.1"))
    res = fix.fix_path(root, apply=True, allow_dirty=True)
    assert res.resolved == 0 and not res.feed_errors


def test_each_installed_copy_fixed_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _osv(monkeypatch)
    root = _tree(tmp_path, _lock(qs="6.14.2", express__qs="6.15.1"))
    _npm_writes(monkeypatch, _lock(qs="6.14.2", express__qs="6.16.1"))   # one of two copies fixed
    assert fix.fix_path(root, apply=True, allow_dirty=True).resolved == len(QS_IDS)
    (root / "package-lock.json").write_text(_lock(qs="6.14.2", express__qs="6.15.1"))
    _npm_writes(monkeypatch, _lock(qs="6.16.1", express__qs="6.16.1"))
    assert fix.fix_path(root, apply=True, allow_dirty=True).resolved == 2 * len(QS_IDS)


def test_a_new_advisory_elsewhere_does_not_offset_a_fix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _osv(monkeypatch)
    root = _tree(tmp_path, _lock(qs="6.14.2", ms="2.1.3"))
    _npm_writes(monkeypatch, _lock(qs="6.16.1", ms="2.0.0"))   # qs fixed, ms moved onto an affected release
    assert fix.fix_path(root, apply=True, allow_dirty=True).resolved == len(QS_IDS)


def test_unchanged_dry_run_and_unreadable_are_not_measured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list = []
    _osv(monkeypatch, seen)
    root = _tree(tmp_path, _lock(qs="6.14.2"))
    monkeypatch.setattr(fix, "npm_fix", lambda directory, **kw: "up to date")
    res = fix.fix_path(root, apply=True, allow_dirty=True)
    assert res.resolved == 0 and seen == []                    # nothing changed: OSV is not asked
    assert fix.fix_path(root, apply=False, npm=False).resolved is None
    _npm_writes(monkeypatch, "{not json")
    assert fix.fix_path(root, apply=True, allow_dirty=True).resolved is None   # unknown, so never refused


def test_private_packages_are_never_looked_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list = []
    _osv(monkeypatch, seen)
    _npm_writes(monkeypatch, "")
    path = tmp_path / "package-lock.json"
    path.write_text(_lock(qs="6.14.2", **{"@corp/secret": "1.0.0"}))
    counts = fix.advisory_counts(path, ("@corp/*",))
    assert counts is not None and sum(counts.values()) == len(QS_IDS)
    assert {n for _, n, _ in seen} == {"qs"}             # @corp/secret never left the machine


def test_a_feed_error_is_recorded_not_guessed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def down(items: object) -> dict:
        raise _http.FeedError("OSV unreachable")
    monkeypatch.setattr(osv, "vulnerable_ids", down)
    root = _tree(tmp_path, _lock(qs="6.14.2"))
    _npm_writes(monkeypatch, _lock(qs="6.14.3"))
    res = fix.fix_path(root, apply=True, allow_dirty=True)
    assert res.feed_errors and "OSV unreachable" in res.feed_errors[0]


# ---------------------------------------------------------------- the refusal (stage-fix, real git)

def _npm_repo(tmp: Path) -> Path:
    return _repo_with_remote(tmp, {"package.json": '{"name": "app", "dependencies": {"express": "^4"}}\n',
                                   "package-lock.json": _lock(qs="6.14.2")})


@pytest.mark.skipif(not GIT, reason="git not available")
def test_stage_fix_refuses_a_change_that_resolves_nothing(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _npm_repo(env["tmp"])
    _install(env, ops=["stage-fix"], hosts=[])
    _osv(monkeypatch)
    _npm_writes(monkeypatch, _lock(qs="6.14.3"))
    res = executor.act("stage-fix", str(repo), env["cfg"], request_id="1" * 32)
    assert res["status"] == "failed"
    assert res["detail"].startswith(retry_guard.FUTILE_DETAIL) and "resolves no advisory" in res["detail"]
    _assert_refused_cleanly(repo, "1" * 32)
    assert retry_guard._last_futile(str(repo.resolve()), ledger.read()) is not None   # held until the plan changes


@pytest.mark.skipif(not GIT, reason="git not available")
def test_stage_fix_stages_a_change_that_resolves_an_advisory(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _npm_repo(env["tmp"])
    _install(env, ops=["stage-fix"], hosts=[])
    _osv(monkeypatch)
    _npm_writes(monkeypatch, _lock(qs="6.16.1"))
    res = executor.act("stage-fix", str(repo), env["cfg"])
    assert res["status"] == "done" and res["changed"] == ["package-lock.json"]
    staged = json.loads(executor.staged_file(repo.resolve()).read_text())
    assert staged["resolved"] == len(QS_IDS)


# ---------------------------------------------------------------- already fixed upstream (2026-10-30)
# sc_collectors: its default branch already pins the fixed release while the scanned local checkout is behind,
# so stage-fix (which works on the default branch) has nothing to change. That is not "only major fixes".


@pytest.mark.skipif(not GIT, reason="git not available")
def test_stage_fix_reports_a_fix_already_on_the_default_branch(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"], {"package.json": '{"name": "app", "dependencies": {"express": "^4"}}\n',
                                          "package-lock.json": _lock(qs="6.16.1")})
    _install(env, ops=["stage-fix"], hosts=[])
    _osv(monkeypatch)
    _npm_writes(monkeypatch, _lock(qs="6.16.1"))   # npm changes nothing: nothing is vulnerable
    res = executor.act("stage-fix", str(repo), env["cfg"], request_id="2" * 32)
    assert res["status"] == "failed"
    assert res["detail"].startswith(retry_guard.UPSTREAM_DETAIL) and "update the local checkout" in res["detail"]
    assert retry_guard.FUTILE_DETAIL not in res["detail"]
    _assert_refused_cleanly(repo, "2" * 32)
    assert retry_guard._last_futile(str(repo.resolve()), ledger.read()) is not None   # held like a futile result


@pytest.mark.skipif(not GIT, reason="git not available")
def test_stage_fix_with_only_major_fixes_still_reports_a_major_jump(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _npm_repo(env["tmp"])   # qs 6.14.2 is vulnerable at the base
    _install(env, ops=["stage-fix"], hosts=[])
    _osv(monkeypatch)
    _npm_writes(monkeypatch, _lock(qs="6.14.2"))   # npm cannot move it (a major jump)
    res = executor.act("stage-fix", str(repo), env["cfg"], request_id="3" * 32)
    assert res["status"] == "failed" and res["detail"] == retry_guard.FUTILE_DETAIL


def test_a_scan_that_checks_nothing_is_not_nothing_vulnerable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _osv(monkeypatch)
    assert not executor._nothing_vulnerable(tmp_path)             # no manifest: unknown, never "fixed upstream"
    def down(items: object) -> dict:
        raise _http.FeedError("OSV unreachable")
    monkeypatch.setattr(osv, "vulnerable_ids", down)
    assert not executor._nothing_vulnerable(_tree(tmp_path, _lock(qs="6.16.1")))   # feed down: unknown


def test_the_held_reason_names_the_upstream_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [{"op": "stage-fix", "target": "/r", "kind": "failed", "ts": "2026-10-30T09:30:00+00:00",
             "detail": f"{retry_guard.UPSTREAM_DETAIL} (origin/main): nothing vulnerable there; update the local checkout"}]
    monkeypatch.setattr(retry_guard, "plan_digest", lambda repo, day=None: "same")
    why = retry_guard.futile_reason("stage-fix", "/r", rows)
    assert why.startswith(retry_guard.HELD_PREFIX) and retry_guard.UPSTREAM_DETAIL in why
    assert "update the local checkout" in why and "major" not in why
