"""
grok_config_emit.py — the agentteams-managed block in a project's ``.grok/config.toml`` (Grok Build).

A project ``.grok/config.toml`` may set only ``[mcp_servers]``, ``[plugins]`` and ``[permission]`` (Grok Build
settings reference). This module owns one managed block holding up to two of them:

* ``[mcp_servers.<id>]`` (host feature ``grok:mcp``): operator-declared ``mcp_servers[]``, wired only when they
  meet the same first-party / read-only / no-review bar as Codex and Goose (a live, auto-loaded config). Grok
  expands ``${VAR}`` in ``url``, ``command``, ``args``, ``env`` and ``headers`` at load time (07-mcp-servers:335),
  so ``${…}`` appears ONLY in ``env`` values, built from a validated variable name; ``command``/``args``/``url``
  containing ``${`` are refused, and http credentials go through ``bearer_token_env_var`` only (never inline).
* ``[permission]`` (host feature ``grok:sandbox``): ``deny`` Edit/Read rules for every control-plane path (the
  canonical :func:`_sandbox_emit.permission_deny_rules` set, rewritten as unrooted ``**/`` globs because Grok
  joins a relative pattern with the session's working directory and treats a leading ``~/`` as literal text),
  plus ``ask`` for every wired MCP server's tools (the counterpart of Codex's per-server prompt). These rules
  cover Grok's Read/Edit/Write tools, not shell writes, and Grok loads them only after folder trust, so the
  control is DECLARED. An operator's own ``[permission]`` table is never touched: TOML allows one, so the block
  then omits it and says so.

Writes go through :func:`codex_mcp_emit.splice_managed_toml` (content-preservation check: every other key must
survive, or nothing is written) and refuse a symlinked ``.grok`` or config file. @security step-12b
(tmp/by-week/2026-W42/grok-integration.plan.md §12.1).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentteams.codex_mcp_emit import _strip_block, _strip_unmanaged_mcp_tables, mcp_wirable, splice_managed_toml
from agentteams.frameworks._agents_md_ownership import symlink_on_path
from agentteams.mcp_emit import _inert_problems
from agentteams.toml_write import render_tables

GROK_MCP_TOKEN = "grok:mcp"
GROK_SANDBOX_TOKEN = "grok:sandbox"
GROK_CONFIG_REL = ".grok/config.toml"

_MANAGED_HEADER = (
    "# --- BEGIN agentteams-managed grok config (do not hand-edit this block; "
    "re-running agentteams will replace it) ---\n"
)
_MANAGED_FOOTER = "# --- END agentteams-managed grok config ---\n"

_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
#: Control-plane paths Grok's own config adds to the canonical Claude-block set.
_EXTRA_DENY = ("Edit(**/.grok/hooks/**)", "Edit(**/.github/hooks/**)")


@dataclass
class GrokConfigResult:
    """Outcome of :func:`emit_grok_config`."""

    written: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    wired: list[str] = field(default_factory=list)
    not_wired: dict[str, str] = field(default_factory=dict)
    dropped_unmanaged: list[str] = field(default_factory=list)
    permission_written: bool = False


def grok_mcp_enabled(features: list[str]) -> bool:
    """Return True iff the ``grok:mcp`` host-feature token is active.

    Args:
        features: Active host-feature tokens.

    Returns:
        Whether Grok MCP emission is on.

    Raises:
        Nothing.
    """
    return GROK_MCP_TOKEN in set(features or [])


def grok_sandbox_enabled(features: list[str]) -> bool:
    """Return True iff the ``grok:sandbox`` host-feature token is active (confined/exclusive profile).

    Args:
        features: Active host-feature tokens.

    Returns:
        Whether Grok confinement artifacts are requested.

    Raises:
        Nothing.
    """
    return GROK_SANDBOX_TOKEN in set(features or [])


def grok_mcp_entry(server: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Map one mcp-server.schema.json entry to a Grok ``[mcp_servers.<id>]`` table.

    Args:
        server: A schema-valid server definition.

    Returns:
        ``(fields, None)`` when wirable, or ``(None, reason)`` when in scope but not safely expressible.

    Raises:
        Nothing.
    """
    if not mcp_wirable(server):
        return None, "needs operator authorization (not first-party read-only)"
    auth = server.get("auth") or {}
    mechanism = auth.get("mechanism", "none")
    if mechanism in ("secret-store", "oauth"):
        return None, f"credential mechanism '{mechanism}' not expressible in a project Grok config"
    ref = auth.get("credential_ref") if mechanism == "env" else None
    if ref is not None and not _ENV_NAME_RE.match(str(ref)):
        return None, f"credential_ref {ref!r} is not a plain environment variable name"
    transport = server.get("transport")
    if transport == "stdio":
        command = str(server.get("command") or "")
        if not command:
            return None, "stdio server has no 'command' to launch"
        args = [str(a) for a in (server.get("args") or [])]
        if "${" in command or any("${" in a for a in args):
            return None, "command/args contain '${' (Grok would expand it from its own environment)"
        fields: dict[str, Any] = {"command": command}
        if args:
            fields["args"] = args
        if ref:
            fields["env"] = {str(ref): "${" + str(ref) + "}"}
        return fields, None
    if transport == "http":
        url = str(auth.get("url") or "")
        if not url:
            return None, "http server has no auth.url endpoint"
        if "${" in url:
            return None, "url contains '${' (Grok would expand it from its own environment)"
        fields = {"url": url}
        if ref:
            fields["bearer_token_env_var"] = str(ref)
        return fields, None
    return None, f"unknown transport {transport!r}"


