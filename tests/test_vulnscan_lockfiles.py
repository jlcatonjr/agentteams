"""Go, pnpm, Bun, RubyGems, Gradle and NuGet lockfiles (vlc N7): parsing, discovery, plan-only fix plans, refusals.

No network: OSV is the canned :class:`tests.test_vulnscan_core.Feed`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams.vulnscan import coverage, engine, fix, lockfiles, manifests
from agentteams.vulnscan import project_policy as pp
from tests.test_vulnscan_core import Feed, _adv, feed  # noqa: F401  (feed is a fixture)

GO_MOD = """module example.com/app

go 1.21

require (
\tgithub.com/gin-gonic/gin v1.9.0
\tgolang.org/x/net v0.0.0-20220722155237-a158d28d115b // indirect
\t"github.com/quoted/mod" v1.0.0
\texample.com/local v0.1.0
\tgithub.com/old/mod v1.2.0
)

require github.com/single/line v2.0.0+incompatible // a comment

replace example.com/local => ../local

replace (
\tgithub.com/old/mod v1.2.0 => github.com/new/mod v1.3.0
)
"""

PNPM_V6 = """lockfileVersion: '6.0'

settings:
  autoInstallPeers: true

dependencies:
  react:
    specifier: ^18.0.0
    version: 18.2.0

