"""stage-fix after ``npm audit fix``: exact-peer groups and direct parents move together (2026-11-01).

data-quality-monitor's vitest and ``@vitest/mocker`` had a fix inside ``^4.1.x``, but ``@vitest/coverage-v8`` pins
its peer vitest to its own exact version (and vitest pins it back), so ``npm audit fix`` found nothing to do; its
``qs`` (and traffic-file-enrichment-app's) needed ``express 4.22.3``, inside the declared ``^4.19.2``, because
``express 4.22.1`` requires ``qs ~6.14.0``. ``npm_ops.npm_moves`` now updates the group or the parent lockfile-only.
Network-free: OSV and npm are stubbed (one test runs ``npm_fix`` against a fake npm binary).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from agentteams.vulnscan import fix, manifests, npm_groups, npm_ops, osv

MOCKER_IDS = ["GHSA-mock-1", "GHSA-mock-2"]
VITEST_IDS = ["GHSA-vite-1"]
QS_IDS = ["GHSA-qs-1", "GHSA-qs-2"]
_FIXED = {"@vitest/mocker": "4.1.12", "vitest": "4.1.11", "qs": "6.16.1", "minimatch": "11.0.1"}
_IDS = {"@vitest/mocker": MOCKER_IDS, "vitest": VITEST_IDS, "qs": QS_IDS, "minimatch": ["GHSA-mm"]}


def _v(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in text.split("."))


def _entry(name: str, version: str, **extra: object) -> dict:
    return {"version": version, "resolved": f"https://registry.npmjs.org/{name}/-/{name.split('/')[-1]}-{version}.tgz",
            **extra}


def _vitest_lock(v: str = "4.1.5", *, peer_spec: str | None = None, minimatch: str = "10.2.5") -> str:
    """The frontend shape: vitest and @vitest/coverage-v8 pin each other exactly; vitest pins @vitest/mocker."""
    spec = peer_spec or v
    return json.dumps({"name": "frontend", "lockfileVersion": 3, "packages": {
        "": {"name": "frontend", "devDependencies": {"@vitest/coverage-v8": "^4.1.5", "vitest": "^4.1.5",
                                                     "eslint": "^10.2.1"}},
        "node_modules/vitest": _entry("vitest", v, dev=True, dependencies={"@vitest/mocker": v},
                                      peerDependencies={"@vitest/coverage-v8": spec, "@vitest/ui": spec,
                                                        "jsdom": "*"}),
        "node_modules/@vitest/coverage-v8": _entry("@vitest/coverage-v8", v, dev=True,
                                                   peerDependencies={"vitest": spec, "@vitest/browser": spec}),
        "node_modules/@vitest/mocker": _entry("@vitest/mocker", v, dev=True, peerDependencies={"vite": "^8.0.0"}),
        "node_modules/eslint": _entry("eslint", "10.2.1", dev=True, dependencies={"minimatch": "^10.2.4"}),
        "node_modules/minimatch": _entry("minimatch", minimatch, dev=True),
    }}, indent=2) + "\n"


def _express_lock(express: str = "4.22.1", qs: str = "6.14.3", *, qs_range: str = "~6.14.0") -> str:
    """The backend shape: express pins qs ~6.14.0; body-parser holds its own nested, already fixed qs."""
    return json.dumps({"name": "backend", "lockfileVersion": 3, "packages": {
        "": {"name": "backend", "dependencies": {"express": "^4.19.2"}},
        "node_modules/express": _entry("express", express, dependencies={"qs": qs_range, "body-parser": "~1.20.3"}),
        "node_modules/body-parser": _entry("body-parser", "1.20.9", dependencies={"qs": "~6.16.0"}),
        "node_modules/body-parser/node_modules/qs": _entry("qs", "6.16.2"),
        "node_modules/qs": _entry("qs", qs),
    }}, indent=2) + "\n"


def _osv(monkeypatch: pytest.MonkeyPatch, seen: list | None = None) -> None:
    def vulnerable_ids(items: object) -> dict:
        items = sorted(set(items))   # type: ignore[arg-type]
        if seen is not None:
            seen.extend(items)
        return {k: _IDS[k[1]] for k in items if k[1] in _FIXED and _v(k[2]) < _v(_FIXED[k[1]])}
    monkeypatch.setattr(osv, "vulnerable_ids", vulnerable_ids)
    monkeypatch.setattr(osv, "minimal_fix", lambda ids, eco, name, ver: (_FIXED[name], 0))
    monkeypatch.setattr(fix, "_npm_available", lambda: True)


def _npm(monkeypatch: pytest.MonkeyPatch, results: dict[tuple[str, ...], str], calls: list) -> None:
    """Stub npm: the audit fix changes nothing; ``npm update <names>`` writes ``results[names]`` when present."""
    def npm_fix(directory: Path, **kw: object) -> str:
        calls.append(kw)
        update = tuple(kw.get("update") or ())   # type: ignore[arg-type]
        if update in results:
            (directory / "package-lock.json").write_text(results[update])
            return "changed 3 packages"
        return "up to date, audited 1 package"
    monkeypatch.setattr(fix, "npm_fix", npm_fix)
    _confined(monkeypatch)


def _confined(monkeypatch: pytest.MonkeyPatch, on: bool = True) -> None:
    """Pretend the tools container is (or is not) active: npm_moves runs nothing outside it."""
    monkeypatch.setattr(npm_ops, "_container_active", lambda: on)


def _tree(tmp: Path, pkg: dict, lock: str) -> Path:
    root = tmp / "tree"
    root.mkdir()
    (root / "package.json").write_text(json.dumps(pkg, indent=2) + "\n")
    (root / "package-lock.json").write_text(lock)
    return root


_FRONT_PKG = {"name": "frontend", "devDependencies": {"@vitest/coverage-v8": "^4.1.5", "vitest": "^4.1.5",
                                                      "eslint": "^10.2.1"}}
_BACK_PKG = {"name": "backend", "dependencies": {"express": "^4.19.2"}}


# ---------------------------------------------------------------- the lockfile graph (npm_groups)

def test_an_exact_peer_group_is_read_from_the_lockfile(tmp_path: Path) -> None:
    path = tmp_path / "package-lock.json"
    path.write_text(_vitest_lock())
    graph = npm_groups.LockGraph(npm_groups.load(path))
    # @vitest/ui and @vitest/browser are exact peers but not installed; jsdom "*" and vite "^8" do not bind
    assert graph.peer_group("vitest") == ["@vitest/coverage-v8", "vitest"]
    assert graph.peer_group("@vitest/coverage-v8") == ["@vitest/coverage-v8", "vitest"]
    assert graph.peer_group("eslint") == ["eslint"]
    assert graph.moves("vitest", "4.1.5") == [(("@vitest/coverage-v8", "vitest"), "exact-peer group")]
    assert graph.moves("@vitest/mocker", "4.1.5") == [
        (("@vitest/coverage-v8", "vitest"), "via direct parent vitest and its exact-peer group")]
    assert graph.moves("eslint", "10.2.1") == []   # direct, no exact peer: npm audit fix had all it needed


def test_a_range_peer_spec_binds_nothing(tmp_path: Path) -> None:
    path = tmp_path / "package-lock.json"
    path.write_text(_vitest_lock(peer_spec="^4.1.5"))
    graph = npm_groups.LockGraph(npm_groups.load(path))
    assert graph.peer_group("vitest") == ["vitest"] and graph.moves("vitest", "4.1.5") == []


def test_a_transitive_package_moves_through_the_direct_parent_that_resolves_to_it(tmp_path: Path) -> None:
    path = tmp_path / "package-lock.json"
    path.write_text(_express_lock())
    graph = npm_groups.LockGraph(npm_groups.load(path))
    assert graph.direct_parents("qs", "6.14.3") == ["express"]          # node_modules/qs, required by express
    assert graph.direct_parents("qs", "6.16.2") == ["express"]          # the nested copy, via body-parser
    assert graph.moves("qs", "6.14.3") == [(("express",), "via direct parent express")]


def test_names_that_could_read_as_options_are_never_returned(tmp_path: Path) -> None:
    lock = json.loads(_express_lock())
    lock["packages"][""]["dependencies"] = {"--registry=https://evil.example/": "^1"}
    lock["packages"]["node_modules/--registry=https://evil.example/"] = _entry("x", "1.0.0", dependencies={"qs": "*"})
    path = tmp_path / "package-lock.json"
    path.write_text(json.dumps(lock))
    assert npm_groups.LockGraph(npm_groups.load(path)).moves("qs", "6.14.3") == []


def test_an_unreadable_or_v1_lockfile_has_no_graph(tmp_path: Path) -> None:
    assert npm_groups.load(tmp_path / "missing.json") == {}
    (tmp_path / "v1.json").write_text('{"lockfileVersion": 1, "dependencies": {"qs": {"version": "6.14.3"}}}')
    assert npm_groups.load(tmp_path / "v1.json") == {}


# ---------------------------------------------------------------- npm_moves / fix_path (apply)

def test_the_peer_group_is_updated_together(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _osv(monkeypatch)
    root = _tree(tmp_path, _FRONT_PKG, _vitest_lock())
    calls: list = []
    _npm(monkeypatch, {("@vitest/coverage-v8", "vitest"): _vitest_lock("4.1.13")}, calls)
    res = fix.fix_path(root, apply=True, allow_dirty=True)
    updates = [c["update"] for c in calls if c.get("update")]
    assert updates == [("@vitest/coverage-v8", "vitest")]               # one npm update, the whole group
    assert all(c["apply"] is True and not c.get("install") for c in calls)
    assert res.resolved == len(MOCKER_IDS) + len(VITEST_IDS) and not res.feed_errors
    assert any("npm update @vitest/coverage-v8 vitest: kept" in out for _, out in res.npm)
    assert json.loads((root / "package.json").read_text()) == _FRONT_PKG    # package.json never changes
    manifests.check_lock_edit("package-lock.json", _vitest_lock(), (root / "package-lock.json").read_text())


def test_a_transitive_pin_moves_its_direct_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _osv(monkeypatch)
    root = _tree(tmp_path, _BACK_PKG, _express_lock())
    calls: list = []
    _npm(monkeypatch, {("express",): _express_lock("4.22.3", "6.16.1", qs_range="~6.16.0")}, calls)
    res = fix.fix_path(root, apply=True, allow_dirty=True)
    assert [c["update"] for c in calls if c.get("update")] == [("express",)]
    assert res.resolved == len(QS_IDS)
    lock = json.loads((root / "package-lock.json").read_text())
    assert lock["packages"]["node_modules/express"]["version"] == "4.22.3"
    assert lock["packages"][""]["dependencies"] == {"express": "^4.19.2"}   # the declared range is kept


def test_a_move_that_resolves_nothing_is_reverted_and_stays_futile(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """The newest express inside ^4.19.2 still pins the old qs: the parent bump is undone byte for byte."""
    _osv(monkeypatch)
    before = _express_lock()
    root = _tree(tmp_path, _BACK_PKG, before)
    calls: list = []
    _npm(monkeypatch, {("express",): _express_lock("4.22.2", "6.14.4")}, calls)
    res = fix.fix_path(root, apply=True, allow_dirty=True)
    assert (root / "package-lock.json").read_text() == before
    assert res.resolved == 0                                     # executor.stage_fix: "nothing fixable ..." (held)
    out = next(o for _, o in res.npm if o.startswith("npm update express"))
    assert "reverted (it resolves no advisory)" in out


def test_a_move_that_adds_an_advisory_or_fails_is_reverted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _osv(monkeypatch)
    before = _vitest_lock()
    root = _tree(tmp_path, _FRONT_PKG, before)
    calls: list = []
    # the group moves, but the update drags minimatch back onto an affected release it did not have
    worse = json.loads(_vitest_lock("4.1.13", minimatch="10.2.5"))
    lock = json.loads(before)
    lock["packages"]["node_modules/minimatch"]["version"] = "11.0.1"
    (root / "package-lock.json").write_text(before := json.dumps(lock))
    _npm(monkeypatch, {("@vitest/coverage-v8", "vitest"): json.dumps(worse)}, calls)
    res = fix.fix_path(root, apply=True, allow_dirty=True)
    assert (root / "package-lock.json").read_text() == before and res.resolved == 0
    assert any("reverted (it adds advisories: minimatch)" in o for _, o in res.npm)

    def failing(directory: Path, **kw: object) -> str:
        if kw.get("update"):
            (directory / "package-lock.json").write_text(_vitest_lock("4.1.13"))
            return "npm error code ERESOLVE\nnpm error could not resolve"
        return "up to date"
    monkeypatch.setattr(fix, "npm_fix", failing)
    res = fix.fix_path(root, apply=True, allow_dirty=True)
    assert (root / "package-lock.json").read_text() == before
    out = next(o for _, o in res.npm if o.startswith("npm update"))
    assert "reverted (npm error code ERESOLVE)" in out
    # indented: executor.stage_fix reads a line starting "npm error" as the audit fix failing, not as futile
    assert not any(ln.startswith("npm error") for ln in out.splitlines())


def test_out_of_range_fixes_get_no_move_and_stay_major_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """minimatch's fix is a new major (10 → 11): no npm update is tried, and the plan still says major-held."""
    seen: list = []
    _osv(monkeypatch, seen)
    lock = json.loads(_vitest_lock("4.1.13"))   # vitest and mocker already fixed; only minimatch is vulnerable
    root = _tree(tmp_path, _FRONT_PKG, json.dumps(lock))
    calls: list = []
    _npm(monkeypatch, {("eslint",): "{}"}, calls)
    res = fix.fix_path(root, apply=True, allow_dirty=True)
    assert not [c for c in calls if c.get("update")] and res.resolved == 0
    rows, _note = fix.plan_npm_lock(root / "package-lock.json", label="package-lock.json")
    assert [(c.name, c.action) for c in rows] == [("minimatch", "major-held")] and "npm update" not in rows[0].note


