"""Tests for lightweight cross-framework bridge generation."""

from __future__ import annotations

import json
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


def test_bridge_merge_preserves_content_outside_fence(tmp_path: Path):
    """--bridge-merge must not touch content outside AGENTTEAMS-BRIDGE fences."""
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)
    out_root = tmp_path / "out"

    # First-time generation creates fenced target files.
    first = run_bridge(
        source_dir=source_dir,
        source_framework="copilot-vscode",
        target_framework="claude",
        output_root=out_root,
        dry_run=False,
        overwrite=True,
        check_only=False,
    )
    assert first.success

    claude_md = out_root / "CLAUDE.md"
    original = claude_md.read_text(encoding="utf-8")
    assert "AGENTTEAMS-BRIDGE:BEGIN claude-bridge-entry" in original

    # Consumer adds content outside the fence.
    customized = original + "\n## Consumer Notes\n\nProject-specific guidance.\n"
    claude_md.write_text(customized, encoding="utf-8")

    # Merge re-run: fenced region may update, outside content preserved.
    merged = run_bridge(
        source_dir=source_dir,
        source_framework="copilot-vscode",
        target_framework="claude",
        output_root=out_root,
        dry_run=False,
        overwrite=False,
        check_only=False,
        merge_only=True,
    )
    assert merged.success

    final = claude_md.read_text(encoding="utf-8")
    assert "## Consumer Notes" in final
    assert "Project-specific guidance." in final
    assert "AGENTTEAMS-BRIDGE:BEGIN claude-bridge-entry" in final


def test_bridge_merge_skips_files_without_fence(tmp_path: Path):
    """--bridge-merge skips existing target files that lack any bridge fence."""
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)
    out_root = tmp_path / "out"

    # Pre-create CLAUDE.md WITHOUT any bridge fence (legacy consumer state).
    (out_root).mkdir(parents=True, exist_ok=True)
    (out_root / "CLAUDE.md").write_text("# Legacy Claude entry\n\nNo fences here.\n", encoding="utf-8")

    result = run_bridge(
        source_dir=source_dir,
        source_framework="copilot-vscode",
        target_framework="claude",
        output_root=out_root,
        dry_run=False,
        overwrite=False,
        check_only=False,
        merge_only=True,
    )
    assert result.success
    assert str(out_root / "CLAUDE.md") in result.skipped

    report_path = out_root / "references" / "bridges" / "copilot-vscode-to-claude" / "bridge-merge.report.md"
    assert report_path.exists()
    report = report_path.read_text(encoding="utf-8")
    assert "no AGENTTEAMS-BRIDGE fence" in report


def test_bridge_merge_creates_missing_files(tmp_path: Path):
    """--bridge-merge creates target files that do not yet exist."""
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)
    out_root = tmp_path / "out"

    result = run_bridge(
        source_dir=source_dir,
        source_framework="copilot-vscode",
        target_framework="claude",
        output_root=out_root,
        dry_run=False,
        overwrite=False,
        check_only=False,
        merge_only=True,
    )
    assert result.success
    assert (out_root / "CLAUDE.md").exists()
    assert (out_root / ".claude" / "agent-team.md").exists()


def test_bridge_emits_domain_boundary(tmp_path: Path):
    """Every bridge run emits domain-boundary.md under references/bridges/<pair>/."""
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)
    out_root = tmp_path / "out"

    run_bridge(
        source_dir=source_dir,
        source_framework="copilot-vscode",
        target_framework="claude",
        output_root=out_root,
        dry_run=False,
        overwrite=True,
        check_only=False,
    )
    boundary = out_root / "references" / "bridges" / "copilot-vscode-to-claude" / "domain-boundary.md"
    assert boundary.exists()
    text = boundary.read_text(encoding="utf-8")
    assert "memory-history retrieval only" in text
    assert "separate" in text.lower()


def test_bridge_emits_recall_skill_for_claude_target(tmp_path: Path):
    """Claude target with emit_skills=True (default) emits .claude/skills/recall.md."""
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)
    out_root = tmp_path / "out"

    run_bridge(
        source_dir=source_dir,
        source_framework="copilot-vscode",
        target_framework="claude",
        output_root=out_root,
        dry_run=False,
        overwrite=True,
        check_only=False,
    )
    assert (out_root / ".claude" / "skills" / "recall.md").exists()


