"""Every common lockfile is detected (vlc N1): one vulnscan does not read is *unsupported*, never *nothing to scan*.

Covers discovery, the unscannable reason and the coverage labels (``coverage.unsupported_manifests``), the Julia
sibling rule, the lockfile fallbacks and the "without a lockfile" manifests. No network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.vulnscan import coverage, manifests

# file -> (sibling files, ecosystem label)
NEW_UNSUPPORTED = {
    "Package.resolved": ((), "SwiftPM"),
    "Podfile.lock": ((), "CocoaPods"),
    "pubspec.lock": ((), "Pub"),
    "mix.lock": ((), "Hex"),
    "bun.lock": ((), "Bun (npm packages)"),
    "bun.lockb": ((), "Bun binary lockfile (npm packages)"),
    "deno.lock": ((), "Deno (npm/JSR)"),
    "renv.lock": ((), "CRAN"),
    "stack.yaml.lock": ((), "Hackage"),
    "cabal.project.freeze": ((), "Hackage"),
    "conan.lock": ((), "Conan (C/C++)"),
    "conanfile.txt": ((), "Conan (C/C++)"),
    "conanfile.py": ((), "Conan (C/C++)"),
    "vcpkg.json": ((), "vcpkg (C/C++)"),
    "Manifest.toml": (("Project.toml",), "Julia"),
    "Gemfile": ((), "RubyGems"),
}


@pytest.mark.parametrize(("name", "siblings", "eco"), [(n, s, e) for n, (s, e) in sorted(NEW_UNSUPPORTED.items())])
def test_new_unsupported_manifests_are_discovered_and_counted_as_a_coverage_gap(
        tmp_path: Path, name: str, siblings: tuple[str, ...], eco: str) -> None:
    for f in (name, *siblings):
        (tmp_path / f).write_text("content\n")
    assert [p.name for p in manifests.discover(tmp_path)] == [name]
    m = manifests.parse(tmp_path / name)
    assert not m.deps and m.unscannable == f"unsupported ecosystem: {eco}"
    assert manifests.unsupported_ecosystem(name) == eco
    assert coverage.unsupported_manifests(tmp_path) == ([f"{name} ({eco})"], True)
    assert coverage.unsupported_only(tmp_path) and not coverage.nothing_to_scan(tmp_path)
    assert name not in manifests.LOCKFILES   # Rule 12: none of them hides a sibling package.json / pyproject.toml


def test_a_bare_manifest_toml_is_not_listed(tmp_path: Path) -> None:
    (tmp_path / "Manifest.toml").write_text("x = 1\n")
    assert manifests.discover(tmp_path) == []
    assert coverage.unsupported_manifests(tmp_path) == ([], False) and coverage.nothing_to_scan(tmp_path)
    assert manifests.unsupported_ecosystem("Manifest.toml") == "Julia"   # name-only; discover applies the sibling


@pytest.mark.parametrize(("manifest", "lock"), [("conanfile.txt", "conan.lock"), ("conanfile.py", "conan.lock"),
                                                ("Gemfile", "Gemfile.lock")])
def test_a_fallback_next_to_its_lockfile_is_listed_once(tmp_path: Path, manifest: str, lock: str) -> None:
    (tmp_path / manifest).write_text("x\n")
    (tmp_path / lock).write_text("x\n")
    assert [p.name for p in manifests.discover(tmp_path)] == [lock]


@pytest.mark.parametrize(("lock", "eco"), [("bun.lockb", "Bun binary lockfile (npm packages)"),
                                         ("bun.lock", "Bun (npm packages)")])
def test_a_bun_lock_does_not_hide_package_json(tmp_path: Path, lock: str, eco: str) -> None:
    (tmp_path / "package.json").write_text('{"name": "x"}')
    (tmp_path / lock).write_bytes(b"\x00bun-lockfile")
    assert {p.name for p in manifests.discover(tmp_path)} == {"package.json", lock}
    assert "package.json without a lockfile" in manifests.parse(tmp_path / "package.json").unscannable
    labels, only = coverage.unsupported_manifests(tmp_path)
    assert labels == [f"{lock} ({eco})"] and not only


@pytest.mark.parametrize(("name", "lock", "hint"), [
    ("composer.json", "composer.lock", "composer.json without a lockfile — run `composer update --lock`"),
    ("Cargo.toml", "Cargo.lock", "Cargo.toml without a lockfile — run `cargo generate-lockfile`")])
def test_manifests_without_their_lockfile_are_unscannable_not_unsupported(tmp_path: Path, name: str, lock: str,
                                                                         hint: str) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / name).write_text("{}\n")
    assert manifests.parse(tmp_path / "a" / name).unscannable == hint
    assert manifests.unsupported_ecosystem(name) == ""
    assert coverage.unsupported_manifests(tmp_path) == ([], False) and not coverage.nothing_to_scan(tmp_path)
    (tmp_path / "a" / lock).write_text("version = 3\n" if lock == "Cargo.lock" else "{}")
    assert [p.name for p in manifests.discover(tmp_path)] == [lock]


def test_a_cargo_workspace_member_is_pinned_by_the_root_lock(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[workspace]\n")
    (tmp_path / "Cargo.lock").write_text("version = 3\n")
    (tmp_path / "crates" / "core").mkdir(parents=True)
    (tmp_path / "crates" / "core" / "Cargo.toml").write_text("[package]\n")
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "Cargo.toml").write_text("[package]\n")
    assert [p.relative_to(tmp_path).as_posix() for p in manifests.discover(tmp_path / "other")] == ["other/Cargo.toml"]
    assert [p.relative_to(tmp_path).as_posix() for p in manifests.discover(tmp_path)] == ["Cargo.lock"]
