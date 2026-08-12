"""Tests for lightweight cross-framework bridge generation."""

from __future__ import annotations

import builtins
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


def test_bridge_manifest_source_dir_is_relative_when_nested_in_output_root(tmp_path: Path):
    """OPSEC regression: when source_dir sits inside output_root (the realistic case —
    e.g. `.github/agents` inside the project root that is also output_root, exactly how
    self-hosting repos like agentteams/researchteam/OrthodoxLLM are actually bridged),
    bridge-manifest.json's source_dir must be relative, not an absolute filesystem path
    carrying the local OS username. Found leaking into two real consumer repos' committed
    bridge-manifest.json before this fix — see agentteams/bridge.py."""
    output_root = tmp_path / "project"
    source_dir = output_root / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)

    result = run_bridge(
        source_dir=source_dir,
        source_framework="copilot-vscode",
        target_framework="claude",
        output_root=output_root,
        dry_run=False,
        overwrite=True,
        check_only=False,
    )
    assert result.success, f"errors: {result.errors}"

    pair_dir = output_root / "references" / "bridges" / "copilot-vscode-to-claude"
    manifest = json.loads((pair_dir / "bridge-manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_dir"] == str(Path(".github") / "agents")
    assert not Path(manifest["source_dir"]).is_absolute()
    assert str(tmp_path) not in manifest["source_dir"]


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
    # The boundary now names three distinct retrieval surfaces (memory-index,
    # code-index, project retrieval-integrator) that must not be conflated.
    assert "must not be conflated" in text
    assert "Memory-index" in text and "Code index" in text
    assert "retrieval-integrator" in text.lower()


def test_goose_bridge_entry_advertises_research_capability(tmp_path: Path):
    """A bridged Goose team's entry files must name `agentteams.research`.

    Regression guard for a real 2026-07-24 failure: `agentteams/frameworks/goose.py`
    documents the research module in the hints it generates, but the BRIDGE path
    writes its own AGENTS.md/.goosehints and silently dropped that reference. The
    live repo measured `grep -c agentteams.research AGENTS.md .goosehints` -> 0, 0,
    and the failing turn's 20k-char system prompt contained zero occurrences of
    "research". The agent therefore had no idea a search tool existed, guessed URLs,
    scraped a homepage, and put 29,654 chars of navigation HTML into its own context.
    Capability present + never advertised == capability absent, from the agent's view.
    """
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)
    out_root = tmp_path / "out"

    run_bridge(
        source_dir=source_dir,
        source_framework="copilot-vscode",
        target_framework="goose",
        output_root=out_root,
        dry_run=False,
        overwrite=True,
        check_only=False,
    )
    agents_md = (out_root / "AGENTS.md").read_text(encoding="utf-8")
    assert "agentteams.research" in agents_md
    # Must teach verify-first, not just assert the tool exists (it is an optional extra).
    assert "python -m agentteams.research --help" in agents_md
    # Must distinguish search from fetch -- conflating them is what caused the failure.
    assert "search" in agents_md.lower() and "web_scrape" in agents_md
    # Recency: relevance ranking is not date ordering.
    assert "recency" in agents_md.lower() or "most recent" in agents_md.lower()
    # .goosehints must still pull AGENTS.md in, or none of the above reaches the model.
    hints = (out_root / ".goosehints").read_text(encoding="utf-8")
    assert "@AGENTS.md" in hints


def test_bridge_emits_recall_skill_for_claude_target(tmp_path: Path):
    """Claude target with emit_skills=True (default) emits .claude/skills/recall/SKILL.md."""
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
    assert (out_root / ".claude" / "skills" / "recall" / "SKILL.md").exists()


