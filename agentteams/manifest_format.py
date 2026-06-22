"""
manifest_format.py — manifest field derivation/formatting helpers (the _format_*/_default_*/
_collect_*/_derive_* cluster) carved from analyze.py (CH-07). analyze.build_manifest imports
these back; re-exported from analyze so external importers resolve them from agentteams.analyze.
"""

from __future__ import annotations

import re
from typing import Any

_RETRIEVAL_MODES: set[str] = {
    "none",
    "relational-metadata",
    "lexical-index",
    "sparse-vector",
    "embedding-vector",
}


def _default_reference_db_path(description: dict) -> str | None:
    """Infer a sensible `reference_db_path` for projects with a doc site.

    Triggers only when the descriptor declares a `doc_site_config_file`
    AND a `docs/` directory exists on disk at the project root. Returns
    None otherwise so the manual-placeholder fallback is preserved.
    Plan: references/plans/F1-self-team-manual-placeholders-2026-05-25.md
    """
    if not description.get("doc_site_config_file"):
        return None
    project_path = description.get("existing_project_path")
    if not project_path:
        return None
    from pathlib import Path as _Path

    if (_Path(project_path) / "docs").is_dir():
        return "docs/"
    return None


def _default_style_reference_path(description: dict) -> str | None:
    """Infer a sensible `style_reference_path` for doc-site projects.

    Prefers `docs_src/` (the mkdocs convention used in agentteams),
    falls back to `docs/` if `docs_src/` is absent. None otherwise.
    """
    if not description.get("doc_site_config_file"):
        return None
    project_path = description.get("existing_project_path")
    if not project_path:
        return None
    from pathlib import Path as _Path

    root = _Path(project_path)
    if (root / "docs_src").is_dir():
        return "docs_src/"
    if (root / "docs").is_dir():
        return "docs/"
    return None


def _default_primary_output_dir(project_type: str) -> str:
    return {
        "writing": "docs/",
        "software": "src/",
        "data-pipeline": "src/",
        "research": "docs/",
        "documentation": "docs/",
        "mixed": "src/",
        "unknown": "src/",
    }.get(project_type, "src/")


def _default_output_format(project_type: str) -> str:
    return {
        "writing": "PDF",
        "software": "Python modules",
        "data-pipeline": "CSV",
        "research": "PDF",
        "documentation": "HTML",
        "mixed": "HTML",
        "unknown": "plain text",
    }.get(project_type, "plain text")


def _format_deliverable_type(deliverables: list[str], project_type: str) -> str:
    if not deliverables:
        return _default_output_format(project_type)
    if len(deliverables) == 1:
        return deliverables[0]
    return ", ".join(deliverables[:-1]) + " and " + deliverables[-1]


# ---------------------------------------------------------------------------
# Placeholder builders
# ---------------------------------------------------------------------------

def _build_placeholder_map(**kwargs: str) -> dict[str, str]:
    """Build the auto_resolved_placeholders dict from keyword arguments."""
    mapping: dict[str, str] = {}
    for key, val in kwargs.items():
        placeholder = key.upper()
        mapping[placeholder] = val
    return mapping


def _format_authority_hierarchy(hierarchy: list[dict[str, Any]]) -> str:
    if not hierarchy:
        return "1. **Project source files** — ground truth for all technical claims"
    lines = []
    for src in hierarchy:
        scope = f" — {src['scope']}" if src.get("scope") else ""
        lines.append(f"{src['rank']}. **{src['name']}** (`{src['path']}`){scope}")
    return "\n".join(lines)


def _format_authority_sources_list(hierarchy: list[dict[str, Any]]) -> str:
    if not hierarchy:
        return "- Project source files (read-only)"
    return "\n".join(f"- `{src['path']}` — {src.get('scope', 'general')}" for src in hierarchy)