def test_bridge_quickstart_and_entrypoint_advertise_retrieval(tmp_path: Path):
    """Bridge quickstart and entrypoint must surface the memory-index retrieval CLI.

    Closes the consumption-loop defect: prior to this, consumers reading the
    bridge artifacts had no hint that --query-index existed. The bridge is
    the bridge consumer's primary documentation surface, so the retrieval
    affordance must appear here, not only in the consumer-side CLAUDE.md.
    """
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)
    out_root = tmp_path / "out"

    run_bridge(
        source_dir=source_dir,
        source_framework="copilot-vscode",
        target_framework="claude",
        output_root=out_root,
        dry_run=False,
        overwrite=True,
        check_only=False,
    )
    pair_dir = out_root / "references" / "bridges" / "copilot-vscode-to-claude"
    quickstart = (pair_dir / "quickstart-snippet.md").read_text(encoding="utf-8")
    entrypoint = (pair_dir / "entrypoint.md").read_text(encoding="utf-8")

    assert "--query-index" in quickstart
    assert "--query-strategy vector" in quickstart
    assert "--query-index" in entrypoint
    assert "--query-strategy vector" in entrypoint
    assert "domain-boundary.md" in entrypoint


def test_bridge_skips_recall_skill_when_disabled(tmp_path: Path):
    """emit_skills=False suppresses recall.md emission."""
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)
    out_root = tmp_path / "out"

    run_bridge(
        source_dir=source_dir,
        source_framework="copilot-vscode",
        target_framework="claude",
        output_root=out_root,
        dry_run=False,
        overwrite=True,
        check_only=False,
        emit_skills=False,
    )
    assert not (out_root / ".claude" / "skills" / "recall.md").exists()


# --------------------------------------------------------------------------
# Phase 2: Goose bridge TARGET (copilot/claude -> goose)
# --------------------------------------------------------------------------

_GOOSE_TARGET_FILES = ("AGENTS.md", ".goosehints", ".goose/README.md")
_GOOSE_FENCE_REGIONS = ("goose-bridge-entry", "goose-bridge-hints", "goose-bridge-readme")


@pytest.mark.parametrize("source_framework", ["copilot-vscode", "claude"])
def test_bridge_goose_first_time_creates_exact_file_set(tmp_path: Path, source_framework: str):
    """T1: first-time goose bridge writes exactly AGENTS.md/.goosehints/.goose/README.md,
    each carrying its specific AGENTTEAMS-BRIDGE region, plus the pair-dir artifacts."""
    source_dir = tmp_path / "src" / _source_rel(source_framework)
    _build_source(source_framework, source_dir)
    out_root = tmp_path / "out"

    result = run_bridge(
        source_dir=source_dir,
        source_framework=source_framework,
        target_framework="goose",
        output_root=out_root,
    )
    assert result.success, f"errors: {result.errors}"

    for rel, region in zip(_GOOSE_TARGET_FILES, _GOOSE_FENCE_REGIONS):
        path = out_root / rel
        assert path.exists(), f"missing {rel}"
        body = path.read_text(encoding="utf-8")
        assert f"AGENTTEAMS-BRIDGE:BEGIN {region}" in body
    # .goosehints integrates the bridged brief via @AGENTS.md
    assert "@AGENTS.md" in (out_root / ".goosehints").read_text(encoding="utf-8")
    # pair-dir bridge-internal artifacts exist
    pair_dir = out_root / "references" / "bridges" / f"{source_framework}-to-goose"
    assert (pair_dir / "bridge-manifest.json").exists()
    assert (pair_dir / "agent-inventory.md").exists()