def grok_permission_table(wired_ids: list[str]) -> dict[str, Any]:
    """Return the ``[permission]`` table: control-plane denies plus an ask for each wired MCP server.

    Args:
        wired_ids: Server ids wired into ``[mcp_servers]`` by this run.

    Returns:
        ``{"deny": [...], "ask": [...]}`` (``ask`` omitted when no server is wired).

    Raises:
        Nothing.
    """
    from agentteams.frameworks._sandbox_emit import permission_deny_rules

    deny: list[str] = []
    for rule in [*permission_deny_rules("claude"), *_EXTRA_DENY]:
        grok_rule = re.sub(r"\((?:/|~/)", "(**/", rule, count=1)
        if grok_rule not in deny:
            deny.append(grok_rule)
    table: dict[str, Any] = {"deny": deny}
    if wired_ids:
        table["ask"] = [f"MCPTool({sid}__*)" for sid in wired_ids]
    return table


def emit_grok_config(
    *,
    servers: list[dict[str, Any]],
    features: list[str],
    output_root: Path,
    dry_run: bool = False,
) -> GrokConfigResult:
    """Write the agentteams-managed block of ``<output_root>/.grok/config.toml``.

    Args:
        servers: mcp-server.schema.json-conformant definitions (used when ``grok:mcp`` is on).
        features: Active host-feature tokens (``grok:mcp`` and/or ``grok:sandbox``).
        output_root: The project root.
        dry_run: When True, compute without writing.

    Returns:
        :class:`GrokConfigResult`; ``errors`` is non-empty when the write was refused.

    Raises:
        OSError: The config cannot be read or written.
    """
    result = GrokConfigResult()
    root = Path(output_root)
    out_path = root / GROK_CONFIG_REL
    want_mcp = grok_mcp_enabled(features) and bool(servers)
    want_perm = grok_sandbox_enabled(features)
    if not (want_mcp or want_perm):
        return result
    linked = symlink_on_path(out_path, root)
    if linked is not None:
        result.errors.append(f"{linked} is a symlink; refusing to write {out_path}")
        return result

    tables: dict[str, dict[str, Any]] = {}
    if want_mcp:
        for s in servers:
            problems = _inert_problems(s)
            if problems:
                sid = s.get("server_id", "<unknown>") if isinstance(s, dict) else "<non-object>"
                result.errors.append(f"{sid}: {'; '.join(problems)}")
                continue
            fields, reason = grok_mcp_entry(s)
            if fields is None:
                result.not_wired[s["server_id"]] = reason or "not expressible"
                continue
            tables[f"mcp_servers.{s['server_id']}"] = fields
            result.wired.append(s["server_id"])

    existing_text = out_path.read_text(encoding="utf-8") if out_path.is_file() else ""
    try:
        outside = tomllib.loads(_strip_block(existing_text, _MANAGED_HEADER, _MANAGED_FOOTER))
    except tomllib.TOMLDecodeError as exc:
        result.errors.append(f"existing {out_path} is not valid TOML, refusing to splice: {exc}")
        return result
    if want_perm:
        if "permission" in outside:
            result.notices.append(
                f"{out_path} already has an operator [permission] table; agentteams' control-plane deny rules were "
                "not added (TOML allows one table). Merge them by hand from a fresh render if you want them.")
        else:
            tables["permission"] = grok_permission_table(result.wired)
            result.permission_written = True
    if not tables:
        return result

    owned = (("mcp_servers",) if want_mcp else ()) + (("permission",) if result.permission_written else ())
    result.dropped_unmanaged = sorted(
        set((outside.get("mcp_servers") or {}).keys()) - set(result.wired)) if want_mcp else []
    strip = _strip_unmanaged_mcp_tables if want_mcp else None
    splice = splice_managed_toml(out_path, render_tables(tables), owned_keys=owned, header=_MANAGED_HEADER,
                                 footer=_MANAGED_FOOTER, strip_unmanaged=strip, dry_run=dry_run)
    if splice.error:
        result.errors.append(splice.error)
        return result
    result.written.append(str(out_path))
    return result


__all__ = [
    "GROK_CONFIG_REL",
    "GROK_MCP_TOKEN",
    "GROK_SANDBOX_TOKEN",
    "GrokConfigResult",
    "emit_grok_config",
    "grok_mcp_enabled",
    "grok_mcp_entry",
    "grok_permission_table",
    "grok_sandbox_enabled",
]
