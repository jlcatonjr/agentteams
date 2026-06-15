"""
render_pipeline.py — template rendering + content-merge helpers.

Extracted verbatim from build_team.py (CH-07 modular structure). build_team
re-exports these names so main and tests resolve them unchanged. TEMPLATES_DIR
is recomputed from the package location (identical to build_team's
_SCRIPT_DIR/agentteams/templates) to avoid importing build_team back (no cycle).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

from agentteams import emit, render
from agentteams.frameworks.claude import ClaudeAdapter
from agentteams.frameworks.copilot_cli import CopilotCLIAdapter
from agentteams.frameworks.copilot_vscode import CopilotVSCodeAdapter

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"

def _apply_placeholder_policy(
    manifest: dict,
    *,
    strict_manual_placeholders: bool,
) -> None:
    """Apply dual-mode policy for optional governance placeholders.

    In strict mode, unresolved {MANUAL:*} tokens are preserved as-is.
    In usability mode, selected optional placeholders are replaced with
    explicit defaults and removed from SETUP-REQUIRED tracking.
    """
    if strict_manual_placeholders:
        return

    auto = manifest.get("auto_resolved_placeholders", {})
    ref_key = "REFERENCE_DB_PATH"
    style_key = "STYLE_REFERENCE_PATH"
    ref_manual = "{MANUAL:REFERENCE_DB_PATH}"
    style_manual = "{MANUAL:STYLE_REFERENCE_PATH}"

    if str(auto.get(ref_key, "")).strip() == ref_manual:
        auto[ref_key] = "N/A - no citation database configured for this project"

    if str(auto.get(style_key, "")).strip() == style_manual:
        desc = manifest.get("description", {}) or {}
        style_value = desc.get("style_reference") or desc.get("style_reference_path")
        auto[style_key] = (
            str(style_value)
            if style_value
            else "N/A - no formal style guide defined for this project"
        )

    manual_items = manifest.get("manual_required_placeholders", [])
    if manual_items:
        filtered = [
            item for item in manual_items
            if item.get("placeholder") not in {ref_key, style_key}
        ]
        manifest["manual_required_placeholders"] = filtered
def _resolve_strict_manual_mode(*, strict_arg: bool | None, self_update: bool) -> bool:
    """Resolve strict/manual policy from CLI args.

    Explicit CLI flags win. Otherwise strict mode defaults to True in
    self-maintenance mode and False for normal generation.
    """
    if strict_arg is not None:
        return bool(strict_arg)
    return bool(self_update)
def _build_final_rendered(
    manifest: dict[str, Any],
    adapter: CopilotVSCodeAdapter | CopilotCLIAdapter | ClaudeAdapter,
    project_name: str,
) -> list[tuple[str, str]]:
    """Render templates and apply framework post-processing.

    Returns a list of (relative_path, content) pairs including
    runtime-handoffs (when the adapter uses manifest delivery) and the
    pipeline graph. This is the shared rendering step used by the generate
    path, ``--update``, and ``--check``; ``--check`` uses the result for
    content comparison only and does not write to disk.
    """
    from agentteams import graph as _graph

    rendered = render.render_all(manifest, templates_dir=TEMPLATES_DIR)
    final: list[tuple[str, str]] = []
    runtime_handoff_agents: list[dict[str, object]] = []
    for rel_path, content in rendered:
        file_type = _guess_file_type(rel_path)
        if file_type == "agent":
            slug = Path(rel_path).stem.replace(".agent", "")
            if adapter.handoff_delivery_mode() == "manifest":
                handoffs = adapter.extract_handoffs(content)
                if handoffs:
                    runtime_handoff_agents.append({"agent": slug, "handoffs": handoffs})
            content = adapter.render_agent_file(content, slug, manifest)
        elif file_type == "instructions":
            content = adapter.render_instructions_file(content, manifest)
        elif file_type == "skill":
            slug = Path(rel_path).stem
            content = adapter.render_skill_file(content, slug, manifest)
        final_path = adapter.finalize_output_path(rel_path, file_type)
        final.append((final_path, content))

    if runtime_handoff_agents:
        final.append((
            "references/runtime-handoffs.json",
            json.dumps({
                "schema_version": "1.0",
                "framework": adapter.framework_id,
                "project_name": project_name,
                "agents": runtime_handoff_agents,
            }, indent=2) + "\n",
        ))

    final.append((
        "references/pipeline-graph.md",
        _graph.generate_graph_document(dict(final), project_name=project_name),
    ))
    return final
def _make_content_matches(
    output_dir: Path,
    rendered_by_path: dict[str, str],
    security_refresh_paths: set[str],
) -> Callable[[str], bool]:
    """Return a predicate: does a file's disk content match its rendered content?

    Files in ``security_refresh_paths`` always return False (they are
    force-written on every ``--update``). Missing files return False.
    The comparison mirrors what ``emit`` writes: manual-value preservation
    followed by merge-fence normalization.
    """
    def _matches(path: str) -> bool:
        if path in security_refresh_paths:
            return False
        rendered = rendered_by_path.get(path)
        if rendered is None:
            return False
        disk_path = emit._resolve_path(output_dir, path)
        if not disk_path.exists():
            return False
        disk_text = disk_path.read_text(encoding="utf-8")
        preserved = _preserve_manual_values(disk_text, rendered)
        effective = emit._normalize_generated_content(path, preserved)
        effective = emit._ensure_project_notes_section(path, effective)
        return effective == disk_text
    return _matches
def _stale_tool_agent_paths(
    manifest: dict[str, Any],
    output_dir: Path,
    framework_id: str,
) -> list[Path]:
    """Return existing legacy tool-AGENT files for tools now emitted as docs.

    Targets only the exact `tool-<slug>` agent file for each tool the current
    team carries (copilot: `tool-<slug>.agent.md`; claude: `tool-<slug>.md` in
    the agents dir) — never touches unrelated agents.
    """
    suffix = ".md" if framework_id == "claude" else ".agent.md"
    paths: list[Path] = []
    for ta in manifest.get("tool_agents", []):
        slug = ta.get("slug", "")
        if not slug:
            continue
        candidate = output_dir / f"{slug}{suffix}"
        if candidate.is_file():
            paths.append(candidate)
    return paths
def _remove_stale_tool_agents(
    manifest: dict[str, Any],
    output_dir: Path,
    framework_id: str,
    *,
    overwrite: bool,
    dry_run: bool,
) -> tuple[list[str], list[str]]:
    """Migrate legacy tool-*.agent.md files (tools are now docs/skills).

    Overwrite mode backs the files up then deletes them; otherwise a notice is
    returned and the file is left in place. Returns (removed_paths, notices).
    """
    stale = _stale_tool_agent_paths(manifest, output_dir, framework_id)
    if not stale:
        return [], []
    if dry_run:
        for p in stale:
            print(f"[DRY RUN] REMOVE (legacy tool agent → now a doc) {p}")
        return [str(p) for p in stale], []
    if not overwrite:
        return [], [
            f"legacy tool agent {p.name} remains on disk — {p.stem} is now a tool "
            f"document; re-run with --overwrite to remove it."
            for p in stale
        ]
    # Overwrite: back up before deleting so any hand edits are recoverable.
    rels: list[str] = []
    for p in stale:
        try:
            rels.append(str(p.relative_to(output_dir)))
        except ValueError:
            rels.append(p.name)
    try:
        emit.backup_output_dir(
            output_dir,
            files_to_backup=rels,
            reason="stale-tool-agent-removal",
            framework=framework_id,
        )
    except Exception as exc:  # backup is best-effort; never block the migration
        print(f"  !  stale tool-agent backup failed: {exc}", file=sys.stderr)
    removed: list[str] = []
    notices: list[str] = []
    for p in stale:
        try:
            p.unlink()
            removed.append(str(p))
        except OSError as exc:
            notices.append(f"could not remove legacy tool agent {p}: {exc}")
    return removed, notices
def _guess_file_type(rel_path: str) -> str:
    lower = rel_path.lower()
    if "copilot-instructions" in lower or rel_path.endswith("/CLAUDE.md") or rel_path == "../CLAUDE.md":
        return "instructions"
    if "SETUP-REQUIRED" in rel_path:
        return "setup-required"
    if "team-builder" in rel_path:
        return "builder"
    if rel_path.startswith("../skills/") or "/skills/" in rel_path:
        return "skill"
    if rel_path.startswith("references/") or "/references/" in rel_path:
        return "reference"
    return "agent"
_MANUAL_RE = re.compile(r"\{MANUAL:([A-Z][A-Z0-9_]*)\}")
def _preserve_manual_values(existing_content: str, new_content: str) -> str:
    """Carry forward manually-filled {MANUAL:*} values from existing files.

    Scans the existing file for any {MANUAL:NAME} tokens that have been
    replaced with actual values, and applies those same replacements to
    the newly rendered content.

    Args:
        existing_content: Content of the currently-deployed agent file.
        new_content:      Freshly rendered content (may have {MANUAL:*} tokens).

    Returns:
        New content with manual values preserved from the existing file.
    """
    # Find all {MANUAL:*} tokens in the new content
    manual_tokens = set(_MANUAL_RE.findall(new_content))
    if not manual_tokens:
        return new_content

    # For each token, check if the existing file has a non-placeholder value
    # at the same location. We match by looking for the line context.
    result = new_content
    for token_name in manual_tokens:
        placeholder = f"{{MANUAL:{token_name}}}"
        # If the existing file still has the placeholder, nothing to preserve
        if placeholder in existing_content:
            continue
        # The existing file had this token resolved — find what it was replaced with.
        # Strategy: find lines in existing that would have contained this token,
        # by looking for the surrounding text pattern in the template.
        resolved_value = _extract_resolved_value(existing_content, new_content, placeholder)
        if resolved_value is not None:
            result = result.replace(placeholder, resolved_value)

    return result
def _extract_resolved_value(existing: str, new: str, placeholder: str) -> str | None:
    """Extract the value that replaced a placeholder in an existing file.

    Finds the line in new content containing the placeholder, builds a regex
    from the surrounding text, and matches it against the existing content.

    Args:
        existing:    Content of the existing file.
        new:         New template content with placeholder.
        placeholder: The {MANUAL:*} token to look up.

    Returns:
        The resolved value string, or None if it cannot be determined.
    """
    for new_line in new.splitlines():
        if placeholder not in new_line:
            continue
        # Build a pattern: escape everything except the placeholder
        parts = new_line.split(placeholder)
        if len(parts) != 2:
            continue  # Multiple occurrences on same line — skip for safety
        prefix = re.escape(parts[0].strip())
        suffix = re.escape(parts[1].strip())
        if not prefix and not suffix:
            continue
        pattern = prefix + r"(.+?)" + suffix if suffix else prefix + r"(.+)"
        try:
            match = re.search(pattern, existing)
        except re.error:
            continue
        if match:
            return match.group(1).strip()
    return None
