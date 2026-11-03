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

from agentteams.cli.sync_switch import run_sync_cli
from agentteams.interop_helpers import FENCE_DOWNGRADE_NOTICE, keep_newer_fences, merge_instruction_file
from agentteams.multi_sync import run_sync, sync_init
from agentteams.sync_hub_refresh import refresh_hub_instructions


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


def test_sync_reports_but_never_takes_a_peers_newer_fence(tmp_path: Path) -> None:
    """Pin wins: an automatic --sync does not spread a peer file's fence body to every framework."""
    _pinned_project(tmp_path)
    (tmp_path / ".github" / "copilot-instructions.md").write_text(_V1_STALE + _USER_RULE, encoding="utf-8")
    peer = _V3.replace("HMAC management-directive bound", "PEER_ONLY_TEXT") + _USER_RULE
    (tmp_path / ".claude" / "CLAUDE.md").write_text(peer, encoding="utf-8")
    result = run_sync(tmp_path, since="")
    assert "PEER_ONLY_TEXT" not in _hub_instructions(tmp_path)
    pin_file = (tmp_path / ".github" / "copilot-instructions.md").read_text(encoding="utf-8")
    assert "PEER_ONLY_TEXT" not in pin_file and "constitutional_core v=1" in pin_file
    assert "constitutional_core v=3" in (tmp_path / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")  # guard
    assert any("claude deploys newer fences than the pin" in n for n in result.notices)


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