def test_bridge_goose_merge_updates_fence_preserves_outside(tmp_path: Path):
    """T2: --bridge-merge re-renders only the fenced region of AGENTS.md."""
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)
    out_root = tmp_path / "out"
    agents_md = out_root / "AGENTS.md"
    agents_md.parent.mkdir(parents=True, exist_ok=True)
    agents_md.write_text(
        "# Agent Team (Goose bridge)\n\nKEEP THIS USER LINE\n\n"
        "<!-- AGENTTEAMS-BRIDGE:BEGIN goose-bridge-entry v=1 -->\nSTALE BODY\n"
        "<!-- AGENTTEAMS-BRIDGE:END goose-bridge-entry -->\n",
        encoding="utf-8",
    )
    result = run_bridge(
        source_dir=source_dir, source_framework="copilot-vscode",
        target_framework="goose", output_root=out_root, merge_only=True,
    )
    after = agents_md.read_text(encoding="utf-8")
    assert "KEEP THIS USER LINE" in after       # content outside the fence preserved
    assert "STALE BODY" not in after            # fenced region re-rendered
    assert str(agents_md) in result.written


def test_bridge_goose_merge_skips_unfenced_agents_md(tmp_path: Path):
    """T3 (SAFETY): an existing UNFENCED AGENTS.md (another tool's) is skipped under
    --bridge-merge and left byte-identical. This is the load-bearing §5.1 guarantee."""
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)
    out_root = tmp_path / "out"
    agents_md = out_root / "AGENTS.md"
    agents_md.parent.mkdir(parents=True, exist_ok=True)
    foreign = "# My Project\n\nAGENTS.md owned by another tool (Cursor/Codex).\n"
    agents_md.write_text(foreign, encoding="utf-8")

    result = run_bridge(
        source_dir=source_dir, source_framework="copilot-vscode",
        target_framework="goose", output_root=out_root, merge_only=True,
    )
    assert agents_md.read_text(encoding="utf-8") == foreign  # untouched
    assert str(agents_md) in result.skipped


def test_bridge_goose_refresh_overwrites(tmp_path: Path):
    """T4: --bridge-refresh overwrites the shared AGENTS.md (the documented destructive path)."""
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)
    out_root = tmp_path / "out"
    agents_md = out_root / "AGENTS.md"
    agents_md.parent.mkdir(parents=True, exist_ok=True)
    foreign = "# Another tool's AGENTS.md\n"
    agents_md.write_text(foreign, encoding="utf-8")

    run_bridge(
        source_dir=source_dir, source_framework="copilot-vscode",
        target_framework="goose", output_root=out_root, overwrite=True,
    )
    assert agents_md.read_text(encoding="utf-8") != foreign
    assert "goose-bridge-entry" in agents_md.read_text(encoding="utf-8")


def test_bridge_goose_first_time_create_emits_shared_notice(tmp_path: Path):
    """T5: creating AGENTS.md (in any mode, here --bridge-merge into an empty repo)
    emits the shared-multi-tool-file notice."""
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)
    out_root = tmp_path / "out"

    result = run_bridge(
        source_dir=source_dir, source_framework="copilot-vscode",
        target_framework="goose", output_root=out_root, merge_only=True,
    )
    assert (out_root / "AGENTS.md").exists()  # created even under merge
    assert any("shared AGENTS.md" in n for n in result.notices)


def test_bridge_goose_merge_skips_unfenced_goosehints(tmp_path: Path):
    """T6: a pre-existing unfenced .goosehints (as Phase-1 generate emits) is skipped
    under --bridge-merge — the bridge hint is not added, the file is unchanged."""
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)
    out_root = tmp_path / "out"
    hints = out_root / ".goosehints"
    hints.parent.mkdir(parents=True, exist_ok=True)
    generated = "@AGENTS.md\n\nGoose operational notes (generated by agentteams)\n"
    hints.write_text(generated, encoding="utf-8")

    result = run_bridge(
        source_dir=source_dir, source_framework="copilot-vscode",
        target_framework="goose", output_root=out_root, merge_only=True,
    )
    assert hints.read_text(encoding="utf-8") == generated
    assert str(hints) in result.skipped


