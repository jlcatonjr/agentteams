"""Tests for agentteams.research.cache — the external-retrieval TTL disk cache.

This cache persists untrusted third-party bytes, so most of what is asserted here is failure
behaviour rather than happy-path storage: a corrupt, oversized, expired, or unwritable entry
must degrade to a plain miss and never raise, because a cache is a performance device and a
broken one must not be able to break the call it was accelerating.

Every test pins the cache to a tmp_path — nothing here touches the real cache directory.
"""

from __future__ import annotations

import json
import time

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