def _format_agent_list(slugs: list[str]) -> str:
    if not slugs:
        return "[]"
    formatted = ["  - " + s for s in slugs]
    return "\n" + "\n".join(formatted)


def _format_workstream_source_map(components: list[dict[str, Any]]) -> str:
    if not components:
        return "No components defined."
    lines = []
    for comp in components:
        output = comp.get("output_file") or "TBD"
        lines.append(f"- `{comp['slug']}` → `{output}`")
    return "\n".join(lines)


def _format_style_rules(rules: list[str]) -> str:
    if not rules:
        return "No project-specific style rules defined."
    return "\n".join(f"- {r}" for r in rules)


def _format_string_list(values: list[str], *, default: str) -> str:
    """Format a plain Markdown bullet list or return a default string."""
    cleaned = [v for v in values if v]
    if not cleaned:
        return default
    return "\n".join(f"- {v}" for v in cleaned)


_DIAGRAM_TOOL_MAP: dict[str, tuple[str, str]] = {
    # tool-name-lower → (display-name, file-extension)
    "mermaid": ("Mermaid", "mmd"),
    "graphviz": ("Graphviz/DOT", "dot"),
    "dot": ("Graphviz/DOT", "dot"),
    "plantuml": ("PlantUML", "puml"),
    "d2": ("D2", "d2"),
    "drawio": ("Draw.io", "drawio"),
}


def _derive_diagram_tools(tools: list[dict[str, Any]]) -> tuple[str, str]:
    """Return (diagram_tools_display, diagram_extension) from the tools list.

    Args:
        tools: List of tool dicts from the project description.

    Returns:
        Tuple of (display name string, file extension string).
    """
    for tool in tools:
        name_lower = tool.get("name", "").lower()
        if name_lower in _DIAGRAM_TOOL_MAP:
            display, ext = _DIAGRAM_TOOL_MAP[name_lower]
            return display, ext
    # Default when no diagram tool is explicitly listed
    return "Mermaid or Graphviz/DOT", "mmd"


def _format_domain_agent_list(agent_slugs: list[str]) -> str:
    lines = []
    descriptions = {
        "work-summarizer": "synthesizes daily/weekly/monthly work summaries from plan artifacts and git history",
        "primary-producer": "drafts and revises primary deliverables",
        "quality-auditor": "read-only structural and prose quality audit",
        "cohesion-repairer": "repairs within-section cohesion failures",
        "style-guardian": "enforces voice and style fidelity",
        "technical-validator": "verifies technical accuracy against authority sources",
        "retrieval-integrator": "validates retrieval query, maintenance, and trigger contracts",
        "format-converter": "converts deliverables to final output format",
        "reference-manager": "manages the reference/bibliography database",
        "output-compiler": "assembles components into the final deliverable package",
        "visual-designer": "creates and revises diagrams and figures",
    }
    for slug in _dedupe_keep_order(agent_slugs):
        desc = descriptions.get(slug, "specialized domain agent")
        lines.append(f"- `@{slug}` — {desc}")
    return "\n".join(lines) if lines else "No domain agents selected."


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _format_workstream_expert_list(components: list[dict[str, Any]]) -> str:
    lines = []
    for comp in components:
        lines.append(f"- `@{comp['slug']}-expert` — {comp['name']}")
    return "\n".join(lines) if lines else "No workstream experts defined."


_MANUAL_TOKEN_FULLMATCH_RE = re.compile(r"\{MANUAL:([A-Z][A-Z0-9_]*)\}")


def _collect_manual_required(placeholder_map: dict[str, str]) -> list[dict[str, str]]:
    """Identify placeholders whose resolved value is itself a {MANUAL:...} token.

    Uses fullmatch to avoid false positives from composed values (e.g.
    authority hierarchy entries) whose text happens to contain MANUAL tokens
    as embedded documentation or path references.
    """
    manual = []
    for placeholder, value in placeholder_map.items():
        if value is None:
            continue
        if _MANUAL_TOKEN_FULLMATCH_RE.fullmatch(value.strip()):
            manual.append({
                "placeholder": placeholder,
                "agent_file": "multiple",
                "context": f"The placeholder {{{placeholder}}} could not be auto-resolved.",
                "suggestion": "",
            })
    return manual


