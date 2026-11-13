"""A deleted git-tracked signing-governed.marker still governs; every deny list protects it (IM item 17, G-22-7).

Before: ``is_governed`` (and the inline copies in ``decision_log`` and ``projection_marker``) tested only that the
file existed, and the sandbox left it writable ("creating it only makes the workspace stricter"). Deleting it
turned governed fail-closed signing off: ``enforce_decision_signing: false`` applied again, and a missing switch
reverted to legacy OFF instead of raising.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from agentteams import projection_marker, signing_switch
from agentteams.cli.decision_log import _enforce_decision_signing
from agentteams.frameworks._goose_sandbox_emit import _build_seatbelt_profile
from agentteams.frameworks._sandbox_emit import ALL_TEAM_FRAMEWORKS, permission_deny_rules, team_agents_dir

MARKER = signing_switch.GOVERNED_MARKER
REPO = Path(__file__).resolve().parents[1]
_GIT = shutil.which("git")
needs_git = pytest.mark.skipif(_GIT is None, reason="git not installed")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                    "-C", str(root), *args], check=True, capture_output=True)


def _governed_repo(tmp_path: Path) -> Path:
    """A git repo whose `.claude/agents` team commits the marker."""
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / MARKER).write_text("governed\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "governed")
    return agents


# --------------------------------------------------------------------------- the predicate


@needs_git
def test_a_deleted_tracked_marker_still_governs(tmp_path):
    agents = _governed_repo(tmp_path)
    (agents / MARKER).unlink()
    assert signing_switch.is_governed(agents)


@needs_git
def test_a_staged_deletion_still_governs_a_committed_one_does_not(tmp_path):
    agents = _governed_repo(tmp_path)
    _git(tmp_path, "rm", "-q", f".claude/agents/{MARKER}")
    assert signing_switch.is_governed(agents)               # index lost it, HEAD still has it
    _git(tmp_path, "commit", "-q", "-m", "ungovern")
    assert not signing_switch.is_governed(agents)           # the honest limit: a committed deletion, in history


def test_outside_git_only_the_file_counts(tmp_path):
    assert not signing_switch.is_governed(tmp_path)
    (tmp_path / MARKER).write_text("")
    assert signing_switch.is_governed(tmp_path)


@needs_git
def test_a_planted_fsmonitor_is_not_run(tmp_path):
    """The check runs from the gate hook in a repository the agent can write (integrity._manifest_expected)."""
    agents = _governed_repo(tmp_path)
    (agents / MARKER).unlink()
    canary = tmp_path / "canary"
    hook = tmp_path / "fsmon.sh"
    hook.write_text(f"#!/bin/sh\ntouch {canary}\n")
    hook.chmod(0o755)
    _git(tmp_path, "config", "core.fsmonitor", str(hook))
    assert signing_switch.is_governed(agents)
    assert not canary.exists()


# --------------------------------------------------------------------------- the three readers


@needs_git
def test_decision_log_fails_closed_after_the_marker_is_deleted(tmp_path):
    agents = _governed_repo(tmp_path)
    (agents / MARKER).unlink()
    with pytest.raises(RuntimeError, match="governed workspace"):
        _enforce_decision_signing(agents)


@needs_git
def test_the_switch_is_not_relaxed_after_the_marker_is_deleted(tmp_path):
    agents = _governed_repo(tmp_path)
    (agents / MARKER).unlink()
    outcome = signing_switch.apply_switch(agents, False, signing_switch.NATIVE_NOTE)
    assert signing_switch.read_switch(agents / signing_switch.SWITCH_REL) is True
    assert any(signing_switch.KEPT_PREFIX in n for n in outcome.notices)


@needs_git
def test_projection_treats_the_team_as_governed_after_the_marker_is_deleted(tmp_path):
    agents = _governed_repo(tmp_path)
    (agents / MARKER).unlink()
    source = tmp_path / "canonical"
    source.mkdir()
    assert projection_marker._switch_value(source, agents) is True


def test_one_predicate():
    """decision_log and projection_marker no longer carry their own existence test."""
    for rel in ("agentteams/cli/decision_log.py", "agentteams/projection_marker.py"):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "is_governed(" in text and '"signing-governed.marker").exists()' not in text
        assert "_GOVERNED_MARKER) for d in" not in text


# --------------------------------------------------------------------------- the deny lists


@pytest.mark.parametrize("key", ALL_TEAM_FRAMEWORKS)
def test_the_goose_profile_denies_writing_each_teams_marker(key):
    profile = _build_seatbelt_profile(None)
    deny = profile[profile.index(";; --- Control-plane protection"):]
    rel = f"{team_agents_dir(key)}/{MARKER}"
    assert rel in deny or (key == "claude" and '"/.claude"))' in deny), key   # claude: the whole .claude dir


@pytest.mark.parametrize("key", ALL_TEAM_FRAMEWORKS)
def test_the_claude_edit_rules_cover_each_teams_marker(key):
    assert f"Edit(/{team_agents_dir(key)}/{MARKER})" in permission_deny_rules("claude")


def test_the_goose_edit_rules_cover_its_own_marker():
    assert f"Edit(/.goose/recipes/{MARKER})" in permission_deny_rules("goose")


def test_the_launcher_protects_a_present_marker():
    text = (REPO / "agentteams/templates/universal/sandbox/confine-run.sh").read_text(encoding="utf-8")
    assert f"GOVERNED_MARKER_REL={MARKER}" in text
    assert 'for m in "$TEAM_MARKER_REL" "$GOVERNED_MARKER_REL"; do' in text


def test_the_comment_no_longer_says_the_marker_is_left_writable():
    text = (REPO / "agentteams/frameworks/_sandbox_emit.py").read_text(encoding="utf-8")
    assert "creating it only makes the workspace stricter" not in text
    assert signing_switch.GOVERNED_MARKER == "signing-governed.marker"
