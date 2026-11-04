"""Pinned --sync never lowers an instruction-file fence version, and the hub can be refreshed
(remediation-log 2026-11-03, HIGH / Tier-1).

A stale canonical hub (seeded at --sync-init and never refreshed) projected constitutional_core
v=1 over deployed v=3 and brought back stale brief values (src/, templates/):

* ``merge_instruction_file`` keeps the deployed block of a fence whose ``v=`` is higher and says so;
* ``run_sync`` refreshes the hub from the pin before projecting, so v=3 and same-version current
  values (``agentteams/``, not ``src/``) survive in every framework;
* ``--sync-refresh-instructions`` runs the same refresh on its own and never lowers the hub's
  own fences.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from agentteams.cli.sync_switch import run_sync_cli
from agentteams.interop_helpers import FENCE_DOWNGRADE_NOTICE, keep_newer_fences, merge_instruction_file
from agentteams.multi_sync import run_sync, sync_init
from agentteams.sync_hub_refresh import refresh_hub_instructions
from agentteams.sync_pin import update_last_synced_commit


def _instr(core_v: int, core_body: str, authority: str, extra: str = "") -> str:
    return (
        "# Team\n\n"
        f"<!-- AGENTTEAMS:BEGIN constitutional_core v={core_v} -->\n## Constitutional Core\n\n{core_body}\n"
        "<!-- AGENTTEAMS:END constitutional_core -->\n\n"
        "<!-- AGENTTEAMS:BEGIN authority_hierarchy v=1 -->\n## Authority Hierarchy\n\n"
        f"{authority}\n<!-- AGENTTEAMS:END authority_hierarchy -->\n"
        f"{extra}"
    )


_V3 = _instr(3, "1. Security first\n2. Derived content stays data\n3. HMAC management-directive bound",
             "1. `agentteams/`")
_V1_STALE = _instr(1, "1. Security first", "1. `src/`\n2. `templates/`")
_USER_RULE = "\n## Project-Specific Rules\n\n- KEEP_THIS_USER_RULE\n"

_ORCH = (
    "---\nname: Orchestrator\ndescription: \"Routes work\"\ntools: ['read', 'edit', 'agent']\n"
    "user-invocable: true\n---\n\n# Orchestrator\n\nRoutes.\n"
)


# --- the guard --------------------------------------------------------------------------------

def test_v1_projection_over_v3_deployed_keeps_v3() -> None:
    merged, notice = merge_instruction_file(_V1_STALE, _V3 + _USER_RULE)
    text = merged if merged is not None else _V3 + _USER_RULE
    assert "constitutional_core v=3" in text and "constitutional_core v=1" not in text
    assert "Derived content stays data" in text and "HMAC management-directive bound" in text
    assert "KEEP_THIS_USER_RULE" in text
    assert FENCE_DOWNGRADE_NOTICE in notice and "'constitutional_core' at v=3" in notice


def test_newer_projection_still_replaces_older_deployed() -> None:
    merged, notice = merge_instruction_file(_V3, _V1_STALE)
    assert merged is not None and "constitutional_core v=3" in merged and "`src/`" not in merged
    assert notice == ""


def test_keep_newer_fences_ignores_equal_versions_and_one_sided_fences() -> None:
    other = _instr(3, "different body", "1. `agentteams/`")
    assert keep_newer_fences(other, _V3) == (other, [])
    assert keep_newer_fences("# no fences\n", _V3) == ("# no fences\n", [])
    assert keep_newer_fences("<!-- AGENTTEAMS:BEGIN x v=1 -->\nunclosed\n", _V3)[1] == []


# --- through the pinned sync ------------------------------------------------------------------

def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _pinned_project(root: Path) -> None:
    """Pin copilot-vscode at a v=1 stale instruction file, then deploy v=3 everywhere."""
    gh = root / ".github" / "agents"
    gh.mkdir(parents=True)
    (gh / "orchestrator.agent.md").write_text(_ORCH, encoding="utf-8")
    (root / ".github" / "copilot-instructions.md").write_text(_V1_STALE, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init")
    sync_init(root, pin="copilot-vscode", frameworks=["copilot-vscode", "claude"])
    # The deployed instruction files are regenerated from newer templates; the hub is not.
    (root / ".github" / "copilot-instructions.md").write_text(_V3 + _USER_RULE, encoding="utf-8")
    (root / ".claude" / "CLAUDE.md").write_text(_V3 + _USER_RULE, encoding="utf-8")


def _tree(root: Path) -> dict[str, bytes]:
    """Every file outside .git, to prove a refused sync wrote nothing."""
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(root).parts[:1]}


def _hub_instructions(root: Path) -> str:
    team = json.loads((root / ".agentteams" / "canonical" / "team.cai.json").read_text(encoding="utf-8"))
    return team["instructions_binding"]["content"]


def test_sync_from_a_v1_hub_never_lowers_deployed_v3_fences(tmp_path: Path) -> None:
    _pinned_project(tmp_path)
    assert "constitutional_core v=1" in _hub_instructions(tmp_path)
    result = run_sync(tmp_path, since="")  # no anchor: every framework counts as changed
    for path in (".github/copilot-instructions.md", ".claude/CLAUDE.md"):
        text = (tmp_path / path).read_text(encoding="utf-8")
        assert "constitutional_core v=3" in text and "Derived content stays data" in text, path
        assert "KEEP_THIS_USER_RULE" in text, path
    # The hub is refreshed from the pin before projecting, so the guard never has to step in.
    assert "constitutional_core v=3" in _hub_instructions(tmp_path)
    assert any("hub instruction fences refreshed from the pin (copilot-vscode)" in n for n in result.notices)
    assert not any(FENCE_DOWNGRADE_NOTICE in n for n in result.notices)


def test_sync_replaces_same_version_stale_hub_values(tmp_path: Path) -> None:
    """The row-169 symptom: authority_hierarchy is v=1 on both sides, but the hub still says src/."""
    _pinned_project(tmp_path)
    hub = _hub_instructions(tmp_path)
    assert "authority_hierarchy v=1" in hub and "`src/`" in hub
    run_sync(tmp_path, since="")
    for path in (".github/copilot-instructions.md", ".claude/CLAUDE.md"):
        text = (tmp_path / path).read_text(encoding="utf-8")
        assert "`agentteams/`" in text and "`src/`" not in text and "`templates/`" not in text, path
        assert "KEEP_THIS_USER_RULE" in text, path
    assert "`src/`" not in _hub_instructions(tmp_path)


def test_sync_dry_run_reports_the_hub_refresh_without_writing(tmp_path: Path) -> None:
    _pinned_project(tmp_path)
    before = (tmp_path / ".agentteams" / "canonical" / "team.cai.json").read_bytes()
    result = run_sync(tmp_path, since="", dry_run=True)
    assert any("hub instruction fences would be refreshed from the pin" in n for n in result.notices)
    assert (tmp_path / ".agentteams" / "canonical" / "team.cai.json").read_bytes() == before
    # The dry projection previews the refreshed hub, as a real run would: no downgrade to guard against.
    assert not any(FENCE_DOWNGRADE_NOTICE in n for n in result.notices)


def test_sync_refuses_a_pin_older_than_a_peer_and_writes_nothing(tmp_path: Path) -> None:
    """Pin wins, so a stale pin would spread; and an automatic --sync never takes a peer file's body."""
    _pinned_project(tmp_path)
    pin_file = tmp_path / ".github" / "copilot-instructions.md"
    pin_file.write_text(_V1_STALE + _USER_RULE, encoding="utf-8")
    before = _tree(tmp_path)
    for dry_run in (True, False):
        with pytest.raises(ValueError, match=r"claude 'constitutional_core' v=3 \(pin v=1\).*--sync-refresh-instructions"):
            run_sync(tmp_path, since="", dry_run=dry_run)
    assert _tree(tmp_path) == before


