"""Tests for CAI-based cross-framework interop pipeline."""

from __future__ import annotations

from pathlib import Path
import itertools
import re

import pytest

from agentteams.interop import detect_framework, export_to_cai, import_from_cai, run_interop
from agentteams.interop import _strip_framework_wrappers, _frontmatter_value


def _vscode_agent(slug: str) -> str:
    return (
        "---\n"
        f"name: {slug} — Demo\n"
        "description: \"demo\"\n"
        "user-invokable: false\n"
        "tools: ['read']\n"
        "model: [\"Claude Sonnet 4.6 (copilot)\"]\n"
        "handoffs:\n"
        "  - label: Example\n"
        "    agent: orchestrator\n"
        "    prompt: hi\n"
        "    send: false\n"
        "---\n\n"
        f"# {slug}\n\n"
        "Body line one.\n\n"
        "Body line two with token KEEP_ME_ALWAYS.\n\n"
        "## Responsibilities\n\n"
        "- Do work\n\n"
        "- Preserve this bullet KEEP_BULLET\n\n"
        "## Handoffs\n\n"
        "handoff prose KEEP_HANDOFF\n"
    )


def _claude_agent(slug: str) -> str:
    return (
        "---\n"
        f"name: {slug} — Demo\n"
        "description: \"demo\"\n"
        "allowed-tools: Bash, Read, Write, Edit\n"
        "---\n\n"
        f"# {slug}\n\n"
        "Body line one.\n\n"
        "Body line two with token KEEP_ME_ALWAYS.\n\n"
        "## Responsibilities\n\n"
        "- Do work\n"
        "- Preserve this bullet KEEP_BULLET\n"
    )


def _cli_agent(slug: str) -> str:
    return (
        f"# {slug}\n\n"
        "Body line one.\n\n"
        "Body line two with token KEEP_ME_ALWAYS.\n\n"
        "## Responsibilities\n\n"
        "- Do work\n"
        "- Preserve this bullet KEEP_BULLET\n"
    )


def _build_source(source_framework: str, source_dir: Path) -> None:
    source_dir.mkdir(parents=True, exist_ok=True)
    if source_framework == "copilot-vscode":
        (source_dir / "orchestrator.agent.md").write_text(_vscode_agent("orchestrator"), encoding="utf-8")
        (source_dir.parent / "copilot-instructions.md").write_text(
            "# Instructions\n\nKEEP_INSTRUCTIONS_TOKEN\n",
            encoding="utf-8",
        )
    elif source_framework == "copilot-cli":
        (source_dir / "orchestrator.md").write_text(_cli_agent("orchestrator"), encoding="utf-8")
        (source_dir.parent / "copilot-instructions.md").write_text(
            "# Instructions\n\nKEEP_INSTRUCTIONS_TOKEN\n",
            encoding="utf-8",
        )
    else:
        (source_dir / "orchestrator.md").write_text(_claude_agent("orchestrator"), encoding="utf-8")
        (source_dir.parent / "CLAUDE.md").write_text(
            "# Instructions\n\nKEEP_INSTRUCTIONS_TOKEN\n",
            encoding="utf-8",
        )


def _agents_rel(framework: str) -> Path:
    if framework == "copilot-vscode":
        return Path(".github/agents")
    if framework == "copilot-cli":
        return Path(".github/copilot")
    return Path(".claude/agents")


def _agent_filename(slug: str, framework: str) -> str:
    if framework == "copilot-vscode":
        return f"{slug}.agent.md"
    return f"{slug}.md"


def _instructions_filename(framework: str) -> str:
    return "CLAUDE.md" if framework == "claude" else "copilot-instructions.md"


def _combined_target_text(target_framework: str, target_dir: Path, slug: str = "orchestrator") -> str:
    agent_content = (target_dir / _agent_filename(slug, target_framework)).read_text(encoding="utf-8")
    instructions_content = (target_dir.parent / _instructions_filename(target_framework)).read_text(
        encoding="utf-8"
    )
    return agent_content + "\n" + instructions_content


