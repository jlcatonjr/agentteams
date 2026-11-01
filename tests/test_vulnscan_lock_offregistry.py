"""check_lock_edit keeps an off-registry lockfile entry only when it is identical to the base (portal's CDN xlsx).

Each Rule 12 case (what the exclusion could newly permit) has its own refusal test here.
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agentteams.vulnscan import executor, manifests
from tests._vulnscan_helpers import operator_env

CDN = "https://cdn.sheetjs.com/xlsx-0.20.3/xlsx-0.20.3.tgz"
SHA = "sha512-" + "A" * 86 + "=="   # shape of an npm sha512 integrity (placeholder digest)
REL = "package-lock.json"


def _base() -> dict[str, Any]:
    """A v3 lockfile shaped like portal's: a registry package and SheetJS's CDN-only xlsx."""
    return {"lockfileVersion": 3, "packages": {
        "": {"dependencies": {"a": "^1.0.0", "xlsx": CDN}},
        "node_modules/a": {"version": "1.0.0", "resolved": "https://registry.npmjs.org/a/-/a-1.0.0.tgz",
                           "integrity": "sha512-aaa"},
        "node_modules/xlsx": {"version": "0.20.3", "resolved": CDN, "integrity": SHA, "license": "Apache-2.0",
                              "bin": {"xlsx": "bin/xlsx.njs"}, "engines": {"node": ">=0.8"}},
    }}


def _bump_a(lock: dict[str, Any]) -> dict[str, Any]:
    """The registry change npm audit fix would make."""
    lock["packages"]["node_modules/a"].update(version="1.0.1",
                                              resolved="https://registry.npmjs.org/a/-/a-1.0.1.tgz")
    return lock


def _check(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    return manifests.check_lock_edit(REL, json.dumps(old), json.dumps(new))


def test_an_identical_off_registry_entry_is_kept_and_reported() -> None:
    notes = _check(_base(), _bump_a(_base()))
    assert notes == [f"{REL}: unchanged off-registry entry kept: node_modules/xlsx {CDN}"]


def test_a_registry_only_edit_reports_nothing() -> None:
    old = _base()
    del old["packages"]["node_modules/xlsx"]
    new = _bump_a(copy.deepcopy(old))
    assert _check(old, new) == []


def test_a_new_off_registry_entry_is_refused() -> None:
    old = _base()
    del old["packages"]["node_modules/xlsx"]   # base without it: also covers removed-at-base, re-added now
    with pytest.raises(ValueError, match="registry allowlist"):
        _check(old, _bump_a(_base()))


@pytest.mark.parametrize("field,value", [
    ("resolved", "https://cdn.sheetjs.com/xlsx-0.20.4/xlsx-0.20.4.tgz"),
    ("integrity", "sha512-" + "B" * 86 + "=="),
    ("version", "0.20.4"),
    ("dependencies", {"evil": "^1.0.0"}),
    ("optionalDependencies", {"evil": "^1.0.0"}),
    ("peerDependencies", {"evil": "^1.0.0"}),
    ("hasInstallScript", True),
    ("bin", {"xlsx": "bin/other.js"}),
])
def test_a_changed_off_registry_entry_is_refused(field: str, value: object) -> None:
    new = _bump_a(_base())
    new["packages"]["node_modules/xlsx"][field] = value
    with pytest.raises(ValueError, match="registry allowlist"):
        _check(_base(), new)


def test_a_dependency_change_of_a_kept_entry_that_already_had_dependencies_is_refused() -> None:
    old = _base()
    old["packages"]["node_modules/xlsx"]["dependencies"] = {"a": "^1.0.0"}
    new = _bump_a(copy.deepcopy(old))
    assert _check(old, new)   # unchanged: kept
    new["packages"]["node_modules/xlsx"]["dependencies"] = {"a": "^1.0.0", "b": "^2.0.0"}
    with pytest.raises(ValueError, match="registry allowlist"):
        _check(old, new)


def test_an_integrity_removed_is_refused() -> None:
    new = _bump_a(_base())
    del new["packages"]["node_modules/xlsx"]["integrity"]
    with pytest.raises(ValueError, match="registry allowlist"):
        _check(_base(), new)


@pytest.mark.parametrize("integrity", [None, "sha1-abc", ""])
def test_an_unchanged_entry_without_a_sha512_integrity_is_refused(integrity: str | None) -> None:
    old = _base()
    if integrity is None:
        del old["packages"]["node_modules/xlsx"]["integrity"]
    else:
        old["packages"]["node_modules/xlsx"]["integrity"] = integrity
    with pytest.raises(ValueError, match="registry allowlist"):
        _check(old, _bump_a(copy.deepcopy(old)))


@pytest.mark.parametrize("url", ["http://cdn.sheetjs.com/xlsx-0.20.3/xlsx-0.20.3.tgz",
                                 "git+https://github.com/SheetJS/sheetjs.git#abc"])
def test_an_unchanged_entry_not_over_https_is_refused(url: str) -> None:
    old = _base()
    old["packages"]["node_modules/xlsx"]["resolved"] = url
    with pytest.raises(ValueError, match="registry allowlist"):
        _check(old, _bump_a(copy.deepcopy(old)))


def test_an_entry_moved_to_another_path_key_is_refused() -> None:
    new = _bump_a(_base())
    new["packages"]["node_modules/a/node_modules/xlsx"] = new["packages"].pop("node_modules/xlsx")
    with pytest.raises(ValueError, match="node_modules/a/node_modules/xlsx resolves outside"):
        _check(_base(), new)


def test_a_second_copy_at_another_path_key_is_refused() -> None:
    new = _bump_a(_base())
    new["packages"]["node_modules/a/node_modules/xlsx"] = dict(new["packages"]["node_modules/xlsx"])
    with pytest.raises(ValueError, match="node_modules/a/node_modules/xlsx resolves outside"):
        _check(_base(), new)


def test_a_lockfile_with_a_duplicate_key_is_refused() -> None:
    """json.loads keeps the last copy; a reader that keeps the first would install the evil one."""
    good = json.dumps(_base()["packages"]["node_modules/xlsx"])
    evil = json.dumps({**_base()["packages"]["node_modules/xlsx"], "resolved": "https://evil.example/x.tgz"})
    text = json.dumps(_bump_a(_base())).replace('"node_modules/xlsx": ' + good,
                                                '"node_modules/xlsx": ' + evil + ', "node_modules/xlsx": ' + good)
    assert text.count('"node_modules/xlsx"') == 2
    with pytest.raises(ValueError, match="duplicate key 'node_modules/xlsx'"):
        manifests.check_lock_edit(REL, json.dumps(_base()), text)


def test_a_duplicate_key_inside_an_entry_is_refused() -> None:
    text = json.dumps(_bump_a(_base())).replace(f'"resolved": "{CDN}"',
                                                f'"resolved": "https://evil.example/x.tgz", "resolved": "{CDN}"')
    with pytest.raises(ValueError, match="duplicate key 'resolved'"):
        manifests.check_lock_edit(REL, json.dumps(_base()), text)


def test_the_v1_legacy_copy_is_judged_on_its_own() -> None:
    """lockfileVersion 2: the packages copy may be kept while the legacy dependencies copy changes."""
    old = _base()
    old["lockfileVersion"] = 2
    old["dependencies"] = {"xlsx": {"version": "0.20.3", "resolved": CDN, "integrity": SHA}}
    new = _bump_a(copy.deepcopy(old))
    assert len(_check(old, new)) == 2   # both copies unchanged: both kept
    new["dependencies"]["xlsx"]["requires"] = {"evil": "^1.0.0"}
    with pytest.raises(ValueError, match="xlsx resolves outside"):
        _check(old, new)


def test_an_entry_moved_between_sections_is_refused() -> None:
    """The same key and content under `dependencies` in the base does not vouch for it under `packages`."""
    old = _base()
    old["lockfileVersion"] = 2
    old["dependencies"] = {"node_modules/xlsx": dict(old["packages"].pop("node_modules/xlsx"))}
    new = _bump_a(copy.deepcopy(old))
    assert _check(old, new)   # same section, same key: kept
    new["packages"]["node_modules/xlsx"] = dict(new["dependencies"].pop("node_modules/xlsx"))
    with pytest.raises(ValueError, match="registry allowlist"):
        _check(old, new)


def test_the_root_spec_still_may_not_change() -> None:
    new = _bump_a(_base())
    new["packages"][""]["dependencies"]["xlsx"] = "https://cdn.sheetjs.com/xlsx-0.20.4/xlsx-0.20.4.tgz"
    with pytest.raises(ValueError, match="root package dependencies changed"):
        _check(_base(), new)


def test_stage_major_still_refuses_an_off_registry_base() -> None:
    """S1 is not relaxed: npm install re-resolves every package.json spec."""
    manifest = json.dumps({"dependencies": {"a": "^1.0.0", "xlsx": CDN}})
    assert "not a registry spec" in manifests.npm_off_registry(REL, manifest, json.dumps(_base()))
    manifest = json.dumps({"dependencies": {"a": "^1.0.0"}})
    assert "resolves outside the registry allowlist" in manifests.npm_off_registry(REL, manifest,
                                                                                   json.dumps(_base()))


@pytest.mark.skipif(not shutil.which("git"), reason="git not available")
def test_validate_diff_passes_and_reports_the_kept_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    operator_env(tmp_path, monkeypatch)
    repo = tmp_path / "r"
    repo.mkdir()

    def sh(*args: str) -> str:
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout

    sh("init", "-q")
    (repo / REL).write_text(json.dumps(_base(), indent=2))
    sh("add", "-A")
    sh("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "i")
    base = sh("rev-parse", "HEAD").strip()
    (repo / REL).write_text(json.dumps(_bump_a(_base()), indent=2))
    notes: list[str] = []
    assert executor.validate_diff(repo, base, notes=notes) == [REL]
    assert notes == [f"{REL}: unchanged off-registry entry kept: node_modules/xlsx {CDN}"]
    new = _bump_a(_base())
    new["packages"]["node_modules/xlsx"]["resolved"] = "https://cdn.sheetjs.com/xlsx-latest/xlsx-latest.tgz"
    (repo / REL).write_text(json.dumps(new, indent=2))
    with pytest.raises(executor.ExecError, match="registry allowlist"):
        executor.validate_diff(repo, base)
