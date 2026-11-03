"""vfr-c.C2: ``stage-major`` on a base with a kept off-registry entry (portal: SheetJS xlsx from cdn.sheetjs.com).

The rule (vfail F2, ``@security`` 2026-11-02): allowed only when every off-registry entry comes out of
``npm install --package-lock-only --ignore-scripts`` byte-identical (version, resolved, integrity, dependencies and
every other field) and none is added; npm's egress is the registry only. Each Rule 12 case (what the exclusion could
newly permit) has its own refusal test. Network-free: OSV and npm are stubbed.
"""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agentteams.vulnscan import fix, manifests, npm_kept, osv, subproc_confine
from agentteams.vulnscan.executor_env import ExecError

CDN = "https://cdn.sheetjs.com/xlsx-0.20.3/xlsx-0.20.3.tgz"
SHA = "sha512-" + "A" * 86 + "=="
REL = "package-lock.json"


def _pkg(xlsx: str = CDN) -> str:
    return json.dumps({"name": "portal", "dependencies": {"echarts": "^5.5.1", "xlsx": xlsx}}, indent=2) + "\n"


def _lock(echarts: str = "5.5.1", spec: str = "^5.5.1") -> dict[str, Any]:
    return {"name": "portal", "lockfileVersion": 3, "packages": {
        "": {"name": "portal", "dependencies": {"echarts": spec, "xlsx": CDN}},
        "node_modules/echarts": {"version": echarts, "integrity": "sha512-e",
                                 "resolved": f"https://registry.npmjs.org/echarts/-/echarts-{echarts}.tgz"},
        "node_modules/xlsx": {"version": "0.20.3", "resolved": CDN, "integrity": SHA, "license": "Apache-2.0",
                              "bin": {"xlsx": "bin/xlsx.njs"}, "engines": {"node": ">=0.8"}}}}


def _portal(tmp: Path, monkeypatch: pytest.MonkeyPatch, *, after: Any = None, pkg: str | None = None,
            lock: dict[str, Any] | None = None) -> list[list[str]]:
    """A portal-shaped tree: echarts 5.5.1 (direct, major-held: 6.0.0 fixes it) and the CDN xlsx.

    ``after(lock)`` edits the lockfile the fake ``npm install`` writes (default: echarts 6.0.0, xlsx untouched).
    """
    (tmp / "package.json").write_text(pkg or _pkg())
    (tmp / REL).write_text(json.dumps(lock or _lock(), indent=2) + "\n")
    monkeypatch.setattr(osv, "vulnerable_ids", lambda items: {
        k: ["GHSA-echarts"] for k in set(items) if (k[1], k[2]) == ("echarts", "5.5.1")})
    monkeypatch.setattr(osv, "minimal_fix", lambda ids, eco, name, ver: ("6.0.0", 0))
    monkeypatch.setattr(fix, "git_config_problem", lambda directory: "")
    monkeypatch.setattr(fix, "_dirty", lambda path: False)
    scratch = tmp.parent / f"{tmp.name}-scratch"
    scratch.mkdir(exist_ok=True)
    monkeypatch.setattr(subproc_confine, "active", lambda: subproc_confine.Confined(image="tools:test"))
    monkeypatch.setattr(subproc_confine, "scratch_parent", lambda: scratch)
    calls: list[list[str]] = []

    def run_npm(args: list, *, workdir: Path, scratch_root: Path, env: dict, timeout: float = 600) -> object:
        calls.append(list(args))
        if args[0] == "install":
            spec = json.loads((workdir / "package.json").read_text())["dependencies"]["echarts"]
            new = _lock("6.0.0", spec)
            new = after(new) or new if after else new
            (workdir / REL).write_text(json.dumps(new, indent=2) + "\n")
        return subprocess.CompletedProcess(args, 0, "ok\n", "")

    monkeypatch.setattr(subproc_confine, "run_npm", run_npm)
    return calls


def test_portal_takes_the_echarts_major_and_keeps_xlsx_byte_identical(tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _portal(tmp_path, monkeypatch)
    res = fix.fix_path(tmp_path, apply=True, allow_major=True, allow_dirty=True)
    [row] = res.changes
    assert (row.name, row.action, row.major, row.new) == ("echarts", "fix", True, "6.0.0")
    install = calls[0]
    assert install[:4] == ["install", "--no-audit", "--package-lock-only", "--ignore-scripts"]
    after = json.loads((tmp_path / REL).read_text())
    assert after["packages"]["node_modules/xlsx"] == _lock()["packages"]["node_modules/xlsx"]
    notes = manifests.check_lock_edit(REL, json.dumps(_lock()), json.dumps(after),
                                      manifest=json.loads((tmp_path / "package.json").read_text()))
    assert notes and CDN in notes[0]   # validate_diff's own check keeps it, and names it for the PR body


@pytest.mark.parametrize("field,value", [
    ("resolved", "https://cdn.sheetjs.com/xlsx-0.20.4/xlsx-0.20.4.tgz"),
    ("integrity", "sha512-" + "B" * 86 + "=="),
    ("version", "0.20.4"),
    ("dependencies", {"evil": "^1.0.0"}),
    ("hasInstallScript", True),
])
def test_an_off_registry_entry_npm_changed_refuses_the_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                             field: str, value: object) -> None:
    def change(lock: dict) -> None:
        lock["packages"]["node_modules/xlsx"][field] = value
    _portal(tmp_path, monkeypatch, after=change)
    with pytest.raises(ExecError, match="added or changed an off-registry entry"):
        fix.fix_path(tmp_path, apply=True, allow_major=True, allow_dirty=True)