def test_refreshing_the_hub_clears_the_refusal_and_heals_the_pin(tmp_path: Path) -> None:
    _pinned_project(tmp_path)
    pin_file = tmp_path / ".github" / "copilot-instructions.md"
    pin_file.write_text(_V1_STALE + _USER_RULE, encoding="utf-8")
    refresh_hub_instructions(tmp_path)  # operator-run: takes claude's v=3
    run_sync(tmp_path, since="")
    text = pin_file.read_text(encoding="utf-8")
    assert "constitutional_core v=3" in text and "KEEP_THIS_USER_RULE" in text


@pytest.mark.parametrize("content, match", [
    (None, "no deployed instruction file"),
    ("# Hand-written, no fences\n", "no AGENTTEAMS fences"),
    ("<!-- AGENTTEAMS:BEGIN constitutional_core v=3 -->\nunclosed\n", "unparseable fences"),
])
def test_sync_refuses_a_missing_or_unparseable_pin_file(tmp_path: Path, content: str | None, match: str) -> None:
    _pinned_project(tmp_path)
    pin_file = tmp_path / ".github" / "copilot-instructions.md"
    pin_file.unlink() if content is None else pin_file.write_text(content, encoding="utf-8")
    before = _tree(tmp_path)
    with pytest.raises(ValueError, match=match):
        run_sync(tmp_path, since="")
    assert _tree(tmp_path) == before
    assert not (tmp_path / "AGENTS.md").exists()