def test_bridge_goose_check_mode(tmp_path: Path):
    """T7: --bridge-check passes against a fresh manifest, fails after source drift."""
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)
    out_root = tmp_path / "out"
    run_bridge(
        source_dir=source_dir, source_framework="copilot-vscode",
        target_framework="goose", output_root=out_root, overwrite=True,
    )
    fresh = run_bridge(
        source_dir=source_dir, source_framework="copilot-vscode",
        target_framework="goose", output_root=out_root, check_only=True,
    )
    assert fresh.check_ok
    (source_dir / "orchestrator.agent.md").write_text(
        _vscode_agent("orchestrator") + "\nDRIFT\n", encoding="utf-8")
    stale = run_bridge(
        source_dir=source_dir, source_framework="copilot-vscode",
        target_framework="goose", output_root=out_root, check_only=True,
    )
    assert not stale.check_ok


def test_bridge_goose_target_allowed(tmp_path: Path):
    """T8: target_framework='goose' no longer raises ValueError."""
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)
    # Would raise "Unknown target framework 'goose'" before the allow-set edit.
    result = run_bridge(
        source_dir=source_dir, source_framework="copilot-vscode",
        target_framework="goose", output_root=tmp_path / "out",
    )
    assert result.success


def test_bridge_claude_target_file_set_unchanged_after_goose(tmp_path: Path):
    """T9 (regression): the claude target still writes its full entry-file set after
    the goose allow-set edit (the six-direction test only checks counts)."""
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)
    out_root = tmp_path / "out"
    run_bridge(
        source_dir=source_dir, source_framework="copilot-vscode",
        target_framework="claude", output_root=out_root, overwrite=True,
    )
    for rel in ("CLAUDE.md", ".claude/agent-team.md", ".claude/quickstart-snippet.md", ".claude/README.md"):
        assert (out_root / rel).exists(), f"claude target regressed: missing {rel}"


def test_normalize_bridge_output_root_goose(tmp_path: Path):
    """T10: a bridge --output ending in .goose/recipes or .goose normalizes to repo root."""
    from agentteams.cli.commands import _normalize_bridge_output_root

    root = tmp_path / "proj"
    assert _normalize_bridge_output_root(root / ".goose" / "recipes", "goose") == root
    assert _normalize_bridge_output_root(root / ".goose", "goose") == root
    # A plain repo-root --output is left untouched.
    assert _normalize_bridge_output_root(root, "goose") == root


def test_bridge_merge_backs_up_existing_targets(tmp_path: Path):
    """C3/G08-A1: a merge/overwrite over existing target entry files must create a
    pre-write .agentteams-backups snapshot (no backup on first-time create)."""
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)
    out_root = tmp_path / "out"
    backups = out_root / ".agentteams-backups"

    # First-time create: nothing pre-existing → no backup expected.
    run_bridge(
        source_dir=source_dir,
        source_framework="copilot-vscode",
        target_framework="claude",
        output_root=out_root,
        dry_run=False,
    )
    assert not backups.exists(), "first-time bridge create should not back up (nothing existed)"

    # Merge over the now-existing target files → a backup snapshot must appear.
    run_bridge(
        source_dir=source_dir,
        source_framework="copilot-vscode",
        target_framework="claude",
        output_root=out_root,
        dry_run=False,
        merge_only=True,
    )
    assert backups.exists(), "bridge merge over existing targets must create a backup"
    snapshots = [p for p in backups.iterdir() if p.is_dir()]
    assert snapshots, "expected at least one timestamped backup snapshot"



# ---------------------------------------------------------------------------
# Empty-inventory guard (R1) and markdown-only source hashing (R2)
# Regression coverage for the 2026-06-22 goose-bridge remediation. See
# references/plans/goose-bridge-remediation-2026-06-22.plan.md.
# ---------------------------------------------------------------------------


def test_empty_inventory_emits_notice_on_generate(tmp_path: Path):
    """A source dir with no agent files yields a 0-agent bridge → loud notice (R1a)."""
    source_dir = tmp_path / "src" / ".github" / "agents"
    source_dir.mkdir(parents=True)  # deliberately empty: no *.agent.md files

    result = run_bridge(
        source_dir=source_dir,
        source_framework="copilot-vscode",
        target_framework="goose",
        output_root=tmp_path / "out",
        dry_run=False,
        overwrite=True,
        check_only=False,
    )

    # Generation still succeeds (notice, not a hard error — STABILITY.md).
    assert result.success, f"errors: {result.errors}"
    assert any("Empty bridge inventory" in n for n in result.notices), result.notices
    assert any(".github/agents" in n for n in result.notices), result.notices


