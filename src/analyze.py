"""
analyze.py — Analyze a project description to produce a team manifest.

Takes the normalized description dict from ingest.py and produces a
team manifest dict conforming to schemas/team-manifest.schema.json.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Archetype selection rules
# ---------------------------------------------------------------------------

#: (keywords in project description → archetype slug)
#: First match wins for each archetype; all applicable archetypes are included.
_ARCHETYPE_TRIGGERS: list[tuple[list[str], str]] = [
    # primary-producer is always included
    (["*"], "primary-producer"),
    # quality-auditor: always
    (["*"], "quality-auditor"),
    # cohesion-repairer: writing or documentation projects
    (["chapter", "essay", "paper", "book", "documentation", "doc", "report", "writing"], "cohesion-repairer"),
    # style-guardian: any project with a style reference or writing output
    (["style", "voice", "tone", "brand", "editorial", "chapter", "essay", "paper"], "style-guardian"),
    # technical-validator: code, data, or technical projects
    (["python", "javascript", "rust", "go", "java", "code", "api", "function", "module", "script",
      "pipeline", "data", "sql", "database", "csv", "json", "yaml"], "technical-validator"),
    # format-converter: any project producing a compiled output
    (["latex", "pdf", "pandoc", "html", "markdown", "compile", "build", "convert", "manuscript"], "format-converter"),
    # reference-manager: any project with citations or bibliography
    (["citation", "bibliography", "reference", "bib", "cite", "academic", "paper", "research"], "reference-manager"),
    # output-compiler: multi-component projects that need assembly
    (["chapter", "module", "component", "part", "section", "assemble", "compile", "build", "bundle"], "output-compiler"),
    # visual-designer: projects with figures, diagrams, or visual output
    (["figure", "diagram", "chart", "graph", "visualization", "image", "illustration", "dot", "graphviz"], "visual-designer"),
]

#: Always-included governance agent slugs (tier 2)
GOVERNANCE_AGENTS = [
    "navigator",
    "security",
    "adversarial",
    "conflict-auditor",
    "cleanup",
    "agent-updater",
    "agent-refactor",
]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_manifest(description: dict[str, Any], *, framework: str = "copilot-vscode") -> dict[str, Any]:
    """Build and return a team manifest from a normalized project description.

    Args:
        description: Normalized project description from ingest.load().
        framework:   Target agent framework ('copilot-vscode', 'copilot-cli', 'claude').

    Returns:
        Team manifest dict conforming to schemas/team-manifest.schema.json.
    """
    project_name = _resolve_project_name(description)
    project_goal = description["project_goal"]
    project_type = classify_project_type(description)

    # Core path fields
    primary_output_dir = description.get("primary_output_dir") or _default_primary_output_dir(project_type)
    build_output_dir = description.get("build_output_dir") or "build/"
    figures_dir = description.get("figures_dir") or "figures/"
    reference_db_path = description.get("reference_db_path")
    style_reference_path = description.get("style_reference")
    deliverable_type = _format_deliverable_type(description.get("deliverables", []), project_type)
    output_format = description.get("output_format") or _default_output_format(project_type)
    conversion_pipeline = description.get("conversion_pipeline")

    # Archetype selection
    if "selected_archetypes" in description:
        archetypes = description["selected_archetypes"]
    else:
        archetypes = select_archetypes(description)

    # Tool agents
    tool_agents = detect_tool_agents(description.get("tools", []))

    # Components
    components = _normalize_components(description.get("components", []))

    # Authority hierarchy
    authority_hierarchy = build_authority_hierarchy(description)

    # Workstream expert slugs
    workstream_expert_slugs = [f"{c['slug']}-expert" for c in components]

    # Tool-specific agent slugs
    tool_agent_slugs = [ta["slug"] for ta in tool_agents]

    # All domain agent slugs (archetypes + tool-specific)
    domain_agent_slugs = list(archetypes) + tool_agent_slugs

    # Full slug list for orchestrator
    all_slugs = (
        ["orchestrator"]
        + GOVERNANCE_AGENTS
        + domain_agent_slugs
        + workstream_expert_slugs
    )

    # Auto-resolved placeholder map
    auto_resolved = _build_placeholder_map(
        project_name=project_name,
        project_goal=project_goal,
        primary_output_dir=primary_output_dir,
        build_output_dir=build_output_dir,
        figures_dir=figures_dir,
        reference_db_path=reference_db_path or "{MANUAL:REFERENCE_DB_PATH}",
        style_reference_path=style_reference_path or "{MANUAL:STYLE_REFERENCE_PATH}",
        deliverable_type=deliverable_type,
        output_format=output_format,
        conversion_pipeline=conversion_pipeline or "{MANUAL:CONVERSION_PIPELINE}",
        authority_hierarchy=_format_authority_hierarchy(authority_hierarchy),
        authority_sources_list=_format_authority_sources_list(authority_hierarchy),
        reference_key_convention=description.get("reference_key_convention", "AuthorYear"),
        agent_slug_list=_format_agent_list(all_slugs),
        domain_agent_slugs=_format_agent_list(domain_agent_slugs),
        workstream_expert_slugs=_format_agent_list(workstream_expert_slugs),
        conflict_log_path=".github/agents/references/conflict-log.csv",
        workstream_source_map=_format_workstream_source_map(components),
        style_rules_summary=_format_style_rules(description.get("style_rules", [])),
        domain_agent_list=_format_domain_agent_list(archetypes),
        workstream_expert_list=_format_workstream_expert_list(components),
    )

    # Manual-required placeholders (unfilled MANUAL tokens)
    manual_required = _collect_manual_required(auto_resolved)

    # Output file plan
    output_files = _plan_output_files(archetypes, tool_agents, components, framework)

    return {
        "schema_version": "1.0",
        "project_name": project_name,
        "project_goal": project_goal,
        "project_type": project_type,
        "framework": framework,
        "primary_output_dir": primary_output_dir,
        "build_output_dir": build_output_dir,
        "figures_dir": figures_dir,
        "reference_db_path": reference_db_path,
        "style_reference_path": style_reference_path,
        "deliverable_type": deliverable_type,
        "output_format": output_format,
        "conversion_pipeline": conversion_pipeline,
        "selected_archetypes": archetypes,
        "tool_agents": tool_agents,
        "components": components,
        "authority_hierarchy": authority_hierarchy,
        "agent_slug_list": all_slugs,
        "domain_agent_slugs": domain_agent_slugs,
        "workstream_expert_slugs": workstream_expert_slugs,
        "auto_resolved_placeholders": auto_resolved,
        "manual_required_placeholders": manual_required,
        "output_files": output_files,
    }


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_project_type(description: dict[str, Any]) -> str:
    """Return a project type string based on keyword analysis."""
    text = _description_text(description).lower()

    writing_kw = {"chapter", "essay", "paper", "book", "manuscript", "article", "thesis", "report"}
    software_kw = {"module", "function", "api", "class", "python", "javascript", "rust", "go", "java", "code"}
    data_kw = {"pipeline", "dataset", "csv", "etl", "dataframe", "sql", "database", "transform"}
    research_kw = {"research", "hypothesis", "experiment", "citation", "bibliography", "academic", "study"}
    doc_kw = {"documentation", "docs", "readme", "wiki", "manual", "guide"}

    scores = {
        "writing": sum(1 for kw in writing_kw if kw in text),
        "software": sum(1 for kw in software_kw if kw in text),
        "data-pipeline": sum(1 for kw in data_kw if kw in text),
        "research": sum(1 for kw in research_kw if kw in text),
        "documentation": sum(1 for kw in doc_kw if kw in text),
    }

    if all(v == 0 for v in scores.values()):
        return "unknown"

    top = max(scores, key=lambda k: scores[k])

    # Mixed: if two types both score >= 2
    sorted_scores = sorted(scores.values(), reverse=True)
    if sorted_scores[0] >= 2 and sorted_scores[1] >= 2:
        return "mixed"

    return top


# ---------------------------------------------------------------------------
# Archetype selection
# ---------------------------------------------------------------------------

def select_archetypes(description: dict[str, Any]) -> list[str]:
    """Auto-select domain archetypes from the project description."""
    text = _description_text(description).lower()
    selected: list[str] = []
    seen: set[str] = set()

    for keywords, archetype in _ARCHETYPE_TRIGGERS:
        if archetype in seen:
            continue
        if keywords == ["*"] or any(kw in text for kw in keywords):
            selected.append(archetype)
            seen.add(archetype)

    # Always include primary-producer and quality-auditor
    for required in ("primary-producer", "quality-auditor"):
        if required not in seen:
            selected.append(required)

    return selected


# ---------------------------------------------------------------------------
# Tool agent detection
# ---------------------------------------------------------------------------

def detect_tool_agents(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return tool agent specs for tools that need dedicated agents."""
    agents = []
    for tool in tools:
        if not tool.get("needs_specialist_agent", False):
            continue
        name = tool["name"]
        slug = f"tool-{_slugify(name)}"
        agents.append({
            "slug": slug,
            "tool_name": name,
            "tool_version": tool.get("version", ""),
            "config_files": tool.get("config_files", []),
            "invocation_command": "",
            "invocation_target": "",
        })
    return agents


