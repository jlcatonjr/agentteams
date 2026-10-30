"""Tests for agentteams.research.cache — the external-retrieval TTL disk cache.

This cache persists untrusted third-party bytes, so most of what is asserted here is failure
behaviour rather than happy-path storage: a corrupt, oversized, expired, or unwritable entry
must degrade to a plain miss and never raise, because a cache is a performance device and a
broken one must not be able to break the call it was accelerating.

Every test pins the cache to a tmp_path — nothing here touches the real cache directory.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import warnings
from pathlib import Path

import pytest

from agentteams.research import cache


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Enable the cache (conftest disables it suite-wide) and point it at a tmp dir."""
    monkeypatch.delenv(cache.NO_CACHE_ENV, raising=False)
    monkeypatch.setenv(cache.CACHE_DIR_ENV, str(tmp_path / "rcache"))
    return tmp_path / "rcache"


def test_store_then_load_round_trips():
    key = cache.make_key("search", "query", 5)
    assert cache.store(key, {"results": [1, 2, 3]}) is True
    assert cache.load(key) == {"results": [1, 2, 3]}


def test_missing_entry_is_a_miss_not_an_error():
    assert cache.load(cache.make_key("search", "never stored")) is None


def test_expired_entry_is_a_miss():
    key = cache.make_key("search", "q")
    cache.store(key, "value")
    assert cache.load(key, ttl_s=3600) == "value"
    assert cache.load(key, ttl_s=0) is None  # instantly stale


def test_keys_differ_when_any_parameter_differs():
    """A different k must not silently reuse a differently-shaped cached result."""
    assert cache.make_key("search", "q", 5) != cache.make_key("search", "q", 10)
    assert cache.make_key("search", "q", 5) != cache.make_key("scholar", "q", 5)
    assert cache.make_key("search", "q", 5, "ddg") != cache.make_key("search", "q", 5, "brave")


def test_key_is_a_bare_hex_digest_so_no_external_text_reaches_a_path(_isolated_cache):
    """Filenames must not embed anything an external party influenced."""
    nasty = "../../etc/passwd\x00 rm -rf /"
    key = cache.make_key("search", nasty)
    assert len(key) == 64 and all(c in "0123456789abcdef" for c in key)
    cache.store(key, "v")
    written = list(_isolated_cache.glob("*.json"))
    assert len(written) == 1
    assert written[0].name == f"{key}.json"
    assert "passwd" not in written[0].name


def test_corrupt_entry_is_a_miss_not_a_crash(_isolated_cache):
    key = cache.make_key("search", "q")
    cache.store(key, "value")
    (_isolated_cache / f"{key}.json").write_text("{not json at all", encoding="utf-8")
    assert cache.load(key) is None


def test_entry_missing_required_fields_is_a_miss(_isolated_cache):
    key = cache.make_key("search", "q")
    _isolated_cache.mkdir(parents=True, exist_ok=True)
    (_isolated_cache / f"{key}.json").write_text(json.dumps({"value": "v"}), encoding="utf-8")
    assert cache.load(key) is None


def test_entry_with_non_numeric_timestamp_is_a_miss(_isolated_cache):
    key = cache.make_key("search", "q")
    _isolated_cache.mkdir(parents=True, exist_ok=True)
    (_isolated_cache / f"{key}.json").write_text(
        json.dumps({"stored_at": "yesterday", "value": "v"}), encoding="utf-8"
    )
    assert cache.load(key) is None


def test_oversized_value_is_refused_on_write():
    key = cache.make_key("search", "big")
    assert cache.store(key, "x" * (3 * 1024 * 1024)) is False
    assert cache.load(key) is None


def test_unserialisable_value_is_refused_without_raising():
    assert cache.store(cache.make_key("search", "obj"), object()) is False


def test_disable_flag_suppresses_both_read_and_write(monkeypatch, _isolated_cache):
    key = cache.make_key("search", "q")
    cache.store(key, "value")
    monkeypatch.setenv(cache.NO_CACHE_ENV, "1")
    assert cache.cache_enabled() is False
    assert cache.load(key) is None
    assert cache.store(cache.make_key("search", "other"), "v") is False


@pytest.mark.parametrize("raw,expected", [("", True), ("0", True), ("false", True), ("1", False), ("yes", False)])
def test_disable_flag_interpretation(monkeypatch, raw, expected):
    monkeypatch.setenv(cache.NO_CACHE_ENV, raw)
    assert cache.cache_enabled() is expected


def test_no_temp_files_survive_a_successful_write(_isolated_cache):
    """Atomic write must leave exactly the entry, never a stray .tmp."""
    cache.store(cache.make_key("search", "q"), "v")
    assert list(_isolated_cache.glob("*.tmp")) == []


def test_purge_expired_removes_stale_and_corrupt_but_keeps_fresh(_isolated_cache):
    fresh = cache.make_key("search", "fresh")
    stale = cache.make_key("search", "stale")
    cache.store(fresh, "keep")
    _isolated_cache.mkdir(parents=True, exist_ok=True)
    (_isolated_cache / f"{stale}.json").write_text(
        json.dumps({"stored_at": time.time() - 99999, "value": "drop"}), encoding="utf-8"
    )
    (_isolated_cache / "garbage.json").write_text("{{{", encoding="utf-8")

    removed = cache.purge_expired(ttl_s=3600)

    assert removed == 2
    assert cache.load(fresh) == "keep"


def test_purge_on_absent_directory_is_zero_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setenv(cache.CACHE_DIR_ENV, str(tmp_path / "does-not-exist"))
    assert cache.purge_expired() == 0


# --- default location: never a committable path -------------------------------------------

_GIT = shutil.which("git")
needs_git = pytest.mark.skipif(_GIT is None, reason="git not installed")