def _collect_tool_metadata_manual_required(
    tool_agents: list[dict[str, Any]],
    reference_tools: list[dict[str, Any]],
    framework: str = "copilot-vscode",
) -> list[dict[str, str]]:
    """Return setup items for tool metadata fields still missing after enrichment."""
    manual: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    field_specs = (
        ("docs_url", "TOOL_DOCS_URL", "official documentation URL"),
        ("api_surface", "TOOL_API_SURFACE", "project-relevant API surface summary"),
        ("common_patterns", "TOOL_COMMON_PATTERNS", "tool-specific usage patterns and pitfalls"),
    )

    def add_items(specs: list[dict[str, Any]], *, reference: bool) -> None:
        for spec in specs:
            if reference:
                rel_path = f"references/{spec['slug']}-reference.md"
                doc_label = "reference file"
            else:
                # Operational tool doc — a skill (Claude) or reference doc (Copilot),
                # never an agent. Point setup items at the actual emitted path.
                slug = spec["slug"]
                base = slug[len("tool-"):] if slug.startswith("tool-") else slug
                if framework == "claude":
                    rel_path = f"../skills/{slug}.md"
                    doc_label = "skill document"
                else:
                    rel_path = f"references/ref-{base}-reference.md"
                    doc_label = "reference document"

            for field_name, placeholder, description in field_specs:
                if spec.get(field_name):
                    continue
                dedupe_key = (rel_path, placeholder)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                manual.append({
                    "placeholder": placeholder,
                    "agent_file": rel_path,
                    "context": (
                        f"The {doc_label} for '{spec['tool_name']}' is missing a {description}. "
                        "Add it to the tools[] entry in the project brief or extend the built-in tool metadata catalog."
                    ),
                    "suggestion": "",
                })

    add_items(tool_agents, reference=False)
    add_items(reference_tools, reference=True)
    return manual


