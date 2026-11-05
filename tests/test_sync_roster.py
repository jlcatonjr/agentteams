"""Pinned sync carries the pin's roster, and the sandbox files see every team (remediation-log 2026-11-04).

Roster: an agent the pin deleted came back on the next sync, and one it added never reached the hub. Ordering:
on a first ``--sync-init`` the sandbox files were written framework by framework, so a team marked later in the
same pass was missing from the earlier frameworks' sibling denies.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import agentteams
from agentteams.multi_sync import run_sync, sync_init
from agentteams.sync_pin import update_last_synced_commit

_AGENT = "---\nname: {slug}\ndescription: The {slug} agent.\ntools: [read]\n---\n\n# {slug}\n\nDoes {slug} work.\n"
_PIN = Path(".github/agents")
_PEER = Path(".claude/agents")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True,
                   capture_output=True)


def _commit(root: Path, *, synced: bool = False) -> None:
    """Commit everything; ``synced`` also moves the sync anchor here (after a sync, so its output is not a change)."""
    _git(root, "add", "-Af")
    _git(root, "commit", "-qm", "c", "--allow-empty")
    if synced:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        update_last_synced_commit(root, head)


def _write_pin_agent(root: Path, slug: str) -> None:
    (root / _PIN / f"{slug}.agent.md").write_text(_AGENT.format(slug=slug), encoding="utf-8")


def _team(root: Path, slugs: tuple[str, ...] = ("orchestrator", "helper")) -> None:
    """A copilot-vscode pin with ``slugs``, synced to claude and committed."""
    (root / _PIN).mkdir(parents=True)
    for slug in slugs:
        _write_pin_agent(root, slug)
    _git(root, "init", "-q")
    _git(root, "commit", "-q", "--allow-empty", "-m", "init")
    sync_init(root, pin="copilot-vscode", frameworks=["copilot-vscode", "claude"])
    _commit(root, synced=True)


def _hub(root: Path) -> set[str]:
    return {p.stem for p in (root / ".agentteams" / "canonical" / "agents").glob("*.md")}


def _change_pin(root: Path) -> None:
    """A committed pin change, so the next sync has work to do."""
    path = root / _PIN / "orchestrator.agent.md"
    path.write_text(path.read_text(encoding="utf-8").replace("Does orchestrator", "Routes orchestrator"),
                    encoding="utf-8")


def test_a_committed_pin_deletion_leaves_every_framework(tmp_path: Path) -> None:
    _team(tmp_path)
    (tmp_path / _PIN / "helper.agent.md").unlink()
    _commit(tmp_path)
    result = run_sync(tmp_path)
    assert not (tmp_path / _PIN / "helper.agent.md").exists()
    assert not (tmp_path / _PEER / "helper.md").exists()
    assert _hub(tmp_path) == {"orchestrator"}
    assert any(c.agent == "helper" and c.resolution == "applied (pin removed agent)" for c in result.conflicts)
    assert any("removed agent(s) the pin removed: helper" in n for n in result.notices)
    backups = list((tmp_path / _PEER / ".agentteams-backups").rglob("helper.md.agentteams-bak"))
    assert backups, "the peer's copy must be recoverable from its backup"
    _commit(tmp_path, synced=True)
    _change_pin(tmp_path)
    _commit(tmp_path)
    run_sync(tmp_path)
    assert not (tmp_path / _PIN / "helper.agent.md").exists(), "a later sync brought the deleted agent back"


def test_an_uncommitted_pin_deletion_is_restored_and_logged(tmp_path: Path) -> None:
    _team(tmp_path)
    _change_pin(tmp_path)
    _commit(tmp_path)
    (tmp_path / _PIN / "helper.agent.md").unlink()
    result = run_sync(tmp_path)
    assert (tmp_path / _PIN / "helper.agent.md").exists()
    assert (tmp_path / _PEER / "helper.md").exists()
    assert any(c.agent == "helper" and c.resolution.startswith("proposed (unconfirmed pin deletion")
               for c in result.conflicts)


def test_a_pin_addition_reaches_the_hub_and_every_framework(tmp_path: Path) -> None:
    _team(tmp_path)
    _write_pin_agent(tmp_path, "reviewer")
    _commit(tmp_path)
    result = run_sync(tmp_path)
    assert "reviewer" in _hub(tmp_path)
    assert "Does reviewer work." in (tmp_path / _PEER / "reviewer.md").read_text(encoding="utf-8")
    assert any(c.agent == "reviewer" and c.resolution == "applied (pin added agent)" for c in result.conflicts)
    assert not any(c.agent == "reviewer" and c.resolution.startswith("restored") for c in result.conflicts)


def test_a_peer_only_agent_is_kept_and_proposed_on_every_pass(tmp_path: Path) -> None:
    _team(tmp_path)
    extra = tmp_path / _PEER / "local.md"
    extra.write_text(_AGENT.format(slug="local"), encoding="utf-8")
    for n in range(2):  # the second pass sees the peer-only agent in the peer's baseline
        _write_pin_agent(tmp_path, f"added{n}")  # a committed pin change, so the pass runs
        _commit(tmp_path)
        result = run_sync(tmp_path)
        _commit(tmp_path, synced=True)
        assert extra.is_file()
        assert "local" not in _hub(tmp_path)
        assert any(c.agent == "local" and c.resolution.startswith("proposed (peer-only agent")
                   for c in result.conflicts)


def test_a_peer_deletion_is_restored(tmp_path: Path) -> None:
    _team(tmp_path)
    (tmp_path / _PEER / "helper.md").unlink()
    _commit(tmp_path)
    result = run_sync(tmp_path)
    assert (tmp_path / _PEER / "helper.md").is_file()
    assert _hub(tmp_path) == {"orchestrator", "helper"}
    assert any(c.agent == "helper" and c.resolution.startswith("restored") for c in result.conflicts)


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(root).parts[:1]}


def test_a_pin_that_lost_every_agent_is_refused_before_writing(tmp_path: Path) -> None:
    _team(tmp_path)
    for path in (tmp_path / _PIN).glob("*.agent.md"):
        path.unlink()
    _commit(tmp_path)
    before = _tree(tmp_path)
    with pytest.raises(ValueError, match="has no agents, and the hub has 2"):
        run_sync(tmp_path)
    assert _tree(tmp_path) == before


def test_a_reinit_prunes_the_hub_but_keeps_other_frameworks_files(tmp_path: Path) -> None:
    _team(tmp_path)
    (tmp_path / _PIN / "helper.agent.md").unlink()
    result = sync_init(tmp_path, pin="copilot-vscode", frameworks=["copilot-vscode", "claude"])
    assert _hub(tmp_path) == {"orchestrator"}
    assert (tmp_path / _PEER / "helper.md").is_file()
    assert any("removed agent(s) the pin lacks: helper" in n for n in result.notices)


def test_an_untracked_pin_deletion_needs_every_framework_to_agree(tmp_path: Path) -> None:
    """With the pin's agents directory gitignored, git can't confirm a deletion: fail closed (review 2026-11-05)."""
    _team(tmp_path)
    _git(tmp_path, "rm", "-rq", "--cached", ".github/agents")
    (tmp_path / ".gitignore").write_text(".github/agents/\n", encoding="utf-8")
    _commit(tmp_path)
    (tmp_path / _PIN / "helper.agent.md").unlink()
    run_sync(tmp_path, since="")
    assert (tmp_path / _PEER / "helper.md").is_file() and (tmp_path / _PIN / "helper.agent.md").is_file()
    (tmp_path / _PIN / "helper.agent.md").unlink()
    (tmp_path / _PEER / "helper.md").unlink()
    result = run_sync(tmp_path, since="")
    assert not (tmp_path / _PIN / "helper.agent.md").exists() and "helper" not in _hub(tmp_path)
    assert any(c.agent == "helper" and c.resolution == "applied (pin removed agent)" for c in result.conflicts)


def test_a_reinit_from_an_empty_pin_is_refused_before_writing(tmp_path: Path) -> None:
    _team(tmp_path)
    for path in (tmp_path / _PIN).glob("*.agent.md"):
        path.unlink()
    before = _tree(tmp_path)
    with pytest.raises(ValueError, match="has no agents"):
        sync_init(tmp_path, pin="copilot-vscode", frameworks=["copilot-vscode", "claude"])
    assert _tree(tmp_path) == before


def test_a_dry_run_reports_the_peer_removal_and_deletes_nothing(tmp_path: Path) -> None:
    _team(tmp_path)
    (tmp_path / _PIN / "helper.agent.md").unlink()
    _commit(tmp_path)
    before = _tree(tmp_path)
    result = run_sync(tmp_path, dry_run=True)
    assert any("[claude] removed agent(s) the pin removed: helper" in n for n in result.notices)
    assert _tree(tmp_path) == before


_GITHUB_LITERALS = [f'(literal (string-append (param "WORKSPACE_ROOT") "/{p}"))'
                    for p in (".github", ".github/agents", ".github/agents/references")]


def test_first_sync_init_gives_earlier_frameworks_the_later_teams_denies(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Goose and claude are projected before copilot creates its team; both must still deny it."""
    monkeypatch.setattr(sys, "platform", "darwin")  # goose's Seatbelt profile is emitted on darwin only
    (tmp_path / ".agentteams").mkdir()
    (tmp_path / ".agentteams" / "brief.json").write_text(json.dumps({"privilege_profile": "confined"}),
                                                         encoding="utf-8")
    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()  # the read-only file server a native goose render installs
    shutil.copy(Path(agentteams.__file__).parent / "data" / "goose-readfs-mcp.py", tmp_path / "scripts")
    claude_agent = _AGENT.format(slug="orchestrator").replace("tools: [read]", "tools: Read, Edit, Grep")
    (tmp_path / ".claude" / "agents" / "orchestrator.md").write_text(claude_agent, encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "init")
    sync_init(tmp_path, pin="claude", frameworks=["claude", "goose", "copilot-vscode"])
    sandbox_sb = (tmp_path / ".goose" / "sandbox.sb").read_text(encoding="utf-8")
    for literal in _GITHUB_LITERALS:
        assert literal in sandbox_sb
    example = json.loads((tmp_path / ".claude" / "settings.hooks.example.json").read_text(encoding="utf-8"))
    deny = example["sandbox"]["filesystem"]["denyWrite"]
    assert ".github/agents" in deny