def _missing_signal_count(text: str, signals: list[str]) -> int:
    return sum(1 for signal in signals if signal not in text)


def _semantic_line_set(text: str) -> set[str]:
    """Return a normalized semantic line set from mixed markdown/yaml content."""
    lines: set[str] = set()
    in_front_matter = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "---":
            in_front_matter = not in_front_matter
            continue
        if in_front_matter:
            continue
        # Remove markdown structural prefixes; keep semantic payload.
        line = re.sub(r"^#{1,6}\s+", "", line)
        line = re.sub(r"^-\s+", "", line)
        if line:
            lines.add(line)
    return lines


def _token_counts(text: str, tokens: list[str]) -> dict[str, int]:
    return {token: text.count(token) for token in tokens}


def _extract_agent_body(cai: dict, slug: str) -> str:
    """Return the body_markdown for the named agent from a CAI document.

    Raises AssertionError if the slug is not found, so test failures are
    immediately actionable rather than silently returning an empty string.
    """
    for agent in cai.get("agents", []):
        if agent["slug"] == slug:
            return agent["body_markdown"]
    raise AssertionError(
        f"Slug {slug!r} not found in CAI agents: {[a['slug'] for a in cai.get('agents', [])]}"
    )


@pytest.mark.parametrize(
    "source_framework,target_framework,source_rel,target_rel,inst_name",
    [
        ("copilot-vscode", "copilot-cli", ".github/agents", ".github/copilot", "copilot-instructions.md"),
        ("copilot-vscode", "claude", ".github/agents", ".claude/agents", "CLAUDE.md"),
        ("copilot-cli", "copilot-vscode", ".github/copilot", ".github/agents", "copilot-instructions.md"),
        ("copilot-cli", "claude", ".github/copilot", ".claude/agents", "CLAUDE.md"),
        ("claude", "copilot-vscode", ".claude/agents", ".github/agents", "copilot-instructions.md"),
        ("claude", "copilot-cli", ".claude/agents", ".github/copilot", "copilot-instructions.md"),
    ],
)
def test_interop_direct_all_six_directions(
    tmp_path: Path,
    source_framework: str,
    target_framework: str,
    source_rel: str,
    target_rel: str,
    inst_name: str,
):
    source_dir = tmp_path / "src" / Path(source_rel)
    target_dir = tmp_path / "dst" / Path(target_rel)
    _build_source(source_framework, source_dir)

    result = run_interop(
        source_dir=source_dir,
        source_framework=source_framework,
        target_framework=target_framework,
        target_dir=target_dir,
        mode="direct",
        dry_run=False,
        overwrite=False,
    )

    assert result.success, f"errors: {result.errors}"
    if target_framework == "copilot-vscode":
        agent_out = target_dir / "orchestrator.agent.md"
        assert agent_out.exists()
        content = agent_out.read_text(encoding="utf-8")
        assert content.startswith("---")
        assert "user-invokable:" in content
    elif target_framework == "claude":
        agent_out = target_dir / "orchestrator.md"
        assert agent_out.exists()
        content = agent_out.read_text(encoding="utf-8")
        assert content.startswith("---")
        assert "allowed-tools:" in content
    else:
        agent_out = target_dir / "orchestrator.md"
        assert agent_out.exists()
        content = agent_out.read_text(encoding="utf-8")
        assert not content.startswith("---")

    assert (target_dir.parent / inst_name).exists()


