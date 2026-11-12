"""The carve-out follow-ups (2026-11-12): the exception registry as a trust root, and carve-outs in the exception cap.

The sandbox side (both files governed, stubbed and protect-if-present) is covered with the other control-plane names
in ``tests/test_prd_trust_roots.py``, ``tests/test_team_dir_control_plane.py`` and
``tests/test_sandbox_ancestor_rename.py``.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from agentteams.carveout_checks import active_carveouts_for
from agentteams.carveouts import CarveoutError
from agentteams.cli import exception_registry as er

TODAY = dt.date(2026, 11, 12)
NOW = dt.datetime(2026, 11, 12, 12, tzinfo=dt.timezone.utc)


def _project(tmp_path: Path, entries: list[dict] | None, *, decision_log: str = ".claude/agents") -> Path:
    """A project with a team dir at .claude/agents and, unless *entries* is None, a carve-out registry."""
    (tmp_path / ".claude" / "agents" / "references").mkdir(parents=True)
    if entries is not None:
        (tmp_path / ".agentteams").mkdir()
        (tmp_path / ".agentteams" / "carveouts.json").write_text(json.dumps(
            {"version": 1, "sequence": 1, "decision_log": decision_log, "carveouts": entries}), encoding="utf-8")
    return tmp_path


def _entry(cid: str, *, status: str = "active", expires: object = "2026-12-01") -> dict:
    return {"id": cid, "status": status, "expires": expires}


# ---------------------------------------------------------------------------------------------- counting


def test_counts_active_unexpired_entries_pinned_to_the_team(tmp_path):
    root = _project(tmp_path, [_entry("a"), _entry("b", expires="2026-11-12"), _entry("old", expires="2026-11-11"),
                               _entry("off", status="expired"), "not-an-entry"])
    assert active_carveouts_for(root / ".claude" / "agents", today=TODAY) == 2


def test_an_entry_without_a_readable_expiry_counts(tmp_path):
    """Counting more is the fail-closed direction for a cap."""
    root = _project(tmp_path, [_entry("a", expires=None), _entry("b", expires="soon")])
    assert active_carveouts_for(root / ".claude" / "agents", today=TODAY) == 2


@pytest.mark.parametrize("case", ["no-registry", "other-team", "not-a-team-dir"])
def test_counts_nothing_that_is_not_pinned_here(tmp_path, case):
    root = _project(tmp_path, None if case == "no-registry" else [_entry("a")],
                    decision_log=".github/agents" if case == "other-team" else ".claude/agents")
    team = root / "references" if case == "not-a-team-dir" else root / ".claude" / "agents"
    assert active_carveouts_for(team, today=TODAY) == 0


def test_an_unreadable_registry_raises(tmp_path):
    root = _project(tmp_path, [])
    (root / ".agentteams" / "carveouts.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(CarveoutError):
        active_carveouts_for(root / ".claude" / "agents", today=TODAY)


# ---------------------------------------------------------------------------------------------- the exception cap


def test_minting_an_exception_counts_the_active_carve_outs(tmp_path):
    """The default cap is 1: one active carve-out leaves no room for an exception."""
    team = _project(tmp_path, [_entry("a")]) / ".claude" / "agents"
    with pytest.raises(er.ExceptionRegistryError, match="carve-out"):
        er.assert_within_aggregate_cap(team, now=NOW)
    er.write_registry(team, {"version": 1, "cap": {"max_active_relaxing": 2}, "exceptions": []})
    er.assert_within_aggregate_cap(team, now=NOW)


def test_minting_without_carve_outs_is_unchanged(tmp_path):
    team = _project(tmp_path, [_entry("a", status="expired")]) / ".claude" / "agents"
    er.assert_within_aggregate_cap(team, now=NOW)


def test_an_unreadable_carve_out_registry_refuses_minting(tmp_path):
    root = _project(tmp_path, [])
    (root / ".agentteams" / "carveouts.json").write_text("[]", encoding="utf-8")
    with pytest.raises(er.ExceptionRegistryError, match="can't be counted"):
        er.assert_within_aggregate_cap(root / ".claude" / "agents", now=NOW)


def test_the_audit_counts_carve_outs(tmp_path):
    team = _project(tmp_path, [_entry("a"), _entry("b")]) / ".claude" / "agents"
    er.write_registry(team, {"version": 1, "cap": {"max_active_relaxing": 1}, "exceptions": []})
    problems = er.audit_exceptions(team, now=NOW)
    assert problems == ["active relaxing exceptions (0) plus active carve-outs (2) exceed the cap (1)"]


def test_the_audit_reports_an_unreadable_carve_out_registry(tmp_path):
    root = _project(tmp_path, [])
    (root / ".agentteams" / "carveouts.json").write_text("{", encoding="utf-8")
    assert any("can't be counted" in p for p in er.audit_exceptions(root / ".claude" / "agents", now=NOW))


def test_the_cli_audit_fails_on_a_carve_out_breach(tmp_path, capsys):
    import build_team

    team = _project(tmp_path, [_entry("a"), _entry("b")]) / ".claude" / "agents"
    er.write_registry(team, {"version": 1, "cap": {}, "exceptions": []})
    assert build_team.main(["--audit-exceptions", "--output", str(team)]) == 1
    assert "active carve-outs (2)" in capsys.readouterr().err


# ---------------------------------------------------------------------------------------------- trust root


@pytest.mark.parametrize("path", ["references/exception-registry.json", ".claude/agents/references/Exception_Registry.json",
                                  "exceptionRegistry.json"])
def test_the_exception_registry_is_a_governance_root(path):
    from agentteams.cli.governance_targets import target_is_governance_root

    assert target_is_governance_root(path)


def test_the_exception_registry_module_is_not(tmp_path):
    """The stem names the file, not ``agentteams/cli/exception_registry.py``."""
    from agentteams.cli.governance_targets import target_is_governance_root

    assert not target_is_governance_root("agentteams/cli/exception_registry.py")


def test_a_handoff_or_decision_row_naming_the_exception_registry_is_refused():
    from agentteams.cli.effect_classifier import non_eligibility_reason
    from agentteams.sandbox_handoff import EXTRA_TRUST_ROOT_TOKENS

    assert "exception-registry.json" in EXTRA_TRUST_ROOT_TOKENS
    row = {"action_reviewed": "edit", "verdict": "PASS", "scope": "edit",
           "effect_targets": ".claude/agents/references/exception-registry.json"}
    assert non_eligibility_reason(row, kind="decision")