def test_no_movable_package_asks_osv_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list = []
    _osv(monkeypatch, seen)
    path = tmp_path / "package-lock.json"
    path.write_text(json.dumps({"lockfileVersion": 3, "packages": {"": {"dependencies": {"qs": "^6"}},
                                                                   "node_modules/qs": _entry("qs", "6.14.3")}}))
    # direct qs without exact peers: the audit fix's job alone
    assert npm_ops.npm_moves(path, run=fix.npm_fix, counts=fix.advisory_counts) == [] and seen == []


# ---------------------------------------------------------------- fail closed (security review)

def test_no_move_runs_outside_the_tools_container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """npm update <parent> re-resolves that parent's package.json spec: never on the host."""
    _osv(monkeypatch)
    before = _express_lock()
    root = _tree(tmp_path, _BACK_PKG, before)
    calls: list = []
    _npm(monkeypatch, {("express",): _express_lock("4.22.3", "6.16.1", qs_range="~6.16.0")}, calls)
    _confined(monkeypatch, on=False)
    res = fix.fix_path(root, apply=True, allow_dirty=True)
    assert not [c for c in calls if c.get("update")] and (root / "package-lock.json").read_text() == before
    out = next(o for _, o in res.npm if o.startswith("npm update moves not run"))
    assert "outside the tools container" in out and res.resolved == 0