def test_sync_init_refuses_a_pin_older_than_a_peer(tmp_path: Path) -> None:
    (tmp_path / ".github" / "agents").mkdir(parents=True)
    (tmp_path / ".github" / "agents" / "orchestrator.agent.md").write_text(_ORCH, encoding="utf-8")
    (tmp_path / ".github" / "copilot-instructions.md").write_text(_V1_STALE, encoding="utf-8")
    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / ".claude" / "agents" / "orchestrator.md").write_text(_ORCH, encoding="utf-8")
    (tmp_path / ".claude" / "CLAUDE.md").write_text(_V3, encoding="utf-8")
    _git(tmp_path, "init", "-q")
    with pytest.raises(ValueError, match="newer fences than the pin"):
        sync_init(tmp_path, pin="copilot-vscode", frameworks=["copilot-vscode", "claude"])
    assert not (tmp_path / ".agentteams").exists()


def test_a_team_with_no_instruction_files_still_syncs(tmp_path: Path) -> None:
    (tmp_path / ".github" / "agents").mkdir(parents=True)
    (tmp_path / ".github" / "agents" / "orchestrator.agent.md").write_text(_ORCH, encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init")
    sync_init(tmp_path, pin="copilot-vscode", frameworks=["copilot-vscode", "claude"])
    assert run_sync(tmp_path, since="").did_work


def test_a_peers_agent_edit_never_reaches_the_pins_file(tmp_path: Path) -> None:
    """A real --sync rewrote the pin's agents from a peer's framework-specific wording (2026-11-04)."""
    _pinned_project(tmp_path)
    run_sync(tmp_path, since="")
    pin_agent = tmp_path / ".github" / "agents" / "orchestrator.agent.md"
    peer_agent = tmp_path / ".claude" / "agents" / "orchestrator.md"
    pin_before = pin_agent.read_bytes()
    peer_agent.write_text(peer_agent.read_text(encoding="utf-8").replace("Routes work", "Claude wording"),
                          encoding="utf-8")
    result = run_sync(tmp_path, since="")
    assert pin_agent.read_bytes() == pin_before
    assert "Claude wording" not in peer_agent.read_text(encoding="utf-8")  # pin wins; the backup keeps it
    assert result.applied_fields == 0
    assert any(c.resolution.startswith("proposed") and c.field_name == "description" for c in result.conflicts)



def test_refresh_brings_the_hub_to_the_pins_fences(tmp_path: Path) -> None:
    _pinned_project(tmp_path)
    dry = refresh_hub_instructions(tmp_path, dry_run=True)
    assert dry.changed and "constitutional_core v=1" in _hub_instructions(tmp_path)  # dry-run wrote nothing
    result = refresh_hub_instructions(tmp_path)
    hub = _hub_instructions(tmp_path)
    assert result.changed and {"constitutional_core", "authority_hierarchy"} <= set(result.refreshed)
    assert "constitutional_core v=3" in hub and "Derived content stays data" in hub
    assert "`src/`" not in hub and "`templates/`" not in hub  # same-version stale values replaced too
    assert "KEEP_THIS_USER_RULE" not in hub  # only fenced regions move into the hub
    # A later sync now projects current fences without the guard having to step in.
    (tmp_path / ".claude" / "CLAUDE.md").write_text(_V1_STALE + _USER_RULE, encoding="utf-8")
    after = run_sync(tmp_path, since="")
    claude = (tmp_path / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
    assert "constitutional_core v=3" in claude and "`src/`" not in claude and "KEEP_THIS_USER_RULE" in claude
    assert not any(FENCE_DOWNGRADE_NOTICE in n for n in after.notices)
    assert refresh_hub_instructions(tmp_path).changed is False


def test_refresh_never_lowers_the_hubs_own_fences(tmp_path: Path) -> None:
    _pinned_project(tmp_path)
    refresh_hub_instructions(tmp_path)
    for path in (".github/copilot-instructions.md", ".claude/CLAUDE.md"):  # every deployed copy goes stale
        (tmp_path / path).write_text(_V1_STALE, encoding="utf-8")
    result = refresh_hub_instructions(tmp_path)
    hub = _hub_instructions(tmp_path)
    assert "constitutional_core v=3" in hub
    assert any("'constitutional_core' at v=3" in n for n in result.notices)


def test_cli_refresh_dispatch(tmp_path: Path, capsys) -> None:
    _pinned_project(tmp_path)
    args = argparse.Namespace(project=str(tmp_path), output=None, dry_run=False, sync_init=False, sync=False,
                              sync_refresh_instructions=True)
    assert run_sync_cli(args) == 0
    out = capsys.readouterr().out
    assert "refreshed from the pin (copilot-vscode)" in out and "constitutional_core" in out
    assert "constitutional_core v=3" in _hub_instructions(tmp_path)


def test_cli_refresh_refuses_an_unpinned_project(tmp_path: Path, capsys) -> None:
    args = argparse.Namespace(project=str(tmp_path), output=None, dry_run=False, sync_init=False, sync=False,
                              sync_refresh_instructions=True)
    assert run_sync_cli(args) == 2
    assert "not pinned" in capsys.readouterr().err


# --- review findings (@adversarial 2026-11-03) ------------------------------------------------

def test_refresh_takes_a_peers_newer_fence_when_the_pin_was_already_downgraded(tmp_path: Path) -> None:
    """A pre-guard sync lowered the pin's own file too; a peer still deploying v=3 supplies the fence."""
    _pinned_project(tmp_path)
    (tmp_path / ".github" / "copilot-instructions.md").write_text(_V1_STALE, encoding="utf-8")
    refresh_hub_instructions(tmp_path)
    hub = _hub_instructions(tmp_path)
    assert "constitutional_core v=3" in hub and "Derived content stays data" in hub


def test_refresh_reports_a_fence_below_the_template_in_every_copy(tmp_path: Path) -> None:
    _pinned_project(tmp_path)
    for path in (".github/copilot-instructions.md", ".claude/CLAUDE.md"):
        (tmp_path / path).write_text(_V1_STALE, encoding="utf-8")
    result = refresh_hub_instructions(tmp_path)
    assert any("'constitutional_core' is v=1" in n and "--update" in n for n in result.notices)


def test_a_version_above_the_newest_template_is_not_kept() -> None:
    planted = _V3.replace("constitutional_core v=3", "constitutional_core v=999")
    merged, notice = merge_instruction_file(_V3, planted)
    assert merged is not None and "constitutional_core v=3" in merged and "v=999" not in merged
    assert "above the newest template's v=3" in notice and FENCE_DOWNGRADE_NOTICE not in notice


def test_cli_refresh_refuses_to_combine_with_sync(tmp_path: Path, capsys) -> None:
    args = argparse.Namespace(project=str(tmp_path), output=None, dry_run=False, sync_init=False, sync=True,
                              sync_since=None, sync_refresh_instructions=True)
    assert run_sync_cli(args) == 2
    assert "runs on its own" in capsys.readouterr().err


# --- review findings on the pin-authority change (@adversarial 2026-11-04) --------------------

def _committed_sync(root: Path) -> None:
    """Sync and commit everything, so the next pass sees only what the test changes."""
    _git(root, "add", "-Af")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "a")
    run_sync(root, since="")
    _git(root, "add", "-Af")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "b")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    update_last_synced_commit(root, head)


