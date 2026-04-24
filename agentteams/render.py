"""
render.py — Render agent files from templates by resolving placeholders.

Takes a team manifest (from analyze.py) and a template directory,
and produces a list of (output_path, rendered_content) pairs ready
for the emit phase.
"""

from __future__ import annotations

import hashlib
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
            # Fall back to generic template if category-specific does not exist
            if not full_template_path.exists() and file_spec.get("fallback_template"):
                template_path = file_spec["fallback_template"]
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


def compute_template_hashes(
    manifest: dict[str, Any],
    *,
    templates_dir: Path,
) -> dict[str, str]:
    """Compute SHA-256 hashes of all templates used for a manifest.

    Args:
        manifest:      Team manifest from analyze.build_manifest().
        templates_dir: Root directory of the templates/ folder.

    Returns:
        Dict mapping template relative path to hex digest (first 16 chars).
    """
    hashes: dict[str, str] = {}
    for file_spec in manifest["output_files"]:
        template_path: str = file_spec.get("template", "")
        if not template_path:
            continue
        full_path = templates_dir / template_path
        if not full_path.exists() and file_spec.get("fallback_template"):
            template_path = file_spec["fallback_template"]
            full_path = templates_dir / template_path
        if full_path.exists():
            digest = hashlib.sha256(full_path.read_bytes()).hexdigest()[:16]
            hashes[template_path] = digest
    return hashes


# ---------------------------------------------------------------------------
# Placeholder resolution
# ---------------------------------------------------------------------------

def resolve_placeholders(template_text: str, placeholder_map: dict[str, str]) -> str:
    """Resolve all {PLACEHOLDER} tokens in template_text using placeholder_map.

    Manual tokens ({MANUAL:NAME}) are left as-is if they have no mapping,
    so SETUP-REQUIRED.md can catalogue them.
    """
    yaml_ranges = _yaml_front_matter_ranges(template_text)

    def replace_auto(match: re.Match) -> str:
        key = match.group(1)
        value = placeholder_map.get(key, match.group(0))
        if key in placeholder_map and _index_in_ranges(match.start(), yaml_ranges):
            if _inside_yaml_quoted_string(template_text, match.start()):
                # Already inside "...": only escape inner backslashes and quotes
                return value.replace("\\", "\\\\").replace('"', '\\"')
            return _escape_yaml_scalar(value)
        return value  # leave unresolved tokens intact

    result = _AUTO_PLACEHOLDER_RE.sub(replace_auto, template_text)
    return result


def _yaml_front_matter_ranges(text: str) -> list[tuple[int, int]]:
    """Return [start, end) index ranges for leading YAML front matter blocks."""
    if not text.startswith("---"):
        return []
    lines = text.splitlines(keepends=True)
    if not lines:
        return []

    # Require the first line to be a YAML fence and locate the closing fence.
    if lines[0].strip() != "---":
        return []

    offset = len(lines[0])
    for line in lines[1:]:
        if line.strip() == "---":
            end = offset
            return [(len(lines[0]), end)]
        offset += len(line)
    return []


def _index_in_ranges(index: int, ranges: list[tuple[int, int]]) -> bool:
    for start, end in ranges:
        if start <= index < end:
            return True
    return False


def _inside_yaml_quoted_string(text: str, pos: int) -> bool:
    """Return True if position *pos* in *text* is inside a YAML double-quoted string.

    Counts unescaped ``"`` characters between the start of the current line and
    *pos*. An odd count means we're inside an open ``"..."`` string.
    """
    line_start = text.rfind("\n", 0, pos) + 1
    before = text[line_start:pos]
    # Count unescaped double-quote characters
    count = 0
    i = 0
    while i < len(before):
        if before[i] == "\\" and i + 1 < len(before):
            i += 2  # skip escaped character
            continue
        if before[i] == '"':
            count += 1
        i += 1
    return count % 2 == 1


def _escape_yaml_scalar(value: str) -> str:
    """Return a YAML-safe scalar using minimal quoting for risky values."""
    if not isinstance(value, str):
        return str(value)

    if _looks_like_yaml_fragment(value):
        return value

    if value == "":
        return '""'

    needs_quotes = (
        value.strip() != value
        or "\n" in value
        or value[:1] in {"-", "?", ":"}
        or any(ch in value for ch in [":", "#", "{", "}", "[", "]", ",", "&", "*", "!", "|"])
        or value.lower() in {"true", "false", "null", "~", "yes", "no", "on", "off"}
    )
    if not needs_quotes:
        return value

    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _looks_like_yaml_fragment(value: str) -> bool:
    """Return True if value appears to be preformatted YAML list/map content."""
    if "\n" not in value:
        return False
    lines = [ln for ln in value.splitlines() if ln.strip()]
    if not lines:
        return False
    return all(ln.lstrip().startswith("-") for ln in lines)


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
    if file_spec.get("template", "").endswith("tool-specific.template.md") or \
       "tool-" in file_spec.get("template", "").split("/")[-1]:
        tool_slug = file_spec["path"].replace(".agent.md", "")
        tool_agent = _find_tool_agent(manifest, tool_slug)
        if tool_agent:
            mapping.update(_tool_placeholder_map(tool_agent))

    # Add tool reference overrides for reference files
    if file_spec.get("type") == "reference":
        ref_slug = file_spec["path"].replace("references/", "").replace("-reference.md", "")
        ref_tool = _find_reference_tool(manifest, ref_slug)
        if ref_tool:
            mapping.update(_reference_tool_placeholder_map(ref_tool))

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