@pytest.mark.parametrize("where", ["package.json", "current lock", "base lock"])
def test_no_move_runs_against_an_off_registry_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                     where: str) -> None:
    _osv(monkeypatch)
    pkg, lock = dict(_BACK_PKG), _express_lock()
    if where == "package.json":
        pkg = {**pkg, "dependencies": {"express": "^4.19.2", "evil": "git+https://evil.example/x.git"}}
    root = _tree(tmp_path, pkg, lock)
    path = root / "package-lock.json"
    bad = json.loads(lock)
    bad["packages"]["node_modules/evil"] = {"version": "1.0.0", "resolved": "https://evil.example/evil-1.0.0.tgz"}
    calls: list = []
    _npm(monkeypatch, {("express",): _express_lock("4.22.3", "6.16.1", qs_range="~6.16.0")}, calls)
    if where == "current lock":
        path.write_text(json.dumps(bad))
    base = json.dumps(bad).encode() if where == "base lock" else path.read_bytes()
    current = path.read_bytes()
    out = npm_ops.npm_moves(path, run=fix.npm_fix, counts=fix.advisory_counts, base_lock=base)
    assert len(out) == 1 and out[0].startswith("npm update moves not run: ") and "evil" in out[0]
    assert not [c for c in calls if c.get("update")] and path.read_bytes() == current


