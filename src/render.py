"""
render.py — Render agent files from templates by resolving placeholders.

Takes a team manifest (from analyze.py) and a template directory,
and produces a list of (output_path, rendered_content) pairs ready
for the emit phase.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Regex matching {PLACEHOLDER_NAME} tokens (auto-resolved)
_AUTO_PLACEHOLDER_RE = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")

#: Regex matching {MANUAL:PLACEHOLDER_NAME} tokens
_MANUAL_PLACEHOLDER_RE = re.compile(r"\{MANUAL:([A-Z][A-Z0-9_]*)\}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render_all(
    manifest: dict[str, Any],
    *,
    templates_dir: Path,
) -> list[tuple[str, str]]:
    """Render all output files described in the manifest.

    Args:
        manifest:      Team manifest from analyze.build_manifest().
        templates_dir: Root directory of the templates/ folder.

    Returns:
        List of (output_path_relative_to_agents_dir, rendered_content) tuples.
    """
    results: list[tuple[str, str]] = []

    for file_spec in manifest["output_files"]:
        output_path: str = file_spec["path"]
        template_path: str = file_spec.get("template", "")
        file_type: str = file_spec["type"]
        component_slug: str | None = file_spec.get("component_slug")

        if file_type == "setup-required":
            content = _render_setup_required(manifest)
        elif not template_path:
            continue
        else:
            full_template_path = templates_dir / template_path
            if not full_template_path.exists():
                # Skip missing templates (e.g., optional archetypes not in repo yet)
                continue
            template_text = full_template_path.read_text(encoding="utf-8")
            placeholder_map = _build_placeholder_map_for_file(
                manifest, component_slug=component_slug, file_spec=file_spec
            )
            content = resolve_placeholders(template_text, placeholder_map)

        results.append((output_path, content))

    return results


# ---------------------------------------------------------------------------
# Placeholder resolution
# ---------------------------------------------------------------------------

def resolve_placeholders(template_text: str, placeholder_map: dict[str, str]) -> str:
    """Resolve all {PLACEHOLDER} tokens in template_text using placeholder_map.

    Manual tokens ({MANUAL:NAME}) are left as-is if they have no mapping,
    so SETUP-REQUIRED.md can catalogue them.
    """
    def replace_auto(match: re.Match) -> str:
        key = match.group(1)
        return placeholder_map.get(key, match.group(0))  # leave unresolved tokens intact

    result = _AUTO_PLACEHOLDER_RE.sub(replace_auto, template_text)
    return result


def collect_unresolved_manual(rendered_text: str) -> list[str]:
    """Return a list of {MANUAL:...} tokens still present in rendered text."""
    return [m.group(0) for m in _MANUAL_PLACEHOLDER_RE.finditer(rendered_text)]


# ---------------------------------------------------------------------------
# Per-file placeholder map construction
# ---------------------------------------------------------------------------

def _build_placeholder_map_for_file(
    manifest: dict[str, Any],
    *,
    component_slug: str | None,
    file_spec: dict[str, Any],
) -> dict[str, str]:
    """Build the placeholder map for a single file, merging global and per-component values."""
    # Start with all auto-resolved global placeholders
    mapping = dict(manifest["auto_resolved_placeholders"])

    # Add component-specific overrides for workstream expert files
    if component_slug:
        component = _find_component(manifest, component_slug)
        if component:
            mapping.update(_component_placeholder_map(component, manifest))

    # Add tool-specific overrides for tool-specialist files
    if file_spec.get("template", "").endswith("tool-specific.template.md"):
        tool_slug = file_spec["path"].replace(".agent.md", "")
        tool_agent = _find_tool_agent(manifest, tool_slug)
        if tool_agent:
            mapping.update(_tool_placeholder_map(tool_agent))

    return mapping


def _find_component(manifest: dict[str, Any], slug: str) -> dict[str, Any] | None:
    for comp in manifest.get("components", []):
        if comp["slug"] == slug:
            return comp
    return None


def _find_tool_agent(manifest: dict[str, Any], slug: str) -> dict[str, Any] | None:
    for ta in manifest.get("tool_agents", []):
        if ta["slug"] == slug:
            return ta
    return None


def _component_placeholder_map(component: dict[str, Any], manifest: dict[str, Any]) -> dict[str, str]:
    """Build per-component placeholder values."""
    sections = component.get("sections", [])
    sources = component.get("sources", [])
    cross_refs = component.get("cross_refs", [])
    quality_criteria = component.get("quality_criteria", [])

    sections_formatted = (
        "\n".join(f"{i+1}. {s}" for i, s in enumerate(sections))
        if sections else "{MANUAL:COMPONENT_SECTIONS}"
    )
    sources_formatted = (
        "\n".join(f"- {s}" for s in sources)
        if sources else "{MANUAL:COMPONENT_SOURCES}"
    )
    cross_refs_formatted = (
        "\n".join(f"- `{r}`" for r in cross_refs)
        if cross_refs else "None specified."
    )
    quality_formatted = (
        "\n".join(f"- {c}" for c in quality_criteria)
        if quality_criteria else "{MANUAL:COMPONENT_QUALITY_CRITERIA}"
    )

    output_file = component.get("output_file")
    primary_output_dir = manifest["auto_resolved_placeholders"].get("PRIMARY_OUTPUT_DIR", "src/")
    if not output_file:
        output_file = f"{primary_output_dir}{component['slug']}/{component['name'].lower().replace(' ', '-')}"

    return {
        "COMPONENT_NUMBER": str(component.get("number", 1)),
        "COMPONENT_NAME": component["name"],
        "COMPONENT_SLUG": component["slug"],
        "COMPONENT_OUTPUT_FILE": output_file,
        "COMPONENT_SPEC": component.get("description") or "{MANUAL:COMPONENT_SPEC}",
        "COMPONENT_SECTIONS": sections_formatted,
        "COMPONENT_SOURCES": sources_formatted,
        "COMPONENT_CROSS_REFS": cross_refs_formatted,
        "COMPONENT_QUALITY_CRITERIA": quality_formatted,
    }


def _tool_placeholder_map(tool_agent: dict[str, Any]) -> dict[str, str]:
    """Build per-tool-agent placeholder values."""
    config_files = ", ".join(tool_agent.get("config_files", [])) or "{MANUAL:TOOL_CONFIG_FILES}"
    return {
        "TOOL_NAME": tool_agent["tool_name"],
        "TOOL_VERSION": tool_agent.get("tool_version", ""),
        "TOOL_CONFIG_FILES": config_files,
        "TOOL_INVOCATION_COMMAND": tool_agent.get("invocation_command", "{MANUAL:TOOL_INVOCATION_COMMAND}"),
        "TOOL_INVOCATION_TARGET": tool_agent.get("invocation_target", "{MANUAL:TOOL_INVOCATION_TARGET}"),
        "DIAGRAM_TOOLS": tool_agent["tool_name"],
        "DIAGRAM_EXTENSION": _guess_diagram_extension(tool_agent["tool_name"]),
    }


def _guess_diagram_extension(tool_name: str) -> str:
    mapping = {
        "graphviz": "dot",
        "mermaid": "mmd",
        "plantuml": "puml",
        "draw.io": "drawio",
    }
    return mapping.get(tool_name.lower(), "txt")


# ---------------------------------------------------------------------------
# SETUP-REQUIRED.md rendering
# ---------------------------------------------------------------------------

def _render_setup_required(manifest: dict[str, Any]) -> str:
    manual_items = manifest.get("manual_required_placeholders", [])

    if not manual_items:
        return (
            "# SETUP-REQUIRED.md\n\n"
            "All placeholders were automatically resolved. No manual setup required.\n\n"
            f"Agent team successfully generated for **{manifest['project_name']}**.\n"
        )

    lines = [
        "# SETUP-REQUIRED.md",
        "",
        f"The following **{len(manual_items)} placeholder(s)** could not be automatically resolved",
        f"for project **{manifest['project_name']}** and require manual attention.",
        "",
        "---",
        "",
    ]

    for i, item in enumerate(manual_items, start=1):
        lines += [
            f"## {i}. `{{{item['placeholder']}}}`",
            "",
            f"**Found in:** `{item['agent_file']}`",
            f"**Context:** {item['context']}",
        ]
        if item.get("suggestion"):
            lines.append(f"**Suggested value:** {item['suggestion']}")
        lines += ["", "**Action required:** Search for `{MANUAL:" + item["placeholder"] + "}` across all generated", "agent files and replace with the correct value.", "", "---", ""]

    lines += [
        "",
        "Once all items above are resolved, invoke `@conflict-auditor` to verify consistency.",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_cross_refs(rendered_files: list[tuple[str, str]]) -> list[str]:
    """Return warnings for agent references that don't resolve to a generated file."""
    all_paths = {path for path, _ in rendered_files}
    # Handle both 'slug.agent.md' and 'slug.md' filenames
    all_slugs: set[str] = set()
    for p in all_paths:
        stem = Path(p).stem          # 'slug.agent' for .agent.md files
        if stem.endswith(".agent"):
            stem = stem[:-6]         # strip trailing '.agent'
        all_slugs.add(stem)

    warnings: list[str] = []
    agent_ref_re = re.compile(r"`@([a-z0-9\-]+)`")

    for output_path, content in rendered_files:
        for match in agent_ref_re.finditer(content):
            slug = match.group(1)
            if slug not in all_slugs and slug != "orchestrator":
                warnings.append(
                    f"{output_path}: references `@{slug}` but no corresponding agent file was generated"
                )

    return warnings
