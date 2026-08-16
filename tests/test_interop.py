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
        "user-invocable: false\n"
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
        "tools: Bash, Read, Write, Edit\n"
        "---\n\n"
        f"# {slug}\n\n"
        "Body line one.\n\n"
        "Body line two with token KEEP_ME_ALWAYS.\n\n"
        "## Responsibilities\n\n"
        "- Do work\n"
        "- Preserve this bullet KEEP_BULLET\n"
    )


def _cli_agent(slug: str) -> str:
    """P1 (2026-08-15): a real on-disk copilot-cli file now carries front matter (same
    surface as VS Code) and NEVER has a '## Handoffs' section — the adapter strips it
    before writing, so a realistic source fixture cannot have one either."""
    return (
        "---\n"
        f"name: {slug} — Demo\n"
        "description: \"demo\"\n"
        "user-invocable: false\n"
        "tools: ['read']\n"
        "model: [\"Claude Sonnet 4.6 (copilot)\"]\n"
        "---\n\n"
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
        (source_dir / "orchestrator.agent.md").write_text(_cli_agent("orchestrator"), encoding="utf-8")
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
        return Path(".github/agents")
    return Path(".claude/agents")


def _agent_filename(slug: str, framework: str) -> str:
    if framework in ("copilot-vscode", "copilot-cli"):  # P1: same extension now
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
        ("copilot-vscode", "copilot-cli", ".github/agents", ".github/agents", "copilot-instructions.md"),
        ("copilot-vscode", "claude", ".github/agents", ".claude/agents", "CLAUDE.md"),
        ("copilot-cli", "copilot-vscode", ".github/agents", ".github/agents", "copilot-instructions.md"),
        ("copilot-cli", "claude", ".github/agents", ".claude/agents", "CLAUDE.md"),
        ("claude", "copilot-vscode", ".claude/agents", ".github/agents", "copilot-instructions.md"),
        ("claude", "copilot-cli", ".claude/agents", ".github/agents", "copilot-instructions.md"),
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
        assert "user-invocable:" in content
    elif target_framework == "claude":
        agent_out = target_dir / "orchestrator.md"
        assert agent_out.exists()
        content = agent_out.read_text(encoding="utf-8")
        assert content.startswith("---")
        assert "\ntools:" in content
    elif target_framework == "copilot-cli":
        # P1 convergence (2026-08-15): copilot-cli now shares copilot-vscode's
        # .agent.md-with-front-matter shape rather than plain Markdown.
        agent_out = target_dir / "orchestrator.agent.md"
        assert agent_out.exists()
        content = agent_out.read_text(encoding="utf-8")
        assert content.startswith("---")
        assert "user-invocable:" in content
    else:
        agent_out = target_dir / "orchestrator.md"
        assert agent_out.exists()
        content = agent_out.read_text(encoding="utf-8")
        assert not content.startswith("---")

    assert (target_dir.parent / inst_name).exists()


@pytest.mark.parametrize(
    "source_framework,target_framework,source_rel,target_rel",
    [
        ("copilot-vscode", "copilot-cli", ".github/agents", ".github/agents"),
        ("copilot-vscode", "claude", ".github/agents", ".claude/agents"),
        ("copilot-cli", "copilot-vscode", ".github/agents", ".github/agents"),
        ("copilot-cli", "claude", ".github/agents", ".claude/agents"),
        ("claude", "copilot-vscode", ".claude/agents", ".github/agents"),
        ("claude", "copilot-cli", ".claude/agents", ".github/agents"),
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


def test_export_to_cai_captures_copilot_cli_tool_scopes(tmp_path):
    """P1 fix (2026-08-15): before this fix, export_to_cai's capability-capture
    branch treated copilot-cli like agents-md (no capability channel at all),
    so capabilities.tool_scopes was always []. copilot-cli's tools: line is
    byte-identical in shape to copilot-vscode's since the convergence and must
    be captured the same way."""
    agents_dir = tmp_path / ".github" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "orchestrator.agent.md").write_text(_cli_agent("orchestrator"), encoding="utf-8")

    cai = export_to_cai(agents_dir, source_framework="copilot-cli")
    agent = cai["agents"][0]
    assert agent["capabilities"].get("tool_scopes") == ["read"], agent["capabilities"]


def test_import_to_copilot_cli_preserves_capabilities_not_defaults(tmp_path):
    """P1 fix (2026-08-15) regression guard: import_from_cai's tool_scopes and
    raw_front_matter threading were gated to ("copilot-vscode", "claude"),
    excluding copilot-cli even after it gained the identical front-matter
    channel. The result was silent: every cross-framework import INTO
    copilot-cli reverted user-invocable/tools/model to
    copilot_vscode._YAML_DEFAULTS regardless of what the source specified.
    Reproduced directly against a source with non-default values so a
    defaulted output cannot pass by accident."""
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()

    (source_dir / "orchestrator.agent.md").write_text(
        "---\n"
        "name: Orchestrator\n"
        "description: Coordinates the team\n"
        "user-invocable: true\n"
        "tools: ['read', 'edit']\n"
        'model: ["Claude Sonnet 4.6 (copilot)"]\n'
        "---\n\n"
        "Body prose.\n",
        encoding="utf-8",
    )

    cai = export_to_cai(source_dir, source_framework="copilot-vscode")
    result = import_from_cai(cai, "copilot-cli", target_dir, overwrite=True)
    assert result.errors == []

    content = (target_dir / "orchestrator.agent.md").read_text(encoding="utf-8")
    assert "user-invocable: true" in content, content
    assert "tools: ['read', 'edit']" in content, content
    # The pre-fix defaulting fallback added 'search' and flipped to false —
    # neither may appear now that the real source value threads through.
    assert "user-invocable: false" not in content
    assert "'search'" not in content


def test_strip_framework_wrappers_does_not_consume_adjacent_fence_on_handoff_prefixed_heading():
    """Regression: interop.py's own _strip_handoffs_section re-introduced a bug already
    fixed once in FrameworkAdapter._strip_handoffs_section (tests/test_handoff_strip_fence_
    safety.py). A naive `^#{1,3}\\s+Handoff.*?(?=^#{1,3}\\s|\\Z)` regex matches ANY heading
    merely starting with the word "Handoff" -- not just a literal "## Handoffs" section --
    and, lacking a fence-marker stop condition, ran past a heading-only fenced section
    (`## Handoff Payload Conflict Codes`) straight through to the next real `##` heading,
    silently deleting an entire adjacent fenced section (its END marker, and a whole
    unrelated `handoff_payload_codes` fence) in the process. Reproduces the exact shape
    that corrupted .claude/agents/conflict-auditor.md and .github/agents/conflict-auditor.
    agent.md this session.
    """
    body = (
        "<!-- AGENTTEAMS:BEGIN handoff_payload_conflict_codes v=1 -->\n"
        "## Handoff Payload Conflict Codes\n"
        "<!-- AGENTTEAMS:END handoff_payload_conflict_codes -->\n"
        "\n"
        "<!-- AGENTTEAMS:BEGIN handoff_payload_codes v=1 -->\n"
        "Body of the payload-codes fence that must survive stripping.\n"
        "<!-- AGENTTEAMS:END handoff_payload_codes -->\n"
        "\n"
        "---\n"
        "\n"
        "## Project-Specific Notes\n"
        "notes here\n"
    )
    content = "---\nname: x\ndescription: y\n---\n" + body
    stripped = _strip_framework_wrappers(content)
    assert "AGENTTEAMS:END handoff_payload_conflict_codes" in stripped
    assert "AGENTTEAMS:BEGIN handoff_payload_codes" in stripped
    assert "AGENTTEAMS:END handoff_payload_codes" in stripped
    assert "Body of the payload-codes fence that must survive stripping." in stripped
    assert "## Project-Specific Notes" in stripped


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


# ---------------------------------------------------------------------------
# MAP-16 (H.2): parametrized canonical round trips — every registered
# framework to the durable canonical format and back to the SAME framework.
# ---------------------------------------------------------------------------

from agentteams.canonical import load_canonical, materialize_canonical

_MAP16_FRAMEWORKS = ("copilot-vscode", "copilot-cli", "claude", "goose", "agents-md", "codex")


def _map16_goose_recipe(slug: str, title: str, body: str, sub_paths: list[str] | None = None) -> str:
    lines = [
        'version: "1.0.0"',
        f'title: "{title}"',
        f'description: "{title} recipe for MAP-16 round trip"',
        "instructions: |",
    ]
    lines += [f"  {b}" for b in body.splitlines()]
    lines += [
        "extensions:",
        "  - type: builtin",
        "    name: developer",
        "    bundled: true",
        "    timeout: 300",
    ]
    if sub_paths:
        lines.append("sub_recipes:")
        for p in sub_paths:
            name = Path(p).stem
            lines += [f"  - name: {name}", f'    path: "./{p}"', '    description: ""']
    return "\n".join(lines) + "\n"


def _build_map16_source(framework: str, root: Path) -> Path:
    """Build a synthetic source team for *framework* under *root*; return its agents dir."""
    if framework in ("copilot-vscode", "copilot-cli", "claude"):
        rel = {
            "copilot-vscode": ".github/agents",
            "copilot-cli": ".github/agents",
            "claude": ".claude/agents",
        }[framework]
        source_dir = root / rel
        _build_source(framework, source_dir)
        return source_dir
    if framework == "goose":
        recipes = root / ".goose" / "recipes"
        recipes.mkdir(parents=True)
        (root / "AGENTS.md").write_text("# Instructions\n\nKEEP_INSTRUCTIONS_TOKEN\n", encoding="utf-8")
        (recipes / "orchestrator.yaml").write_text(
            _map16_goose_recipe(
                "orchestrator", "Orchestrator",
                "You are the orchestrator. KEEP_BODY_TOKEN", ["worker.yaml"]
            ),
            encoding="utf-8",
        )
        (recipes / "worker.yaml").write_text(
            _map16_goose_recipe("worker", "Worker", "You do the work. KEEP_WORKER_TOKEN"),
            encoding="utf-8",
        )
        return recipes
    # agents-md and codex share the .agents detail-file layout.
    agents_dir = root / ".agents"
    agents_dir.mkdir(parents=True)
    (root / "AGENTS.md").write_text("# Instructions\n\nKEEP_INSTRUCTIONS_TOKEN\n", encoding="utf-8")
    (agents_dir / "orchestrator.md").write_text(
        "# Orchestrator\n\nBody one. KEEP_BODY_TOKEN\n", encoding="utf-8"
    )
    (agents_dir / "worker.md").write_text("# Worker\n\nBody two. KEEP_WORKER_TOKEN\n", encoding="utf-8")
    return agents_dir


def _map16_target_dir(framework: str, root: Path) -> Path:
    return {
        "copilot-vscode": root / ".github" / "agents",
        # P1 (2026-08-15): converged onto VS Code's path.
        "copilot-cli": root / ".github" / "agents",
        "claude": root / ".claude" / "agents",
        "goose": root / ".goose" / "recipes",
        "agents-md": root / ".agents",
        "codex": root / ".agents",
    }[framework]


@pytest.mark.parametrize("framework", _MAP16_FRAMEWORKS)
def test_map16_framework_to_canonical_and_back(tmp_path: Path, framework: str):
    """source(fw) -> CAI -> canonical dir -> CAI -> target(fw), losslessly."""
    source_dir = _build_map16_source(framework, tmp_path / "src")

    cai = export_to_cai(source_dir, framework)
    assert cai["source_framework"] == framework
    assert cai["agents"], f"{framework}: discovery must yield agents"

    # Leg 1: the CAI document survives the exploded canonical form exactly.
    canonical_dir = tmp_path / "canon"
    materialize_canonical(cai, canonical_dir)
    cai2 = load_canonical(canonical_dir)
    assert cai2 == cai

    # Leg 2: the canonical CAI imports into the same framework without errors
    # and re-lands every agent file.
    target_dir = _map16_target_dir(framework, tmp_path / "dst")
    result = import_from_cai(cai2, framework, target_dir, overwrite=True)
    assert result.errors == []
    adapter_ext = {
        "copilot-vscode": ".agent.md",
        "copilot-cli": ".agent.md",  # P1 convergence (2026-08-15)
        "goose": ".yaml",
    }.get(framework, ".md")
    for slug in (a["slug"] for a in cai["agents"]):
        assert (target_dir / f"{slug}{adapter_ext}").is_file(), f"{framework}: {slug} missing"

    # Body fidelity: the KEEP tokens survive the full trip. Shared builders
    # carry KEEP_ME_ALWAYS; the goose/agents-md synthetic sources built here
    # carry KEEP_BODY_TOKEN.
    token = "KEEP_ME_ALWAYS" if framework in ("copilot-vscode", "copilot-cli", "claude") else "KEEP_BODY_TOKEN"
    orch = (target_dir / f"orchestrator{adapter_ext}").read_text(encoding="utf-8")
    assert token in orch
    assert "KEEP_INSTRUCTIONS_TOKEN" in cai2["instructions_binding"]["content"]


# ---------------------------------------------------------------------------
# H.3: goose-source discovery + agents-md instructions classification
# ---------------------------------------------------------------------------

def test_goose_source_discovery_yields_agents(tmp_path: Path):
    """F.1 regression: a .goose/recipes source yields non-zero agents."""
    source_dir = _build_map16_source("goose", tmp_path)
    cai = export_to_cai(source_dir)  # auto-detection, no explicit framework
    assert cai["source_framework"] == "goose"
    assert len(cai["agents"]) == 2
    slugs = {a["slug"] for a in cai["agents"]}
    assert slugs == {"orchestrator", "worker"}
    # instructions found two levels up at the project root (F.1)
    assert cai["instructions_binding"]["source_name"] == "AGENTS.md"
    assert "KEEP_INSTRUCTIONS_TOKEN" in cai["instructions_binding"]["content"]


def test_agents_md_instructions_classified_not_agent(tmp_path: Path):
    """F.1 regression: AGENTS.md is instructions binding, never a spurious agent."""
    source_dir = _build_map16_source("agents-md", tmp_path)
    cai = export_to_cai(source_dir)  # auto-detection via .agents dir shape
    assert cai["source_framework"] == "agents-md"
    assert cai["instructions_binding"]["source_name"] == "AGENTS.md"
    slugs = {a["slug"] for a in cai["agents"]}
    assert slugs == {"orchestrator", "worker"}