def test_confined_and_registry_only_the_move_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _osv(monkeypatch)
    root = _tree(tmp_path, _BACK_PKG, _express_lock())
    path = root / "package-lock.json"
    calls: list = []
    _npm(monkeypatch, {("express",): _express_lock("4.22.3", "6.16.1", qs_range="~6.16.0")}, calls)
    out = npm_ops.npm_moves(path, run=fix.npm_fix, counts=fix.advisory_counts, base_lock=path.read_bytes())
    assert [c["update"] for c in calls] == [("express",)] and out[0].startswith("npm update express: kept")


def test_install_with_update_is_a_callers_mistake(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exclusive"):
        fix.npm_fix(tmp_path, apply=True, install=True, update=("express",))


def test_a_registry_rule_break_in_the_moved_lockfile_is_still_refused() -> None:
    """validate_diff's check is unchanged: an npm update result is judged like the audit fix's."""
    moved = json.loads(_express_lock("4.22.3", "6.16.1"))
    moved["packages"]["node_modules/qs"]["resolved"] = "https://evil.example/qs-6.16.1.tgz"
    with pytest.raises(ValueError, match="outside the registry allowlist"):
        manifests.check_lock_edit("package-lock.json", _express_lock(), json.dumps(moved))
    raised = json.loads(_express_lock("4.22.3", "6.16.1"))
    raised["packages"][""]["dependencies"] = {"express": "^4.22.3"}   # a range change is stage-major's, never this
    with pytest.raises(ValueError, match="root package dependencies changed"):
        manifests.check_lock_edit("package-lock.json", _express_lock(), json.dumps(raised))


# ---------------------------------------------------------------- npm_fix(update=...) against a fake npm

_FAKE_NPM = """
import json, os, sys
with open(os.environ["FAKE_NPM_LOG"], "a") as fh:
    fh.write(json.dumps({"args": sys.argv[1:], "rc": os.environ.get("NPM_CONFIG_USERCONFIG")}) + "\\n")
with open("package-lock.json", "w") as fh:
    fh.write('{"lockfileVersion": 3, "packages": {"": {}}, "updated": true}')
print("changed 2 packages")
"""


@pytest.mark.skipif(sys.platform == "win32", reason="shebang fake npm")
def test_npm_update_runs_lockfile_only_with_the_audit_fixs_flags(tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "npm").write_text(f"#!{sys.executable}\n{_FAKE_NPM}")
    (bindir / "npm").chmod(0o755)
    log = tmp_path / "npm.log"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_NPM_LOG", str(log))
    repo = tmp_path / "repo"
    repo.mkdir()
    pkg = '{"name": "x", "devDependencies": {"vitest": "^4.1.5"}}'
    (repo / "package.json").write_text(pkg)
    (repo / "package-lock.json").write_text('{"lockfileVersion": 3, "packages": {"": {}}}')
    assert "changed 2" in fix.npm_fix(repo, apply=True, update=("@vitest/coverage-v8", "vitest"))
    args = json.loads(log.read_text().splitlines()[-1])["args"]
    assert args[:4] == ["update", "--no-save", "@vitest/coverage-v8", "vitest"]
    assert {"--package-lock-only", "--ignore-scripts", "--registry=https://registry.npmjs.org/",
            "--strict-ssl=true"} <= set(args) and "--force" not in args
    assert '"updated": true' in (repo / "package-lock.json").read_text()
    assert (repo / "package.json").read_text() == pkg                  # only the lockfile is copied back
    runs = len(log.read_text().splitlines())
    for bad in (("--registry=https://evil.example/",), ("-g",), ("file:../x",), ("a b",)):
        assert "not a plain package name" in fix.npm_fix(repo, apply=True, update=bad)
    assert len(log.read_text().splitlines()) == runs                   # npm never ran for them


# ---------------------------------------------------------------- the retry guard's key (the fix plan)

def test_the_plan_names_the_move_so_a_held_repository_is_retried(tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """retry_guard keys the "nothing fixable" hold on the dry-run fix plan's digest: the move must change it."""
    _osv(monkeypatch)
    root = _tree(tmp_path, _FRONT_PKG, _vitest_lock())
    plan = fix.fix_path(root, apply=False, npm=False).markdown()
    assert "`npm update @vitest/coverage-v8 vitest` (exact-peer group)" in plan
    assert "via direct parent vitest and its exact-peer group" in plan
    assert "kept only when it removes an advisory" in plan
    (root / "package-lock.json").write_text(_vitest_lock(peer_spec="^4.1.5"))   # what the old plan could see
    unbound = fix.fix_path(root, apply=False, npm=False).markdown()
    assert "(exact-peer group)" not in unbound