packages:

  /react@18.2.0:
    resolution: {integrity: sha512-x}
    engines: {node: '>=0.10.0'}
    dependencies:
      loose-envify: 1.4.0
    dev: false

  /@babel/core@7.20.0(supports-color@5.5.0):
    resolution: {integrity: sha512-y}
    dev: true

  github.com/user/repo/abc123:
    resolution: {tarball: https://codeload.github.com/user/repo/tar.gz/abc}
    name: repo
    version: 1.0.0
    dev: false
"""

PNPM_V9 = """lockfileVersion: '9.0'

importers:

  .:
    dependencies:
      react:
        specifier: ^18.0.0
        version: 18.2.0
    devDependencies:
      '@babel/core':
        specifier: ^7
        version: 7.20.0(supports-color@5.5.0)
      shared:
        specifier: ^1
        version: 1.0.0

packages:

  react@18.2.0:
    resolution: {integrity: sha512-x}

  '@babel/core@7.20.0':
    resolution: {integrity: sha512-y}
    deprecated: |
      text that looks like a key:
  loose-envify@1.4.0:
    resolution: {integrity: sha512-z}
  shared@1.0.0:
    resolution: {integrity: sha512-z}
  debug@4.0.0:
    resolution: {integrity: sha512-z}
  foo@https://codeload.github.com/x/y/tar.gz/abc:
    resolution: {tarball: https://codeload.github.com/x/y/tar.gz/abc}

snapshots:

  react@18.2.0:
    dependencies:
      loose-envify: 1.4.0
      shared: 1.0.0

  '@babel/core@7.20.0(supports-color@5.5.0)':
    dependencies:
      debug: 4.0.0

  loose-envify@1.4.0: {}
  shared@1.0.0: {}
  debug@4.0.0: {}
"""

BUN_LOCK = """{
  // a comment, and trailing commas below
  "lockfileVersion": 1,
  "workspaces": {
    "": { "name": "app", "dependencies": { "lodash": "^4" }, },
  },
  "packages": {
    "lodash": ["lodash@4.17.20", "", {}, "sha512-a//b"],
    "@types/node": ["@types/node@20.1.0", "", {}, "sha512-c"],
    "ws": ["ws@workspace:packages/ws"],
    "g": ["g@github:user/repo#abc", {}],
  },
}
"""

GEMFILE_LOCK = """GIT
  remote: https://github.com/x/y.git
  revision: abc
  specs:
    ygem (0.1.0)

PATH
  remote: .
  specs:
    mygem (0.1.0)

GEM
  remote: https://rubygems.org/
  specs:
    actionpack (7.0.4)
      rack (~> 2.0)
    nokogiri (1.13.10-x86_64-linux)
      racc (~> 1.4)
    nokogiri (1.13.10-arm64-darwin)
    racc (1.6.0)
    rails (7.1.0.rc1)

GEM
  remote: https://gems.example.com/
  specs:
    secret (1.0.0)

PLATFORMS
  x86_64-linux

DEPENDENCIES
  rails

BUNDLED WITH
   2.4.0
"""

GRADLE_LOCK = """# This is a Gradle generated file for dependency locking.
# Manual edits can break the build and are not advised.
com.google.guava:guava:31.1-jre=compileClasspath,runtimeClasspath
junit:junit:4.13.2=testCompileClasspath,testRuntimeClasspath
org.springframework:spring-core:5.3.18.RELEASE=runtimeClasspath
empty=annotationProcessor
"""

NUGET_LOCK = json.dumps({"version": 1, "dependencies": {
    "net6.0": {"Newtonsoft.Json": {"type": "Direct", "requested": "[13.0.1, )", "resolved": "13.0.1"},
               "System.Text.Json": {"type": "Transitive", "resolved": "4.7.0"},
               "MyLib": {"type": "Project"}},
    "net6.0/win-x64": {"newtonsoft.json": {"type": "Direct", "resolved": "13.0.1"}}}})


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _rows(m: manifests.Manifest) -> list[tuple[str, str, str, str]]:
    return [(d.ecosystem, d.name, d.version, d.scope) for d in m.deps]


# ---------------------------------------------------------------- parsing


def test_go_mod_reads_requires_applies_replaces_and_carries_its_notes(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    m = manifests.parse(_write(tmp_path / "go.mod", GO_MOD))
    assert _rows(m) == [("Go", "github.com/gin-gonic/gin", "1.9.0", "runtime"),
                        ("Go", "golang.org/x/net", "0.0.0-20220722155237-a158d28d115b", "runtime"),
                        ("Go", "github.com/quoted/mod", "1.0.0", "runtime"),
                        ("Go", "github.com/new/mod", "1.3.0", "runtime"),
                        ("Go", "github.com/single/line", "2.0.0+incompatible", "runtime")]
    assert m.unscannable == ("1 module(s) replaced by a local path, not scanned: example.com/local; "
                             "Go standard library / toolchain vulnerabilities not judged")


def test_old_go_directive_and_go_work_are_noted(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    _write(tmp_path / "go.work", "go 1.22\nuse ./svc\n")
    m = manifests.parse(_write(tmp_path / "svc" / "go.mod", "module x\n\ngo 1.16\n\nrequire a.example/b v1.0.0\n"))
    assert _rows(m) == [("Go", "a.example/b", "1.0.0", "runtime")]
    assert m.unscannable.split("; ") == [lockfiles.GO_OLD_NOTE, lockfiles.GO_WORK_NOTE, lockfiles.GO_STDLIB_NOTE]
    bare = manifests.parse(_write(tmp_path / "old" / "go.mod", "module y\n"))
    assert bare.deps == [] and lockfiles.GO_OLD_NOTE in bare.unscannable


def test_pnpm_v6_keys_peer_suffixes_and_dev_flags(tmp_path: Path) -> None:
    m = manifests.parse(_write(tmp_path / "pnpm-lock.yaml", PNPM_V6))
    assert m.unscannable == ""
    assert _rows(m) == [("npm", "react", "18.2.0", "runtime"), ("npm", "@babel/core", "7.20.0", "dev")]


def test_pnpm_v9_dev_scope_only_when_reachable_solely_from_dev_dependencies(tmp_path: Path) -> None:
    m = manifests.parse(_write(tmp_path / "pnpm-lock.yaml", PNPM_V9))
    assert m.unscannable == ""
    assert sorted(_rows(m)) == [("npm", "@babel/core", "7.20.0", "dev"), ("npm", "debug", "4.0.0", "dev"),
                                ("npm", "loose-envify", "1.4.0", "runtime"), ("npm", "react", "18.2.0", "runtime"),
                                ("npm", "shared", "1.0.0", "runtime")]   # shared: dev and runtime -> runtime
    no_snapshots = PNPM_V9[:PNPM_V9.index("snapshots:")]
    m2 = manifests.parse(_write(tmp_path / "b" / "pnpm-lock.yaml", no_snapshots))
    assert {d.scope for d in m2.deps} == {"runtime"}   # cannot tell: conservative


def test_bun_lock_jsonc_and_non_registry_entries(tmp_path: Path) -> None:
    m = manifests.parse(_write(tmp_path / "bun.lock", BUN_LOCK))
    assert m.unscannable == ""
    assert _rows(m) == [("npm", "lodash", "4.17.20", "runtime"), ("npm", "@types/node", "20.1.0", "runtime")]


def test_gemfile_lock_gem_sections_platforms_and_other_sources(tmp_path: Path) -> None:
    m = manifests.parse(_write(tmp_path / "Gemfile.lock", GEMFILE_LOCK))
    assert _rows(m) == [("RubyGems", "actionpack", "7.0.4", "runtime"), ("RubyGems", "nokogiri", "1.13.10", "runtime"),
                        ("RubyGems", "racc", "1.6.0", "runtime"), ("RubyGems", "rails", "7.1.0.rc1", "runtime")]
    assert m.unscannable == "1 gem(s) from a source other than rubygems.org, not scanned"


def test_gradle_lockfile_coordinates_and_test_only_configurations(tmp_path: Path) -> None:
    m = manifests.parse(_write(tmp_path / "gradle.lockfile", GRADLE_LOCK))
    assert m.unscannable == ""
    assert _rows(m) == [("Maven", "com.google.guava:guava", "31.1-jre", "runtime"),
                        ("Maven", "junit:junit", "4.13.2", "dev"),
                        ("Maven", "org.springframework:spring-core", "5.3.18.RELEASE", "runtime")]


def test_nuget_lock_resolved_versions_once_and_projects_skipped(tmp_path: Path) -> None:
    m = manifests.parse(_write(tmp_path / "packages.lock.json", NUGET_LOCK))
    assert m.unscannable == ""
    assert _rows(m) == [("NuGet", "Newtonsoft.Json", "13.0.1", "runtime"), ("NuGet", "System.Text.Json", "4.7.0", "runtime")]


HOSTILE = [
    ("go.mod", "module x\ngo 1.21\nrequire evil|mod v1.0.0\n"),
    ("go.mod", "module x\ngo 1.21\nrequire a.example/b v1.0.0`id`\n"),
    ("go.mod", "module x\ngo 1.21\nrequire (\n a.example/b v1.0.0\n"),                 # unterminated block
    ("go.mod", "module x\ngo 1.21\nrequire a.example/b\n"),                             # malformed require
    ("go.mod", "module x\ngo 1.21\nrequire a.example/b v1.0.0\nreplace a.example/b => evil|x v1.0.0\n"),
    ("go.mod", "module x\ngo one\n"),
    ("pnpm-lock.yaml", "lockfileVersion: 5.4\npackages:\n  /a/1.0.0:\n    dev: false\n"),     # v5: not read
    ("pnpm-lock.yaml", "packages:\n  /a@1.0.0:\n    dev: false\n"),                         # no version
    ("pnpm-lock.yaml", "lockfileVersion: '9.0'\npackages:\n  'evil|x@1.0.0':\n    resolution: {}\n"),
    ("pnpm-lock.yaml", "lockfileVersion: '9.0'\npackages:\n  'a@1.0.0`id`':\n    resolution: {}\n"),
    ("pnpm-lock.yaml", "lockfileVersion: '6.0'\npackages:\n  /noversion:\n    dev: false\n"),
    ("bun.lock", '{"lockfileVersion": 1, "packages": {"a": ["evil|x@1.0.0"]}}'),
    ("bun.lock", '{"lockfileVersion": 1, "packages": {"a": ["a@1.0.0\\n| row |"]}}'),
    ("bun.lock", '{"lockfileVersion": 1, "packages": {"a": "a@1.0.0"}}'),
    ("bun.lock", '{"lockfileVersion": 7, "packages": {}}'),
    ("bun.lock", '{"lockfileVersion": 1, /* unterminated'),
    ("bun.lock", "[" * 100000),
    ("Gemfile.lock", "GEM\n  remote: https://rubygems.org/\n  specs:\n    evil|x (1.0.0)\n"),
    ("Gemfile.lock", "GEM\n  remote: https://rubygems.org/\n  specs:\n    x (1.0.0-weird-suffix)\n"),
    ("Gemfile.lock", "GEM\n  remote: https://rubygems.org/\n  specs:\n    x 1.0.0\n"),
    ("gradle.lockfile", "not a coordinate line\n"),
    ("gradle.lockfile", "g:a:1.0`id`=runtimeClasspath\n"),
    ("packages.lock.json", '{"version": 1, "dependencies": {"net6.0": {"evil|x": {"resolved": "1.0.0"}}}}'),
    ("packages.lock.json", '{"version": 1, "dependencies": {"net6.0": {"X": {"resolved": 1}}}}'),
    ("packages.lock.json", '{"version": 9, "dependencies": {}}'),
    ("packages.lock.json", "{not json"),
    ("packages.lock.json", '{"version": 1, "dependencies": ["x"]}'),
]


@pytest.mark.parametrize(("name", "text"), HOSTILE)
def test_hostile_or_malformed_lockfiles_are_unscannable_and_never_raise(tmp_path: Path, name: str, text: str) -> None:
    path = _write(tmp_path / name, text)
    m = manifests.parse(path)
    assert m.deps == [] and m.unscannable
    assert fix.plan_lockfile(path) == []   # nothing of it reaches a fix plan


@pytest.mark.parametrize("name", sorted(lockfiles.FORMATS))
def test_non_utf8_and_oversized_lockfiles_are_unscannable(tmp_path: Path, name: str,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / name
    path.write_bytes(b"\xff\xfe\x00bad")
    assert manifests.parse(path).unscannable.startswith("could not parse")
    monkeypatch.setattr(lockfiles, "MAX_BYTES", 2)
    assert "larger than 2 bytes" in manifests.parse(path).unscannable


# ---------------------------------------------------------------- discovery and coverage


def test_new_lockfiles_are_scanned_not_unsupported(tmp_path: Path) -> None:
    for name in lockfiles.FORMATS:
        assert manifests.unsupported_ecosystem(name) == "", name
        assert name not in manifests.LOCKFILES   # never hides a pyproject.toml
    for name in ("Gemfile", "build.gradle", "App.csproj"):   # manifests still unsupported without their lockfile
        assert manifests.unsupported_ecosystem(name)


def test_go_mod_and_go_sum_side_by_side_and_go_sum_alone(tmp_path: Path) -> None:
    _write(tmp_path / "both" / "go.mod", "module x\ngo 1.21\nrequire a.example/b v1.0.0\n")
    _write(tmp_path / "both" / "go.sum", "a.example/b v1.0.0 h1:x=\n")
    _write(tmp_path / "sum" / "go.sum", "a.example/b v1.0.0 h1:x=\n")
    got = [p.relative_to(tmp_path).as_posix() for p in manifests.discover(tmp_path)]
    assert got == ["both/go.mod", "sum/go.sum"]
    assert manifests.parse(tmp_path / "sum" / "go.sum").unscannable == "unsupported ecosystem: Go (go.sum without go.mod)"
    assert coverage.unsupported_manifests(tmp_path / "sum") == (["go.sum (Go (go.sum without go.mod))"], True)
    assert coverage.unsupported_manifests(tmp_path / "both") == ([], False)


@pytest.mark.parametrize(("lock", "text"), [("pnpm-lock.yaml", PNPM_V9), ("bun.lock", BUN_LOCK)])
def test_pnpm_and_bun_locks_cover_a_package_json_but_not_a_pyproject(tmp_path: Path, lock: str, text: str) -> None:
    _write(tmp_path / lock, text)
    _write(tmp_path / "package.json", '{"name": "x"}')
    _write(tmp_path / "pyproject.toml", '[project]\nname = "x"\n')
    assert {p.name for p in manifests.discover(tmp_path)} == {lock, "pyproject.toml"}


def test_bun_binary_lock_still_does_not_cover_package_json(tmp_path: Path) -> None:
    _write(tmp_path / "bun.lockb", "\x00")
    _write(tmp_path / "package.json", '{"name": "x"}')
    assert {p.name for p in manifests.discover(tmp_path)} == {"bun.lockb", "package.json"}


def test_mixed_directory_of_every_new_format(tmp_path: Path) -> None:
    for name, text in (("go.mod", GO_MOD), ("pnpm-lock.yaml", PNPM_V6), ("Gemfile.lock", GEMFILE_LOCK),
                       ("Gemfile", "source 'https://rubygems.org'\n"), ("gradle.lockfile", GRADLE_LOCK),
                       ("build.gradle", "plugins {}\n"), ("packages.lock.json", NUGET_LOCK),
                       ("App.csproj", "<Project/>\n"), ("requirements.txt", "lib-a==1.0\n")):
        _write(tmp_path / name, text)
    assert {p.name for p in manifests.discover(tmp_path)} == {"go.mod", "pnpm-lock.yaml", "Gemfile.lock",
                                                              "gradle.lockfile", "packages.lock.json",
                                                              "requirements.txt"}
    assert coverage.unsupported_manifests(tmp_path) == ([], False)


# ---------------------------------------------------------------- engine, privacy


def test_scan_finds_vulnerable_go_module_and_withholds_private_names_case_insensitively(
        tmp_path: Path, feed: Feed) -> None:  # noqa: F811
    repo = tmp_path / "app"
    (repo / ".git").mkdir(parents=True)
    _write(repo / "go.mod", GO_MOD)
    _write(repo / "packages.lock.json", NUGET_LOCK)
    _write(repo / ".agentteams" / "brief.json", json.dumps({
        "project_goal": "a goal for tests",
        pp.POLICY_KEY: {"level": "scan", "private_packages": ["*QUOTED*", "system.text.*"]}}))
    feed.vulns[("Go", "golang.org/x/net", "0.0.0-20220722155237-a158d28d115b")] = ["GO-2022-0969"]
    feed.records["GO-2022-0969"] = _adv("GO-2022-0969", "Go", "golang.org/x/net", "0", "0.0.0-20220906165146-f3363e06e74c")
    rep = engine.scan_path(tmp_path)
    assert rep.feed_errors == []
    assert sorted(rep.withheld) == ["app: System.Text.Json (NuGet)", "app: github.com/quoted/mod (Go)"]
    [f] = rep.findings
    assert (f.ecosystem, f.name, f.fix) == ("Go", "golang.org/x/net", "0.0.0-20220906165146-f3363e06e74c")
    [(where, why)] = rep.unscannable   # partial: scanned, and the notes reported
    assert where == "app/go.mod" and why.endswith(lockfiles.GO_STDLIB_NOTE)


def test_is_private_stays_case_insensitive_for_new_ecosystems() -> None:
    assert pp.is_private("com.Acme:Core", ["com.acme:*"], "Maven")
    assert pp.is_private("github.com/Acme/x", ["github.com/acme/*"], "Go")
    assert pp.is_private("Acme.Core", ["acme.*"], "NuGet")
    assert pp.is_private("Acme_Gem", ["acme_*"], "RubyGems")


# ---------------------------------------------------------------- fix plan (plan only)

PLAN_CASES = [
    ("go.mod", GO_MOD, "Go", "github.com/gin-gonic/gin", "1.9.0", "1.9.1",
     "`go get github.com/gin-gonic/gin@v1.9.1`", "## Go: `go.mod`"),
    ("pnpm-lock.yaml", PNPM_V9, "npm", "@babel/core", "7.20.0", "7.23.2", "`pnpm update @babel/core@7.23.2`",
     "## npm (pnpm): `pnpm-lock.yaml`"),
    ("bun.lock", BUN_LOCK, "npm", "lodash", "4.17.20", "4.17.21", "`bun update lodash@4.17.21`",
     "## npm (Bun): `bun.lock`"),
    ("Gemfile.lock", GEMFILE_LOCK, "RubyGems", "nokogiri", "1.13.10", "1.14.3",
     "`bundle update --conservative nokogiri`", "## RubyGems: `Gemfile.lock`"),
    ("gradle.lockfile", GRADLE_LOCK, "Maven", "com.google.guava:guava", "31.1-jre", "32.0.0-jre",
     "set `com.google.guava:guava` to `32.0.0-jre` in the build script, then `./gradlew dependencies --write-locks`",
     "## Maven (Gradle): `gradle.lockfile`"),
    ("packages.lock.json", NUGET_LOCK, "NuGet", "System.Text.Json", "4.7.0", "8.0.4",
     "`dotnet add package System.Text.Json --version 8.0.4`", "## NuGet: `packages.lock.json`"),
]


@pytest.mark.parametrize(("name", "text", "eco", "pkg", "old", "new", "command", "heading"), PLAN_CASES)
def test_fix_plan_is_plan_only_and_never_edits_the_lockfile(tmp_path: Path, feed: Feed, name: str,  # noqa: F811
                                                            text: str, eco: str, pkg: str, old: str, new: str,
                                                            command: str, heading: str) -> None:
    lock = _write(tmp_path / name, text)
    feed.vulns[(eco, pkg, old)] = ["GHSA-test-0001"]
    feed.records["GHSA-test-0001"] = _adv("GHSA-test-0001", eco, pkg, "0", new)
    before = lock.read_bytes()
    for apply in (False, True):
        res = fix.fix_path(tmp_path, apply=apply, npm=False, allow_dirty=True)
        assert lock.read_bytes() == before and res.changes == [] and res.feed_errors == []
        assert [(c.name, c.old, c.new, c.action) for c in res.lock_plan] == [(pkg, old, new, "plan-only")]
        assert res.exit_code() == 1
    md = res.markdown()
    assert f"{heading} — plan-only (no staged edit)" in md
    assert fix.lockfile_note(name) in md and "do not request `stage-fix`" in md
    assert command in md


def test_fix_plan_writes_no_command_for_an_unusual_fixed_version(tmp_path: Path, feed: Feed,  # noqa: F811
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path / "bun.lock", BUN_LOCK)
    feed.vulns[("npm", "lodash", "4.17.20")] = ["GHSA-test-0002"]
    feed.records["GHSA-test-0002"] = _adv("GHSA-test-0002", "npm", "lodash", "0", "4.17.21")
    monkeypatch.setattr(fix.osv, "minimal_fix", lambda *_a: ("4.17.21`id`", 0))   # the feed is data too
    [row] = fix.plan_lockfile(tmp_path / "bun.lock")
    assert "fix by hand" in row.note and "`id`" not in row.note
    res = fix.FixResult(lock_plan=[row])
    assert "`id`" not in res.markdown() and "(unusual)" in res.markdown()


def test_operator_check_strings_stay(tmp_path: Path, feed: Feed) -> None:  # noqa: F811
    """An operator script greps "(crates.io)" in summary.md and "plan-only (no staged edit)" in fixplan headers."""
    repo = tmp_path / "app"
    (repo / ".git").mkdir(parents=True)
    crates = "registry+https://github.com/rust-lang/crates.io-index"
    _write(repo / "Cargo.lock", f'version = 3\n[[package]]\nname = "smallvec"\nversion = "1.6.0"\nsource = "{crates}"\n')
    _write(repo / "go.mod", "module x\ngo 1.21\nrequire a.example/b v1.0.0\n")
    feed.vulns[("crates.io", "smallvec", "1.6.0")] = ["RUSTSEC-2021-0003"]
    feed.records["RUSTSEC-2021-0003"] = _adv("RUSTSEC-2021-0003", "crates.io", "smallvec", "1.0.0", "1.6.1")
    feed.vulns[("Go", "a.example/b", "1.0.0")] = ["GO-2026-0001"]
    feed.records["GO-2026-0001"] = _adv("GO-2026-0001", "Go", "a.example/b", "0", "1.0.1")
    assert "smallvec (crates.io)" in engine.scan_path(tmp_path).markdown()
    md = fix.fix_path(repo, npm=False).markdown()
    assert "## crates.io: `Cargo.lock` — plan-only (no staged edit)" in md
    assert "## Go: `go.mod` — plan-only (no staged edit)" in md


def test_executor_refuses_stage_fix_where_every_manifest_is_plan_only(tmp_path: Path) -> None:
    from agentteams.vulnscan import executor, executor_guard

    repo = tmp_path / "goapp"
    _write(repo / "svc" / "go.mod", "module x\ngo 1.21\nrequire a.example/b v1.0.0\n")
    _write(repo / "web" / "pnpm-lock.yaml", PNPM_V9)
    with pytest.raises(executor.ExecError, match="only plan-only fixes here"):
        executor_guard.refuse_plan_only("stage-fix", str(repo), executor.ALLOWED_CHANGED)
    _write(repo / "requirements.txt", "requests==2.31.0\n")
    executor_guard.refuse_plan_only("stage-fix", str(repo), executor.ALLOWED_CHANGED)   # editable manifest: allowed


@pytest.mark.parametrize("name", sorted({*lockfiles.FORMATS, "go.sum"}))
def test_validate_diff_allowlist_refuses_every_new_lockfile(name: str) -> None:
    from agentteams.vulnscan import executor

    for rel in (name, f"sub/dir/{name}"):
        assert not executor.ALLOWED_CHANGED.search(rel)
