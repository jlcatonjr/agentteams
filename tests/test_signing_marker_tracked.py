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
    assert not signing_switch.is_governed(agents)           # no upstream, no origin/HEAD: the deletion is everywhere


def _clone_with_upstream(tmp_path: Path) -> tuple[Path, Path]:
    origin = tmp_path / "origin"
    origin.mkdir()
    _governed_repo(origin)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
    return origin, work / ".claude" / "agents"


@needs_git
def test_a_deletion_committed_only_on_the_agents_branch_still_governs(tmp_path):
    """@security item 17 condition 4: git commit is not gated, so HEAD alone cannot end governance."""
    origin, agents = _clone_with_upstream(tmp_path)
    work = agents.parents[1]
    _git(work, "rm", "-q", f".claude/agents/{MARKER}")
    _git(work, "commit", "-q", "-m", "ungovern on my branch")
    assert signing_switch.is_governed(agents)               # upstream / origin/HEAD still track it
    _git(origin, "rm", "-q", f".claude/agents/{MARKER}")
    _git(origin, "commit", "-q", "-m", "operator ungoverns")
    _git(work, "fetch", "-q")
    assert not signing_switch.is_governed(agents)           # the deletion reached the upstream: governance ends


@needs_git
@pytest.mark.parametrize("var", ["GIT_INDEX_FILE", "GIT_DIR", "GIT_CEILING_DIRECTORIES"])
def test_an_inherited_git_variable_cannot_redirect_the_check(tmp_path, monkeypatch, var):
    agents = _governed_repo(tmp_path)
    (agents / MARKER).unlink()
    other = tmp_path.parent / f"{tmp_path.name}-other"
    other.mkdir()
    _git(other, "init", "-q")
    value = {"GIT_INDEX_FILE": str(other / ".git" / "index"), "GIT_DIR": str(other / ".git"),
             "GIT_CEILING_DIRECTORIES": str(tmp_path)}[var]
    monkeypatch.setenv(var, value)
    assert signing_switch.is_governed(agents)


@needs_git
def test_a_repository_planted_inside_the_project_fails_closed(tmp_path):
    """@security: `git init` in the team dir would make the check ask an empty repo."""
    agents = _governed_repo(tmp_path)
    (agents / MARKER).unlink()
    _git(agents, "init", "-q")
    assert signing_switch.is_governed(agents)


def test_a_git_timeout_fails_closed(tmp_path, monkeypatch):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)

    def hang(*_a, **_k):
        raise subprocess.TimeoutExpired("git", 10)

    monkeypatch.setattr(signing_switch.subprocess, "run", hang)
    assert signing_switch.is_governed(agents)


