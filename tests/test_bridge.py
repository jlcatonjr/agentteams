"""Tests for lightweight cross-framework bridge generation."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.bridge import run_bridge


def _vscode_agent(slug: str) -> str:
    return (
        "---\n"
        f"name: {slug} — Demo\n"
        "description: \"demo role\"\n"
        "user-invokable: true\n"
        "tools: ['read']\n"
        "model: [\"Claude Sonnet 4.6 (copilot)\"]\n"
        "---\n\n"
        f"# {slug}\n\n"
        "Body line one.\n"
    )


def _claude_agent(slug: str) -> str:
    return (
        "---\n"
        f"name: {slug} — Demo\n"
        "description: \"demo role\"\n"
        "allowed-tools: Bash, Read, Write, Edit\n"
        "---\n\n"
        f"# {slug}\n\n"
        "Body line one.\n"
    )


def _cli_agent(slug: str) -> str:
    return f"# {slug}\n\nBody line one.\n"


def _source_rel(framework: str) -> Path:
    if framework == "copilot-vscode":
        return Path(".github/agents")
    if framework == "copilot-cli":
        return Path(".github/copilot")
    return Path(".claude/agents")


def _build_source(framework: str, source_dir: Path) -> None:
    source_dir.mkdir(parents=True, exist_ok=True)
    if framework == "copilot-vscode":
        (source_dir / "orchestrator.agent.md").write_text(_vscode_agent("orchestrator"), encoding="utf-8")
        (source_dir.parent / "copilot-instructions.md").write_text("# Instructions\n", encoding="utf-8")
    elif framework == "copilot-cli":
        (source_dir / "orchestrator.md").write_text(_cli_agent("orchestrator"), encoding="utf-8")
        (source_dir.parent / "copilot-instructions.md").write_text("# Instructions\n", encoding="utf-8")
    else:
        (source_dir / "orchestrator.md").write_text(_claude_agent("orchestrator"), encoding="utf-8")
        (source_dir.parent / "CLAUDE.md").write_text("# Instructions\n", encoding="utf-8")


@pytest.mark.parametrize(
    "source_framework,target_framework",
    [
        ("copilot-vscode", "copilot-cli"),
        ("copilot-vscode", "claude"),
        ("copilot-cli", "copilot-vscode"),
        ("copilot-cli", "claude"),
        ("claude", "copilot-vscode"),
        ("claude", "copilot-cli"),
    ],
)
def test_bridge_generation_all_six_directions(tmp_path: Path, source_framework: str, target_framework: str):
    source_dir = tmp_path / "src" / _source_rel(source_framework)
    _build_source(source_framework, source_dir)

    # capture source snapshot to ensure no source rewrites
    source_before = {p: p.read_text(encoding="utf-8") for p in source_dir.parent.glob("**/*") if p.is_file()}

    result = run_bridge(
        source_dir=source_dir,
        source_framework=source_framework,
        target_framework=target_framework,
        output_root=tmp_path / "out",
        dry_run=False,
        overwrite=True,
        check_only=False,
    )

    assert result.success, f"errors: {result.errors}"
    assert len(result.written) >= 4

    pair_dir = tmp_path / "out" / "references" / "bridges" / f"{source_framework}-to-{target_framework}"
    assert (pair_dir / "bridge-manifest.json").exists()
    assert (pair_dir / "agent-inventory.md").exists()
    assert (pair_dir / "quickstart-snippet.md").exists()
    assert (pair_dir / "entrypoint.md").exists()

    # Ensure source canonical files are unchanged.
    source_after = {p: p.read_text(encoding="utf-8") for p in source_dir.parent.glob("**/*") if p.is_file()}
    assert source_before == source_after


def test_bridge_check_detects_staleness(tmp_path: Path):
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)

    out_root = tmp_path / "out"
    initial = run_bridge(
        source_dir=source_dir,
        source_framework="copilot-vscode",
        target_framework="claude",
        output_root=out_root,
        dry_run=False,
        overwrite=True,
        check_only=False,
    )
    assert initial.success

    # mutate source after bridge generation
    (source_dir / "orchestrator.agent.md").write_text(_vscode_agent("orchestrator") + "\nCHANGED\n", encoding="utf-8")

    checked = run_bridge(
        source_dir=source_dir,
        source_framework="copilot-vscode",
        target_framework="claude",
        output_root=out_root,
        dry_run=False,
        overwrite=False,
        check_only=True,
    )
    assert not checked.success
    assert checked.check_ok is False
    report = Path(checked.check_report_path)
    assert report.exists()
    assert "FAIL" in report.read_text(encoding="utf-8")


def test_bridge_check_passes_when_fresh(tmp_path: Path):
    source_dir = tmp_path / "src" / ".claude" / "agents"
    _build_source("claude", source_dir)

    out_root = tmp_path / "out"
    generated = run_bridge(
        source_dir=source_dir,
        source_framework="claude",
        target_framework="copilot-cli",
        output_root=out_root,
        dry_run=False,
        overwrite=True,
        check_only=False,
    )
    assert generated.success

    checked = run_bridge(
        source_dir=source_dir,
        source_framework="claude",
        target_framework="copilot-cli",
        output_root=out_root,
        dry_run=False,
        overwrite=False,
        check_only=True,
    )
    assert checked.success
    assert checked.check_ok is True
    report = Path(checked.check_report_path)
    assert report.exists()
    assert "PASS" in report.read_text(encoding="utf-8")


def test_bridge_check_missing_manifest_hints_at_refresh(tmp_path: Path):
    """When no manifest exists, --bridge-check should point the user at --bridge-refresh."""
    source_dir = tmp_path / "src" / ".claude" / "agents"
    _build_source("claude", source_dir)

    checked = run_bridge(
        source_dir=source_dir,
        source_framework="claude",
        target_framework="copilot-vscode",
        output_root=tmp_path / "out",
        dry_run=False,
        overwrite=False,
        check_only=True,
    )
    assert not checked.success
    assert checked.check_ok is False
    assert checked.manifest_missing is True
    report = Path(checked.check_report_path)
    assert report.exists()
    text = report.read_text(encoding="utf-8")
    assert "FAIL" in text
    assert "--bridge-refresh" in text
    assert "missing" in text.lower()


def test_bridge_generate_emits_skip_notice_when_files_exist(tmp_path: Path):
    """Generate mode without --bridge-refresh must surface a notice when any file is skipped."""
    source_dir = tmp_path / "src" / ".claude" / "agents"
    _build_source("claude", source_dir)
    out_root = tmp_path / "out"

    first = run_bridge(
        source_dir=source_dir,
        source_framework="claude",
        target_framework="copilot-vscode",
        output_root=out_root,
        dry_run=False,
        overwrite=True,
        check_only=False,
    )
    assert first.success
    assert first.skipped == []
    assert first.notices == []

    # Second run without overwrite: every file already exists, expect notice.
    second = run_bridge(
        source_dir=source_dir,
        source_framework="claude",
        target_framework="copilot-vscode",
        output_root=out_root,
        dry_run=False,
        overwrite=False,
        check_only=False,
    )
    assert second.success
    assert len(second.skipped) >= 4
    assert second.notices, "expected a skip notice when files were skipped"
    assert any("--bridge-refresh" in n for n in second.notices)


def test_bridge_generate_no_notice_when_overwriting(tmp_path: Path):
    """Refresh mode (overwrite=True) must not emit the skip notice."""
    source_dir = tmp_path / "src" / ".claude" / "agents"
    _build_source("claude", source_dir)
    out_root = tmp_path / "out"

    run_bridge(
        source_dir=source_dir,
        source_framework="claude",
        target_framework="copilot-vscode",
        output_root=out_root,
        dry_run=False,
        overwrite=True,
        check_only=False,
    )
    refreshed = run_bridge(
        source_dir=source_dir,
        source_framework="claude",
        target_framework="copilot-vscode",
        output_root=out_root,
        dry_run=False,
        overwrite=True,
        check_only=False,
    )
    assert refreshed.success
    assert refreshed.notices == []