@pytest.mark.parametrize(
    "source_framework,target_framework,source_rel,target_rel",
    [
        ("copilot-vscode", "copilot-cli", ".github/agents", ".github/copilot"),
        ("copilot-vscode", "claude", ".github/agents", ".claude/agents"),
        ("copilot-cli", "copilot-vscode", ".github/copilot", ".github/agents"),
        ("copilot-cli", "claude", ".github/copilot", ".claude/agents"),
        ("claude", "copilot-vscode", ".claude/agents", ".github/agents"),
        ("claude", "copilot-cli", ".claude/agents", ".github/copilot"),
    ],
)
def test_interop_bundle_all_six_directions(
    tmp_path: Path,
    source_framework: str,
    target_framework: str,
    source_rel: str,
    target_rel: str,
):
    source_dir = tmp_path / "src" / Path(source_rel)
    target_dir = tmp_path / "dst" / Path(target_rel)
    _build_source(source_framework, source_dir)

    result = run_interop(
        source_dir=source_dir,
        source_framework=source_framework,
        target_framework=target_framework,
        target_dir=target_dir,
        mode="bundle",
        dry_run=False,
        overwrite=False,
    )

    assert result.success
    bundle_dir = target_dir / "references" / "interop" / f"{source_framework}-to-{target_framework}"
    assert (bundle_dir / "team-manifest.cai.json").exists()
    assert (bundle_dir / "interop-manifest.json").exists()
    assert (bundle_dir / "routing-map.json").exists()
    assert (bundle_dir / "instructions-map.json").exists()
    assert (bundle_dir / "compatibility-report.md").exists()


def test_detect_framework_by_directory_shape(tmp_path: Path):
    claude_dir = tmp_path / ".claude" / "agents"
    claude_dir.mkdir(parents=True)
    assert detect_framework(claude_dir) == "claude"

    cli_dir = tmp_path / ".github" / "copilot"
    cli_dir.mkdir(parents=True)
    assert detect_framework(cli_dir) == "copilot-cli"


def test_interop_dry_run_writes_nothing(tmp_path: Path):
    source_dir = tmp_path / "src" / ".github" / "agents"
    target_dir = tmp_path / "dst" / ".claude" / "agents"
    _build_source("copilot-vscode", source_dir)

    result = run_interop(
        source_dir=source_dir,
        source_framework="copilot-vscode",
        target_framework="claude",
        target_dir=target_dir,
        mode="bundle",
        dry_run=True,
        overwrite=True,
    )

    assert result.success
    assert len(result.converted) >= 1
    assert len(result.bundle_files) >= 1
    assert not target_dir.exists()


@pytest.mark.parametrize(
    "source_framework,middle_framework",
    [
        ("copilot-vscode", "copilot-cli"),
        ("copilot-vscode", "claude"),
        ("copilot-cli", "copilot-vscode"),
        ("copilot-cli", "claude"),
        ("claude", "copilot-vscode"),
        ("claude", "copilot-cli"),
    ],
)
def test_roundtrip_a_to_b_to_a_not_worse_than_direct_a_to_a(
    tmp_path: Path,
    source_framework: str,
    middle_framework: str,
):
    source_dir = tmp_path / "rt" / "source" / _agents_rel(source_framework)
    _build_source(source_framework, source_dir)

    direct_target = tmp_path / "rt" / "direct" / _agents_rel(source_framework)
    chained_mid = tmp_path / "rt" / "mid" / _agents_rel(middle_framework)
    chained_target = tmp_path / "rt" / "chained" / _agents_rel(source_framework)

    # Direct baseline: A -> A
    direct = run_interop(
        source_dir=source_dir,
        source_framework=source_framework,
        target_framework=source_framework,
        target_dir=direct_target,
        mode="direct",
        dry_run=False,
        overwrite=True,
    )
    assert direct.success, f"direct errors: {direct.errors}"

    # Chained path: A -> B -> A
    first = run_interop(
        source_dir=source_dir,
        source_framework=source_framework,
        target_framework=middle_framework,
        target_dir=chained_mid,
        mode="direct",
        dry_run=False,
        overwrite=True,
    )
    assert first.success, f"first leg errors: {first.errors}"

    second = run_interop(
        source_dir=chained_mid,
        source_framework=middle_framework,
        target_framework=source_framework,
        target_dir=chained_target,
        mode="direct",
        dry_run=False,
        overwrite=True,
    )
    assert second.success, f"second leg errors: {second.errors}"

    direct_text = _combined_target_text(source_framework, direct_target)
    chained_text = _combined_target_text(source_framework, chained_target)

    # Core fidelity signals that should survive framework transformations.
    signals = [
        "Body line one.",
        "KEEP_ME_ALWAYS",
        "## Responsibilities",
        "KEEP_BULLET",
        "KEEP_INSTRUCTIONS_TOKEN",
    ]

    direct_loss = _missing_signal_count(direct_text, signals)
    chained_loss = _missing_signal_count(chained_text, signals)
    assert chained_loss <= direct_loss

    # Strict semantic equivalence: chained roundtrip must preserve the same
    # normalized semantic payload as direct baseline.
    assert _semantic_line_set(chained_text) == _semantic_line_set(direct_text)

    # Critical marker frequencies must not decrease on chained path.
    critical_tokens = ["KEEP_ME_ALWAYS", "KEEP_BULLET", "KEEP_INSTRUCTIONS_TOKEN"]
    direct_token_counts = _token_counts(direct_text, critical_tokens)
    chained_token_counts = _token_counts(chained_text, critical_tokens)
    for token in critical_tokens:
        assert chained_token_counts[token] >= direct_token_counts[token]