def _collect_component_manual_required(components: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Return setup items for unresolved per-component placeholders."""
    manual: list[dict[str, str]] = []
    for component in components:
        rel_path = f"{component['slug']}-expert.agent.md"
        field_specs = (
            ("description", "COMPONENT_SPEC", "component specification"),
            ("sections", "COMPONENT_SECTIONS", "section outline"),
            ("sources", "COMPONENT_SOURCES", "source list"),
            ("quality_criteria", "COMPONENT_QUALITY_CRITERIA", "quality criteria list"),
        )
        for field_name, placeholder, label in field_specs:
            value = component.get(field_name)
            if value:
                continue
            manual.append({
                "placeholder": placeholder,
                "agent_file": rel_path,
                "context": (
                    f"Component '{component['name']}' is missing its {label}. "
                    "Add the field to the component entry in the project brief so the expert brief renders completely."
                ),
                "suggestion": "",
            })
    return manual


def _description_text(description: dict[str, Any]) -> str:
    """Return a single string for keyword analysis."""
    parts = [
        description.get("project_goal", ""),
        description.get("project_name", ""),
        description.get("output_format", ""),
        " ".join(description.get("deliverables", [])),
        " ".join(c.get("description", "") for c in description.get("components", [])),
    ]
    # Include tool names, categories, and versions for richer keyword matching
    for t in description.get("tools", []):
        parts.append(t.get("name", ""))
        parts.append(t.get("category", ""))
        parts.append(t.get("version", ""))
    retrieval = description.get("retrieval_integration", {})
    if isinstance(retrieval, dict):
        parts.append(retrieval.get("mode", ""))
        parts.extend(retrieval.get("query_entrypoints", []))
        parts.extend(retrieval.get("maintenance_entrypoints", []))
        parts.extend(retrieval.get("trigger_sources", []))
    return " ".join(parts)


def _normalize_retrieval_integration(raw: Any) -> dict[str, Any]:
    """Normalize retrieval integration contract to a stable shape."""
    if not isinstance(raw, dict):
        return {
            "mode": "none",
            "query_entrypoints": [],
            "maintenance_entrypoints": [],
            "trigger_sources": ["manual"],
            "source_of_truth": [],
            "staleness_slo_minutes": 60,
            "trigger_contract_version": "v1",
        }

    mode = str(raw.get("mode", "none")).strip().lower()
    if mode not in _RETRIEVAL_MODES:
        mode = "none"

    def _list(name: str) -> list[str]:
        values = raw.get(name, [])
        if not isinstance(values, list):
            return []
        return [str(v) for v in values if str(v).strip()]

    trigger_sources = _list("trigger_sources")
    if not trigger_sources:
        trigger_sources = ["manual"]

    try:
        staleness = int(raw.get("staleness_slo_minutes", 60) or 60)
    except (TypeError, ValueError):
        staleness = 60

    return {
        "mode": mode,
        "query_entrypoints": _list("query_entrypoints"),
        "maintenance_entrypoints": _list("maintenance_entrypoints"),
        "trigger_sources": trigger_sources,
        "source_of_truth": _list("source_of_truth"),
        "staleness_slo_minutes": staleness,
        "trigger_contract_version": str(raw.get("trigger_contract_version", "v1") or "v1"),
    }


def _has_unknown_tool_metadata(
    tool_agents: list[dict[str, Any]],
    reference_tools: list[dict[str, Any]],
) -> bool:
    """Return True if any tool is missing docs_url, api_surface, or common_patterns.

    Args:
        tool_agents:    Specialist-tier tool agent specs from detect_tool_agents().
        reference_tools: Reference-tier tool specs from detect_reference_tools().

    Returns:
        True when at least one tool has an incomplete metadata set.
    """
    fields = ("docs_url", "api_surface", "common_patterns")
    for spec in list(tool_agents) + list(reference_tools):
        if any(not spec.get(f) for f in fields):
            return True
    return False


def _format_unresolved_tool_list(
    tool_agents: list[dict[str, Any]],
    reference_tools: list[dict[str, Any]],
    framework: str = "copilot-vscode",
) -> str:
    """Format a Markdown bullet list of tools with missing documentation metadata.

    Args:
        tool_agents:    Operational tool-doc specs from detect_tool_agents().
        reference_tools: Reference-tier tool specs from detect_reference_tools().
        framework:      Target framework — determines the doc path shown.

    Returns:
        Markdown string listing each tool with its missing fields, or a 'none'
        message when all tools are fully resolved.
    """
    fields_checked = (("docs_url", "docs URL"), ("api_surface", "API surface"), ("common_patterns", "usage patterns"))
    lines: list[str] = []

    for spec in tool_agents:
        gaps = [label for field, label in fields_checked if not spec.get(field)]
        if gaps:
            slug = spec["slug"]
            base = slug[len("tool-"):] if slug.startswith("tool-") else slug
            if framework == "claude":
                doc_ref = f"skill `.claude/skills/{slug}.md`"
            else:
                doc_ref = f"reference doc `references/ref-{base}-reference.md`"
            lines.append(
                f"- **{spec['tool_name']}** ({doc_ref}) "
                f"— missing: {', '.join(gaps)}"
            )

    for spec in reference_tools:
        gaps = [label for field, label in fields_checked if not spec.get(field)]
        if gaps:
            ref_path = f"references/{spec['slug']}-reference.md"
            lines.append(
                f"- **{spec['tool_name']}** (reference file `{ref_path}`) "
                f"— missing: {', '.join(gaps)}"
            )

    return "\n".join(lines) if lines else "No tools with missing metadata."
