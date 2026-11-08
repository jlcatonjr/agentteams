"""Pinned-sync low-severity fixes (remediation-log 2026-11-04 rows 645 and 651, 2026-11-05 row 657).

Each test runs real ``sync_init`` / ``run_sync`` passes (or the projection they call) and fails on the code
before the fix: a missing pin directory gave a traceback; a dry run classified the peers against the old hub;
a peer's untemplated fence at a higher ``v=`` blocked every sync; a fence-less hub never refreshed; a goose
pin's bridge fence reached CLAUDE.md; only one of two same-slug copies was deleted; hub deletions had no backup.
"""

from __future__ import annotations

import contextlib
import io
import subprocess
from pathlib import Path

import pytest

import agentteams

from agentteams.canonical import load_canonical, materialize_canonical
from agentteams.interop import export_to_cai, import_from_cai
from agentteams.interop_helpers import keep_newer_fences
from agentteams.multi_sync import run_sync, sync_init
from agentteams.sync_pin import update_last_synced_commit
from agentteams.sync_roster import remove_dropped_peer_agents

_AGENT = "---\nname: {slug}\ndescription: The {slug} agent.\ntools: [read]\n---\n\n# {slug}\n\nDoes {slug} work.\n"
_PIN = Path(".github/agents")
_PEER = Path(".claude/agents")
_PIN_INSTR = Path(".github/copilot-instructions.md")
_PEER_INSTR = Path(".claude/CLAUDE.md")
_HUB = Path(".agentteams/canonical")


def _fence(sid: str, v: int, body: str) -> str:
    return f"<!-- AGENTTEAMS:BEGIN {sid} v={v} -->\n{body}\n<!-- AGENTTEAMS:END {sid} -->\n"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True,
                   capture_output=True)


def _commit(root: Path, *, synced: bool = False) -> None:
    _git(root, "add", "-Af")
    _git(root, "commit", "-qm", "c", "--allow-empty")
    if synced:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        update_last_synced_commit(root, head)


