"""test_vulnscan_private_sources.py — names a repository's tooling marks private never go to OSV (security review, M).

Go (``GOPRIVATE`` / ``GONOPROXY`` / ``GONOSUMDB`` with Go's prefix-glob rule), NuGet (a ``nuget.config`` naming a
non-nuget.org source), pnpm/Bun (``.npmrc`` scope registries) and the ``gradle.lockfile`` opt-in. No network: OSV
is the canned :class:`tests.test_vulnscan_core.Feed`, wrapped to record every name queried.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentteams.vulnscan import _http, engine, fix, private_sources
from tests.test_vulnscan_core import Feed, feed  # noqa: F401  (feed is a fixture)
from tests.test_vulnscan_lockfiles import BUN_LOCK, GO_MOD, GRADLE_LOCK, NUGET_LOCK, PNPM_V9


@pytest.fixture
def sent(feed: Feed, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:  # noqa: F811
    """Every (ecosystem, name) sent to OSV's querybatch."""
    names: list[tuple[str, str]] = []

    def spy(url: str, payload: dict | None = None, **kw: object) -> object:
        if url.endswith("/querybatch") and payload:
            names.extend((q["package"]["ecosystem"], q["package"]["name"]) for q in payload["queries"])
        return feed(url, payload, **kw)

    monkeypatch.setattr(_http, "fetch_json", spy)
    return names


def _repo(root: Path, files: dict[str, str] | None = None) -> Path:
    repo = root / "app"
    (repo / ".git").mkdir(parents=True)
    for name, text in (files or {}).items():
        (repo / name).write_text(text, encoding="utf-8")
    return repo


# ---------------------------------------------------------------- Go


@pytest.mark.parametrize(("globs", "module", "hit"), [
    ("github.com/quoted", "github.com/quoted/mod", True),
    ("github.com/quoted/", "github.com/quoted/mod/v2", True),
    ("*.corp.example", "git.corp.example/team/x", True),
    ("github.com/*/mod", "github.com/quoted/mod", True),
    ("github.com/*/mod", "github.com/quoted/other", False),
    ("github.com/qu?ted", "github.com/quoted/mod", True),
    ("github.com/[a-p]*", "github.com/quoted/mod", False),
    ("github.com/[!a-p]*", "github.com/quoted/mod", True),
    ("github.com", "github.com/gin-gonic/gin", True),
    ("github.com/gin-gonic/gin/extra", "github.com/gin-gonic/gin", False),   # more elements than the module
    ("example.com/a,,example.org", "example.org/x", True),
    ("*", "github.com/x", True),            # one element: matches every first element
    ("*/x", "github.com/y", False),
    ("", "github.com/x", False),
    ("github.com/[", "github.com/x", True),  # malformed: withheld, never sent
])
def test_go_private_match_follows_go_prefix_globs(globs: str, module: str, hit: bool) -> None:
    assert private_sources.go_private_match(globs, module) is hit


