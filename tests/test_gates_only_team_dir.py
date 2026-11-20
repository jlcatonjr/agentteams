"""#578: a gates-only team dir for signed sign-off rows, and which HALTs bind a clearance checked there.

@security ruling (2026-11-20): a HALT in the repository's canonical team dirs, or in a declared ``parent_team``,
binds a clearance checked in any team dir; the link and the canonical dirs are resolved fail-closed. A dir outside
a git repository keeps today's behaviour (only its own log), and refuses if it declares a parent.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import pytest

from agentteams.cli import clearance_binding as cb

_COLS = ["timestamp", "requesting_agent", "action_reviewed", "verdict", "conditions", "conditions_verified"]


def _team(path: Path, *, privilege: dict | None = None, build_log: bool = False) -> Path:
    refs = path / "references"
    refs.mkdir(parents=True, exist_ok=True)
    if privilege is not None or not build_log:
        (refs / "agent-privilege.json").write_text(json.dumps(privilege or {}), encoding="utf-8")
    if build_log:
        (refs / "build-log.json").write_text("{}", encoding="utf-8")
    return path


def _decisions(team: Path, rows: list[tuple[str, str]]) -> None:
    (team / "references").mkdir(parents=True, exist_ok=True)
    with (team / "references" / "security-decisions.log.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_COLS)
        writer.writeheader()
        for action, verdict in rows:
            writer.writerow({"timestamp": "2026-11-20T00:00:00Z", "requesting_agent": "security",
                             "action_reviewed": action, "verdict": verdict, "conditions": "",
                             "conditions_verified": ""})


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    return root


@pytest.fixture()
def script(tmp_path: Path) -> Path:
    path = tmp_path / "gate-s5.manifest"
    path.write_text("manifest v1\n")
    return path


def _gates(repo: Path, script: Path, *, privilege: dict | None = None) -> Path:
    gates = _team(repo / ".github" / "survey-gates", privilege=privilege or {})
    _decisions(gates, [(cb.clearance_action("survey-s5", script), "PASS")])
    return gates


def test_a_gates_only_dir_clears_while_the_main_log_stays_unsigned(repo: Path, script: Path) -> None:
    main = _team(repo / ".github" / "agents", build_log=True)
    _decisions(main, [("unrelated-scope", "PASS")])  # unsigned legacy-shaped log, never migrated
    gates = _gates(repo, script)
    assert cb.check_file_clearance(gates, "survey-s5", script) == (True, "")
    assert (main / "references" / "security-decisions.log.csv").read_text().splitlines()[0].count("signature") == 0


def test_a_wrong_manifest_byte_fails(repo: Path, script: Path) -> None:
    gates = _gates(repo, script)
    script.write_text("manifest v2\n")
    assert cb.check_file_clearance(gates, "survey-s5", script)[0] is False


def test_a_canonical_team_halt_binds_without_any_link(repo: Path, script: Path) -> None:
    main = _team(repo / ".claude" / "agents", build_log=True)
    _decisions(main, [("survey-s5", "HALT")])
    gates = _gates(repo, script)
    ok, reason = cb.check_file_clearance(gates, "survey-s5", script)
    assert ok is False and "HALT" in reason


def test_a_canonical_halt_on_the_bound_action_binds(repo: Path, script: Path) -> None:
    main = _team(repo / ".github" / "agents", build_log=True)
    _decisions(main, [(cb.clearance_action("survey-s5", script), "HALT")])
    gates = _gates(repo, script)
    assert cb.check_file_clearance(gates, "survey-s5", script)[0] is False


def test_an_unrelated_canonical_halt_does_not_bind(repo: Path, script: Path) -> None:
    """NEGATIVE CONTROL."""
    main = _team(repo / ".github" / "agents", build_log=True)
    _decisions(main, [("other-scope", "HALT")])
    gates = _gates(repo, script)
    assert cb.check_file_clearance(gates, "survey-s5", script) == (True, "")


def test_a_declared_parent_halt_binds(repo: Path, script: Path) -> None:
    parent = _team(repo / "governance" / "team", build_log=True)
    _decisions(parent, [("survey-s5", "HALT")])
    gates = _gates(repo, script, privilege={"parent_team": "governance/team"})
    assert cb.check_file_clearance(gates, "survey-s5", script)[0] is False
    _decisions(parent, [("survey-s5", "PASS")])
    assert cb.check_file_clearance(gates, "survey-s5", script) == (True, "")


def test_a_parent_outside_the_canonical_dirs_binds_only_when_declared(repo: Path, script: Path) -> None:
    parent = _team(repo / "governance" / "team", build_log=True)
    _decisions(parent, [("survey-s5", "HALT")])
    gates = _gates(repo, script)
    assert cb.check_file_clearance(gates, "survey-s5", script) == (True, "")  # documented limit


@pytest.mark.parametrize("declared", ["../outside", "/abs/team", "governance/../../x", "missing/team", ""])
def test_a_bad_parent_path_refuses(repo: Path, script: Path, declared: str) -> None:
    gates = _gates(repo, script, privilege={"parent_team": declared})
    ok, reason = cb.check_file_clearance(gates, "survey-s5", script)
    assert ok is False and "parent_team" in reason


def test_a_parent_that_is_not_a_team_dir_refuses(repo: Path, script: Path) -> None:
    (repo / "plain" / "dir").mkdir(parents=True)
    gates = _gates(repo, script, privilege={"parent_team": "plain/dir"})
    assert cb.check_file_clearance(gates, "survey-s5", script)[0] is False


def test_a_symlinked_parent_refuses(repo: Path, script: Path) -> None:
    real = _team(repo / "real" / "team", build_log=True)
    os.symlink(real, repo / "linked")
    gates = _gates(repo, script, privilege={"parent_team": "linked"})
    ok, reason = cb.check_file_clearance(gates, "survey-s5", script)
    assert ok is False and "symbolic link" in reason


def test_a_parent_naming_itself_refuses(repo: Path, script: Path) -> None:
    gates = _gates(repo, script, privilege={"parent_team": ".github/survey-gates"})
    ok, reason = cb.check_file_clearance(gates, "survey-s5", script)
    assert ok is False and "itself" in reason


def test_a_chained_parent_refuses(repo: Path, script: Path) -> None:
    _team(repo / "a" / "team", privilege={"parent_team": "b/team"})
    _team(repo / "b" / "team", build_log=True)
    gates = _gates(repo, script, privilege={"parent_team": "a/team"})
    ok, reason = cb.check_file_clearance(gates, "survey-s5", script)
    assert ok is False and "one level" in reason


def test_a_symlinked_canonical_dir_refuses(repo: Path, script: Path, tmp_path: Path) -> None:
    elsewhere = _team(tmp_path / "elsewhere" / "agents", build_log=True)
    (repo / ".claude").mkdir()
    os.symlink(elsewhere, repo / ".claude" / "agents")
    gates = _gates(repo, script)
    ok, reason = cb.check_file_clearance(gates, "survey-s5", script)
    assert ok is False and "symbolic link" in reason


def test_an_unreadable_canonical_log_refuses(repo: Path, script: Path) -> None:
    main = _team(repo / ".github" / "agents", build_log=True)
    (main / "references" / "security-decisions.log.csv").write_text(
        ",".join(_COLS) + "\n2026-11-20,security,survey-s5,\"HALT\n")  # an open quote can hide a HALT
    gates = _gates(repo, script)
    assert cb.check_file_clearance(gates, "survey-s5", script)[0] is False


def test_a_canonical_dir_without_a_log_adds_nothing(repo: Path, script: Path) -> None:
    _team(repo / ".github" / "agents", build_log=True)
    gates = _gates(repo, script)
    assert cb.check_file_clearance(gates, "survey-s5", script) == (True, "")


def test_outside_a_repo_only_the_team_dir_is_scanned(tmp_path: Path, script: Path) -> None:
    gates = _team(tmp_path / "loose" / "gates")
    _decisions(gates, [(cb.clearance_action("survey-s5", script), "PASS")])
    assert cb.halt_scan_dirs(gates) == [gates]
    assert cb.check_file_clearance(gates, "survey-s5", script) == (True, "")


def test_outside_a_repo_a_declared_parent_refuses(tmp_path: Path, script: Path) -> None:
    gates = _team(tmp_path / "loose" / "gates", privilege={"parent_team": "team"})
    _decisions(gates, [(cb.clearance_action("survey-s5", script), "PASS")])
    ok, reason = cb.check_file_clearance(gates, "survey-s5", script)
    assert ok is False and "git repository" in reason


def test_a_privilege_only_main_team_without_a_halt_clears_as_before(repo: Path, script: Path) -> None:
    """A real team with agent-privilege.json and no build-log.json is not reclassified."""
    main = _team(repo / ".claude" / "agents")
    _decisions(main, [(cb.clearance_action("survey-s5", script), "PASS")])
    assert cb.halt_scan_dirs(main) == [main]
    assert cb.check_file_clearance(main, "survey-s5", script) == (True, "")


def test_a_main_team_halt_in_the_other_canonical_dir_binds(repo: Path, script: Path) -> None:
    claude = _team(repo / ".claude" / "agents", build_log=True)
    _decisions(claude, [(cb.clearance_action("survey-s5", script), "PASS")])
    github = _team(repo / ".github" / "agents", build_log=True)
    _decisions(github, [("survey-s5", "HALT")])
    assert cb.check_file_clearance(claude, "survey-s5", script)[0] is False


def test_a_malformed_privilege_file_refuses(repo: Path, script: Path) -> None:
    gates = _gates(repo, script)
    (gates / "references" / "agent-privilege.json").write_text("[1, 2]")
    assert cb.check_file_clearance(gates, "survey-s5", script)[0] is False
