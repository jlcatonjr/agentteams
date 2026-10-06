"""Goose recipe YAML emission (hand-built, schema version 1.0.0).

Split out of ``agentteams.frameworks.goose`` (CH-07 module-size ceiling), the emit counterpart to
``goose_recipe_read.py`` / ``goose_recipe_validate.py``. Holds the leaf recipe-serialization helpers
and the three constants they use. It imports one direction only — from ``.base`` and
``agentteams.yaml_frontmatter`` — never from ``goose.py`` (which re-imports these names and re-exports
``_emit_recipe`` and ``_MCP_EXT_TIMEOUT`` so existing importers keep resolving).

Note: ``_RECIPE_VERSION`` ("1.0.0") is also hard-coded in ``goose_recipe_validate.py``'s
``_RECIPE_VERSION_RE`` (pre-existing duplication) — a version bump must touch both.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .base import FrameworkAdapter
from agentteams.yaml_frontmatter import parse_yaml_front_matter as _parse_yaml_front_matter
from agentteams.frameworks.goose_recipe_merge import SUB_RECIPES_MANAGED_COMMENT

#: Goose recipe schema version (hand-built emitter).
_RECIPE_VERSION = "1.0.0"

#: Default MCP-extension timeout (seconds) — used by ``_emit_recipe`` here and by the retained
#: ``_goose_extension_for`` in ``goose.py`` (which re-imports it), and re-exported for
#: ``goose_coordination.py``.
_MCP_EXT_TIMEOUT = 300

#: Scans ``key: value`` scalar lines out of VS Code YAML front matter.
_YAML_SCALAR_RE = re.compile(r'^([a-zA-Z][a-zA-Z0-9_-]*)\s*:\s*"?([^"\n]+)"?\s*$', re.MULTILINE)


def _extract_name_description(
    content: str,
    agent_slug: str,
    manifest: dict[str, Any],
) -> tuple[str, str]:
    """Pull (name, description) from VS Code YAML front matter, with fallbacks."""
    name = ""
    description = ""
    yaml_text, _ = _parse_yaml_front_matter(content)
    if yaml_text is not None:
        for key_match in _YAML_SCALAR_RE.finditer(yaml_text):
            key = key_match.group(1).strip()
            val = key_match.group(2).strip().strip("\"'")
            if key == "name" and not name:
                name = val
            elif key == "description" and not description:
                description = val
    if not name:
        project_name = manifest.get("project_name", "")
        agent_name = FrameworkAdapter._slug_to_name(agent_slug)
        name = f"{agent_name} — {project_name}" if project_name else agent_name
    return name, description


def _load_section(targets: list[dict[str, Any]]) -> str:
    """Render the depth-2 reference block (load instead of nested delegation)."""
    lines = [
        "## Delegation & references (Goose)",
        "",
        "Goose forbids nested delegation, so when you need another specialist's "
        "guidance, load that recipe into your own context with the `summon` "
        "`load` tool (do not try to spawn a sub-agent):",
        "",
    ]
    for h in targets:
        slug = h["agent"]
        label = h.get("label") or h.get("prompt") or slug
        lines.append(
            f'- **{label}** — call `load("{slug}")` to bring `{slug}`\'s '
            f"instructions into context, then act on them here."
        )
    return "\n".join(lines)


def _yaml_dq(value: str) -> str:
    """Return a single-line double-quoted YAML scalar."""
    s = (value or "").replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\r", " ").replace("\n", " ").strip()
    return f'"{s}"'


def _indent_block(body: str, indent: str = "  ") -> str:
    """Indent body for a YAML literal block scalar (blank lines stay empty)."""
    out: list[str] = []
    for line in body.split("\n"):
        out.append(indent + line if line.strip() else "")
    return "\n".join(out)


def _emit_recipe(
    *,
    title: str,
    description: str,
    instructions: str,
    extensions: list[str],
    sub_recipes: list[dict[str, str]] | None = None,
    prompt: str | None = None,
    parameters: list[dict[str, str]] | None = None,
    response: dict[str, Any] | None = None,
    retry: dict[str, Any] | None = None,
    mcp_extensions: list[dict[str, Any]] | None = None,
    available_tools: dict[str, list[str]] | None = None,
    mcp_notes: list[str] | None = None,
) -> str:
    """Serialize a Goose recipe to YAML (hand-built, schema version 1.0.0).

    W6: The optional `prompt` field, when provided, is emitted after `description`
    and enables non-interactive `goose run --recipe` execution in CI pipelines
    (the --recipe and --text flags are mutually exclusive in Goose CLI).

    Phase-4a: ``parameters`` (opt-in), when non-empty, is emitted as a `parameters:`
    block (Goose recipe runtime inputs). Each entry is a normalized dict with `key`,
    `input_type`, `requirement`, optional `default`, optional `description` — every
    scalar double-quoted so special chars cannot break the hand-built YAML. Callers
    that reference the keys via ``{{ key }}`` (e.g. the orchestrator prompt) keep the
    Goose params↔template coupling valid. Defaults to None → byte-identical baseline.

    Phase-4b: ``response`` (opt-in), when truthy, is a JSON Schema emitted as a
    `response:` block whose ``json_schema:`` value is single-line compact JSON (valid
    YAML, since YAML is a JSON superset) so goose validates the agent's final output.
    Defaults to None → byte-identical baseline.

    Phase-4c: ``retry`` (opt-in), when truthy, is a normalized dict emitted as a
    `retry:` block (bounded ``max_retries`` + ``timeout_seconds`` + shell ``checks``)
    so goose re-runs until the checks pass. Defaults to None → byte-identical baseline.

    ``mcp_extensions`` (opt-in) are operator-specified MCP servers rendered as
    ``stdio``/``streamable_http`` extensions after the builtin/platform ones; every
    scalar (incl. list items) is double-quoted so special chars cannot break the
    hand-built YAML. ``mcp_notes`` are operator-visible, Goose-ignored ``#`` comments
    for in-scope servers that were NOT wired (skipped for safety/launch reasons).
    Both default to empty, so a non-opted-in build is byte-identical to baseline.

    ``available_tools`` (``goose_tool_scoping: "grant"``) maps a builtin/platform extension name to
    its allowlist, emitted as that extension's ``available_tools:`` line; a stdio entry in
    ``mcp_extensions`` carries its own ``available_tools`` key. ``analyze`` is rendered as a platform
    extension. Defaults to None, so legacy recipes are byte-identical, with one deliberate exception: a
    recipe with no extensions at all is written ``extensions: []`` rather than a bare ``extensions:``,
    which Goose 1.37 reads as "load the user's configured extensions" (see the spike record).
    """
    lines: list[str] = [
        f'version: "{_RECIPE_VERSION}"',
        f"title: {_yaml_dq(title)}",
        f"description: {_yaml_dq(description or title)}",
    ]
    if prompt is not None:
        lines.append(f"prompt: {_yaml_dq(prompt)}")
    lines += [
        "instructions: |",
        _indent_block(instructions),
    ]
    for note in mcp_notes or []:
        # Column-0 comment terminates the instructions block scalar; Goose ignores it.
        lines.append(f"# agentteams MCP: {note}")
    if parameters:
        lines.append("parameters:")
        for p in parameters:
            lines.append(f"  - key: {_yaml_dq(p['key'])}")
            lines.append(f"    input_type: {_yaml_dq(p.get('input_type', 'string'))}")
            lines.append(f"    requirement: {_yaml_dq(p.get('requirement', 'optional'))}")
            if "default" in p:
                lines.append(f"    default: {_yaml_dq(p['default'])}")
            if p.get("description"):
                lines.append(f"    description: {_yaml_dq(p['description'])}")
    if response:
        # YAML is a JSON superset, so the arbitrary-depth JSON Schema is emitted as a
        # single-line compact flow mapping (json.dumps). Being mid-line, none of its
        # keys can column-0 match the _RECIPE_FORBIDDEN_* guards. sort_keys → stable.
        lines.append("response:")
        lines.append(
            f"  json_schema: {json.dumps(response, sort_keys=True, separators=(',', ':'))}"
        )
    if retry:
        # Hand-built (flat): ints unquoted, command/on_failure strings via _yaml_dq
        # (which collapses newlines + escapes quotes → no YAML-structure breakout).
        lines.append("retry:")
        lines.append(f"  max_retries: {int(retry['max_retries'])}")
        lines.append(f"  timeout_seconds: {int(retry['timeout_seconds'])}")
        if "on_failure_timeout_seconds" in retry:
            lines.append(f"  on_failure_timeout_seconds: {int(retry['on_failure_timeout_seconds'])}")
        lines.append("  checks:")
        for check in retry["checks"]:
            lines.append(f"    - type: {_yaml_dq(check.get('type', 'shell'))}")
            lines.append(f"      command: {_yaml_dq(check['command'])}")
        if retry.get("on_failure"):
            lines.append(f"  on_failure: {_yaml_dq(retry['on_failure'])}")
    if not extensions and not mcp_extensions:
        # A bare `extensions:` (null) or a missing key makes Goose 1.37 load the user's configured
        # extensions, usually the full developer (verified live). An agent granted nothing gets an
        # explicit empty list, which Goose honours as "no extensions".
        lines.append("extensions: []")
    else:
        lines.append("extensions:")
    allow = available_tools or {}

    def _allow_line(name: str) -> list[str]:
        # goose_tool_scoping "grant": an allowlist per extension (absent in legacy mode).
        return [f"    available_tools: [{', '.join(allow[name])}]"] if name in allow else []

    for ext in extensions:
        if ext == "developer":
            lines += [
                "  - type: builtin",
                "    name: developer",
                "    bundled: true",
                "    timeout: 300",
            ] + _allow_line(ext)
        elif ext in ("summon", "analyze"):
            lines += [
                "  - type: platform",
                f"    name: {ext}",
            ] + _allow_line(ext)
        else:
            # Generic builtin (Gap 1: scoped recipe_extensions may name other
            # bundled servers, e.g. memory). Rendered with the same bundled/timeout
            # shape as developer so goose treats it as a builtin extension.
            lines += [
                "  - type: builtin",
                f"    name: {_yaml_dq(ext)}",
                "    bundled: true",
                "    timeout: 300",
            ]
    for mx in mcp_extensions or []:
        lines.append(f"  - type: {mx['type']}")
        lines.append(f"    name: {_yaml_dq(mx['name'])}")
        if mx["type"] == "stdio":
            lines.append(f"    cmd: {_yaml_dq(mx['cmd'])}")
            if mx.get("args"):
                lines.append("    args:")
                lines += [f"      - {_yaml_dq(a)}" for a in mx["args"]]
        else:  # streamable_http
            lines.append(f"    uri: {_yaml_dq(mx['uri'])}")
        if mx.get("env_keys"):
            lines.append("    env_keys:")
            lines += [f"      - {_yaml_dq(k)}" for k in mx["env_keys"]]
        lines.append(f"    timeout: {int(mx.get('timeout', _MCP_EXT_TIMEOUT))}")
        if mx.get("available_tools"):
            lines.append(f"    available_tools: [{', '.join(mx['available_tools'])}]")
    if sub_recipes:
        # Column-0 managed-key comment (follow-up #15): the key is owned structurally on
        # --update --merge by goose_recipe_merge.reconcile_sub_recipes. Goose ignores it.
        lines.append(SUB_RECIPES_MANAGED_COMMENT)
        lines.append("sub_recipes:")
        for sr in sub_recipes:
            lines.append(f"  - name: {_yaml_dq(sr['name'])}")
            lines.append(f"    path: {_yaml_dq(sr['path'])}")
            if sr.get("description"):
                lines.append(f"    description: {_yaml_dq(sr['description'])}")
    return "\n".join(lines).rstrip() + "\n"
