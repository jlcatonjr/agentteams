"""Per-recipe Goose extensions beyond the grant map, and the orchestrator's delegation table.

Carved out of ``agentteams.frameworks.goose`` (CH-07 module-size ceiling); ``goose.py`` re-imports every name, so
``from agentteams.frameworks.goose import _GOOSE_MCP_TOKEN`` (and the others) keep resolving. It holds:

* the operator MCP wiring (``goose:mcp``): :func:`_goose_wirable`, :func:`_goose_extension_for`,
  :func:`_mcp_recipe_extensions`, moved verbatim;
* :func:`recipe_extras`, the one place that decides which operator MCP servers, ``agentteams_runner`` and
  coordination server a recipe carries. ``GooseAdapter.render_agent_file`` emits them, and the delegation
  table reports them as withheld, so the two can't drift apart;
* :func:`delegation_rows`: the grant-mode orchestrator's per-target ``delegate`` arguments. A Goose delegate runs
  with the delegating session's extensions, not its recipe's (biographies pilot 1, G1/G2/G9;
  ``references/goose-tool-scoping-spike.md``), so the orchestrator is told what to pass on each call.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agentteams import write_policy as _write_policy
from agentteams.frameworks.goose_coordination import (
    COORDINATION_AGENT_SLUGS,
    coordination_enabled,
    coordination_extension,
)
from agentteams.frameworks.goose_recipe_emit import _MCP_EXT_TIMEOUT
from agentteams.frameworks.goose_tool_scoping import (
    declared_from_marker,
    declared_tools,
    delegate_extensions,
    filter_operator_mcp,
    grant_mode,
)

#: MCP-extension wiring is opt-in via this host-feature token.
_GOOSE_MCP_TOKEN = "goose:mcp"


def _tool_name(slug: str) -> str:
    """Goose generates a tool name per sub-recipe; keep it identifier-safe."""
    return re.sub(r"[^a-z0-9_]", "_", slug.replace("-", "_").lower())


# ---------------------------------------------------------------------------
# MCP-extension wiring (opt-in; report §5.4/§6 + goose-integration.plan §6.5)
# ---------------------------------------------------------------------------

def _goose_wirable(server: dict[str, Any]) -> bool:
    """True iff a specified MCP server is safe to AUTO-WIRE as a runnable Goose
    extension. Wiring makes the server runnable (an activation step), so the bar is
    report §5.4's named auto-activation candidate — STRICTER than the inert Claude
    emitter's ``_requires_authorization`` (which also allows ``write``):

      first-party  AND  every tool side_effects == "read"  AND  no review required.

    Anything else (third-party, any write/destructive tool, security_review.required)
    is skipped and surfaced for explicit operator handling — never silently activated.
    """
    if server.get("trust_tier") != "first-party":
        return False
    if (server.get("security_review") or {}).get("required") is True:
        return False
    tools = server.get("tools")
    if not isinstance(tools, list) or not tools:
        return False
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("side_effects") != "read":
            return False
    return True


def _goose_extension_for(server: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Map one mcp-server.schema.json entry to a Goose recipe extension dict.

    Returns ``(extension, None)`` when runnable, or ``(None, reason)`` when the
    server is in-scope but cannot be faithfully/ safely wired (surfaced as a recipe
    comment, never silently dropped). Credentials are referenced by name via
    ``env_keys`` — never inlined.
    """
    if not _goose_wirable(server):
        return None, "needs operator authorization (not first-party read-only)"

    auth = server.get("auth") or {}
    mechanism = auth.get("mechanism", "none")
    env_keys: list[str] = []
    if mechanism == "env":
        ref = auth.get("credential_ref")
        if ref:
            env_keys = [str(ref)]
    elif mechanism in ("secret-store", "oauth"):
        return None, f"credential mechanism '{mechanism}' not expressible as Goose env_keys"
    # mechanism == "none" -> no env_keys

    name = _tool_name(str(server.get("server_id", "")))
    transport = server.get("transport")
    if transport == "stdio":
        command = server.get("command")
        if not command:
            return None, "stdio server has no 'command' to launch"
        return {
            "type": "stdio",
            "name": name,
            "cmd": str(command),
            "args": [str(a) for a in (server.get("args") or [])],
            "env_keys": env_keys,
            "timeout": _MCP_EXT_TIMEOUT,
        }, None
    if transport == "http":
        uri = auth.get("url")
        if not uri:
            return None, "http server has no auth.url (uri) endpoint"
        return {
            "type": "streamable_http",
            "name": name,
            "uri": str(uri),
            "env_keys": env_keys,
            "timeout": _MCP_EXT_TIMEOUT,
        }, None
    return None, f"unknown transport {transport!r}"