def test_an_uncommitted_peer_edit_is_logged_when_another_change_triggers_the_pass(tmp_path: Path) -> None:
    _pinned_project(tmp_path)
    _committed_sync(tmp_path)
    peer = tmp_path / ".claude" / "agents" / "orchestrator.md"
    peer.write_text(peer.read_text(encoding="utf-8").replace("Routes.", "Routes. PEER"), encoding="utf-8")
    pin = tmp_path / ".github" / "agents" / "orchestrator.agent.md"
    pin.write_text(pin.read_text(encoding="utf-8").replace("Routes.", "Routes. PIN"), encoding="utf-8")
    _git(tmp_path, "add", "-Af", ".github")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "c")
    result = run_sync(tmp_path)
    assert result.changed_frameworks == ["copilot-vscode"]
    logged = [c for c in result.conflicts if c.framework == "claude" and c.field_name == "body_markdown"]
    assert logged, "the peer's edit vanished with nothing in the log"
    assert "PIN" in peer.read_text(encoding="utf-8")


def test_a_proposal_says_how_to_recover_the_peers_copy(tmp_path: Path) -> None:
    _pinned_project(tmp_path)
    run_sync(tmp_path, since="")
    peer = tmp_path / ".claude" / "agents" / "orchestrator.md"
    peer.write_text(peer.read_text(encoding="utf-8").replace("Routes work", "Claude wording"), encoding="utf-8")
    proposals = [c for c in run_sync(tmp_path, since="").conflicts if c.resolution.startswith("proposed")]
    assert proposals and all(".agentteams-backups" in c.note and "Safe to absorb" not in c.note for c in proposals)