def test_go_patterns_come_from_the_environment_and_go_env_run_from_root(tmp_path: Path,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    go = bindir / "go"
    go.write_text('#!/bin/sh\n[ "$(pwd)" = / ] && [ "$GOTOOLCHAIN" = local ] && [ -z "$GOFLAGS" ] || exit 9\n'
                  'printf "corp.example/*\\n\\nonly.sumdb/x\\n"\n')
    go.chmod(0o755)
    env = {"PATH": str(bindir), "GOPRIVATE": "env.example", "GOFLAGS": "-mod=mod", "GOTOOLCHAIN": "auto"}
    assert private_sources.go_private_patterns(env) == "env.example,corp.example/*,only.sumdb/x"
    assert private_sources.go_private_patterns({"PATH": str(tmp_path / "none"), "GONOSUMDB": "a.b"}) == "a.b"
    go.write_text("#!/bin/sh\nexit 1\n")           # a failing go env adds nothing; the environment still applies
    assert private_sources.go_private_patterns({"PATH": str(bindir), "GONOPROXY": "x.y"}) == "x.y"

    def boom(*a: object, **kw: object) -> object:
        raise subprocess.TimeoutExpired("go", 10)

    monkeypatch.setattr(private_sources.subprocess, "run", boom)
    assert private_sources.go_private_patterns({"PATH": str(bindir), "GOPRIVATE": "p.q"}) == "p.q"


def test_scan_withholds_goprivate_modules(tmp_path: Path, sent: list[tuple[str, str]],
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    _repo(tmp_path, {"go.mod": GO_MOD})
    monkeypatch.setattr(private_sources, "go_private_patterns", lambda: "github.com/quoted,golang.org/x")
    rep = engine.scan_path(tmp_path)
    assert sorted(rep.withheld) == ["app: github.com/quoted/mod (Go)", "app: golang.org/x/net (Go)"]
    names = {n for _e, n in sent}
    assert names and not any(n.startswith(("github.com/quoted", "golang.org/x")) for n in names)
    assert "github.com/gin-gonic/gin" in names


def test_fix_plan_withholds_goprivate_modules(tmp_path: Path, sent: list[tuple[str, str]],
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "go.mod").write_text(GO_MOD, encoding="utf-8")
    monkeypatch.setattr(private_sources, "go_private_patterns", lambda: "github.com/quoted")
    fix.fix_path(tmp_path, npm=False, allow_dirty=True)
    names = {n for _e, n in sent}
    assert names and "github.com/quoted/mod" not in names and "github.com/gin-gonic/gin" in names


# ---------------------------------------------------------------- NuGet

NUGET_PRIVATE = """<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <packageSources>
    <clear />
    <add key="nuget.org" value="https://api.nuget.org/v3/index.json" />
    <add key="corp" value="https://pkgs.dev.azure.com/corp/_packaging/feed/nuget/v3/index.json" />
  </packageSources>
</configuration>
"""
NUGET_PUBLIC = NUGET_PRIVATE.replace(
    '    <add key="corp" value="https://pkgs.dev.azure.com/corp/_packaging/feed/nuget/v3/index.json" />\n', "")


@pytest.mark.parametrize(("config", "where", "withheld"), [
    (NUGET_PRIVATE, "repo", True),
    (NUGET_PRIVATE, "root", True),          # a parent within the scan root counts
    (NUGET_PUBLIC, "repo", False),
    ("<configuration><packageSources>", "repo", True),            # unreadable: fail closed
    ('<!DOCTYPE x [<!ENTITY a "b">]><configuration/>', "repo", True),
])
def test_nuget_config_naming_another_source_withholds_the_repositorys_packages(
        tmp_path: Path, sent: list[tuple[str, str]], config: str, where: str, withheld: bool) -> None:
    repo = _repo(tmp_path, {"packages.lock.json": NUGET_LOCK})
    (repo if where == "repo" else tmp_path).joinpath("NuGet.Config").write_text(config, encoding="utf-8")
    rep = engine.scan_path(tmp_path)
    nuget_sent = [n for e, n in sent if e == "NuGet"]
    if withheld:
        assert nuget_sent == [] and rep.withheld and all(w.endswith("(NuGet)") for w in rep.withheld)
    else:
        assert nuget_sent and rep.withheld == []


def test_nuget_config_above_the_scan_root_is_not_read(tmp_path: Path, sent: list[tuple[str, str]]) -> None:
    (tmp_path / "nuget.config").write_text(NUGET_PRIVATE, encoding="utf-8")
    root = tmp_path / "scan"
    _repo(root, {"packages.lock.json": NUGET_LOCK})
    rep = engine.scan_path(root)
    assert rep.withheld == [] and [n for e, n in sent if e == "NuGet"]


# ---------------------------------------------------------------- pnpm / Bun: .npmrc scopes


@pytest.mark.parametrize(("lock", "text"), [("pnpm-lock.yaml", PNPM_V9), ("bun.lock", BUN_LOCK)])
def test_npmrc_scope_registry_withholds_that_scope(tmp_path: Path, sent: list[tuple[str, str]], lock: str,
                                                    text: str) -> None:
    repo = _repo(tmp_path)
    (repo / lock).write_text(text, encoding="utf-8")
    scope = "@babel" if lock == "pnpm-lock.yaml" else "@types"
    (repo / ".npmrc").write_text(f"{scope}:registry=https://npm.corp.example/\n"
                                 "@public:registry=https://registry.npmjs.org/\n", encoding="utf-8")
    rep = engine.scan_path(tmp_path)
    names = [n for e, n in sent if e == "npm"]
    assert names and not any(n.startswith(scope + "/") for n in names)
    assert rep.withheld and all(w.startswith(f"app: {scope}/") for w in rep.withheld)


def test_npmrc_default_registry_withholds_every_pnpm_name(tmp_path: Path, sent: list[tuple[str, str]]) -> None:
    repo = _repo(tmp_path, {"pnpm-lock.yaml": PNPM_V9})
    (repo / ".npmrc").write_text("registry=https://npm.corp.example/\n", encoding="utf-8")
    rep = engine.scan_path(tmp_path)
    assert [n for e, n in sent if e == "npm"] == [] and rep.withheld


def test_npmrc_public_registry_withholds_nothing(tmp_path: Path, sent: list[tuple[str, str]]) -> None:
    repo = _repo(tmp_path)
    (repo / "bun.lock").write_text(BUN_LOCK, encoding="utf-8")
    (repo / ".npmrc").write_text("registry=https://registry.npmjs.org/\n@types:registry=https://registry.npmjs.org\n",
                                 encoding="utf-8")
    rep = engine.scan_path(tmp_path)
    assert rep.withheld == [] and ("npm", "@types/node") in sent


# ---------------------------------------------------------------- Gradle: opt-in


def test_gradle_lockfile_is_not_read_by_default(tmp_path: Path, sent: list[tuple[str, str]]) -> None:
    _repo(tmp_path)
    (tmp_path / "app" / "gradle.lockfile").write_text(GRADLE_LOCK, encoding="utf-8")
    rep = engine.scan_path(tmp_path)
    assert rep.unscannable == [("app/gradle.lockfile", private_sources.GRADLE_OPT_IN)]
    assert rep.unscannable[0][1].startswith("gradle.lockfile not read (opt-in: ")
    assert sent == []
    res = fix.fix_path(tmp_path, npm=False, allow_dirty=True)
    assert res.refused == [("app/gradle.lockfile", private_sources.GRADLE_OPT_IN)] and sent == []
    rep = engine.scan_path(tmp_path, gradle_lockfiles=True)
    assert ("Maven", "com.google.guava:guava") in sent and not rep.unscannable