def test_populated_inventory_emits_no_empty_notice(tmp_path: Path):
    """The R1a notice fires strictly on len(inventory) == 0 (guards notices==[] tests)."""
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)  # one orchestrator agent

    result = run_bridge(
        source_dir=source_dir,
        source_framework="copilot-vscode",
        target_framework="goose",
        output_root=tmp_path / "out",
        dry_run=False,
        overwrite=True,
        check_only=False,
    )
    assert result.success
    assert not any("Empty bridge inventory" in n for n in result.notices), result.notices


def test_bridge_check_fails_on_empty_inventory(tmp_path: Path):
    """--bridge-check must FAIL a 0-inventory manifest even when hashes are consistent (R1b)."""
    source_dir = tmp_path / "src" / ".github" / "agents"
    source_dir.mkdir(parents=True)  # empty source
    out_root = tmp_path / "out"

    generated = run_bridge(
        source_dir=source_dir,
        source_framework="copilot-vscode",
        target_framework="claude",
        output_root=out_root,
        dry_run=False,
        overwrite=True,
        check_only=False,
    )
    assert generated.success  # generation succeeds with the empty-inventory notice

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
    text = Path(checked.check_report_path).read_text(encoding="utf-8")
    assert "FAIL" in text
    assert "Empty Inventory" in text


def test_source_hashes_exclude_non_markdown_junk(tmp_path: Path):
    """Build artifacts and OS junk must not enter the manifest hash set (R2)."""
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)
    # The real-world offenders: a gitignored build-tool artifact and macOS junk.
    (source_dir / "_build-description.json").write_text('{"project_name": "Demo"}', encoding="utf-8")
    (source_dir / ".DS_Store").write_bytes(b"\x00junk")

    out_root = tmp_path / "out"
    result = run_bridge(
        source_dir=source_dir,
        source_framework="copilot-vscode",
        target_framework="claude",
        output_root=out_root,
        dry_run=False,
        overwrite=True,
        check_only=False,
    )
    assert result.success

    manifest = json.loads(
        (out_root / "references" / "bridges" / "copilot-vscode-to-claude" / "bridge-manifest.json")
        .read_text(encoding="utf-8")
    )
    paths = [row["path"] for row in manifest["source_hashes"]]
    assert not any("_build-description.json" in p for p in paths), paths
    assert not any(".DS_Store" in p for p in paths), paths
    # Sanity: the genuine agent definition IS still hashed.
    assert any(p.endswith("orchestrator.agent.md") for p in paths), paths


# ---------------------------------------------------------------------------
# Goose-as-SOURCE bridging (plan P2): detect, recipe-yaml inventory,
# framework-aware hashing (both directions), goose->claude bridge.
# ---------------------------------------------------------------------------

from agentteams.bridge_sources import _collect_source_files, _extract_inventory  # noqa: E402
from agentteams.interop import detect_framework  # noqa: E402

_RECIPE = (
    'version: "1.0.0"\n'
    'title: "{title}"\n'
    'description: "{desc}"\n'
    '{entry}'
    'instructions: |\n'
    '  Body for {title}.\n'
    'extensions:\n'
    '  - type: builtin\n'
    '    name: developer\n'
    '    bundled: true\n'
    '    timeout: 300\n'
)


def _goose_source(tmp_path: Path) -> Path:
    recipes = tmp_path / "proj" / ".goose" / "recipes"
    recipes.mkdir(parents=True)
    (recipes / "orchestrator.yaml").write_text(
        _RECIPE.format(title="Orchestrator — Demo", desc="Coordinates", entry='prompt: "go"\n'),
        encoding="utf-8")
    (recipes / "cleanup.yaml").write_text(
        _RECIPE.format(title="Cleanup — Demo", desc="Removes stale files", entry=""), encoding="utf-8")
    (recipes / "_build-description.json").write_text("{}", encoding="utf-8")  # junk, must not hash
    return recipes