@pytest.fixture
def default_location(tmp_path, monkeypatch):
    """Drop the env override, reset the per-process memo and warn-once flag, pin XDG_CACHE_HOME."""
    monkeypatch.delenv(cache.CACHE_DIR_ENV, raising=False)
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(cache, "_warned_fallback", False)
    cache._default_cache_dir.cache_clear()
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg))
    yield xdg / "agentteams" / "research"
    cache._default_cache_dir.cache_clear()


def _git_init(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run([_GIT, "init", "-q", str(path)], check=True, capture_output=True)
    return path


def test_env_override_wins_over_every_default(tmp_path, monkeypatch, default_location):
    repo = _git_init(tmp_path / "repo") if _GIT else tmp_path
    monkeypatch.chdir(repo)
    monkeypatch.setenv(cache.CACHE_DIR_ENV, str(tmp_path / "chosen"))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert cache.cache_dir() == tmp_path / "chosen"


def test_outside_git_keeps_in_repo_default(tmp_path, monkeypatch, default_location):
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.chdir(plain)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert cache.cache_dir() == Path.cwd() / "references" / "research-cache"


@needs_git
def test_git_repo_that_ignores_the_path_keeps_in_repo_default(tmp_path, monkeypatch, default_location):
    repo = _git_init(tmp_path / "repo")
    (repo / ".gitignore").write_text("references/research-cache/\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert cache.cache_dir() == Path.cwd() / "references" / "research-cache"


@needs_git
def test_git_repo_without_ignore_rule_uses_user_cache_and_warns_once(
    tmp_path, monkeypatch, default_location
):
    repo = _git_init(tmp_path / "repo")
    monkeypatch.chdir(repo)
    with pytest.warns(UserWarning, match="not gitignored"):
        assert cache.cache_dir() == default_location
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # second call: no second warning
        assert cache.cache_dir() == default_location


@needs_git
def test_store_in_unignored_repo_writes_nothing_under_the_repo(tmp_path, monkeypatch, default_location):
    repo = _git_init(tmp_path / "repo")
    monkeypatch.chdir(repo)
    with pytest.warns(UserWarning):
        assert cache.store(cache.make_key("search", "q"), "v") is True
    assert not (repo / "references").exists()
    assert len(list(default_location.glob("*.json"))) == 1


@needs_git
def test_subdirectory_of_unignored_repo_uses_user_cache(tmp_path, monkeypatch, default_location):
    repo = _git_init(tmp_path / "repo")
    sub = repo / "a" / "b"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    with pytest.warns(UserWarning):
        assert cache.cache_dir() == default_location


@needs_git
def test_hook_git_env_does_not_redirect_the_probe(tmp_path, monkeypatch, default_location):
    """A hook's GIT_DIR pointing at a repo that ignores the path must not make ours look ignored."""
    other = _git_init(tmp_path / "other")
    (other / ".gitignore").write_text("references/\n", encoding="utf-8")
    repo = _git_init(tmp_path / "repo")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.chdir(repo)
    with pytest.warns(UserWarning):
        assert cache.cache_dir() == default_location


def test_git_missing_fails_toward_user_cache(tmp_path, monkeypatch, default_location):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)  # looks like a work tree; git itself is unavailable

    def _no_git(*_a, **_kw):
        raise FileNotFoundError("git")

    monkeypatch.setattr(cache.subprocess, "run", _no_git)
    monkeypatch.chdir(repo)
    with pytest.warns(UserWarning, match="FileNotFoundError"):
        assert cache.cache_dir() == default_location


def test_git_timeout_fails_toward_user_cache(tmp_path, monkeypatch, default_location):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)

    def _hang(*_a, **_kw):
        raise subprocess.TimeoutExpired(cmd="git", timeout=5)

    monkeypatch.setattr(cache.subprocess, "run", _hang)
    monkeypatch.chdir(repo)
    with pytest.warns(UserWarning, match="TimeoutExpired"):
        assert cache.cache_dir() == default_location


def test_git_error_exit_fails_toward_user_cache(tmp_path, monkeypatch, default_location):
    """Exit 128 (e.g. "dubious ownership") is an error, not "ignored"."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    fake = subprocess.CompletedProcess(args=["git"], returncode=128, stdout="",
                                       stderr="fatal: detected dubious ownership\n")
    monkeypatch.setattr(cache.subprocess, "run", lambda *_a, **_kw: fake)
    monkeypatch.chdir(repo)
    with pytest.warns(UserWarning, match="dubious ownership"):
        assert cache.cache_dir() == default_location


def test_probe_runs_once_per_cwd(tmp_path, monkeypatch, default_location):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    calls = []

    def _ignored(argv, **_kw):
        calls.append(argv)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cache.subprocess, "run", _ignored)
    monkeypatch.chdir(repo)
    for _ in range(3):
        assert cache.cache_dir() == Path.cwd() / "references" / "research-cache"
    assert len(calls) == 1
    assert calls[0][:2] == ["git", "-C"]  # argument list, no shell


@pytest.mark.parametrize(
    ("platform", "xdg", "expected_parts"),
    [
        ("linux", None, (".cache", "agentteams", "research")),
        ("darwin", None, ("Library", "Caches", "agentteams", "research")),
        ("linux", "relative/ignored", (".cache", "agentteams", "research")),
    ],
)
def test_user_cache_dir_platform_defaults(tmp_path, monkeypatch, platform, xdg, expected_parts):
    monkeypatch.setattr(cache.sys, "platform", platform)
    monkeypatch.setenv("HOME", str(tmp_path))
    if xdg is None:
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_CACHE_HOME", xdg)
    assert cache.user_cache_dir() == tmp_path.joinpath(*expected_parts)