def test_every_emitted_claude_skill_matches_the_discovery_contract(tmp_path: Path):
    """Every emitted skill must be `<name>/SKILL.md` — the only shape Claude Code loads.

    Regression guard for the 2026-08-07 finding: skills were emitted as flat
    `.claude/skills/<name>.md`, which Claude Code never discovers. The failure was
    silent — the retrieval layer degrades to grep by design, so an unreachable
    index and a working one look identical from the outside. Nothing failed; the
    feature was simply never used.

    This asserts the shape for ALL emitted skills, not just `recall` — the original
    defect spanned four (`recall`, `code-recall`, `todo-from-plan`, `parallelize-plan`)
    and a fix covering only the first two would have left it live.

    Contract: https://code.claude.com/docs/en/skills.md — "Each skill is a directory
    with SKILL.md as the entrypoint." The DIRECTORY name is the invocable command
    name, not the `name:` front-matter key.
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

    skills_root = out_root / ".claude" / "skills"
    assert skills_root.is_dir(), "claude bridge must emit a .claude/skills/ tree"

    flat = sorted(p.name for p in skills_root.glob("*.md"))
    assert not flat, (
        f"flat skill files are never discovered by Claude Code: {flat}. "
        "Emit `<name>/SKILL.md` instead."
    )

    emitted = sorted(p.name for p in skills_root.iterdir() if p.is_dir())
    assert emitted, "expected at least one emitted skill directory"
    for name in emitted:
        entry = skills_root / name / "SKILL.md"
        assert entry.is_file(), (
            f"skill directory {name!r} must contain SKILL.md exactly "
            f"(found: {sorted(p.name for p in (skills_root / name).iterdir())})"
        )


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
    """emit_skills=False suppresses recall/SKILL.md emission."""
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
    assert not (out_root / ".claude" / "skills" / "recall" / "SKILL.md").exists()


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


@pytest.mark.parametrize("source_framework", ["copilot-vscode", "claude"])
def test_bridge_goose_entry_has_always_present_behavioral_lines(tmp_path: Path, source_framework: str):
    """AGENTS.md's goose-bridge-entry fence must carry the two always-applicable behavioral
    lines regardless of orchestrator-persona adoption — see
    tmp/by-week/2026-W30/goose-bridge-entry-actionability.plan.md. These are reachable without
    reading any further file, which is the whole point (empirically confirmed: the orchestrator
    persona is not adopted for off-topic queries, so anything gated behind it is unreachable
    for exactly the failure case this fixes)."""
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

    agents_md = (out_root / "AGENTS.md").read_text(encoding="utf-8")
    assert "apply to every request in this session" in agents_md
    assert "web_scrape" in agents_md
    assert "computercontroller" in agents_md
    assert "don't default to refusal without" in agents_md
    assert "closest well-known match" in agents_md
    # Ambiguity carve-out must survive — regression guard against a confident-but-wrong answer
    # when multiple entities are genuinely comparably plausible.
    assert "comparably plausible" in agents_md


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


# ---------------------------------------------------------------------------
# Canonical-as-SOURCE bridging (open-items remediation OPEN-2/OPEN-4): a durable
# canonical directory (team.cai.json + agents/*.md) as a --bridge-source-framework
# value, with --bridge-check covering both agent files and team.cai.json itself.
# ---------------------------------------------------------------------------

from agentteams import canonical as _canonical_mod  # noqa: E402
from agentteams.interop import export_to_cai  # noqa: E402


def _canonical_source(tmp_path: Path) -> Path:
    """Build a real materialized canonical directory from a synthetic copilot-vscode
    source, so the fixture has the exact shape the real pipeline produces rather than
    hand-rolled YAML that might drift from it."""
    native = tmp_path / "native" / ".github" / "agents"
    _build_source("copilot-vscode", native)
    cai = export_to_cai(native, "copilot-vscode")
    canon_root = tmp_path / "proj" / ".agentteams" / "canonical"
    _canonical_mod.materialize_canonical(cai, canon_root)
    return canon_root


def test_canonical_source_collect_and_inventory(tmp_path: Path):
    canon_root = _canonical_source(tmp_path)
    collected = sorted(p.name for p in _collect_source_files(canon_root, "canonical"))
    assert collected == ["orchestrator.md", "team.cai.json"]

    inv = _extract_inventory(canon_root, "canonical")
    assert [r["display_name"] for r in inv] == ["orchestrator — Demo"]
    # Canonical front matter has no user-invokable key — honest "no", not a guess.
    assert inv[0]["invokable"] == "no"


def test_collect_source_files_canonical_hashes_team_cai_json(tmp_path: Path):
    # OPEN-4: team.cai.json must be in the hashed set, or a hand-edit to
    # instructions/mcp_servers/framework_extensions would be invisible to --bridge-check.
    canon_root = _canonical_source(tmp_path)
    hashed_names = {p.name for p in _collect_source_files(canon_root, "canonical")}
    assert "team.cai.json" in hashed_names


def test_canonical_source_collect_and_inventory_without_pyyaml(tmp_path: Path):
    """2026-08-10 coverage gap: the canonical-source fix (reusing canonical.py's
    _load_agent_file for correct JSON-escape decoding) was verified only under
    this repo's default PyYAML-installed test posture. This repo's own declared
    default is stdlib-only (PyYAML is a test-only dependency) — reuses
    test_canonical.py's own builtins.__import__ refusal pattern. The fixture's
    orchestrator name already carries a real em dash ("orchestrator — Demo",
    from _build_source's _vscode_agent), so a mangled-escape regression (the
    literal 6 characters \\u2014 instead of the real character) would be caught
    without a custom fixture."""
    canon_root = _canonical_source(tmp_path)

    real_import = builtins.__import__

    def refuse_yaml(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("simulated PyYAML absence")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = refuse_yaml
    try:
        collected = sorted(p.name for p in _collect_source_files(canon_root, "canonical"))
        inv = _extract_inventory(canon_root, "canonical")
    finally:
        builtins.__import__ = real_import

    assert collected == ["orchestrator.md", "team.cai.json"]
    assert inv[0]["display_name"] == "orchestrator — Demo"


def test_canonical_source_without_team_cai_json_fails_clearly(tmp_path: Path):
    """2026-08-10 finding: a dir with no team.cai.json but a coincidentally
    agents/*.md-shaped folder must error, not silently produce a plausible-
    looking partial bridge."""
    fake_root = tmp_path / "not-canonical"
    (fake_root / "agents").mkdir(parents=True)
    (fake_root / "agents" / "orchestrator.md").write_text("# Orchestrator\n", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="team.cai.json"):
        _collect_source_files(fake_root, "canonical")
    with pytest.raises(FileNotFoundError, match="team.cai.json"):
        _extract_inventory(fake_root, "canonical")


def test_canonical_to_claude_bridge_end_to_end_and_check(tmp_path: Path):
    canon_root = _canonical_source(tmp_path)
    out = tmp_path / "out"
    gen = run_bridge(source_dir=canon_root, target_framework="claude", output_root=out, overwrite=True)
    assert gen.success
    manifest = json.loads(
        (out / "references" / "bridges" / "canonical-to-claude" / "bridge-manifest.json").read_text())
    assert manifest["source_framework"] == "canonical" and manifest["inventory_count"] == 1

    # fresh -> PASS
    chk = run_bridge(source_dir=canon_root, target_framework="claude", output_root=out, check_only=True)
    assert chk.check_ok is True

    # mutate an agents/*.md file -> FAIL
    agent_file = canon_root / "agents" / "orchestrator.md"
    agent_file.write_text(agent_file.read_text() + "\nExtra line.\n", encoding="utf-8")
    chk2 = run_bridge(source_dir=canon_root, target_framework="claude", output_root=out, check_only=True)
    assert chk2.check_ok is False

    # re-baseline against the still-mutated agent file (not a revert — this
    # isolates the next assertion to team.cai.json alone), then mutate
    # team.cai.json itself -> FAIL
    gen2 = run_bridge(source_dir=canon_root, target_framework="claude", output_root=out, overwrite=True)
    assert gen2.success
    chk2b = run_bridge(source_dir=canon_root, target_framework="claude", output_root=out, check_only=True)
    assert chk2b.check_ok is True  # confirms gen2 actually re-baselined before the next mutation
    team_file = canon_root / _canonical_mod.TEAM_FILE_NAME
    team_file.write_text(team_file.read_text() + " ", encoding="utf-8")
    chk3 = run_bridge(source_dir=canon_root, target_framework="claude", output_root=out, check_only=True)
    assert chk3.check_ok is False


def test_canonical_cannot_be_a_bridge_target(tmp_path: Path):
    canon_root = _canonical_source(tmp_path)
    with pytest.raises(ValueError, match="Unknown target framework"):
        run_bridge(source_dir=canon_root, source_framework="canonical", target_framework="canonical",
                   output_root=tmp_path / "out", overwrite=True)


def test_canonical_agents_subdir_misdetects_as_copilot_vscode(tmp_path: Path):
    """Named trap (plan section 2.2): team.cai.json detection only fires at the
    canonical ROOT. Pointing --bridge-from at its agents/ subdirectory instead
    skips that marker check entirely, and a canonical agent file's handoffs:
    front-matter key then trips detect_framework's copilot-vscode content-sniff
    heuristic (interop.py) — silently, not with an error. --bridge-from for a
    canonical source MUST be the root, never its agents/ subdirectory."""
    canon_root = _canonical_source(tmp_path)
    assert detect_framework(canon_root / "agents") == "copilot-vscode"
    # ...and because copilot-vscode's own filter requires *.agent.md (canonical
    # emits plain *.md), the misdetected read yields zero agents, not wrong ones.
    assert _extract_inventory(canon_root / "agents", "copilot-vscode") == []


def test_canonical_bridge_directory_depth_resolves_to_project_root(tmp_path: Path):
    """Pin the coincidence: .agentteams/canonical sits at the same depth below
    project root as .github/agents / .claude/agents, so cli/commands.py's
    project_root = source_dir.parent.parent resolution (used when --output is
    omitted) lands artifacts at the project root, not somewhere unexpected."""
    from agentteams.cli.commands import _run_bridge

    canon_root = _canonical_source(tmp_path)
    project_root = canon_root.parent.parent
    assert project_root == tmp_path / "proj"

    # check_only=True (not dry_run): avoids the live security-freshness gate
    # (cli/commands.py only invokes it when both dry_run and check_only are
    # False) while still writing a real bridge-check.report.md, so the depth
    # math is pinned by an on-disk path rather than coupled to a print
    # statement's exact wording.
    rc = _run_bridge(
        source_dir=canon_root,
        source_framework="canonical",
        target_framework="claude",
        output=None,
        dry_run=False,
        overwrite=False,
        check_only=True,
        merge_only=False,
    )
    assert rc in (0, 1)  # 1 = "stale" (no prior manifest yet) — still proves the path
    report = project_root / "references" / "bridges" / "canonical-to-claude" / "bridge-check.report.md"
    assert report.exists()


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
    skill = tmp_path / "out" / ".claude" / "skills" / "parallelize-plan" / "SKILL.md"
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
    skill = tmp_path / "out" / ".claude" / "skills" / "parallelize-plan" / "SKILL.md"
    assert not skill.exists()  # opt-in via host feature only


# ---------------------------------------------------------------------------
# Generic bridge TARGET (open-items remediation OPEN-3): a bridge-any-target
# flavor with no native adapter of its own — only the framework-agnostic
# pair-dir artifacts, zero native consumer entry files.
# ---------------------------------------------------------------------------

_GENERIC_PAIR_ARTIFACTS = (
    "bridge-manifest.json",
    "agent-inventory.md",
    "quickstart-snippet.md",
    "entrypoint.md",
    "domain-boundary.md",
)


def test_bridge_generic_target_allowed(tmp_path: Path):
    """target_framework='generic' does not raise ValueError."""
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)
    result = run_bridge(
        source_dir=source_dir, source_framework="copilot-vscode",
        target_framework="generic", output_root=tmp_path / "out",
    )
    assert result.success, f"errors: {result.errors}"


def test_bridge_generic_target_writes_zero_native_files(tmp_path: Path):
    """A generic target has no native framework to write consumer entry files
    for — without the explicit generic branch in _render_target_files, this
    would silently fall through to the copilot-cli file shape instead."""
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)
    out_root = tmp_path / "out"
    result = run_bridge(
        source_dir=source_dir, source_framework="copilot-vscode",
        target_framework="generic", output_root=out_root, overwrite=True,
    )
    # Airtight against any native target-file path, known or future: everything
    # written must live under the pair-dir artifacts directory (target_files is
    # [] for generic, so _render_target_files contributes nothing to `written`).
    pair_dir = out_root / "references" / "bridges" / "copilot-vscode-to-generic"
    assert result.written
    for path_str in result.written:
        assert Path(path_str).is_relative_to(pair_dir), f"unexpected write outside pair-dir: {path_str}"
    for native in ("CLAUDE.md", "AGENTS.md", ".goosehints", ".github/copilot-instructions.md",
                   ".github/copilot", ".github/agents", ".claude", ".goose"):
        assert not (out_root / native).exists(), f"unexpected native file/dir: {native}"


def test_bridge_generic_pair_dir_artifacts_present(tmp_path: Path):
    """The pair-dir artifacts (manifest/inventory/quickstart/entrypoint/
    domain-boundary) are all framework-agnostic already and carry the full
    picture on their own — kept for generic targets too, not trimmed down to
    the bare inventory+quickstart+entrypoint trio, since domain-boundary.md
    costs nothing extra (zero target-framework branching in its renderer) and
    is genuinely useful retrieval-surface guidance for a no-adapter consumer."""
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)
    out_root = tmp_path / "out"
    run_bridge(
        source_dir=source_dir, source_framework="copilot-vscode",
        target_framework="generic", output_root=out_root, overwrite=True,
    )
    pair_dir = out_root / "references" / "bridges" / "copilot-vscode-to-generic"
    for artifact in _GENERIC_PAIR_ARTIFACTS:
        assert (pair_dir / artifact).is_file(), f"missing {artifact}"


def test_bridge_generic_artifacts_do_not_invoke_agentteams_cli(tmp_path: Path):
    """2026-08-10 finding: a generic target's whole point is a consumer with
    zero agentteams tooling — quickstart/entrypoint must not instruct them to
    run one. domain-boundary.md is deliberately exempt (test_bridge_generic_
    pair_dir_artifacts_present's own docstring): it only *describes* the three
    retrieval surfaces conceptually, never tells the reader to run a command."""
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)
    out_root = tmp_path / "out"
    run_bridge(
        source_dir=source_dir, source_framework="copilot-vscode",
        target_framework="generic", output_root=out_root, overwrite=True,
    )
    pair_dir = out_root / "references" / "bridges" / "copilot-vscode-to-generic"
    for artifact in ("quickstart-snippet.md", "entrypoint.md"):
        text = (pair_dir / artifact).read_text(encoding="utf-8")
        assert "agentteams --query-" not in text, f"{artifact} still invokes the CLI: {text}"


def test_bridge_generic_quickstart_names_canonical_tree(tmp_path: Path):
    source_dir = tmp_path / "src" / ".github" / "agents"
    _build_source("copilot-vscode", source_dir)
    out_root = tmp_path / "out"
    run_bridge(
        source_dir=source_dir, source_framework="copilot-vscode",
        target_framework="generic", output_root=out_root, overwrite=True,
    )
    quickstart = (out_root / "references" / "bridges" / "copilot-vscode-to-generic"
                  / "quickstart-snippet.md").read_text(encoding="utf-8")
    assert ".agentteams/canonical/" in quickstart
    assert "team.cai.json" in quickstart
    assert "--framework canonical" in quickstart


def test_canonical_source_to_generic_target_end_to_end(tmp_path: Path):
    """The combination the plan's own risk analysis named as untested: a
    canonical source bridged to a generic target. Also pins the fix for a
    degenerate self-referential command this combination used to produce
    (the generic quickstart note would tell the operator to regenerate the
    canonical tree from itself)."""
    canon_root = _canonical_source(tmp_path)
    out = tmp_path / "out"
    result = run_bridge(source_dir=canon_root, target_framework="generic", output_root=out, overwrite=True)
    assert result.success

    quickstart = (out / "references" / "bridges" / "canonical-to-generic"
                  / "quickstart-snippet.md").read_text(encoding="utf-8")
    assert "--interop-source-framework canonical --framework canonical" not in quickstart
    assert "it is the source of this bridge" in quickstart


def test_generic_requires_bridge_from_at_parse_time():
    """Mirrors canonical's existing --interop-from-only restriction: generic is
    bridge-only, so --framework generic combined with --convert-from (which
    would otherwise reach cli/commands.py's unguarded FRAMEWORKS[...] lookup,
    since generic has no frameworks/registry.py entry) must be rejected before
    any pipeline code runs, not left to crash further down."""
    from agentteams.cli.parser import _build_parser
    from agentteams.cli.parser_validate import _validate_option_combinations

    parser = _build_parser()
    args = parser.parse_args(["--framework", "generic", "--convert-from", "some/dir"])
    with pytest.raises(SystemExit):
        _validate_option_combinations(parser, args)

    # paired with --bridge-from, the same combination is accepted
    parser2 = _build_parser()
    args2 = parser2.parse_args(["--framework", "generic", "--bridge-from", "some/dir"])
    _validate_option_combinations(parser2, args2)  # must not raise