def _quiet(fn, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202 - test helper
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


def _team(root: Path, slugs: tuple[str, ...] = ("orchestrator", "helper"), *, instructions: str = "",
          frameworks: tuple[str, ...] = ("copilot-vscode", "claude")) -> None:
    (root / _PIN).mkdir(parents=True)
    for slug in slugs:
        (root / _PIN / f"{slug}.agent.md").write_text(_AGENT.format(slug=slug), encoding="utf-8")
    if instructions:
        (root / _PIN_INSTR).write_text(instructions, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "commit", "-q", "--allow-empty", "-m", "init")
    _quiet(sync_init, root, pin="copilot-vscode", frameworks=list(frameworks))
    _commit(root, synced=True)


def _change_pin(root: Path) -> None:
    path = root / _PIN / "orchestrator.agent.md"
    path.write_text(path.read_text(encoding="utf-8").replace("Does orchestrator", "Routes orchestrator"),
                    encoding="utf-8")


def test_a_pin_with_no_agents_directory_is_refused_by_name(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    with pytest.raises(ValueError, match=r"sync refused, nothing was written: the pin \(agents-md\) has no agents"):
        sync_init(tmp_path, pin="agents-md", frameworks=["agents-md", "claude"])
    assert not (tmp_path / ".agentteams").exists()


def test_a_deleted_pin_directory_is_refused_by_name_and_the_hub_is_kept(tmp_path: Path) -> None:
    _team(tmp_path)
    hub_before = sorted(p.name for p in (tmp_path / _HUB / "agents").glob("*.md"))
    for path in (tmp_path / _PIN).rglob("*"):
        if path.is_file():
            path.unlink()
    for path in sorted((tmp_path / _PIN).rglob("*"), reverse=True):
        path.rmdir()
    (tmp_path / _PIN).rmdir()
    _commit(tmp_path)
    with pytest.raises(ValueError, match=r"the pin \(copilot-vscode\) has no agents directory"):
        run_sync(tmp_path)
    assert sorted(p.name for p in (tmp_path / _HUB / "agents").glob("*.md")) == hub_before
    assert (tmp_path / _PEER / "helper.md").is_file()


def test_a_dry_run_reports_what_the_real_run_does(tmp_path: Path) -> None:
    _team(tmp_path, ("orchestrator", "helper", "writer"), frameworks=("copilot-vscode", "claude", "agents-md"))
    helper = tmp_path / _PIN / "helper.agent.md"
    helper.write_text(helper.read_text(encoding="utf-8").replace("The helper agent.", "The changed helper."),
                      encoding="utf-8")
    (tmp_path / _PIN / "newbie.agent.md").write_text(_AGENT.format(slug="newbie"), encoding="utf-8")
    (tmp_path / _PIN / "writer.agent.md").unlink()
    _commit(tmp_path)

    def rows(result) -> list[tuple[str, str, str, str]]:  # noqa: ANN001 - SyncResult
        return sorted((c.framework, c.agent, c.field_name, c.resolution) for c in result.conflicts)

    dry = _quiet(run_sync, tmp_path, dry_run=True)
    assert subprocess.check_output(["git", "status", "--porcelain"], cwd=tmp_path, text=True) == ""
    real = _quiet(run_sync, tmp_path)
    assert rows(dry) == rows(real)
    assert ("claude", "writer", "__agent__", "removed (the pin removed this agent)") in rows(dry)
    assert dry.applied_fields == real.applied_fields
    assert dry.projected_frameworks == real.projected_frameworks
    assert [n.replace("would be ", "") for n in dry.notices] == real.notices


def test_a_peer_cannot_block_sync_with_an_untemplated_fence(tmp_path: Path) -> None:
    _team(tmp_path, instructions="# Team\n\n" + _fence("project_notes", 1, "Pin notes."))
    peer = tmp_path / _PEER_INSTR
    text = peer.read_text(encoding="utf-8")
    assert "Pin notes." in text
    peer.write_text(text.replace("project_notes v=1", "project_notes v=9").replace("Pin notes.", "Peer notes."),
                    encoding="utf-8")
    _change_pin(tmp_path)
    _commit(tmp_path)
    result = _quiet(run_sync, tmp_path)  # refused before the fix: "other frameworks deploy newer fences"
    text = peer.read_text(encoding="utf-8")
    assert "Pin notes." in text and "Peer notes." not in text
    assert any("none writes it" in n for n in result.notices)


def test_a_fence_a_release_writes_from_code_is_still_never_lowered() -> None:
    # write_policy is emitted by write_policy.py, not a template (review MED-1): it keeps "never lowered".
    kept, notices = keep_newer_fences(_fence("write_policy", 0, "pin"), _fence("write_policy", 1, "peer"))
    assert "peer" in kept and notices and "kept newer fence" in notices[0]
    kept, _ = keep_newer_fences(_fence("project_notes", 1, "pin"), _fence("project_notes", 2, "peer"))
    assert "pin" in kept and "peer" not in kept


def test_the_fences_a_release_writes_from_code_are_exactly_these() -> None:
    # A fence-looking literal added to the package raises that id's ceiling (review LOW-A): make it visible here.
    from agentteams.interop_helpers import _VERSIONED_BEGIN_RE

    package = Path(agentteams.__file__).parent
    found = {(m["sid"], int(m["v"])) for p in package.rglob("*.py")
             for m in _VERSIONED_BEGIN_RE.finditer(p.read_text(encoding="utf-8"))}
    assert found == {("write_policy", 1), ("goose_operational_notes", 1), ("content", 1)}


def test_a_fence_less_hub_refreshes_from_a_fenced_pin(tmp_path: Path) -> None:
    _team(tmp_path, instructions="# Team\n\nNo fences yet.\n")
    (tmp_path / _PIN_INSTR).write_text("# Team\n\n" + _fence("project_notes", 1, "Now fenced."), encoding="utf-8")
    _change_pin(tmp_path)
    _commit(tmp_path)
    result = _quiet(run_sync, tmp_path)
    hub = load_canonical(tmp_path / _HUB)
    assert "Now fenced." in hub["instructions_binding"]["content"]
    assert any("had no AGENTTEAMS fences; replaced with the pin's" in n for n in result.notices)
    assert not any("not refreshed" in n for n in result.notices)


def test_goose_owned_fences_reach_goose_only(tmp_path: Path) -> None:
    (tmp_path / _PIN).mkdir(parents=True)
    (tmp_path / _PIN / "orchestrator.agent.md").write_text(_AGENT.format(slug="orchestrator"), encoding="utf-8")
    (tmp_path / _PIN_INSTR).write_text("# Team\n\n" + _fence("goose_bridge_entry", 2, "Goose bridge entry.")
                                       + "\n" + _fence("project_notes", 1, "Shared notes."), encoding="utf-8")
    cai = export_to_cai(tmp_path / _PIN, "copilot-vscode")
    _quiet(import_from_cai, cai, "claude", tmp_path / _PEER, overwrite=True)
    claude = (tmp_path / _PEER_INSTR).read_text(encoding="utf-8")
    assert "Shared notes." in claude and "goose_bridge_entry" not in claude
    _quiet(import_from_cai, cai, "goose", tmp_path / ".goose" / "recipes", overwrite=True)
    assert "goose_bridge_entry" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")


def test_every_same_slug_copy_of_a_dropped_agent_is_removed(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    for rel in ("helper.md", "sub/helper.md", "keep.md"):
        (tmp_path / rel).write_text("x", encoding="utf-8")
    cai = {"agents": [{"slug": "helper", "source_path": "helper.md"},
                      {"slug": "helper", "source_path": "sub/helper.md"},
                      {"slug": "keep", "source_path": "keep.md"}]}
    assert remove_dropped_peer_agents(tmp_path, cai, ["helper"], dry_run=False) == ["helper"]
    assert not (tmp_path / "helper.md").exists() and not (tmp_path / "sub" / "helper.md").exists()
    assert (tmp_path / "keep.md").exists()


def test_a_hub_agent_the_pin_removed_is_backed_up(tmp_path: Path) -> None:
    _team(tmp_path)
    (tmp_path / _PIN / "helper.agent.md").unlink()
    _commit(tmp_path)
    _quiet(run_sync, tmp_path)
    assert not (tmp_path / _HUB / "agents" / "helper.md").exists()
    assert list((tmp_path / _HUB / "agents" / ".agentteams-backups").rglob("helper.md*")), "hub copy backed up"
    assert {a["slug"] for a in load_canonical(tmp_path / _HUB)["agents"]} == {"orchestrator"}
    materialize_canonical(load_canonical(tmp_path / _HUB), tmp_path / _HUB)  # the backup never reads as an agent
    assert {a["slug"] for a in load_canonical(tmp_path / _HUB)["agents"]} == {"orchestrator"}


@pytest.mark.parametrize("target, instr", [
    ("claude", Path(".claude/CLAUDE.md")),
    ("grok", Path("AGENTS.md")),
    ("goose", Path("AGENTS.md")),
])
def test_a_planted_codex_write_policy_fence_reaches_codex_only(tmp_path: Path, target: str, instr: Path) -> None:
    """CX-1 @security finding (1), operator ruling 2026-11-08: Codex's write policy is a framework-owned fence."""
    (tmp_path / _PIN).mkdir(parents=True)
    (tmp_path / _PIN / "orchestrator.agent.md").write_text(_AGENT.format(slug="orchestrator"), encoding="utf-8")
    (tmp_path / _PIN_INSTR).write_text("# Team\n\n" + _fence("codex_write_policy", 1, "Planted Codex policy.")
                                       + "\n" + _fence("project_notes", 1, "Shared notes."), encoding="utf-8")
    cai = export_to_cai(tmp_path / _PIN, "copilot-vscode")
    out = {"claude": _PEER, "grok": Path(".grok/agents"), "goose": Path(".goose/recipes")}[target]
    _quiet(import_from_cai, cai, target, tmp_path / out, overwrite=True)
    text = (tmp_path / instr).read_text(encoding="utf-8")
    assert "Shared notes." in text and "codex_write_policy" not in text and "Planted Codex policy." not in text


def test_a_codex_write_policy_fence_never_reaches_copilot_instructions(tmp_path: Path) -> None:
    (tmp_path / _PEER).mkdir(parents=True)
    (tmp_path / _PEER / "orchestrator.md").write_text(_AGENT.format(slug="orchestrator"), encoding="utf-8")
    (tmp_path / _PEER_INSTR).write_text("# Team\n\n" + _fence("codex_write_policy", 1, "Planted Codex policy.")
                                        + "\n" + _fence("project_notes", 1, "Shared notes."), encoding="utf-8")
    cai = export_to_cai(tmp_path / _PEER, "claude")
    _quiet(import_from_cai, cai, "copilot-vscode", tmp_path / _PIN, overwrite=True)
    text = (tmp_path / _PIN_INSTR).read_text(encoding="utf-8")
    assert "Shared notes." in text and "codex_write_policy" not in text


def test_codex_s_own_import_keeps_its_write_policy_fence(tmp_path: Path) -> None:
    (tmp_path / _PIN).mkdir(parents=True)
    (tmp_path / _PIN / "orchestrator.agent.md").write_text(_AGENT.format(slug="orchestrator"), encoding="utf-8")
    (tmp_path / _PIN_INSTR).write_text("# Team\n\n" + _fence("codex_write_policy", 1, "Codex policy.")
                                       + "\n" + _fence("project_notes", 1, "Shared notes."), encoding="utf-8")
    cai = export_to_cai(tmp_path / _PIN, "copilot-vscode")
    _quiet(import_from_cai, cai, "codex", tmp_path / ".codex" / "agents", overwrite=True)
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "Codex policy." in text and text.count("AGENTTEAMS:BEGIN codex_write_policy ") == 1