@pytest.mark.parametrize(
    "source_framework,middle_framework,target_framework",
    [
        combo
        for combo in itertools.permutations(["copilot-vscode", "copilot-cli", "claude"], 3)
    ],
)
def test_chained_a_to_b_to_c_not_worse_than_direct_a_to_c(
    tmp_path: Path,
    source_framework: str,
    middle_framework: str,
    target_framework: str,
):
    source_dir = tmp_path / "chain" / "source" / _agents_rel(source_framework)
    _build_source(source_framework, source_dir)

    direct_target = tmp_path / "chain" / "direct" / _agents_rel(target_framework)
    chained_mid = tmp_path / "chain" / "mid" / _agents_rel(middle_framework)
    chained_target = tmp_path / "chain" / "chained" / _agents_rel(target_framework)

    # Direct baseline: A -> C
    direct = run_interop(
        source_dir=source_dir,
        source_framework=source_framework,
        target_framework=target_framework,
        target_dir=direct_target,
        mode="direct",
        dry_run=False,
        overwrite=True,
    )
    assert direct.success, f"direct errors: {direct.errors}"

    # Chained path: A -> B -> C
    first = run_interop(
        source_dir=source_dir,
        source_framework=source_framework,
        target_framework=middle_framework,
        target_dir=chained_mid,
        mode="direct",
        dry_run=False,
        overwrite=True,
    )
    assert first.success, f"first leg errors: {first.errors}"

    second = run_interop(
        source_dir=chained_mid,
        source_framework=middle_framework,
        target_framework=target_framework,
        target_dir=chained_target,
        mode="direct",
        dry_run=False,
        overwrite=True,
    )
    assert second.success, f"second leg errors: {second.errors}"

    direct_text = _combined_target_text(target_framework, direct_target)
    chained_text = _combined_target_text(target_framework, chained_target)

    signals = [
        "Body line one.",
        "KEEP_ME_ALWAYS",
        "## Responsibilities",
        "KEEP_BULLET",
        "KEEP_INSTRUCTIONS_TOKEN",
    ]

    direct_loss = _missing_signal_count(direct_text, signals)
    chained_loss = _missing_signal_count(chained_text, signals)

    # Chained conversion must not add additional information loss over direct conversion.
    assert chained_loss <= direct_loss

    # Strict semantic equivalence: chained payload should match direct payload.
    assert _semantic_line_set(chained_text) == _semantic_line_set(direct_text)

    # Critical marker frequencies must not decrease on chained path.
    critical_tokens = ["KEEP_ME_ALWAYS", "KEEP_BULLET", "KEEP_INSTRUCTIONS_TOKEN"]
    direct_token_counts = _token_counts(direct_text, critical_tokens)
    chained_token_counts = _token_counts(chained_text, critical_tokens)
    for token in critical_tokens:
        assert chained_token_counts[token] >= direct_token_counts[token]