def test_detect_framework_goose():
    # path-based (.goose in parts) does not require the dir to exist
    assert detect_framework(Path("/x/.goose/recipes")) == "goose"


def test_goose_source_collect_and_inventory(tmp_path: Path):
    recipes = _goose_source(tmp_path)
    # framework-aware hashing: goose -> .yaml only, junk .json excluded
    collected = sorted(p.name for p in _collect_source_files(recipes, "goose"))
    assert collected == ["cleanup.yaml", "orchestrator.yaml"]
    assert "_build-description.json" not in collected
    # recipe-yaml inventory: titles, roles, invokability, orchestrator first
    inv = _extract_inventory(recipes, "goose")
    assert [r["display_name"] for r in inv] == ["Orchestrator — Demo", "Cleanup — Demo"]
    assert inv[0]["invokable"] == "yes" and inv[1]["invokable"] == "no"
    assert inv[1]["role"] == "Removes stale files"


def test_collect_source_files_both_directions(tmp_path: Path):
    # The task-2 hardening must survive for non-goose sources.
    md = tmp_path / "agents"
    md.mkdir()
    (md / "orchestrator.agent.md").write_text("# o\n", encoding="utf-8")
    (md / "_build-description.json").write_text("{}", encoding="utf-8")
    (md / ".DS_Store").write_bytes(b"junk")
    names = sorted(p.name for p in _collect_source_files(md, "copilot-vscode"))
    assert names == ["orchestrator.agent.md"]  # md hashed, json + DS_Store excluded


def test_goose_to_claude_bridge_check(tmp_path: Path):
    recipes = _goose_source(tmp_path)
    out = tmp_path / "out"
    gen = run_bridge(source_dir=recipes, target_framework="claude", output_root=out, overwrite=True)
    assert gen.success
    import json as _json
    manifest = _json.loads(
        (out / "references" / "bridges" / "goose-to-claude" / "bridge-manifest.json").read_text())
    assert manifest["source_framework"] == "goose" and manifest["inventory_count"] == 2
    assert all(r["path"].endswith(".yaml") for r in manifest["source_hashes"])
    # fresh -> PASS
    chk = run_bridge(source_dir=recipes, target_framework="claude", output_root=out, check_only=True)
    assert chk.check_ok is True
    # mutate -> FAIL
    (recipes / "cleanup.yaml").write_text((recipes / "cleanup.yaml").read_text() + "\n# x\n", encoding="utf-8")
    chk2 = run_bridge(source_dir=recipes, target_framework="claude", output_root=out, check_only=True)
    assert chk2.check_ok is False


def test_goose_to_goose_forbidden(tmp_path: Path):
    recipes = _goose_source(tmp_path)
    with pytest.raises(ValueError, match="goose-to-goose"):
        run_bridge(source_dir=recipes, source_framework="goose", target_framework="goose",
                   output_root=tmp_path / "out", overwrite=True)


def test_bridge_emits_parallelize_skill_when_feature_enabled(tmp_path: Path):
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)
    result = run_bridge(
        source_dir=source_dir,
        source_framework="copilot-vscode",
        target_framework="claude",
        output_root=tmp_path / "out",
        overwrite=True,
        host_features=["bridge:copilot-vscode-to-claude:parallelize"],
    )
    assert result.success, f"errors: {result.errors}"
    skill = tmp_path / "out" / ".claude" / "skills" / "parallelize-plan.md"
    assert skill.exists()
    body = skill.read_text(encoding="utf-8")
    assert "name: parallelize-plan" in body
    assert "parallel_plan" in body


def test_bridge_omits_parallelize_skill_by_default(tmp_path: Path):
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)
    result = run_bridge(
        source_dir=source_dir,
        source_framework="copilot-vscode",
        target_framework="claude",
        output_root=tmp_path / "out",
        overwrite=True,
    )
    assert result.success, f"errors: {result.errors}"
    skill = tmp_path / "out" / ".claude" / "skills" / "parallelize-plan.md"
    assert not skill.exists()  # opt-in via host feature only
