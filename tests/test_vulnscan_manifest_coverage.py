"""Scanner coverage: pip ``.lock``/``.in`` requirements, a pyproject next to requirements files, and
unsupported lockfiles reported as unscannable (never skipped silently).

No network: OSV lookups are replaced by a recorder that fails on anything unexpected.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams.vulnscan import _http, engine, fix, manifests, osv
from agentteams.vulnscan import project_policy as pp


class Recorder:
    """Stands in for every HTTP call; records each (name, version) sent to OSV."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def __call__(self, url: str, payload: dict | None = None, **_: object) -> object:
        if url.endswith("/querybatch"):
            self.sent += [(q["package"]["name"], q["version"]) for q in payload["queries"]]
            return {"results": [{} for _q in payload["queries"]]}
        raise _http.FeedError(f"unexpected URL {url}")


@pytest.fixture
def rec(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    r = Recorder()
    monkeypatch.setattr(_http, "fetch_json", r)
    osv._DETAILS.clear()
    return r


def _rel(paths: list[Path], root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in paths}


def test_requirements_lock_and_in_files_are_read_but_never_editable(tmp_path: Path) -> None:
    (tmp_path / "requirements-extra.lock").write_text("lib-a==1.2.0\nlib-b==2.0 \\\n    --hash=sha256:00\n")
    (tmp_path / "requirements.in").write_text("-r requirements-extra.lock\nlib-c>=1\n")
    assert _rel(manifests.discover(tmp_path), tmp_path) == {"requirements-extra.lock", "requirements.in"}
    lock = manifests.parse(tmp_path / "requirements-extra.lock")
    assert [(d.name, d.version) for d in lock.deps] == [("lib-a", "1.2.0"), ("lib-b", "2.0")]
    assert "no exact" in manifests.parse(tmp_path / "requirements.in").unscannable
    # only requirements*.txt is a staged-fix target (executor.validate_diff checks those with check_pin_edit)
    assert not manifests.REQ_NAME.match("requirements-extra.lock")
    assert not manifests.REQ_NAME.match("requirements.in")
    assert manifests.REQ_NAME.match("requirements-dev.txt")


def test_pyproject_next_to_requirements_contributes_its_exact_pins_once(tmp_path: Path) -> None:
    (tmp_path / "requirements-run.txt").write_text("lib-a==1.0\nlib-c==3.0\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\n'
        'dependencies = ["lib_a==1.0", "lib-b==2.0 ; python_version > \'3\'", "lib-c>=3", "lib-d>=1", "lib-e==1.*"]\n'
        '[project.optional-dependencies]\ntest = ["lib-f==0.5"]\nextra = ["lib-g==4.1"]\n')
    assert "pyproject.toml" in _rel(manifests.discover(tmp_path), tmp_path)
    m = manifests.parse(tmp_path / "pyproject.toml")
    # lib_a==1.0 is already pinned alongside (PEP 503 names): not counted twice
    assert [(d.name, d.version, d.scope) for d in m.deps] == [
        ("lib-b", "2.0", "runtime"), ("lib-f", "0.5", "dev"), ("lib-g", "4.1", "runtime")]
    # lib-c is loose here but pinned alongside; lib-d and the wildcard lib-e are pinned nowhere
    assert m.unpinned == 2 and "2 [project] dependency(ies) without an exact" in m.unscannable


def test_pyproject_alone_or_empty_beside_requirements(tmp_path: Path) -> None:
    alone = tmp_path / "alone"
    alone.mkdir()
    (alone / "pyproject.toml").write_text('[project]\nname = "x"\ndependencies = ["lib-a==1.0"]\n')
    assert "without a lockfile" in manifests.parse(alone / "pyproject.toml").unscannable   # unchanged
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "requirements.txt").write_text("lib-a==1.0\n")
    (empty / "pyproject.toml").write_text('[project]\nname = "x"\ndependencies = []\n')
    m = manifests.parse(empty / "pyproject.toml")
    assert not m.deps and not m.unscannable          # nothing of its own: no noise row
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "requirements.txt").write_text("lib-a==1.0\n")
    (locked / "uv.lock").write_text('[[package]]\nname = "lib-a"\nversion = "1.0"\n')
    (locked / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert "locked/pyproject.toml" not in _rel(manifests.discover(tmp_path), tmp_path)   # a lockfile covers it
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "requirements.txt").write_text("lib-a==1.0\n")
    (bad / "pyproject.toml").write_text('[project]\ndependencies = [1]\n')
    assert "could not parse" in manifests.parse(bad / "pyproject.toml").unscannable


@pytest.mark.parametrize(("name", "eco"), [
    ("environment.yml", "conda"), ("environment.yaml", "conda"),
    ("lake-manifest.json", "Lean")])
def test_unsupported_lockfiles_are_reported_unscannable(tmp_path: Path, name: str, eco: str) -> None:
    (tmp_path / name).write_text("content\n")
    (tmp_path / "target").mkdir()
    assert _rel(manifests.discover(tmp_path), tmp_path) == {name}
    m = manifests.parse(tmp_path / name)
    assert not m.deps and m.unscannable == f"unsupported ecosystem: {eco}"


def test_scan_reports_unsupported_and_honours_the_brief_for_new_manifests(tmp_path: Path, rec: Recorder) -> None:
    repo = tmp_path / "app"
    (repo / ".git").mkdir(parents=True)
    (repo / "requirements.txt").write_text("lib-a==1.0\n")
    (repo / "pyproject.toml").write_text('[project]\nname = "x"\ndependencies = ["lib-a==1.0", "acme-core==2.0", '
                                         '"lib-b==3.0"]\n')
    (repo / "requirements-gpu.lock").write_text("lib-c==4.0\n")
    (repo / "crate").mkdir()
    (repo / "crate" / "Cargo.lock").write_text("version = 3\n")   # no crates.io packages: unscannable
    (repo / "skipped").mkdir()
    (repo / "skipped" / "requirements.lock").write_text("lib-hidden==0.1\n")
    (repo / "node_modules" / "x").mkdir(parents=True)
    (repo / "node_modules" / "x" / "Cargo.lock").write_text("version = 3\n")
    brief = repo / ".agentteams" / "brief.json"
    brief.parent.mkdir()
    brief.write_text(json.dumps({"project_goal": "a goal for tests",
                                 pp.POLICY_KEY: {"level": "scan", "private_packages": ["ACME-*"],
                                                 "exclude_paths": ["skipped"]}}))
    rep = engine.scan_path(tmp_path)
    assert sorted(rec.sent) == [("lib-a", "1.0"), ("lib-b", "3.0"), ("lib-c", "4.0")]   # lib-a once
    assert rep.withheld == ["app: acme-core (PyPI)"] and rep.excluded == ["app: skipped/requirements.lock"]
    assert rep.unscannable == [("app/crate/Cargo.lock", "no crates.io packages")]


def test_fix_never_plans_or_edits_scan_only_requirements(tmp_path: Path, rec: Recorder) -> None:
    (tmp_path / "requirements.lock").write_text("lib-a==1.0\n")
    (tmp_path / "Cargo.lock").write_text("version = 3\n")
    res = fix.fix_path(tmp_path, apply=False, npm=False)
    assert res.changes == [] and res.refused == [] and rec.sent == []
