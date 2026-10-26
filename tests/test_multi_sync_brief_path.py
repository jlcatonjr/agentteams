"""Pinned sync finds the team's brief where fleet discovery does, so a root brief.json projects sandbox/.

baseAgent, researchteam and mathAgents keep their brief at the repository root (privilege_profile: confined). The sync
read only .agentteams/brief.json, so their sandbox/ artifacts (launcher, mac-escape-tests.sh, sandbox.sb) were never
re-rendered on an agentteams upgrade: the v1.0.2 gate-8 fix had to be re-rendered into baseAgent by hand.

A brief other than .agentteams/brief.json counts only when it sets privilege_profile itself. Most generated stubs
carry no profile, and the default (confined) must not start a sandbox rollout across those repos on sync.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agentteams import multi_sync as ms

IS_MAC = sys.platform == "darwin"
STUB = ".github/agents/_build-description.json"


def _brief(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("rel", ["brief.json", STUB])
def test_a_root_brief_or_stub_with_a_profile_carries_the_posture(tmp_path: Path, rel: str) -> None:
    _brief(tmp_path / rel, {"project_name": "T", "privilege_profile": "confined", "workspace_write_roots": ["."]})
    priv = ms._read_brief_privilege(tmp_path)
    assert priv["privilege_profile"] == "confined" and priv["privilege_profile_explicit"] is True
    assert priv["workspace_write_roots"] == ["."]


@pytest.mark.parametrize("rel", ["brief.json", STUB])
def test_a_root_brief_or_stub_without_a_profile_projects_no_boundary(tmp_path: Path, rel: str) -> None:
    _brief(tmp_path / rel, {"project_name": "T"})               # inherits the default: no rollout on sync
    assert ms._read_brief_privilege(tmp_path) == {}


def test_the_agentteams_brief_still_takes_the_default(tmp_path: Path) -> None:
    _brief(tmp_path / ".agentteams" / "brief.json", {"project_name": "T"})
    priv = ms._read_brief_privilege(tmp_path)
    assert priv["privilege_profile"] == "confined" and priv["privilege_profile_explicit"] is False


def test_the_agentteams_brief_wins_over_the_root_brief(tmp_path: Path) -> None:
    _brief(tmp_path / ".agentteams" / "brief.json", {"privilege_profile": "cooperative"})
    _brief(tmp_path / "brief.json", {"privilege_profile": "confined"})
    assert ms._read_brief_privilege(tmp_path) == {}


@pytest.mark.parametrize("text", ["{not json", "[1, 2]", json.dumps({"privilege_profile": "cooperative"})])
def test_an_unreadable_or_cooperative_root_brief_projects_no_boundary(tmp_path: Path, text: str) -> None:
    (tmp_path / "brief.json").write_text(text, encoding="utf-8")
    assert ms._read_brief_privilege(tmp_path) == {}


@pytest.mark.parametrize("inside", [False, True])
def test_a_symlinked_brief_is_ignored(tmp_path: Path, inside: bool) -> None:
    repo = tmp_path / "repo"
    real = repo / "docs" / "real.json" if inside else tmp_path / "outside.json"
    _brief(real, {"privilege_profile": "confined"})
    repo.mkdir(exist_ok=True)
    (repo / "brief.json").symlink_to(real)
    assert ms._read_brief_privilege(repo) == {}


def test_projection_never_writes_outside_the_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = ms.FRAMEWORKS["goose"]

    class Escaping(base):  # type: ignore[misc, valid-type]
        def extra_output_files(self, manifest):  # noqa: ANN001, ANN202
            return [("sandbox/confine-run.sh", "inside"), ("../../escape/confine-run.sh", "outside")]

    monkeypatch.setitem(ms.FRAMEWORKS, "goose", Escaping)
    repo = tmp_path / "a" / "repo"
    repo.mkdir(parents=True)
    written = ms._emit_privilege_artifacts(repo, "goose", {"privilege_profile": "confined"}, dry_run=False)
    assert not (tmp_path / "a" / "escape").exists()
    assert all(Path(p).is_relative_to(repo.resolve()) for p in written)
    assert any(p.endswith("sandbox/confine-run.sh") for p in written)


@pytest.mark.skipif(not IS_MAC, reason="goose Seatbelt emits on macOS only")
def test_sync_init_projects_the_boundary_from_a_root_brief(tmp_path: Path) -> None:
    ad = tmp_path / ".claude" / "agents"
    ad.mkdir(parents=True)
    (ad / "orchestrator.md").write_text(
        "---\nname: orchestrator\ndescription: The orchestrator.\n---\n\n# Orchestrator\n\nBody.\n", encoding="utf-8")
    _brief(tmp_path / "brief.json", {"project_name": "T", "privilege_profile": "confined"})
    ms.sync_init(tmp_path, pin="claude", frameworks=["claude", "goose"])
    assert (tmp_path / ".goose" / "sandbox.sb").is_file()
    team = json.loads((tmp_path / ".agentteams" / "canonical" / "team.cai.json").read_text())
    assert team.get("privilege", {}).get("privilege_profile") == "confined"