def test_export_to_cai_excludes_reference_and_backup_md(tmp_path):
    """export_to_cai must not slurp non-agent .md files (reference docs, skills,
    or backup copies) as agents — only the real flat agent files."""
    agents_dir = tmp_path / ".github" / "agents"
    _build_source("copilot-vscode", agents_dir)  # writes orchestrator.agent.md

    # Decoys that the recursive rglob would otherwise pick up as agents:
    refs = agents_dir / "references"
    refs.mkdir()
    (refs / "pipeline-graph.md").write_text("# Pipeline\n\nnot an agent\n", encoding="utf-8")
    (refs / "ref-pandas-reference.md").write_text("# pandas\n\nnot an agent\n", encoding="utf-8")
    backup = agents_dir / ".agentteams-backups" / "20260615-000000"
    backup.mkdir(parents=True)
    (backup / "orchestrator.agent.md").write_text(_vscode_agent("orchestrator"), encoding="utf-8")

    cai = export_to_cai(agents_dir, source_framework="copilot-vscode")
    slugs = sorted(a["slug"] for a in cai["agents"])
    assert slugs == ["orchestrator"], f"expected only the real agent, got {slugs}"
    # the decoy filenames must not appear as agents under any slug form
    assert not any("pipeline" in s or "reference" in s or "pandas" in s for s in slugs)


@pytest.mark.parametrize(
    "source_framework,middle_framework",
    [
        ("copilot-vscode", "claude"),
        ("copilot-vscode", "copilot-cli"),
        ("claude", "copilot-vscode"),
        ("claude", "copilot-cli"),
        ("copilot-cli", "copilot-vscode"),
        ("copilot-cli", "claude"),
    ],
    ids=[
        "copilot-vscode->claude->copilot-vscode",
        "copilot-vscode->copilot-cli->copilot-vscode",
        "claude->copilot-vscode->claude",
        "claude->copilot-cli->claude",
        "copilot-cli->copilot-vscode->copilot-cli",
        "copilot-cli->claude->copilot-cli",
    ],
)
def test_interop_round_trip_body_fidelity(
    tmp_path: Path,
    source_framework: str,
    middle_framework: str,
):
    """Full round-trip A->B->A preserves body_markdown at the CAI layer.

    This is the test called out in MAP-16: compare the final re-imported
    body_markdown against the *original source* body_markdown, not against
    a re-processed intermediate.  Silent losses in _strip_framework_wrappers,
    _strip_handoffs_section, or front-matter reconstruction are caught here
    before they reach the indirect chained-vs-direct comparisons.
    """
    slug = "orchestrator"

    # --- Build source tree ---
    source_dir = tmp_path / "rt" / "source" / _agents_rel(source_framework)
    _build_source(source_framework, source_dir)

    # --- Leg 0: capture original body_markdown from CAI export of source ---
    cai_original = export_to_cai(source_dir, source_framework=source_framework)
    original_body = _extract_agent_body(cai_original, slug)

    # Sanity-check: the source body must contain the sentinel tokens we care about.
    # If this fires it means _build_source changed and the test must be updated.
    for sentinel in ("Body line one.", "KEEP_ME_ALWAYS", "KEEP_BULLET"):
        assert sentinel in original_body, (
            f"Sentinel {sentinel!r} missing from source body_markdown — "
            "check _build_source() for this framework"
        )

    # --- Leg 1: source -> middle framework ---
    mid_dir = tmp_path / "rt" / "mid" / _agents_rel(middle_framework)
    mid_dir.mkdir(parents=True, exist_ok=True)
    result = import_from_cai(
        cai_original,
        target_framework=middle_framework,
        target_dir=mid_dir,
        dry_run=False,
        overwrite=True,
    )
    assert result.success, f"Leg 1 errors: {result.errors}"

    # --- Leg 2: middle framework -> source framework (back again) ---
    cai_mid = export_to_cai(mid_dir, source_framework=middle_framework)
    final_dir = tmp_path / "rt" / "final" / _agents_rel(source_framework)
    final_dir.mkdir(parents=True, exist_ok=True)
    result = import_from_cai(
        cai_mid,
        target_framework=source_framework,
        target_dir=final_dir,
        dry_run=False,
        overwrite=True,
    )
    assert result.success, f"Leg 2 errors: {result.errors}"

    # --- Leg 3: re-export the final result to CAI to read its body_markdown ---
    cai_final = export_to_cai(final_dir, source_framework=source_framework)
    final_body = _extract_agent_body(cai_final, slug)

    # --- Primary assertion: body_markdown is identical after full round-trip ---
    assert final_body == original_body, (
        f"Round-trip body loss detected: {source_framework} -> {middle_framework} -> {source_framework}\n"
        f"--- original body_markdown ---\n{original_body}\n"
        f"--- final body_markdown ---\n{final_body}\n"
        f"--- diff: missing lines ---\n"
        + "\n".join(
            f"  MISSING: {line!r}"
            for line in original_body.splitlines()
            if line and line not in final_body
        )
    )


