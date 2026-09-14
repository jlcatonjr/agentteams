"""Tests for the aggregate/proliferation guard (exception_registry.py, Workstream C — G1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams.cli import exception_registry as er

_NOW = "2026-09-14T00:00:00Z"
_FUTURE = "2099-01-01T00:00:00Z"
_PAST = "2000-01-01T00:00:00Z"


def _write(root: Path, registry: dict) -> None:
    (root / "references").mkdir(parents=True, exist_ok=True)
    (root / er.EXCEPTION_REGISTRY_REL).write_text(json.dumps(registry), encoding="utf-8")


def _entry(id_: str, **over) -> dict:
    base = {"id": id_, "scope": "x", "status": "active", "expires": _FUTURE,
            "max_activations": 5, "activations": 0, "relaxing": True}
    base.update(over)
    return base


# --- load / shape -----------------------------------------------------------------------------


def test_absent_registry_is_empty_skeleton(tmp_path):
    reg = er.load_registry(tmp_path)
    assert reg["exceptions"] == []


def test_malformed_json_fails_closed(tmp_path):
    (tmp_path / "references").mkdir()
    (tmp_path / er.EXCEPTION_REGISTRY_REL).write_text("{not json", encoding="utf-8")
    with pytest.raises(er.ExceptionRegistryError):
        er.load_registry(tmp_path)


def test_wrong_shape_fails_closed(tmp_path):
    _write(tmp_path, {"version": 1, "exceptions": "nope"})
    with pytest.raises(er.ExceptionRegistryError):
        er.load_registry(tmp_path)


# --- active-set accounting --------------------------------------------------------------------


def test_active_excludes_terminal_expired_exhausted(tmp_path):
    reg = {"cap": {"max_active_relaxing": 10}, "exceptions": [
        _entry("a"),
        _entry("b", status="declined"),
        _entry("c", expires=_PAST),
        _entry("d", max_activations=2, activations=2),
    ]}
    active = er.active_relaxing_exceptions(reg, now=None)
    assert [e["id"] for e in active] == ["a"]


def test_non_relaxing_entry_not_counted(tmp_path):
    reg = {"cap": {"max_active_relaxing": 10}, "exceptions": [_entry("a", relaxing=False)]}
    assert er.active_relaxing_exceptions(reg) == []


def test_missing_relaxing_field_counts_as_relaxing(tmp_path):
    e = _entry("a")
    del e["relaxing"]
    reg = {"cap": {"max_active_relaxing": 10}, "exceptions": [e]}
    assert len(er.active_relaxing_exceptions(reg)) == 1


# --- aggregate cap (G1) -----------------------------------------------------------------------


def test_cap_blocks_activation_over_limit(tmp_path):
    _write(tmp_path, {"cap": {"max_active_relaxing": 1}, "exceptions": [_entry("a")]})
    with pytest.raises(er.ExceptionRegistryError, match="over the cap"):
        er.assert_within_aggregate_cap(tmp_path, adding=1)


def test_cap_allows_activation_within_limit(tmp_path):
    _write(tmp_path, {"cap": {"max_active_relaxing": 2}, "exceptions": [_entry("a")]})
    er.assert_within_aggregate_cap(tmp_path, adding=1)  # 1 active + 1 = 2 == cap, allowed


def test_default_cap_is_one(tmp_path):
    _write(tmp_path, {"exceptions": [_entry("a")]})
    with pytest.raises(er.ExceptionRegistryError, match="cap of 1"):
        er.assert_within_aggregate_cap(tmp_path, adding=1)


def test_lapsed_active_exception_blocks_cap_check(tmp_path):
    # An active-but-expired entry makes the whole guard refuse (stale registry, untrustworthy).
    _write(tmp_path, {"cap": {"max_active_relaxing": 5},
                      "exceptions": [_entry("stale", expires=_PAST)]})
    with pytest.raises(er.ExceptionRegistryError, match="active-but-expired"):
        er.assert_within_aggregate_cap(tmp_path, adding=1)


def test_bad_cap_value_fails_closed(tmp_path):
    _write(tmp_path, {"cap": {"max_active_relaxing": "lots"}, "exceptions": []})
    with pytest.raises(er.ExceptionRegistryError):
        er.assert_within_aggregate_cap(tmp_path, adding=1)


# --- audit / list -----------------------------------------------------------------------------


def test_audit_reports_lapsed_and_is_clean_otherwise(tmp_path):
    _write(tmp_path, {"cap": {"max_active_relaxing": 5}, "exceptions": [_entry("a")]})
    assert er.audit_exceptions(tmp_path) == []
    _write(tmp_path, {"cap": {"max_active_relaxing": 5},
                      "exceptions": [_entry("stale", expires=_PAST)]})
    problems = er.audit_exceptions(tmp_path)
    assert any("expired" in p for p in problems)


def test_malformed_expires_fails_closed(tmp_path):
    _write(tmp_path, {"cap": {"max_active_relaxing": 5},
                      "exceptions": [_entry("a", expires="not-a-date")]})
    problems = er.audit_exceptions(tmp_path)
    assert any("malformed expires" in p for p in problems)


def test_write_then_read_roundtrip(tmp_path):
    reg = {"version": 1, "cap": {"max_active_relaxing": 3}, "exceptions": [_entry("a")]}
    er.write_registry(tmp_path, reg)
    assert er.list_exceptions(tmp_path)[0]["id"] == "a"


def test_write_refuses_malformed(tmp_path):
    with pytest.raises(er.ExceptionRegistryError):
        er.write_registry(tmp_path, {"exceptions": "nope"})


# --- Workstream F: declined terminal state + re-mint block + chain-id --------------------------


def test_register_exception_activates_within_cap(tmp_path):
    _write(tmp_path, {"cap": {"max_active_relaxing": 2}, "exceptions": []})
    er.register_exception(tmp_path, {"id": "e1", "scope": "grant-x", "expires": _FUTURE})
    assert er.list_exceptions(tmp_path)[0]["status"] == "active"


def test_register_exception_blocked_by_cap(tmp_path):
    _write(tmp_path, {"cap": {"max_active_relaxing": 1}, "exceptions": [_entry("a")]})
    with pytest.raises(er.ExceptionRegistryError, match="over the cap"):
        er.register_exception(tmp_path, {"id": "e2", "scope": "grant-x", "expires": _FUTURE})


def test_declined_is_terminal_and_not_active(tmp_path):
    _write(tmp_path, {"cap": {"max_active_relaxing": 5}, "exceptions": [_entry("a")]})
    er.mark_declined(tmp_path, "a", reason="unsafe", chain_id="chain-1")
    assert er.active_relaxing_exceptions(er.load_registry(tmp_path)) == []
    entry = er.list_exceptions(tmp_path)[0]
    assert entry["status"] == "declined" and entry["declined_reason"] == "unsafe"


def test_declined_cannot_be_silently_reminted(tmp_path):
    _write(tmp_path, {"cap": {"max_active_relaxing": 5}, "exceptions": [_entry("a")]})
    er.mark_declined(tmp_path, "a", reason="unsafe")
    with pytest.raises(er.ExceptionRegistryError, match="silently re-mint"):
        er.register_exception(tmp_path, {"id": "a", "scope": "grant-x", "expires": _FUTURE})


def test_declined_reactivation_requires_reason(tmp_path):
    _write(tmp_path, {"cap": {"max_active_relaxing": 5}, "exceptions": [_entry("a")]})
    er.mark_declined(tmp_path, "a", reason="unsafe")
    with pytest.raises(er.ExceptionRegistryError, match="reactivation_reason"):
        er.register_exception(
            tmp_path, {"id": "a", "scope": "grant-x", "expires": _FUTURE}, reactivate=True
        )
    # with a reason, reactivation succeeds
    er.register_exception(
        tmp_path,
        {"id": "a", "scope": "grant-x", "expires": _FUTURE, "reactivation_reason": "root cause fixed"},
        reactivate=True,
    )
    assert er.list_exceptions(tmp_path)[0]["status"] == "active"


def test_mark_declined_requires_reason_and_known_id(tmp_path):
    _write(tmp_path, {"cap": {"max_active_relaxing": 5}, "exceptions": [_entry("a")]})
    with pytest.raises(er.ExceptionRegistryError, match="requires a reason"):
        er.mark_declined(tmp_path, "a", reason="")
    with pytest.raises(er.ExceptionRegistryError, match="unknown exception id"):
        er.mark_declined(tmp_path, "ghost", reason="x")


def test_same_chain():
    assert er.same_chain("c1", "c1") is True
    assert er.same_chain("c1", "c2") is False
    assert er.same_chain("", "") is False  # unknown chain never auto-matches