def _mcp_recipe_extensions(
    manifest: dict[str, Any], agent_slug: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (extensions, skip_notes) for the MCP servers scoped to ``agent_slug``.

    OPT-IN: returns ``([], [])`` unless the ``goose:mcp`` host-feature token is
    active, so a default Goose build is byte-identical to the developer-only
    baseline. Least-privilege: only servers whose ``scope`` lists this agent.
    """
    if _GOOSE_MCP_TOKEN not in (manifest.get("host_features") or []):
        return [], []
    extensions: list[dict[str, Any]] = []
    notes: list[str] = []
    for server in manifest.get("mcp_servers") or []:
        if not isinstance(server, dict):
            continue
        if agent_slug not in (server.get("scope") or []):
            continue
        ext, reason = _goose_extension_for(server)
        if ext is not None:
            extensions.append(ext)
        else:
            notes.append(f"{server.get('server_id', '<unknown>')} not wired ({reason})")
    return extensions, notes


def recipe_extras(manifest: dict[str, Any], agent_slug: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Return the non-grant extensions one agent's recipe carries, and its recipe-comment notes.

    In emit order: operator MCP servers (opt-in ``goose:mcp``), then ``agentteams_runner`` (R6), then the
    coordination server (Phase 2). Under ``write_policy: "orchestrator-only"`` a non-orchestrator recipe carries no
    operator MCP server (it can't be classified as read-only) and no coordination server (it writes request/log
    files). In grant mode an operator server whose name clashes with a managed extension is dropped.

    Args:
        manifest: The team manifest.
        agent_slug: The agent's slug.

    Returns:
        ``(extensions, notes)``: the extension mappings (after the grant-map ones) and the Goose-ignored notes.

    Raises:
        ValueError: From ``runner_mcp.interpreter`` when an agent granted ``agentteams_runner`` has no system python3.
    """
    mcp_exts, mcp_notes = _mcp_recipe_extensions(manifest, agent_slug)
    restricted = _write_policy.enabled(manifest) and agent_slug not in _write_policy.ORCHESTRATOR_SLUGS
    if restricted and mcp_exts:
        mcp_notes = list(mcp_notes) + ["write_policy orchestrator-only: operator MCP servers withheld from "
                                       "this non-orchestrator agent"]
        mcp_exts = []
    if grant_mode(manifest):
        mcp_exts, clash_notes = filter_operator_mcp(list(mcp_exts))
        mcp_notes = list(mcp_notes) + clash_notes
    extras = list(mcp_exts)
    grant = (manifest.get("mcp_grants") or {}).get(agent_slug) if restricted else None
    if isinstance(grant, dict) and grant.get("tools"):
        # R6: a granted agent writes and executes only through the installed agentteams_runner server.
        from agentteams import runner_mcp

        extras.append(runner_mcp.goose_extension(agent_slug, grant["tools"], "staged", runner_mcp.interpreter()))
    if coordination_enabled(manifest) and agent_slug in COORDINATION_AGENT_SLUGS and not restricted:
        extras.append(coordination_extension())
        mcp_notes = list(mcp_notes) + [
            "agentteams_coordination (stdio): file-based cross-repo coordination tools "
            "(read registry, file a Coordination Request, append the log, request clearance). "
            "Records/files only — never grants or executes."
        ]
    return extras, mcp_notes


def member_declared_tools(manifest: dict[str, Any], slug: str, templates_dir: Path) -> frozenset[str] | None:
    """Return a team member's declared tools as its delegation table row should see them.

    Read from the template this run renders for it (narrowed under the write policy, as the render pipeline
    does), then intersected with the ``# agentteams-declared-tools:`` marker of its recipe already on disk, if
    any: an operator's hand-narrowed recipe is never widened by the table. The grant map is monotone, so the
    intersection's grant is within both.

    Args:
        manifest: The team manifest.
        slug: The member's slug.
        templates_dir: The template library root; a template path outside it is ignored.

    Returns:
        The tools, or ``None`` when no template for it is rendered this run or can be read (the caller then
        gives that member a read-only delegate).

    Raises:
        ValueError: From ``write_policy.narrow_tools`` on a malformed template (its own render fails the same way).
    """
    root = templates_dir.resolve()
    tools: frozenset[str] | None = None
    for f in manifest.get("output_files") or []:
        if tools is not None or Path(f.get("path", "")).name != f"{slug}.agent.md":
            continue
        for tpl in (f.get("template"), f.get("fallback_template")):
            path = (root / tpl).resolve() if tpl else None
            if path is None or not path.is_relative_to(root):
                continue
            try:
                text: str | None = path.read_text(encoding="utf-8")
            except OSError:
                text = None  # unreadable: try the fallback template, else no tools known (read-only)
            if text is None:
                continue
            if _write_policy.enabled(manifest) and slug not in _write_policy.ORCHESTRATOR_SLUGS:
                text = _write_policy.narrow_tools(text)
            tools = declared_tools(text)
            break
    project = manifest.get("existing_project_path")
    if tools is not None and project:
        try:
            on_disk = declared_from_marker(
                (Path(project) / ".goose" / "recipes" / f"{slug}.yaml").read_text(encoding="utf-8"))
        except OSError:
            on_disk = None
        if on_disk is not None:
            tools = tools & on_disk
    return tools


def delegation_rows(manifest: dict[str, Any], slugs: list[str], templates_dir: Path,
                    ) -> list[tuple[str, str, list[str], list[str]]]:
    """Return the orchestrator's delegation table: one row per ``sub_recipes`` target.

    Args:
        manifest: The team manifest.
        slugs: The ``sub_recipes`` targets, in order.
        templates_dir: The template library root.

    Returns:
        ``(source, slug, extensions, withheld)`` per target, for ``goose_recipe_emit.delegation_section``.

    Raises:
        ValueError: As :func:`member_declared_tools` and :func:`recipe_extras`.
    """
    rows = []
    for slug in slugs:
        extra = tuple(str(e["name"]) for e in recipe_extras(manifest, slug)[0])
        names, withheld = delegate_extensions(member_declared_tools(manifest, slug, templates_dir), extra)
        rows.append((_tool_name(slug), slug, names, withheld))
    return rows