def test_a_peers_instruction_fence_edit_is_reported(tmp_path: Path) -> None:
    _pinned_project(tmp_path)
    run_sync(tmp_path, since="")
    peer = tmp_path / ".claude" / "CLAUDE.md"
    peer.write_text(peer.read_text(encoding="utf-8").replace("`agentteams/`", "`peer-edit/`"), encoding="utf-8")
    result = run_sync(tmp_path, since="")
    assert any("claude: instruction fences authority_hierarchy differ" in n for n in result.notices)
    assert "peer-edit" not in peer.read_text(encoding="utf-8")


def test_refresh_names_a_fence_it_took_from_a_peer(tmp_path: Path) -> None:
    _pinned_project(tmp_path)
    (tmp_path / ".github" / "copilot-instructions.md").write_text(_V1_STALE, encoding="utf-8")
    result = refresh_hub_instructions(tmp_path)
    assert any("took fence 'constitutional_core' v=3 from claude" in n and "review" in n for n in result.notices)


@pytest.mark.parametrize("content", [None, "# Hand-written, no fences\n"])
def test_a_missing_or_fenceless_pin_is_never_told_to_use_update_merge(tmp_path: Path, content: str | None) -> None:
    """--update --merge skips such a file and doesn't recreate a missing one, so it can't be the remedy."""
    _pinned_project(tmp_path)
    pin_file = tmp_path / ".github" / "copilot-instructions.md"
    pin_file.unlink() if content is None else pin_file.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="restore it from git") as exc:
        run_sync(tmp_path, since="")
    assert "--update" not in str(exc.value)


def test_sync_init_refusal_does_not_suggest_the_refresh_it_cannot_run(tmp_path: Path) -> None:
    (tmp_path / ".github" / "agents").mkdir(parents=True)
    (tmp_path / ".github" / "agents" / "orchestrator.agent.md").write_text(_ORCH, encoding="utf-8")
    (tmp_path / ".github" / "copilot-instructions.md").write_text(_V1_STALE, encoding="utf-8")
    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / ".claude" / "agents" / "orchestrator.md").write_text(_ORCH, encoding="utf-8")
    (tmp_path / ".claude" / "CLAUDE.md").write_text(_V3, encoding="utf-8")
    _git(tmp_path, "init", "-q")
    with pytest.raises(ValueError, match="--update --merge") as exc:
        sync_init(tmp_path, pin="copilot-vscode", frameworks=["copilot-vscode", "claude"])
    assert "--sync-refresh-instructions" not in str(exc.value)