def test_no_git_falls_back_to_the_file(tmp_path, monkeypatch):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)

    def missing(*_a, **_k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(signing_switch.subprocess, "run", missing)
    assert not signing_switch.is_governed(agents)


def test_outside_git_only_the_file_counts(tmp_path):
    assert not signing_switch.is_governed(tmp_path)
    (tmp_path / MARKER).write_text("")
    assert signing_switch.is_governed(tmp_path)


@needs_git
def test_a_planted_fsmonitor_is_not_run(tmp_path):
    """The check runs in a repository the agent can write: its config must not run code (as integrity does)."""
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


@pytest.mark.parametrize("key", [k for k in ALL_TEAM_FRAMEWORKS if k != "claude"])  # claude: whole .claude, pre-existing
def test_the_goose_profile_denies_writing_each_teams_marker(key):
    profile = _build_seatbelt_profile(None)
    deny = profile[profile.index(";; --- Control-plane protection"):]
    assert f"/{team_agents_dir(key)}/{MARKER}" in deny, key


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


@needs_git
def test_a_staged_but_uncommitted_marker_still_governs_once_deleted(tmp_path):
    """Only the index knows a marker that was `git add`ed and not yet committed."""
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "README.md").write_text("team\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "team")
    (agents / MARKER).write_text("governed\n")
    _git(tmp_path, "add", f".claude/agents/{MARKER}")
    (agents / MARKER).unlink()
    assert signing_switch.is_governed(agents)


# --------------------------------------------------------------------------- --doctor names a committed deletion


def _short_head(root: Path) -> str:
    return subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()


def _team(root: Path) -> Path:
    """A governed repo whose team dir holds another file too, so removing the marker leaves the dir in place."""
    agents = _governed_repo(root)
    (agents / "orchestrator.agent.md").write_text("team\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "team file")
    return agents


def _doctor(root: Path, capsys) -> str:
    from agentteams.doctor import run_doctor

    assert run_doctor(str(root)) == 0  # these are WARN / INFO lines, never a FAIL
    return capsys.readouterr().out


@needs_git
def test_doctor_warns_when_a_committed_deletion_ended_governance(tmp_path, capsys):
    """The way out of governance (a deletion on every governing ref) is visible: --doctor names the commit."""
    _team(tmp_path)
    _git(tmp_path, "rm", "-q", f".claude/agents/{MARKER}")
    _git(tmp_path, "commit", "-q", "-m", "drop the signing marker")
    out = _doctor(tmp_path, capsys)
    assert "Signing governance" in out
    assert (f".claude/agents/{MARKER} is absent and was deleted in {_short_head(tmp_path)} drop the signing marker; "
            "no team dir in this project is signing-governed any more") in out


@needs_git
def test_a_marker_head_still_carries_is_not_reported(tmp_path, capsys):
    """Deleted, re-added, then removed only in the working tree: HEAD has it, so no old deletion is named."""
    agents = _team(tmp_path)
    _git(tmp_path, "rm", "-q", f".claude/agents/{MARKER}")
    _git(tmp_path, "commit", "-q", "-m", "old deletion")
    (agents / MARKER).write_text("governed\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "re-added")
    (agents / MARKER).unlink()
    assert signing_switch.marker_deletion_commits(tmp_path) == {}
    assert "Signing governance" not in _doctor(tmp_path, capsys)


@needs_git
def test_the_wording_names_a_sibling_team_that_still_governs(tmp_path, capsys):
    _team(tmp_path)
    (tmp_path / ".github" / "agents").mkdir(parents=True)
    (tmp_path / ".github" / "agents" / MARKER).write_text("governed\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "second team")
    _git(tmp_path, "rm", "-q", f".claude/agents/{MARKER}")
    _git(tmp_path, "commit", "-q", "-m", "drop one")
    out = _doctor(tmp_path, capsys)
    assert "this team dir is no longer signing-governed (.github/agents still is)" in out


@needs_git
def test_a_removed_team_dir_is_one_info_line_not_a_warn(tmp_path, capsys):
    _governed_repo(tmp_path)
    _git(tmp_path, "rm", "-q", "-r", ".claude/agents")
    _git(tmp_path, "commit", "-q", "-m", "remove the team")
    out = _doctor(tmp_path, capsys)
    assert f".claude/agents was removed; its {MARKER} was deleted in {_short_head(tmp_path)} remove the team" in out
    assert "WARN .claude/agents" not in out


@needs_git
@pytest.mark.parametrize("state", ["present", "never-tracked", "uncommitted-deletion"])
def test_doctor_is_quiet_unless_a_deletion_was_committed(tmp_path, capsys, state):
    from agentteams.doctor import run_doctor

    if state == "never-tracked":
        (tmp_path / ".claude" / "agents").mkdir(parents=True)
        (tmp_path / ".claude" / "agents" / "README.md").write_text("team\n")
        _git(tmp_path, "init", "-q")
        _git(tmp_path, "add", "-A")
        _git(tmp_path, "commit", "-q", "-m", "team")
    else:
        agents = _governed_repo(tmp_path)
        if state == "uncommitted-deletion":
            (agents / MARKER).unlink()  # still governed (index/HEAD); nothing to report yet
    run_doctor(str(tmp_path))
    assert "Signing governance" not in capsys.readouterr().out


@needs_git
def test_marker_deletion_commits_is_empty_outside_git_and_when_present(tmp_path):
    (tmp_path / "plain" / ".claude" / "agents").mkdir(parents=True)
    assert signing_switch.marker_deletion_commits(tmp_path / "plain") == {}  # not a work tree
    _governed_repo(tmp_path / "repo")
    assert signing_switch.marker_deletion_commits(tmp_path / "repo") == {}  # marker present


def _deleted_in_history(root: Path) -> None:
    _governed_repo(root)
    _git(root, "rm", "-q", f".claude/agents/{MARKER}")
    _git(root, "commit", "-q", "-m", "drop")


@needs_git
def test_the_history_lookup_does_not_run_a_planted_fsmonitor(tmp_path):
    _deleted_in_history(tmp_path)
    sentinel = tmp_path / "pwned"
    hook = tmp_path / "fsmon.sh"
    hook.write_text(f"#!/bin/sh\ntouch {sentinel}\n")
    hook.chmod(0o755)
    _git(tmp_path, "config", "core.fsmonitor", str(hook))
    assert signing_switch.marker_deletion_commits(tmp_path)
    assert not sentinel.exists()


@needs_git
def test_the_history_lookup_does_not_run_a_planted_gpg_program(tmp_path):
    """@security: log.showSignature in the repo config would run gpg.program to verify a signed commit."""
    _governed_repo(tmp_path)
    sentinel = tmp_path / "pwned"
    fake_gpg = tmp_path / "fake-gpg.sh"
    fake_gpg.write_text("#!/bin/sh\n"
                        f'case " $* " in *" --verify "*) touch {sentinel}; exit 0;; esac\n'
                        'printf "\\n[GNUPG:] SIG_CREATED D 1 8 00 0 0\\n" >&2\n'
                        'printf -- "-----BEGIN PGP SIGNATURE-----\\n\\nAA==\\n-----END PGP SIGNATURE-----\\n"\n')
    fake_gpg.chmod(0o755)
    for key, value in (("gpg.program", str(fake_gpg)), ("user.signingkey", "fake"), ("log.showSignature", "true")):
        _git(tmp_path, "config", key, value)
    _git(tmp_path, "rm", "-q", f".claude/agents/{MARKER}")
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-C", str(tmp_path), "commit",
                    "-q", "-S", "-m", "signed drop"], check=True, capture_output=True)
    sentinel.unlink(missing_ok=True)  # signing at commit time is not the hazard; reading the history is
    assert signing_switch.marker_deletion_commits(tmp_path)
    assert not sentinel.exists()


@needs_git
def test_a_commit_subject_cannot_inject_terminal_escapes(tmp_path):
    _governed_repo(tmp_path)
    _git(tmp_path, "rm", "-q", f".claude/agents/{MARKER}")
    _git(tmp_path, "commit", "-q", "-m", "drop \x1b[2J\x1b]0;owned\x07 now")
    (commit,) = signing_switch.marker_deletion_commits(tmp_path).values()
    assert all(ch.isprintable() for ch in commit)
    assert commit.endswith("drop [2J]0;owned now")


@needs_git
def test_doctor_says_a_branch_only_deletion_still_governs(tmp_path, capsys):
    """A deletion committed only on the agent's branch keeps governing; --doctor must not claim it ended."""
    _origin, agents = _clone_with_upstream(tmp_path)
    work = agents.parents[1]
    (agents / "orchestrator.agent.md").write_text("team\n")  # the team dir outlives its marker
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "team file")
    _git(work, "rm", "-q", f".claude/agents/{MARKER}")
    _git(work, "commit", "-q", "-m", "ungovern on my branch")
    out = _doctor(work, capsys)
    assert "still governs until that deletion reaches the upstream and default branch" in out
    assert "no longer signing-governed" not in out
