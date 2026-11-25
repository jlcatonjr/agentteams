"""``--convert`` resolves host features from the brief's privilege profile (remediation row 208, bl48-41c).

``generate`` sets ``manifest["host_features"]`` before rendering; ``convert_team`` never did, so an emitter gated
on a host feature no-oped on convert.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams import convert
from agentteams.frameworks.claude import ClaudeAdapter

_AGENT = "---\nname: helper\ndescription: The helper agent.\ntools: [read]\n---\n\n# helper\n\nDoes work.\n"


def _seen_features(tmp_path: Path, monkeypatch, profile: str) -> list[str]:
    seen: list[list[str]] = []
    real = ClaudeAdapter.extra_output_files

    def spy(self, manifest):  # noqa: ANN001, ANN202 - test spy
        seen.append(list(manifest.get("host_features", [])))
        return real(self, manifest)

    monkeypatch.setattr(ClaudeAdapter, "extra_output_files", spy)
    src = tmp_path / "src" / ".github" / "agents"
    src.mkdir(parents=True)
    (src / "helper.agent.md").write_text(_AGENT, encoding="utf-8")
    convert.convert_team(src, tmp_path / "out" / ".claude" / "agents", "claude", dry_run=True,
                         project_manifest={"project_name": "P", "privilege_profile": profile})
    assert len(seen) == 1
    return seen[0]


def test_convert_sets_host_features_from_the_profile(tmp_path, monkeypatch):
    assert "claude:sandbox" in _seen_features(tmp_path, monkeypatch, "confined")


def test_cooperative_profile_adds_no_host_feature(tmp_path, monkeypatch):
    assert _seen_features(tmp_path, monkeypatch, "cooperative") == []
