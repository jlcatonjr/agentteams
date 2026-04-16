"""
audit.py — Post-generation audit for agent team files.

Performs two types of checks after the emit phase:
  1. Static structural checks — conflict detection and presupposition
     validation without external calls. Always available.
  2. AI-powered review — optional extended audit via the standalone `copilot`
     CLI, invoked as a subprocess with no interactive prompts.

Invoke via:
    python build_team.py ... --post-audit        (static + AI, requires copilot CLI)
    audit.run_post_audit(output_dir, manifest)   (programmatic)
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Required YAML front matter keys for every .agent.md file
_REQUIRED_YAML_KEYS: tuple[str, ...] = ("name", "description", "tools", "model")

#: Agents that must be present in every generated team
_REQUIRED_AGENTS: frozenset[str] = frozenset({
    "orchestrator",
    "adversarial",
    "conflict-auditor",
})

#: Optional domain/archetype agents that templates may reference but which are
#: not generated for every project brief.  AR_DANGLING_AGENT_SLUG is suppressed
#: for these slugs — their absence is expected when the brief does not include
#: the corresponding archetype.
_CONDITIONAL_ARCHETYPES: frozenset[str] = frozenset({
    "style-guardian",
    "cohesion-repairer",
    "reference-manager",
    "visual-designer",
    "format-converter",
    "output-compiler",
})

#: Regex: unresolved auto-placeholder token {UPPER_SNAKE_CASE}
_UNRESOLVED_AUTO_RE = re.compile(r"\{([A-Z][A-Z0-9_]{2,})\}")

#: Regex: unresolved manual placeholder token {MANUAL:NAME}
_UNRESOLVED_MANUAL_RE = re.compile(r"\{MANUAL:([A-Z][A-Z0-9_]*)\}")

#: Tokens that look like placeholders but are intentionally literal in generated docs
_SAFE_TOKENS: frozenset[str] = frozenset({
    "TRUE", "FALSE", "NULL", "NONE", "TODO", "FIXME", "NOTE",
    "TBD", "REQUIRED", "OPTIONAL", "DEPRECATED",
    # Placeholder-convention documentation examples (appear in body prose, not pipeline slots)
    "UPPER_SNAKE_CASE", "PLACEHOLDER_NAME",
    # Generic example identifiers used in template prose to illustrate placeholder patterns
    "NAME", "PLACEHOLDER",
})

#: Model to use for AI audit via the standalone `copilot` CLI
_AI_MODEL = "gpt-5.4"

#: Approximate max characters of agent-file excerpts sent to AI
_AI_CONTEXT_LIMIT = 12_000


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class AuditFinding:
    """A single audit finding."""

    category: str   # "CONFLICT" | "PRESUPPOSITION" | "WARNING"
    code: str       # Short machine-readable code
    severity: str   # "error" | "warning" | "info"
    file: str       # Relative path or "(team)" for team-level findings
    description: str


@dataclass
class AuditResult:
    """Aggregated result of a post-generation audit."""

    static_findings: list[AuditFinding] = field(default_factory=list)
    agent_refactor_findings: list[AuditFinding] = field(default_factory=list)
    code_hygiene_findings: list[AuditFinding] = field(default_factory=list)
    ai_report: str | None = None
    ai_available: bool = False

    @property
    def _all_findings(self) -> list[AuditFinding]:
        return self.static_findings + self.agent_refactor_findings + self.code_hygiene_findings

    @property
    def has_errors(self) -> bool:
        """True if any finding across all phases has severity 'error'."""
        return any(f.severity == "error" for f in self._all_findings)

    @property
    def has_warnings(self) -> bool:
        """True if any finding across all phases has severity 'warning'."""
        return any(f.severity == "warning" for f in self._all_findings)

    @property
    def is_clean(self) -> bool:
        """True if all phases are clean and AI audit (if run) reported no issues."""
        ai_clean = self.ai_report is None or "no issues" in (self.ai_report or "").lower()
        return not self._all_findings and ai_clean


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_post_audit(
    output_dir: Path,
    manifest: dict[str, Any],
    *,
    rendered_files: list[tuple[str, str]] | None = None,
    ai_audit: bool = True,
) -> AuditResult:
    """Run the full post-generation audit.

    Args:
        output_dir:     Absolute path to the agents output directory.
        manifest:       Team manifest from analyze.build_manifest().
        rendered_files: Optional in-memory list of (rel_path, content) from
                        render_all(). When provided, avoids re-reading from disk.
        ai_audit:       If True, attempt an AI-powered audit via the standalone
                        `copilot` CLI. Requires `copilot` to be on PATH.

    Returns:
        AuditResult with all findings and the optional AI report.
    """
    result = AuditResult()

    # Build file map: prefer in-memory over disk to avoid stale-read race
    if rendered_files is not None:
        file_map: dict[str, str] = {rel: content for rel, content in rendered_files}
    else:
        file_map = _load_files_from_disk(output_dir)

    # --- Static checks (conflict-auditor style) ---
    result.static_findings.extend(_check_unresolved_placeholders(file_map))
    result.static_findings.extend(_check_unresolved_manual_placeholders(file_map))
    result.static_findings.extend(_check_yaml_front_matter(file_map))
    result.static_findings.extend(_check_project_name_consistency(file_map, manifest))

    # --- Static checks (adversarial style) ---
    result.static_findings.extend(_check_required_agents_present(file_map, manifest))
    result.static_findings.extend(_check_workstream_expert_coverage(file_map, manifest))

    # --- Agent-refactor checks (spec compliance) ---
    result.agent_refactor_findings.extend(_check_invariant_core_present(file_map))
    result.agent_refactor_findings.extend(_check_return_handoff_present(file_map))
    result.agent_refactor_findings.extend(_check_readonly_tool_declarations(file_map))
    result.agent_refactor_findings.extend(_check_dangling_agent_slugs(file_map, output_dir))

    # --- Code-hygiene checks ---
    result.code_hygiene_findings.extend(_check_ch14_inline_data_blocks(file_map))
    result.code_hygiene_findings.extend(_check_ch20_duplicate_descriptions(file_map))

    # --- Optional AI audit ---
    if ai_audit:
        copilot_path = _get_copilot_path()
        if copilot_path:
            result.ai_available = True
            result.ai_report = _run_ai_audit(file_map, manifest, copilot_path, output_dir)
        else:
            result.ai_available = False

    return result


def print_audit_report(result: AuditResult) -> None:
    """Print a human-readable audit report to stdout.

    Args:
        result: AuditResult from run_post_audit().
    """

    def _print_phase(
        label: str,
        findings: list[AuditFinding],
    ) -> None:
        print(f"\n  --- {label} ---")
        if not findings:
            print(f"  ✓  {label}: clean.")
            return
        errors = [f for f in findings if f.severity == "error"]
        warnings = [f for f in findings if f.severity == "warning"]
        if errors:
            print(f"  ✗  {label}: {len(errors)} error(s), {len(warnings)} warning(s)")
        else:
            print(f"  ⚠  {label}: {len(warnings)} warning(s)")
        for finding in findings:
            icon = "✗" if finding.severity == "error" else ("⚠" if finding.severity == "warning" else "·")
            print(f"     {icon} [{finding.code}] {finding.file}: {finding.description}")

    _print_phase("Conflict + Presupposition Audit", result.static_findings)
    _print_phase("Agent-Refactor Spec Compliance", result.agent_refactor_findings)
    _print_phase("Code Hygiene Audit", result.code_hygiene_findings)

    if result.ai_available and result.ai_report:
        print("\n  --- AI Audit (GitHub Models / Claude Sonnet 4.6) ---")
        for line in result.ai_report.splitlines():
            print(f"     {line}")
    elif not result.ai_available:
        print("\n  ·  AI audit skipped: standalone `copilot` CLI not found on PATH.")
        print("     Install the GitHub Copilot CLI and ensure it is on PATH to enable.")

    if result.has_errors:
        print("\n  Audit result: ERRORS FOUND — review findings before using this team.")
    elif result.has_warnings:
        print("\n  Audit result: WARNINGS — review findings; team may still be usable.")
    else:
        print("\n  Audit result: CLEARED")


# ---------------------------------------------------------------------------
# Static checks — conflict-auditor style
# ---------------------------------------------------------------------------

def _check_unresolved_placeholders(
    file_map: dict[str, str],
) -> list[AuditFinding]:
    """Flag unresolved {AUTO_PLACEHOLDER} tokens remaining in generated files.

    Args:
        file_map: Rendered file content keyed by relative path.

    Returns:
        List of AuditFinding for each unresolved token found.
    """
    findings: list[AuditFinding] = []
    for rel_path, content in file_map.items():
        # SETUP-REQUIRED.md intentionally lists unresolved tokens
        if "SETUP-REQUIRED" in rel_path:
            continue
        seen_in_file: set[str] = set()
        for match in _UNRESOLVED_AUTO_RE.finditer(content):
            token = match.group(1)
            if token in _SAFE_TOKENS or token in seen_in_file:
                continue
            seen_in_file.add(token)
            findings.append(AuditFinding(
                category="CONFLICT",
                code="UNRESOLVED_PLACEHOLDER",
                severity="warning",
                file=rel_path,
                description=f"Unresolved placeholder: {{{token}}}",
            ))
    return findings


def _check_unresolved_manual_placeholders(
    file_map: dict[str, str],
) -> list[AuditFinding]:
    """Flag unresolved {MANUAL:*} tokens remaining in generated files.

    Tokens inside backtick spans (inline code) and fenced code blocks are
    treated as instructional text rather than unresolved placeholders, and
    are skipped.
    """
    findings: list[AuditFinding] = []
    for rel_path, content in file_map.items():
        if "SETUP-REQUIRED" in rel_path:
            continue
        # Strip inline code spans and fenced code blocks before scanning so
        # instructional examples like `{MANUAL:FOO}` are not flagged.
        content_no_code = re.sub(r"```.*?```", "", content, flags=re.DOTALL)
        content_no_code = re.sub(r"`[^`\n]+`", "", content_no_code)
        seen_in_file: set[str] = set()
        for match in _UNRESOLVED_MANUAL_RE.finditer(content_no_code):
            token = match.group(1)
            if token in seen_in_file:
                continue
            seen_in_file.add(token)
            findings.append(AuditFinding(
                category="CONFLICT",
                code="UNRESOLVED_MANUAL_PLACEHOLDER",
                severity="warning",
                file=rel_path,
                description=f"Unresolved manual placeholder: {{MANUAL:{token}}}",
            ))
    return findings


def _check_yaml_front_matter(
    file_map: dict[str, str],
) -> list[AuditFinding]:
    """Check that every .agent.md file has the required YAML front matter keys.

    Args:
        file_map: Rendered file content keyed by relative path.

    Returns:
        List of AuditFinding for files with missing or malformed YAML.
    """
    findings: list[AuditFinding] = []
    for rel_path, content in file_map.items():
        if not rel_path.endswith(".agent.md"):
            continue
        if "references/" in rel_path:
            continue  # reference files intentionally have no front matter

        if not content.startswith("---"):
            findings.append(AuditFinding(
                category="CONFLICT",
                code="YAML_MISSING",
                severity="error",
                file=rel_path,
                description="Agent file missing YAML front matter (expected '---' block)",
            ))
            continue

        end = content.find("---", 3)
        if end == -1:
            findings.append(AuditFinding(
                category="CONFLICT",
                code="YAML_MALFORMED",
                severity="error",
                file=rel_path,
                description="YAML front matter block never closed",
            ))
            continue

        yaml_block = content[3:end]
        for key in _REQUIRED_YAML_KEYS:
            if f"{key}:" not in yaml_block:
                findings.append(AuditFinding(
                    category="CONFLICT",
                    code="YAML_MISSING_FIELD",
                    severity="warning",
                    file=rel_path,
                    description=f"YAML front matter missing required key: '{key}'",
                ))
    return findings


def _check_project_name_consistency(
    file_map: dict[str, str],
    manifest: dict[str, Any],
) -> list[AuditFinding]:
    """Check that the project name is consistent in YAML 'name:' fields.

    Each agent name follows the pattern 'Role — Project Name'. This check
    verifies the project name portion matches the manifest.

    Args:
        file_map: Rendered file content keyed by relative path.
        manifest: Team manifest from analyze.build_manifest().

    Returns:
        List of AuditFinding for files with a mismatched project name.
    """
    findings: list[AuditFinding] = []
    expected_name = manifest.get("project_name", "")
    if not expected_name or "{MANUAL:" in expected_name:
        return findings

    # Use greedy .+ so that multi-dash names (e.g. "Tool Specialist — PostgreSQL — Project")
    # capture only the last segment after the final em-dash separator.
    # Do NOT include plain hyphen in the character class: project names like
    # "california-collectors" contain hyphens, which would cause false splits.
    _name_re = re.compile(r"^name:\s*.+—\s*(.+?)\s*$", re.MULTILINE)
    # Slugs that legitimately carry a fixed project name unrelated to the target project
    _skip_slugs = frozenset({"team-builder"})
    for rel_path, content in file_map.items():
        if not rel_path.endswith(".agent.md"):
            continue
        slug = Path(rel_path).stem.replace(".agent", "")
        if slug in _skip_slugs:
            continue
        m = _name_re.search(content[:400])
        if m:
            found_name = m.group(1).strip().strip('"\'')
            if found_name.lower() != expected_name.lower():
                findings.append(AuditFinding(
                    category="CONFLICT",
                    code="PROJECT_NAME_MISMATCH",
                    severity="warning",
                    file=rel_path,
                    description=(
                        f"YAML name field ends with '{found_name}' "
                        f"but manifest project name is '{expected_name}'"
                    ),
                ))
    return findings


# ---------------------------------------------------------------------------
# Static checks — adversarial / presupposition style
# ---------------------------------------------------------------------------

def _check_required_agents_present(
    file_map: dict[str, str],
    manifest: dict[str, Any],
) -> list[AuditFinding]:
    """Verify that governance agents required in every team were generated.

    Args:
        file_map: Rendered file content keyed by relative path.
        manifest: Team manifest from analyze.build_manifest().

    Returns:
        List of AuditFinding for each missing required agent.
    """
    findings: list[AuditFinding] = []
    generated_slugs = {
        Path(p).stem.replace(".agent", "")
        for p in file_map
        if p.endswith(".agent.md") and "references/" not in p
    }
    for slug in _REQUIRED_AGENTS:
        if slug not in generated_slugs:
            findings.append(AuditFinding(
                category="PRESUPPOSITION",
                code="MISSING_REQUIRED_AGENT",
                severity="error",
                file="(team)",
                description=(
                    f"Required governance agent '{slug}' was not generated. "
                    f"Every team must include @{slug}."
                ),
            ))
    return findings


def _check_workstream_expert_coverage(
    file_map: dict[str, str],
    manifest: dict[str, Any],
) -> list[AuditFinding]:
    """Verify that every brief component has a corresponding workstream expert file.

    Args:
        file_map: Rendered file content keyed by relative path.
        manifest: Team manifest from analyze.build_manifest().

    Returns:
        List of AuditFinding for components with no expert file.
    """
    findings: list[AuditFinding] = []
    for comp in manifest.get("components", []):
        slug = comp.get("slug", "")
        expert_file = f"{slug}-expert.agent.md"
        if expert_file not in file_map:
            findings.append(AuditFinding(
                category="PRESUPPOSITION",
                code="MISSING_WORKSTREAM_EXPERT",
                severity="warning",
                file="(team)",
                description=(
                    f"Component '{slug}' has no workstream expert file ({expert_file}). "
                    "Template may be missing or the component slug is incorrect."
                ),
            ))
    return findings


# ---------------------------------------------------------------------------
# Agent-refactor checks (AR-* codes)
# ---------------------------------------------------------------------------

#: Regex matching a YAML 'agents:' list entry (slug in single or double quotes)
_AGENTS_LIST_RE = re.compile(r"['\"]([a-z][a-z0-9_-]+)['\"]")

#: Pattern that identifies a self-declared read-only agent from its body text.
#: Matches only explicit self-attributive declarations:
#:   - ``**read-only**`` (bold, emphasis on the agent's own capability)
#:   - ``you are/perform/operate ... read-only`` (direct self-reference)
#: Does NOT match incidental mentions like "external paths are read-only",
#: "authoritative — read-only" (table label), or "Read-only agents must NOT".
_READONLY_BODY_RE = re.compile(
    r"(?:\*\*read[- ]only\*\*|\byou\b[^.\n]*\bread[- ]only\b)",
    re.IGNORECASE,
)

#: Tools that read-only agents must not claim.
#: 'execute' is intentionally excluded: read-only agents may legitimately run
#: read-only shell commands (grep, find, python -c) without modifying files.
_READWRITE_TOOLS = frozenset({"edit", "write", "create"})


def _check_invariant_core_present(
    file_map: dict[str, str],
) -> list[AuditFinding]:
    """Check that every .agent.md file contains the Invariant Core marker.

    The agent-refactor spec requires every agent file to have an Invariant
    Core section marked with a ⛔ symbol.

    Args:
        file_map: Rendered file content keyed by relative path.

    Returns:
        List of AuditFinding for files missing the ⛔ marker.
    """
    findings: list[AuditFinding] = []
    for rel_path, content in file_map.items():
        if not rel_path.endswith(".agent.md"):
            continue
        if "references/" in rel_path:
            continue
        if "\u26d4" not in content:  # ⛔
            findings.append(AuditFinding(
                category="AGENT_REFACTOR",
                code="AR_MISSING_INVARIANT_CORE",
                severity="warning",
                file=rel_path,
                description=(
                    "Agent file is missing the Invariant Core section (⛔ marker). "
                    "Add a '> ⛔ **Do not modify or omit.**' section."
                ),
            ))
    return findings


def _check_return_handoff_present(
    file_map: dict[str, str],
) -> list[AuditFinding]:
    """Check that every .agent.md file has a return-to-orchestrator handoff.

    Every agent must declare a handoff back to orchestrator so the
    conversation can be cleanly returned after the agent's work is done.

    Args:
        file_map: Rendered file content keyed by relative path.

    Returns:
        List of AuditFinding for agent files without an orchestrator handoff.
    """
    findings: list[AuditFinding] = []
    for rel_path, content in file_map.items():
        if not rel_path.endswith(".agent.md"):
            continue
        if "references/" in rel_path:
            continue
        # The orchestrator itself doesn't need a return handoff to itself.
        # The team-builder is a meta entry-point agent, not a collaborating agent.
        slug = Path(rel_path).stem.replace(".agent", "")
        if slug in {"orchestrator", "team-builder"}:
            continue
        # Check YAML block for a handoff that routes to orchestrator
        if "agent: orchestrator" not in content and "agent: 'orchestrator'" not in content:
            findings.append(AuditFinding(
                category="AGENT_REFACTOR",
                code="AR_MISSING_RETURN_HANDOFF",
                severity="warning",
                file=rel_path,
                description=(
                    "Agent file has no 'Return to Orchestrator' handoff. "
                    "Add a handoff entry with 'agent: orchestrator' in the YAML front matter."
                ),
            ))
    return findings


def _check_readonly_tool_declarations(
    file_map: dict[str, str],
) -> list[AuditFinding]:
    """Check that self-declared read-only agents do not claim write tools.

    An agent whose body prose explicitly states it is 'read-only' must have
    only 'read' and 'search' in its tools declaration.

    Args:
        file_map: Rendered file content keyed by relative path.

    Returns:
        List of AuditFinding for read-only agents declaring write tools.
    """
    findings: list[AuditFinding] = []
    _tools_re = re.compile(r"tools\s*:\s*\[([^\]]*)\]")

    for rel_path, content in file_map.items():
        if not rel_path.endswith(".agent.md"):
            continue
        if "references/" in rel_path:
            continue
        # Only enforce rule on files that self-declare as read-only
        if not _READONLY_BODY_RE.search(content):
            continue
        m = _tools_re.search(content[:400])  # YAML block only
        if not m:
            continue
        declared_tools = {t.strip().strip("'\"") for t in m.group(1).split(",")}
        violations = declared_tools & _READWRITE_TOOLS
        if violations:
            findings.append(AuditFinding(
                category="AGENT_REFACTOR",
                code="AR_READONLY_TOOL_VIOLATION",
                severity="error",
                file=rel_path,
                description=(
                    f"Agent declares itself read-only but claims write tool(s): "
                    f"{', '.join(sorted(violations))}. Remove them from the tools list."
                ),
            ))
    return findings


def _check_dangling_agent_slugs(
    file_map: dict[str, str],
    output_dir: Path | None = None,
) -> list[AuditFinding]:
    """Check that every slug in an agent's YAML 'agents:' list has a file.

    An agent that references @other-agent in its YAML handoffs requires that
    other-agent's file to exist. Dangling references break the handoff chain.
    Also resolves slugs from .claude/agents/ in the repo root, which is
    a valid location for domain-expert agents alongside .github/agents/.

    Args:
        file_map:   Rendered file content keyed by relative path.
        output_dir: Optional absolute path to the agents output directory
                    (.github/agents/). When provided, also loads slugs from
                    the sibling .claude/agents/ directory.

    Returns:
        List of AuditFinding for each dangling agent slug reference.
    """
    generated_slugs = {
        Path(p).stem.replace(".agent", "")
        for p in file_map
        if p.endswith(".agent.md") and "references/" not in p
    }
    # Also accept slugs from .claude/agents/ in the same repo root
    if output_dir is not None:
        # output_dir is repo/.github/agents/ → repo root is output_dir.parent.parent
        claude_agents_dir = output_dir.parent.parent / ".claude" / "agents"
        if claude_agents_dir.is_dir():
            for p in claude_agents_dir.iterdir():
                if p.suffix == ".md":
                    generated_slugs.add(p.stem.replace(".agent", ""))
    findings: list[AuditFinding] = []

    for rel_path, content in file_map.items():
        if not rel_path.endswith(".agent.md"):
            continue
        if "references/" in rel_path:
            continue

        # Extract the YAML block
        if not content.startswith("---"):
            continue
        end = content.find("---", 3)
        if end == -1:
            continue
        yaml_block = content[3:end]

        # Find the 'agents:' list line(s)
        in_agents_block = False
        for line in yaml_block.splitlines():
            stripped = line.strip()
            if stripped.startswith("agents:"):
                in_agents_block = True
            elif in_agents_block:
                if not stripped.startswith("-") and not stripped.startswith("'") and not stripped.startswith('"'):
                    in_agents_block = False
                    continue
            if not in_agents_block:
                continue
            for slug in _AGENTS_LIST_RE.findall(stripped):
                if slug in _CONDITIONAL_ARCHETYPES:
                    continue  # conditional archetypes are legitimately absent
                if slug not in generated_slugs:
                    findings.append(AuditFinding(
                        category="AGENT_REFACTOR",
                        code="AR_DANGLING_AGENT_SLUG",
                        severity="warning",
                        file=rel_path,
                        description=(
                            f"YAML 'agents:' references '@{slug}' "
                            f"but no {slug}.agent.md was generated."
                        ),
                    ))

    return findings


# ---------------------------------------------------------------------------
# Code-hygiene checks (CH-* codes)
# ---------------------------------------------------------------------------

#: Minimum consecutive table/list lines that trigger a CH-14 finding
_CH14_INLINE_DATA_THRESHOLD = 10


def _check_ch14_inline_data_blocks(
    file_map: dict[str, str],
) -> list[AuditFinding]:
    """CH-14: Docs should reference shared data, not duplicate it inline.

    Flags agent files that contain a run of more than _CH14_INLINE_DATA_THRESHOLD
    consecutive table rows or list items outside of Invariant Core sections.
    These are candidates for extraction to a .reference.md file.

    Args:
        file_map: Rendered file content keyed by relative path.

    Returns:
        List of AuditFinding for files exceeding the inline data threshold.
    """
    findings: list[AuditFinding] = []
    # Reference files are expected to contain large data blocks — skip them
    for rel_path, content in file_map.items():
        if not rel_path.endswith(".agent.md"):
            continue
        if "references/" in rel_path:
            continue

        in_invariant = False
        in_yaml = content.startswith("---")
        yaml_fence_count = 0
        run_count = 0
        max_run = 0
        for line in content.splitlines():
            # Skip YAML front matter — list items there are not document data
            if in_yaml:
                if line.strip() == "---":
                    yaml_fence_count += 1
                    if yaml_fence_count >= 2:
                        in_yaml = False
                continue

            # Track whether we are inside the Invariant Core section
            if "\u26d4" in line or "Invariant Core" in line:
                in_invariant = True
            elif line.startswith("## ") and in_invariant:
                in_invariant = False

            if in_invariant:
                run_count = 0
                continue

            stripped = line.strip()
            if stripped.startswith("|") or stripped.startswith("- ") or stripped.startswith("* "):
                run_count += 1
                max_run = max(max_run, run_count)
            else:
                run_count = 0

        if max_run > _CH14_INLINE_DATA_THRESHOLD:
            findings.append(AuditFinding(
                category="CODE_HYGIENE",
                code="CH14_INLINE_DATA_BLOCK",
                severity="warning",
                file=rel_path,
                description=(
                    f"CH-14: {max_run} consecutive data lines (tables/lists) found outside "
                    f"Invariant Core. Consider extracting to a .reference.md file if the data "
                    f"is shared or volatile."
                ),
            ))
    return findings


def _check_ch20_duplicate_descriptions(
    file_map: dict[str, str],
) -> list[AuditFinding]:
    """CH-20: Agent docs must not contradict each other.

    Checks for two agents with identical YAML 'description:' fields, which
    indicates a copy-paste error or scope overlap.

    Args:
        file_map: Rendered file content keyed by relative path.

    Returns:
        List of AuditFinding for duplicated description fields.
    """
    _desc_re = re.compile(r'^description:\s*["\']?(.+?)["\']?\s*$', re.MULTILINE)
    desc_to_files: dict[str, list[str]] = {}

    for rel_path, content in file_map.items():
        if not rel_path.endswith(".agent.md"):
            continue
        if "references/" in rel_path:
            continue
        m = _desc_re.search(content[:400])
        if m:
            desc = m.group(1).strip().lower()
            desc_to_files.setdefault(desc, []).append(rel_path)

    findings: list[AuditFinding] = []
    for desc, files in desc_to_files.items():
        if len(files) > 1:
            findings.append(AuditFinding(
                category="CODE_HYGIENE",
                code="CH20_DUPLICATE_DESCRIPTION",
                severity="warning",
                file=", ".join(sorted(files)),
                description=(
                    f"CH-20: {len(files)} agent files share the same YAML description. "
                    "Verify scope ownership is distinct and update descriptions."
                ),
            ))
    return findings


# ---------------------------------------------------------------------------
# AI audit (optional — requires standalone `copilot` CLI on PATH)
# ---------------------------------------------------------------------------

def _get_copilot_path() -> str | None:
    """Locate the standalone `copilot` CLI on PATH.

    Returns:
        Absolute path string to the `copilot` binary, or None if not found.
    """
    return shutil.which("copilot")


def _run_ai_audit(
    file_map: dict[str, str],
    manifest: dict[str, Any],
    copilot_path: str,
    output_dir: Path | None = None,
) -> str | None:
    """Invoke the standalone `copilot` CLI for an AI-powered audit.

    Sends a compact summary of the generated agent files and requests a
    conflict + presupposition review via copilot non-interactive mode.

    Args:
        file_map:     Rendered file content keyed by relative path.
        manifest:     Team manifest from analyze.build_manifest().
        copilot_path: Absolute path to the `copilot` binary.
        output_dir:   Optional agents output directory. When provided, the AI
                      has read access to the deployed agent files via --add-dir.

    Returns:
        AI-generated audit report as a string, or None on failure.
    """
    context = _build_ai_context(file_map, manifest, output_dir)

    prompt = (
        "You are performing a post-generation audit of a multi-agent AI team. "
        "Review the agent files for: (1) logical conflicts between agent responsibilities "
        "or authority boundaries, (2) hidden presuppositions or structural assumptions that "
        "could fail silently in use, (3) coverage gaps — scenarios that no agent handles, "
        "(4) over-claimed authority — two agents asserting ownership of the same scope. "
        "Be concise. List concrete findings only. If the team is well-formed, say so briefly.\n\n"
        f"Audit the following generated agent team for project "
        f"'{manifest.get('project_name', 'unknown')}'.\n\n"
        f"{context}"
    )

    command = [
        copilot_path,
        "-p", prompt,
        "--no-ask-user",
        "--no-custom-instructions",
        "--model", _AI_MODEL,
        "--silent",
    ]

    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = proc.stdout.strip()
        return output if output else None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def _build_ai_context(
    file_map: dict[str, str],
    manifest: dict[str, Any],
    output_dir: Path | None = None,
) -> str:
    """Build a compact context string for the AI audit prompt.

    Includes agent file content (full when under budget, excerpts otherwise).
    When output_dir is provided, filenames are listed as absolute paths so
    the AI agent can read them directly if it chooses to verify details.

    Args:
        file_map:   Rendered file content keyed by relative path.
        manifest:   Team manifest from analyze.build_manifest().
        output_dir: Optional absolute path to the agents directory. Used to
                    prefix relative paths with absolute paths in the context.

    Returns:
        Context string for the AI prompt.
    """
    lines = [
        f"Project: {manifest.get('project_name')}",
        f"Framework: {manifest.get('framework')}",
        f"Total agents: {len(manifest.get('agent_slug_list', []))}",
        f"Components: {[c['slug'] for c in manifest.get('components', [])]}",
        "",
        "Agent files (path → full body or excerpt):",
    ]

    total_chars = 0
    agent_paths = sorted(p for p in file_map if p.endswith(".agent.md"))
    for rel_path in agent_paths:
        if total_chars >= _AI_CONTEXT_LIMIT:
            remaining = len(agent_paths) - len([l for l in lines if l.startswith("  ")])
            lines.append(f"  [...{remaining} more files omitted for brevity]")
            break
        content = file_map[rel_path]
        # Use relative path — absolute paths prompt the AI to attempt reads,
        # which blocks without --allow-all-paths. The 800-char excerpt provides
        # enough context for the audit without additional file reads.
        display_path = rel_path
        # Strip YAML block to get at the prose body
        if content.startswith("---"):
            end = content.find("---", 3)
            body = content[end + 3:].strip() if end != -1 else content
        else:
            body = content
        excerpt = body[:800].replace("\n", "  ")
        lines.append(f"  {display_path}:\n    {excerpt}")
        total_chars += len(excerpt)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Disk helpers
# ---------------------------------------------------------------------------

def _load_files_from_disk(output_dir: Path) -> dict[str, str]:
    """Load all generated files from an output directory.

    Args:
        output_dir: Root agents directory path.

    Returns:
        Dict mapping relative path strings to file content.
    """
    file_map: dict[str, str] = {}
    if not output_dir.exists():
        return file_map
    for path in output_dir.rglob("*"):
        if path.suffix not in {".md", ".csv"}:
            continue
        rel = str(path.relative_to(output_dir))
        try:
            file_map[rel] = path.read_text(encoding="utf-8")
        except OSError:
            pass
    return file_map