def _find_reference_tool(manifest: dict[str, Any], slug: str) -> dict[str, Any] | None:
    for rt in manifest.get("reference_tools", []):
        if rt["slug"] == slug:
            return rt
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

    # Build tool references for this component
    comp_tools = component.get("tools", [])
    if comp_tools:
        # Map tool names to their agent/reference slugs from the manifest
        tool_lines = []
        tool_agent_names = {ta["tool_name"]: ta["slug"] for ta in manifest.get("tool_agents", [])}
        ref_tool_names = {rt["tool_name"]: rt["slug"] for rt in manifest.get("reference_tools", [])}
        for t in comp_tools:
            if t in tool_agent_names:
                tool_lines.append(f"- `@{tool_agent_names[t]}` (specialist agent)")
            elif t in ref_tool_names:
                tool_lines.append(f"- `references/{ref_tool_names[t]}-reference.md`")
            else:
                tool_lines.append(f"- {t}")
        tools_formatted = "\n".join(tool_lines)
    else:
        tools_formatted = "No tool-specific dependencies."

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
        "COMPONENT_TOOLS": tools_formatted,
    }


def _tool_placeholder_map(tool_agent: dict[str, Any]) -> dict[str, str]:
    """Build per-tool-agent placeholder values."""
    config_files = ", ".join(tool_agent.get("config_files", [])) or "N/A"
    docs_url = tool_agent.get("docs_url", "") or "{MANUAL:TOOL_DOCS_URL}"
    api_surface = tool_agent.get("api_surface", "") or "{MANUAL:TOOL_API_SURFACE}"
    common_patterns = tool_agent.get("common_patterns", "") or "{MANUAL:TOOL_COMMON_PATTERNS}"
    return {
        "TOOL_NAME": tool_agent["tool_name"],
        "TOOL_VERSION": tool_agent.get("tool_version", ""),
        "TOOL_CONFIG_FILES": config_files,
        "TOOL_DOCS_URL": docs_url,
        "TOOL_API_SURFACE": api_surface,
        "TOOL_COMMON_PATTERNS": common_patterns,
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


def _reference_tool_placeholder_map(ref_tool: dict[str, Any]) -> dict[str, str]:
    """Build per-reference-tool placeholder values."""
    config_files = ", ".join(ref_tool.get("config_files", [])) or "N/A"
    docs_url = ref_tool.get("docs_url", "") or "{MANUAL:TOOL_DOCS_URL}"
    api_surface = ref_tool.get("api_surface", "") or "{MANUAL:TOOL_API_SURFACE}"
    common_patterns = ref_tool.get("common_patterns", "") or "{MANUAL:TOOL_COMMON_PATTERNS}"
    return {
        "TOOL_NAME": ref_tool["tool_name"],
        "TOOL_VERSION": ref_tool.get("tool_version", ""),
        "TOOL_CATEGORY": ref_tool.get("tool_category", "library"),
        "TOOL_CONFIG_FILES": config_files,
        "TOOL_DOCS_URL": docs_url,
        "TOOL_API_SURFACE": api_surface,
        "TOOL_COMMON_PATTERNS": common_patterns,
    }


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
    """Return warnings for agent references that don't resolve to a generated file.

    Skips:
    - References inside ``*(If `@slug` in team)*`` conditional markers (these are
      intentionally guarded and valid even when the agent is absent).
    - References inside fenced code blocks (output format examples, etc.).
    - Lines that describe routing recommendations rather than hard invocations.
    - Duplicate (file, slug) pairs — one warning per file per missing slug is enough.
    """
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
    # Lines whose content qualifies a reference as conditional / optional.
    # Patterns handled:
    #   *(If `@slug` in team)* ...  — guarded workflow step
    #   | `@slug` | ...             — routing table row
    #   ... `@slug` if in team      — inline "if in team" suffix (any case)
    #   route to `@slug`            — routing recommendation text
    #   `@a` / `@b`                 — slash-separated agent list (routing guidance)
    conditional_re = re.compile(
        r"\*\(If\b"                        # *(If ...) guarded steps
        r"|\bIf `@[a-z0-9\-]+` in team\b"  # explicit "If @slug in team"
        r"|\| `@"                           # routing-table pipe rows
        r"|if in team"                      # inline "if in team" suffix
        r"|route to"                        # "route to @slug" guidance lines
        r"|`@[a-z0-9\-]+` / `@",           # slash-separated agent lists
        re.IGNORECASE,
    )

    for output_path, content in rendered_files:
        seen: set[str] = set()  # deduplicate per (file, slug)
        in_code_block = False
        for line in content.splitlines():
            # Track fenced code block state — refs inside code blocks are examples, not invocations
            if line.strip().startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue
            # Skip routing table rows and conditional workflow markers
            if conditional_re.search(line):
                continue
            for match in agent_ref_re.finditer(line):
                slug = match.group(1)
                if slug not in all_slugs and slug != "orchestrator" and slug not in seen:
                    seen.add(slug)
                    warnings.append(
                        f"{output_path}: references `@{slug}` but no corresponding agent file was generated"
                    )

    return warnings
