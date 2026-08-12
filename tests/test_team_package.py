"""Tests for agentteams/team_package.py and the --package-team CLI surface
(open-items remediation OPEN-5): a durable canonical directory plus its
generic bridge, zipped into one portable archive.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import pytest

from agentteams.cli.app import main
from agentteams.interop import run_interop
from agentteams.team_package import package_team

REPO = Path(__file__).resolve().parents[1]
_COPILOT = REPO / ".github" / "agents"
_CLAUDE = REPO / ".claude" / "agents"

pytestmark = pytest.mark.skipif(
    not any(_COPILOT.glob("*.agent.md")),
    reason="repo source team (.github/agents) not found or gitignored",
)


def test_package_team_creates_zip_with_canonical_and_bridge(tmp_path: Path):
    out_zip = tmp_path / "team.zip"
    result = package_team(source_dir=_COPILOT, output_zip=out_zip)

    assert result.success
    assert result.dry_run is False
    assert result.source_framework == "copilot-vscode"
    assert result.agent_count > 0
    assert out_zip.is_file()

    with zipfile.ZipFile(out_zip) as zf:
        names = zf.namelist()
    canonical_entries = [n for n in names if n.startswith(".agentteams/canonical/")]
    bridge_entries = [n for n in names if n.startswith("references/bridges/copilot-vscode-to-generic/")]
    assert any(n.endswith("team.cai.json") for n in canonical_entries)
    assert any(n.endswith("orchestrator.md") for n in canonical_entries)
    assert {"bridge-manifest.json", "agent-inventory.md", "quickstart-snippet.md",
            "entrypoint.md", "domain-boundary.md"} == {
        n.rsplit("/", 1)[-1] for n in bridge_entries
    }
    # generic target writes zero native consumer files into the zip
    assert not any(n in ("CLAUDE.md", "AGENTS.md") or n.startswith(".claude/") for n in names)


def test_package_team_canonical_and_bridge_roster_parity(tmp_path: Path):
    """The canonical half (materialize_canonical, via export_to_cai) and the
    generic-bridge half (run_bridge, via bridge_sources' own independent
    front-matter reader) parse source_dir through two different code paths —
    not a shared export, see the module docstring. Pin that they agree on the
    agent roster for a well-formed source rather than just asserting fixed
    filenames exist on each side in isolation."""
    out_zip = tmp_path / "team.zip"
    package_team(source_dir=_COPILOT, output_zip=out_zip)

    with zipfile.ZipFile(out_zip) as zf:
        canonical_slugs = {
            n.rsplit("/", 1)[-1][: -len(".md")]
            for n in zf.namelist()
            if n.startswith(".agentteams/canonical/agents/") and n.endswith(".md")
        }
        inventory_md = zf.read(
            "references/bridges/copilot-vscode-to-generic/agent-inventory.md"
        ).decode("utf-8")

    bridge_slugs = {
        Path(m).stem.removesuffix(".agent")
        for m in re.findall(r"`([^`]+\.agent\.md)`", inventory_md)
    }
    assert bridge_slugs, "agent-inventory.md parsed zero source-file references"
    assert canonical_slugs == bridge_slugs


@pytest.mark.skipif(not _CLAUDE.is_dir(), reason="repo source team (.claude/agents) not found")
def test_package_team_includes_skills_when_source_has_them(tmp_path: Path):
    """F.3 close-out (conflict-log.csv, D.7 skills/-in-zip finding): the docs
    already document that a packaged team with first-class skills ships them
    under .agentteams/canonical/skills/<slug>/SKILL.md — this pins that the
    zip actually contains them, using .claude/agents (the only repo source
    team with has_skill_concept()==True) rather than the .github/agents
    fixture the rest of this file uses."""
    out_zip = tmp_path / "team.zip"
    result = package_team(source_dir=_CLAUDE, output_zip=out_zip, source_framework="claude")
    assert result.success

    with zipfile.ZipFile(out_zip) as zf:
        names = zf.namelist()
    skill_entries = [
        n for n in names
        if n.startswith(".agentteams/canonical/skills/") and n.endswith("SKILL.md")
    ]
    assert skill_entries, "expected at least one skills/<slug>/SKILL.md entry in the zip"


def test_package_team_dry_run_writes_nothing(tmp_path: Path):
    out_zip = tmp_path / "team.zip"
    result = package_team(source_dir=_COPILOT, output_zip=out_zip, dry_run=True)

    assert result.success
    assert result.dry_run is True
    assert not out_zip.exists()
    assert list(tmp_path.iterdir()) == []  # no staging artifacts left behind either


def test_package_team_rejects_canonical_source(tmp_path: Path):
    from agentteams.canonical import materialize_canonical
    from agentteams.interop import export_to_cai

    canon_dir = tmp_path / "canonical"
    materialize_canonical(export_to_cai(_COPILOT, "copilot-vscode"), canon_dir)

    with pytest.raises(ValueError, match="not a canonical directory"):
        package_team(source_dir=canon_dir, output_zip=tmp_path / "out.zip")


def test_package_team_round_trips_through_canonical_import(tmp_path: Path):
    out_zip = tmp_path / "team.zip"
    package_team(source_dir=_COPILOT, output_zip=out_zip)

    unpacked = tmp_path / "unpacked"
    with zipfile.ZipFile(out_zip) as zf:
        zf.extractall(unpacked)

    canon_dir = unpacked / ".agentteams" / "canonical"
    assert (canon_dir / "team.cai.json").is_file()

    import_out = tmp_path / "claude-import"
    result = run_interop(
        source_dir=canon_dir, target_framework="claude", target_dir=import_out,
        source_framework="canonical", mode="direct", dry_run=False, overwrite=True,
    )
    assert result.success
    assert any(import_out.glob("*.md"))


def test_package_team_does_not_leak_packaging_machine_absolute_path(tmp_path: Path):
    """2026-08-10 adversarial finding (step D.7): run_bridge's rel_to_root()
    only relativizes a path when it is a descendant of output_root. Every
    OTHER run_bridge caller passes a source_dir that already lives under
    output_root; team_package.py originally didn't, so bridge-manifest.json's
    source_dir field and agent-inventory.md's Source-file column silently fell
    back to the absolute path -- leaking the packaging machine's home
    directory/username into an artifact meant to be handed to a third party
    (the exact leak class rel_to_root's own docstring, commit 1937cbc, was
    written to close). Fixed by copying source_dir under the bridge's own
    output_root before calling run_bridge."""
    out_zip = tmp_path / "team.zip"
    package_team(source_dir=_COPILOT, output_zip=out_zip)

    home = str(Path.home())
    with zipfile.ZipFile(out_zip) as zf:
        manifest = json.loads(zf.read("references/bridges/copilot-vscode-to-generic/bridge-manifest.json"))
        inventory = zf.read("references/bridges/copilot-vscode-to-generic/agent-inventory.md").decode("utf-8")

    assert home not in manifest["source_dir"]
    assert home not in inventory
    assert not str(_COPILOT.resolve()) in manifest["source_dir"]


def test_package_cli_dry_run(tmp_path: Path, capsys):
    out_zip = tmp_path / "team.zip"
    code = main(["--package-team", str(_COPILOT), "--output", str(out_zip), "--dry-run"])
    assert code == 0
    assert not out_zip.exists()
    out = capsys.readouterr().out
    assert "Would package" in out


def test_package_cli_real_run(tmp_path: Path, capsys):
    out_zip = tmp_path / "team.zip"
    code = main(["--package-team", str(_COPILOT), "--output", str(out_zip)])
    assert code == 0
    assert out_zip.is_file()
    out = capsys.readouterr().out
    assert "Wrote" in out


def test_package_team_mutually_exclusive_with_convert_from():
    with pytest.raises(SystemExit) as exc:
        main(["--package-team", str(_COPILOT), "--convert-from", str(_COPILOT)])
    assert exc.value.code == 2


def test_package_team_mutually_exclusive_with_self():
    with pytest.raises(SystemExit) as exc:
        main(["--package-team", str(_COPILOT), "--self"])
    assert exc.value.code == 2


def test_package_source_framework_requires_package_team():
    with pytest.raises(SystemExit) as exc:
        main(["--package-source-framework", "claude"])
    assert exc.value.code == 2


def test_package_team_mutually_exclusive_with_fleet():
    with pytest.raises(SystemExit) as exc:
        main(["--package-team", str(_COPILOT), "--fleet", "some.txt", "--update", "--merge"])
    assert exc.value.code == 2


def test_package_team_mutually_exclusive_with_goose_show():
    with pytest.raises(SystemExit) as exc:
        main(["--package-team", str(_COPILOT), "--goose-show"])
    assert exc.value.code == 2


def test_package_team_output_is_existing_directory_fails_cleanly(tmp_path: Path, capsys):
    """Regression: --output pointing at an existing directory (an easy mistake,
    since every other mode's --output IS a directory) must fail with a
    controlled error, not an uncaught IsADirectoryError traceback — must hold
    even with --overwrite, since overwrite is about replacing an existing
    FILE, never about replacing a directory with a file."""
    out_dir = tmp_path / "not-a-zip"
    out_dir.mkdir()

    code = main(["--package-team", str(_COPILOT), "--output", str(out_dir)])
    assert code == 1
    assert "directory" in capsys.readouterr().err.lower()

    code2 = main(["--package-team", str(_COPILOT), "--output", str(out_dir), "--overwrite"])
    assert code2 == 1
    assert "directory" in capsys.readouterr().err.lower()