# ---------------------------------------------------------------------------
# Authority hierarchy
# ---------------------------------------------------------------------------

def build_authority_hierarchy(description: dict[str, Any]) -> list[dict[str, Any]]:
    """Build an ordered authority hierarchy from description."""
    sources = description.get("authority_sources", [])

    # Normalize ranks
    hierarchy: list[dict[str, Any]] = []
    for i, src in enumerate(sources, start=1):
        hierarchy.append({
            "rank": src.get("rank", i),
            "name": src.get("name", f"Source {i}"),
            "path": src.get("path", ""),
            "scope": src.get("scope", "general"),
        })

    hierarchy.sort(key=lambda x: x["rank"])
    return hierarchy


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _normalize_components(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure each component has all required fields."""
    normalized = []
    for i, comp in enumerate(components, start=1):
        normalized.append({
            "slug": comp.get("slug", f"component-{i:02d}"),
            "name": comp.get("name", f"Component {i}"),
            "number": comp.get("number", i),
            "output_file": comp.get("output_file", ""),
            "description": comp.get("description", ""),
            "sections": comp.get("sections", []),
            "sources": comp.get("sources", []),
            "cross_refs": comp.get("cross_refs", []),
            "quality_criteria": comp.get("quality_criteria", []),
        })
    return normalized


def _resolve_project_name(description: dict[str, Any]) -> str:
    name = description.get("project_name", "")
    if not name and description.get("existing_project_path"):
        name = Path(description["existing_project_path"]).name
    return name or "MyProject"


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


def _format_domain_agent_list(archetypes: list[str]) -> str:
    lines = []
    descriptions = {
        "primary-producer": "drafts and revises primary deliverables",
        "quality-auditor": "read-only structural and prose quality audit",
        "cohesion-repairer": "repairs within-section cohesion failures",
        "style-guardian": "enforces voice and style fidelity",
        "technical-validator": "verifies technical accuracy against authority sources",
        "format-converter": "converts deliverables to final output format",
        "reference-manager": "manages the reference/bibliography database",
        "output-compiler": "assembles components into the final deliverable package",
        "visual-designer": "creates and revises diagrams and figures",
    }
    for slug in archetypes:
        desc = descriptions.get(slug, "specialized domain agent")
        lines.append(f"- `@{slug}` — {desc}")
    return "\n".join(lines) if lines else "No domain agents selected."


def _format_workstream_expert_list(components: list[dict[str, Any]]) -> str:
    lines = []
    for comp in components:
        lines.append(f"- `@{comp['slug']}-expert` — {comp['name']}")
    return "\n".join(lines) if lines else "No workstream experts defined."


def _collect_manual_required(placeholder_map: dict[str, str]) -> list[dict[str, str]]:
    """Identify any placeholders that resolved to a {MANUAL:...} token."""
    manual = []
    for placeholder, value in placeholder_map.items():
        if "{MANUAL:" in value:
            manual.append({
                "placeholder": placeholder,
                "agent_file": "multiple",
                "context": f"The placeholder {{{placeholder}}} could not be auto-resolved.",
                "suggestion": "",
            })
    return manual


def _plan_output_files(
    archetypes: list[str],
    tool_agents: list[dict[str, Any]],
    components: list[dict[str, Any]],
    framework: str,
) -> list[dict[str, Any]]:
    """Plan the list of files the emit phase will generate."""
    files: list[dict[str, Any]] = []
    agents_dir = "universal/"
    domain_dir = "domain/"

    # Governance agents (always)
    for slug in ["orchestrator"] + GOVERNANCE_AGENTS:
        files.append({
            "path": f"{slug}.agent.md",
            "template": f"{agents_dir}{slug}.template.md",
            "type": "agent",
            "component_slug": None,
        })

    # Domain agents
    for slug in archetypes:
        files.append({
            "path": f"{slug}.agent.md",
            "template": f"{domain_dir}{slug}.template.md",
            "type": "agent",
            "component_slug": None,
        })

    # Tool-specific agents
    for ta in tool_agents:
        files.append({
            "path": f"{ta['slug']}.agent.md",
            "template": f"{domain_dir}tool-specific.template.md",
            "type": "agent",
            "component_slug": None,
        })

    # Workstream experts
    for comp in components:
        files.append({
            "path": f"{comp['slug']}-expert.agent.md",
            "template": "workstream-expert.template.md",
            "type": "agent",
            "component_slug": comp["slug"],
        })

    # copilot-instructions.md
    files.append({
        "path": "../copilot-instructions.md",
        "template": "copilot-instructions.template.md",
        "type": "instructions",
        "component_slug": None,
    })

    # Builder agent (framework-native)
    builder_templates = {
        "copilot-vscode": "builder/team-builder-copilot-vscode.template.md",
        "copilot-cli": "builder/team-builder-copilot-cli.template.md",
        "claude": "builder/team-builder-claude.template.md",
    }
    if framework in builder_templates:
        files.append({
            "path": "team-builder.agent.md",
            "template": builder_templates[framework],
            "type": "builder",
            "component_slug": None,
        })

    # SETUP-REQUIRED.md
    files.append({
        "path": "SETUP-REQUIRED.md",
        "template": "",
        "type": "setup-required",
        "component_slug": None,
    })

    return files


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _description_text(description: dict[str, Any]) -> str:
    """Return a single string for keyword analysis."""
    parts = [
        description.get("project_goal", ""),
        description.get("project_name", ""),
        description.get("output_format", ""),
        " ".join(description.get("deliverables", [])),
        " ".join(c.get("description", "") for c in description.get("components", [])),
        " ".join(t.get("name", "") for t in description.get("tools", [])),
    ]
    return " ".join(parts)


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9\s\-]", "", text)
    slug = re.sub(r"\s+", "-", slug.strip())
    return slug.lower()
