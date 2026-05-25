"""Regression tests for agentteams.framework_research.

All network calls are stubbed; no fetches occur. Snapshots live in a
temp tree so that production state under tmp/daily-pipeline is never
touched.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentteams import framework_research as fr


def _write_minimal_module(repo_root: Path) -> None:
    """Lay down just enough of the module tree for _load_local_adapter_constants."""
    (repo_root / "agentteams" / "frameworks").mkdir(parents=True)
    (repo_root / "agentteams" / "frameworks" / "claude.py").write_text(
        '_CLAUDE_DEFAULT_ALLOWED_TOOLS = "Bash, Read, Write, Edit"\n'
        '_CLAUDE_REQUIRED_KEYS = {"name", "description"}\n',
        encoding="utf-8",
    )
    (repo_root / "references").mkdir()
    (repo_root / "references" / "claude-agent-infrastructure-expert.md").write_text(
        "# Claude Agent Infrastructure Expert Reference\n\n"
        "Purpose: canonical guidance.\n",
        encoding="utf-8",
    )
    (repo_root / "references" / "copilot-agent-infrastructure-expert.md").write_text(
        "# Copilot Agent Infrastructure Expert Reference\n\n"
        "Purpose: canonical guidance.\n",
        encoding="utf-8",
    )


def _write_snapshot(repo_root: Path, *, with_drift: bool = True) -> None:
    snap_path = repo_root / fr.SNAPSHOT_REL
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    claude_tokens = {
        "front_matter_keys_present": ["model", "name", "tools"] if with_drift else ["description", "name"],
        "locations_present": [".claude/agents", "CLAUDE.md"],
    }
    keys_diff = (
        {"matched": ["name"], "missing_upstream": ["description"], "new_upstream": ["model", "tools"]}
        if with_drift
        else {"matched": ["description", "name"], "missing_upstream": [], "new_upstream": []}
    )
    snapshot = {
        "schema_version": "1.1",
        "framework": "claude",
        "source_url": fr.CLAUDE_DOC_URL,
        "generated_at": "2026-05-25T00:00:00Z",
        "generated_on": "2026-05-25",
        "fetch_status": "ok",
        "fetch_error": "",
        "raw_bytes": 100,
        "upstream_tokens": claude_tokens,
        "local_adapter": {"required_front_matter_keys": ["description", "name"], "default_allowed_tools": ["Bash"]},
        "keys_diff": keys_diff,
        "frameworks": {
            "claude": {
                "label": "Claude Code Sub-Agents",
                "source_url": fr.CLAUDE_DOC_URL,
                "expert_ref": fr.EXPERT_REF_REL,
                "fetch_status": "ok",
                "upstream_tokens": claude_tokens,
            },
            "copilot_vscode": {
                "label": "GitHub Copilot — VS Code",
                "source_url": "https://example.invalid/vscode",
                "expert_ref": fr.COPILOT_EXPERT_REF_REL,
                "fetch_status": "ok",
                "upstream_tokens": {
                    "front_matter_keys_present": ["description", "tools"] if with_drift else [],
                    "locations_present": [".github/agents"],
                },
            },
        },
    }
    snap_path.write_text(json.dumps(snapshot), encoding="utf-8")


def test_build_framework_placeholders_keys(tmp_path, monkeypatch):
    _write_minimal_module(tmp_path)
    _write_snapshot(tmp_path)
    monkeypatch.setattr(fr, "_snapshot_path", lambda root: tmp_path / fr.SNAPSHOT_REL)
    placeholders = fr.build_framework_placeholders(tmp_path, offline=True)
    expected = {
        "FRAMEWORK_RESEARCH_FRAMEWORK",
        "FRAMEWORK_RESEARCH_SOURCE_URL",
        "FRAMEWORK_RESEARCH_GENERATED_ON",
        "FRAMEWORK_RESEARCH_FETCH_STATUS",
        "FRAMEWORK_RESEARCH_TABLE",
        "FRAMEWORK_RESEARCH_STALE_BANNER",
        "FRAMEWORK_RESEARCH_DIFF_SUMMARY",
    }
    assert expected <= set(placeholders.keys())
    assert "claude" in placeholders["FRAMEWORK_RESEARCH_TABLE"]
    assert "copilot_vscode" in placeholders["FRAMEWORK_RESEARCH_TABLE"]


def test_propose_no_drift_returns_empty(tmp_path, monkeypatch):
    _write_minimal_module(tmp_path)
    monkeypatch.setattr(fr, "_snapshot_path", lambda root: tmp_path / fr.SNAPSHOT_REL)
    # Empty snapshot file → no proposal
    proposal = fr.propose_module_patch(tmp_path)
    assert proposal["changes"] == []


def test_propose_with_drift_targets_both_refs(tmp_path, monkeypatch):
    _write_minimal_module(tmp_path)
    _write_snapshot(tmp_path, with_drift=True)
    monkeypatch.setattr(fr, "_snapshot_path", lambda root: tmp_path / fr.SNAPSHOT_REL)
    proposal = fr.propose_module_patch(tmp_path)
    paths = {c["path"] for c in proposal["changes"]}
    assert fr.EXPERT_REF_REL in paths
    assert fr.COPILOT_EXPERT_REF_REL in paths
    for change in proposal["changes"]:
        assert change["operation"] == "append_or_replace_section"
        assert "Observed Upstream Tokens" in change["new_text"]


def test_apply_refuses_outside_allow_list(tmp_path, monkeypatch):
    _write_minimal_module(tmp_path)
    monkeypatch.setattr(fr, "_snapshot_path", lambda root: tmp_path / fr.SNAPSHOT_REL)
    bad_proposal = {
        "changes": [
            {
                "path": "agentteams/frameworks/claude.py",
                "operation": "append_or_replace_section",
                "new_text": "evil",
            }
        ]
    }
    with pytest.raises(RuntimeError, match="allow-list"):
        fr.apply_module_patch(bad_proposal, tmp_path)


def test_apply_refuses_in_ci(tmp_path, monkeypatch):
    _write_minimal_module(tmp_path)
    _write_snapshot(tmp_path, with_drift=True)
    monkeypatch.setattr(fr, "_snapshot_path", lambda root: tmp_path / fr.SNAPSHOT_REL)
    monkeypatch.setenv("CI", "true")
    monkeypatch.delenv("AGENTTEAMS_ALLOW_CI_APPLY", raising=False)
    proposal = fr.propose_module_patch(tmp_path)
    assert proposal["changes"], "expected drift in fixture"
    with pytest.raises(RuntimeError, match="CI"):
        fr.apply_module_patch(proposal, tmp_path)


def test_apply_runs_in_ci_with_explicit_marker(tmp_path, monkeypatch):
    """T2.D3: AGENTTEAMS_ALLOW_CI_APPLY=1 lifts the CI refusal."""
    _write_minimal_module(tmp_path)
    _write_snapshot(tmp_path, with_drift=True)
    monkeypatch.setattr(fr, "_snapshot_path", lambda root: tmp_path / fr.SNAPSHOT_REL)
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("AGENTTEAMS_ALLOW_CI_APPLY", "1")
    proposal = fr.propose_module_patch(tmp_path)
    result = fr.apply_module_patch(proposal, tmp_path)
    assert result["applied"], "explicit marker should permit CI apply"


def test_apply_writes_observation_blocks(tmp_path, monkeypatch):
    _write_minimal_module(tmp_path)
    _write_snapshot(tmp_path, with_drift=True)
    monkeypatch.setattr(fr, "_snapshot_path", lambda root: tmp_path / fr.SNAPSHOT_REL)
    monkeypatch.delenv("CI", raising=False)
    proposal = fr.propose_module_patch(tmp_path)
    result = fr.apply_module_patch(proposal, tmp_path)
    assert sorted(result["applied"]) == sorted({fr.EXPERT_REF_REL, fr.COPILOT_EXPERT_REF_REL})
    claude_text = (tmp_path / fr.EXPERT_REF_REL).read_text(encoding="utf-8")
    copilot_text = (tmp_path / fr.COPILOT_EXPERT_REF_REL).read_text(encoding="utf-8")
    assert "Observed Upstream Tokens — `claude`" in claude_text
    assert "Observed Upstream Tokens — `copilot_vscode`" in copilot_text


def test_refresh_snapshot_offline_returns_cached(tmp_path, monkeypatch):
    _write_minimal_module(tmp_path)
    _write_snapshot(tmp_path, with_drift=False)
    monkeypatch.setattr(fr, "_snapshot_path", lambda root: tmp_path / fr.SNAPSHOT_REL)
    snap = fr.refresh_snapshot(tmp_path, offline=True)
    assert snap.get("framework") == "claude"
    assert snap.get("generated_on") == "2026-05-25"