def test_a_new_off_registry_entry_refuses_the_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def add(lock: dict) -> None:
        lock["packages"]["node_modules/zrender"] = {"version": "6.0.0", "integrity": SHA,
                                                    "resolved": "https://cdn.example.com/zrender-6.0.0.tgz"}
    _portal(tmp_path, monkeypatch, after=add)
    with pytest.raises(ExecError, match="zrender"):
        fix.fix_path(tmp_path, apply=True, allow_major=True, allow_dirty=True)


def test_a_moved_or_removed_off_registry_entry_refuses_the_stage(tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    def move(lock: dict) -> None:
        lock["packages"]["node_modules/echarts/node_modules/xlsx"] = lock["packages"].pop("node_modules/xlsx")
    _portal(tmp_path, monkeypatch, after=move)
    with pytest.raises(ExecError, match="off-registry entry"):
        fix.fix_path(tmp_path, apply=True, allow_major=True, allow_dirty=True)
    assert npm_kept.after_problem(REL, npm_kept.base_problem(REL, _pkg(), json.dumps(_lock()))[1],
                                  json.dumps({"lockfileVersion": 3, "packages": {"": {}}})).startswith(
        f"{REL}: npm install removed")


@pytest.mark.parametrize("case", ["no-integrity", "sha1", "http", "nested", "registry-spec", "other-url-spec",
                                  "link", "git-spec", "v1-only", "spec-without-entry"])
def test_only_the_portal_shape_is_allowed_at_the_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                      case: str) -> None:
    lock, pkg = _lock(), _pkg()
    x = lock["packages"]["node_modules/xlsx"]
    if case == "no-integrity":
        del x["integrity"]
    elif case == "sha1":
        x["integrity"] = "sha1-abc"
    elif case == "http":
        x["resolved"] = CDN.replace("https:", "http:")
        pkg = _pkg(x["resolved"])
    elif case == "nested":
        lock["packages"]["node_modules/echarts/node_modules/xlsx"] = lock["packages"].pop("node_modules/xlsx")
    elif case == "registry-spec":   # an off-registry entry a registry range points at
        pkg = _pkg("^0.20.3")
    elif case == "other-url-spec":
        pkg = _pkg("https://cdn.sheetjs.com/xlsx-0.20.4/xlsx-0.20.4.tgz")
    elif case == "link":
        x["link"] = True
    elif case == "git-spec":
        pkg = _pkg("git+https://github.com/SheetJS/sheetjs.git")
    elif case == "v1-only":
        lock = {"lockfileVersion": 1, "dependencies": {"xlsx": copy.deepcopy(x), "echarts": copy.deepcopy(
            lock["packages"]["node_modules/echarts"])}}
    elif case == "spec-without-entry":
        del lock["packages"]["node_modules/xlsx"]
    calls = _portal(tmp_path, monkeypatch, pkg=pkg, lock=lock)
    with pytest.raises(ExecError, match="refuses npm install"):
        fix.fix_path(tmp_path, apply=True, allow_major=True, allow_dirty=True)
    assert calls == [] and (tmp_path / "package.json").read_text() == pkg   # nothing ran, nothing written


def test_a_registry_only_base_keeps_nothing() -> None:
    lock = _lock()
    del lock["packages"]["node_modules/xlsx"]
    lock["packages"][""]["dependencies"].pop("xlsx")
    pkg = json.dumps({"dependencies": {"echarts": "^5.5.1"}})
    assert npm_kept.base_problem(REL, pkg, json.dumps(lock)) == ("", frozenset())


def test_npm_egress_is_the_registry_only() -> None:
    """The CDN is never reachable from the install: run_npm's only egress endpoint is the npm registry."""
    seen: dict[str, Any] = {}
    real = subproc_confine.run_confined
    try:
        subproc_confine.run_confined = lambda op, argv, **kw: seen.update(kw) or subprocess.CompletedProcess(  # type: ignore[assignment]
            argv, 0, "", "")
        orig = subproc_confine.active
        subproc_confine.active = lambda: subproc_confine.Confined(image="t")  # type: ignore[assignment]
        subproc_confine.run_npm(["install"], workdir=Path("/tmp"), scratch_root=Path("/tmp"), env={})
    finally:
        subproc_confine.run_confined = real  # type: ignore[assignment]
        subproc_confine.active = orig  # type: ignore[assignment]
    assert seen["egress"] == (subproc_confine.NPM_ENDPOINT,) == ("registry.npmjs.org:443",)
