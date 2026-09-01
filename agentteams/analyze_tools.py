"""Tool importance classification + tool/reference doc detection (CH-07 carve out of ``analyze.py``).

The cohesive "which tools earn a specialist agent / an operational tool doc / a reference-DB entry"
concern, carved from ``analyze.py`` to keep it under the CH-07 module-size ceiling. ``analyze.py``
re-imports every public name here so ``from agentteams.analyze import classify_tool_importance``
(and the ``analyze._SPECIALIST_CATEGORIES`` the test suite reads) keep working.

Pure and stdlib-only; depends only on leaf helpers (``_utils._slugify_tool_name``,
``tool_metadata_catalog``), so no import cycle with ``analyze.py``.
"""

from __future__ import annotations

from typing import Any

from agentteams import tool_metadata_catalog
from agentteams._utils import _slugify_tool_name


# ---------------------------------------------------------------------------
# Tool importance classification
# ---------------------------------------------------------------------------

#: Categories that automatically qualify for a specialist agent
_SPECIALIST_CATEGORIES: set[str] = {"database", "cli", "build-system"}

#: Tool names (lowercased) that always qualify as specialist-tier
_SPECIALIST_TOOLS: set[str] = {
    "postgresql", "postgres", "mysql", "mariadb", "mongodb", "redis",
    "elasticsearch", "cassandra", "sqlite",
    "docker", "docker compose", "kubernetes", "k8s", "terraform",
    "ansible", "pulumi",
    "github actions", "jenkins", "circleci", "gitlab ci",
    "nginx", "apache", "caddy",
}

#: Categories that qualify for a reference file (lightweight docs)
_REFERENCE_CATEGORIES: set[str] = {"framework", "library"}

#: Tool names (lowercased) that always qualify as reference-tier
#:
#: Test frameworks were already here because they live in a project's dev dependencies and
#: still matter. The compiler and bundler entries exist for the same reason and were added
#: 2026-07-31 alongside the `package.json` change that stopped categorising every
#: `devDependencies` entry as `library`: without them, that change would silently demote a
#: TypeScript project's compiler and its bundler from reference tier to passive. They are
#: listed as *reference*, not specialist, so they keep exactly the tier they had before —
#: this preserves behaviour rather than escalating it.
_REFERENCE_TOOLS: set[str] = {
    "fastapi", "django", "flask", "express", "react", "vue", "angular",
    "sqlalchemy", "pandas", "numpy", "scipy", "matplotlib",
    "spring", "rails", "laravel", "nextjs", "next.js",
    "pytest", "jest", "mocha", "junit",
    "graphql", "grpc", "protobuf",
    "typescript", "webpack", "vite", "rollup", "esbuild",
}

def classify_tool_importance(tool: dict[str, Any]) -> str:
    """Classify a tool into an importance tier.

    Args:
        tool: Tool dict with at least 'name', optionally 'category' and
              'needs_specialist_agent'.

    Returns:
        One of 'specialist', 'reference', or 'passive'.
    """
    # An explicit `needs_specialist_agent: true` forces the specialist tier.
    # `false` (and absence) fall back to the category/name heuristics below — a
    # `false` value does not force a non-specialist tier.
    if tool.get("needs_specialist_agent") is True:
        return "specialist"

    return _classify_without_override(tool)


def _classify_without_override(tool: dict[str, Any]) -> str:
    """Classify a tool by its category and name when no explicit override."""
    name_lower = (tool.get("name") or "").lower()
    category = tool.get("category", "other")

    # Specialist tier: databases, infra, CI, build-systems
    if category in _SPECIALIST_CATEGORIES or name_lower in _SPECIALIST_TOOLS:
        return "specialist"

    # Reference tier: frameworks, libraries
    if category in _REFERENCE_CATEGORIES or name_lower in _REFERENCE_TOOLS:
        return "reference"

    return "passive"


def _merge_known_tool_metadata(tool: dict[str, Any]) -> dict[str, Any]:
    """Overlay built-in tool metadata when the brief omits it."""
    merged = dict(tool)
    defaults = tool_metadata_catalog.get_tool_metadata(tool.get("name", ""))
    for field in ("docs_url", "api_surface", "common_patterns"):
        if not merged.get(field) and defaults.get(field):
            merged[field] = defaults[field]
    return merged


# ---------------------------------------------------------------------------
# Tool doc detection
#
# NOTE: tools are never generated as agents. `detect_tool_agents` returns specs
# for *operational tool documents* (Claude skills / Copilot reference docs).
# The historical name and the `tool_agents` manifest key are retained for
# backward compatibility (closed manifest schema, external consumers); the
# OUTPUT is a doc, not an `.agent.md`. See `_plan_output_files`.
# ---------------------------------------------------------------------------

def detect_tool_agents(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return operational tool-doc specs for specialist-tier tools.

    Specialist-tier tools (databases, CLIs, build systems, infra) become
    operational documents — Claude skills or Copilot reference docs — never
    agents. The spec slug stays ``tool-<name>`` to identify the tool.

    Args:
        tools: List of tool dicts from the project description.

    Returns:
        List of tool-doc spec dicts for specialist-tier tools.
    """
    agents = []
    for tool in tools:
        tool = _merge_known_tool_metadata(tool)
        tier = classify_tool_importance(tool)
        if tier != "specialist":
            continue
        # A category-classified tool may lack a name; .get is used in the tier
        # check above, so read it tolerantly here too rather than KeyError.
        name = (tool.get("name") or "").strip()
        if not name:
            continue
        slug = f"tool-{_slugify_tool_name(name)}"
        category = tool.get("category", "other")
        agents.append({
            "slug": slug,
            "tool_name": name,
            "tool_version": tool.get("version", ""),
            "tool_category": category,
            "config_files": tool.get("config_files", []),
            "invocation_command": "",
            "invocation_target": "",
            "docs_url": tool.get("docs_url", ""),
            "api_surface": tool.get("api_surface", ""),
            "common_patterns": tool.get("common_patterns", ""),
        })
    return agents


def detect_reference_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return tool specs for tools classified as reference-tier.

    Args:
        tools: List of tool dicts from the project description.

    Returns:
        List of tool dicts for reference-tier tools.
    """
    refs = []
    for tool in tools:
        tool = _merge_known_tool_metadata(tool)
        tier = classify_tool_importance(tool)
        if tier != "reference":
            continue
        name = (tool.get("name") or "").strip()
        if not name:
            continue
        refs.append({
            "slug": f"ref-{_slugify_tool_name(name)}",
            "tool_name": name,
            "tool_version": tool.get("version", ""),
            "tool_category": tool.get("category", "other"),
            "config_files": tool.get("config_files", []),
            "docs_url": tool.get("docs_url", ""),
            "api_surface": tool.get("api_surface", ""),
            "common_patterns": tool.get("common_patterns", ""),
        })
    return refs