class TestStripFrameworkWrappersEmbeddedDashes:
    """MAP-06 regression: embedded '---' in a block scalar must not cause partial stripping."""

    def test_eof_terminated_front_matter_stripped(self):
        """Regression (MAP-06): old regex misses closing '---' at EOF without trailing newline.
        Old code returned the full content unchanged; new code must strip the front matter."""
        content = "---\nname: foo\n---"  # no trailing newline
        result = _strip_framework_wrappers(content)
        assert "name: foo" not in result
        assert result == ""

    def test_block_scalar_with_dash_separator_fully_stripped(self):
        content = (
            "---\n"
            "name: my-agent\n"
            "description: |\n"
            "  See section:\n"
            "  ---\n"
            "  Details follow.\n"
            "---\n"
            "# Real body\n"
            "Body line.\n"
        )
        result = _strip_framework_wrappers(content)
        assert "# Real body" in result
        assert "Body line." in result
        # The partial front matter that old code would have left behind
        assert "description: |" not in result
        assert "name: my-agent" not in result

    def test_mid_line_dashes_not_treated_as_closing_delimiter(self):
        """MAP-17 mid-line case via _strip_framework_wrappers (mirrors test_graph.py coverage)."""
        content = "---\nname: foo---bar\ndesc: ok\n---\n# Body\n"
        result = _strip_framework_wrappers(content)
        # After stripping, the body is all that remains — front matter keys must not appear
        assert "name: foo---bar" not in result
        assert "# Body" in result

    def test_no_front_matter_returns_content_unchanged(self):
        content = "# Plain markdown\n\nNo front matter.\n"
        assert _strip_framework_wrappers(content) == content


class TestFrontmatterValueEmbeddedDashes:
    """MAP-06 regression: _frontmatter_value must read keys declared after an embedded '---'."""

    def test_eof_terminated_front_matter_returns_value(self):
        """Regression (MAP-06): old regex returned '' for all keys when no trailing newline."""
        content = "---\nname: correct-name\n---"  # no trailing newline
        assert _frontmatter_value(content, "name") == "correct-name"

    def test_name_key_after_block_scalar_with_embedded_dash(self):
        # 'name' is declared AFTER a block scalar that contains '---'
        # Old code would return "" because the regex closed at the embedded '---'.
        content = (
            "---\n"
            "description: |\n"
            "  ---\n"
            "name: correct-name\n"
            "---\n"
            "# Body\n"
        )
        assert _frontmatter_value(content, "name") == "correct-name"

    def test_returns_empty_when_no_front_matter(self):
        assert _frontmatter_value("# No front matter\n", "name") == ""
