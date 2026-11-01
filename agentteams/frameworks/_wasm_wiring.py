"""_wasm_wiring.py — where each framework launches the ``agentteams_wasm`` MCP server (WASM sandbox plan P11, D8).

P10 (:mod:`agentteams.wasm_mcp`) builds the server's entries; this module decides, in one place, **who** gets one and
**where** it is written, so the renderers, the R1 control-plane emission and ``--check-wiring`` can't drift apart:

* only while WASM is on (:func:`agentteams.wasm_config.wasm_switch`), and only for agents P01's
  :func:`~agentteams.wasm_config.wasm_eligible` accepts (an execute-class tool; D12);
* per-agent copies (each launch carries that agent's grant) where the host scopes MCP servers per agent: Claude
  subagent front matter, Goose recipes (grant mode, direct ``goose run --recipe``);
* one compute-only ``--agent _session`` copy where the config is project-wide: Codex ``.codex/config.toml``;
  inert examples the operator merges for the Claude top-level session, VS Code and Copilot CLI (agentteams never
  writes a live ``.mcp.json`` / ``.vscode/mcp.json`` / ``.github/mcp.json``);
* nothing where the format is unverified (Grok: custom agents carry ``mcpInheritance: none``) or absent
  (agents-md), with an explicit notice.

The record is ``manifest["wasm_wiring"]``: ``protected_config_paths`` (every emitted file that launches the server;
METAPLAN R1 — :mod:`._control_plane_protect` adds each to the session denyWrite and an ``Edit`` deny) and one row
per framework (``status``, ``agents``, ``grant_scope``, ``reason``, ``verified``, ``config_paths``). This module
never claims enforcement (P13 owns ``wasm_enforcement``). It imports only P01/P00 state and ``wasm_mcp`` — never a
P13 or P14 module. Integrity-pinned: it decides which agents receive a tool (C-3).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

#: The manifest key P11 owns (team-manifest.schema.json ``wasm_wiring``).
WIRING_KEY = "wasm_wiring"
#: Inert examples (never live configs); project-relative.
CLAUDE_EXAMPLE = ".claude/mcp.agentteams-wasm.example.json"
VSCODE_EXAMPLE = ".vscode/mcp.agentteams-wasm.example.json"
COPILOT_CLI_EXAMPLE = ".github/mcp.agentteams-wasm.example.json"
CODEX_CONFIG = ".codex/config.toml"
#: Per-agent MCP config hosts: ``framework -> (agents dir, extension)`` (project-relative, canonical layout).
PER_AGENT_DIRS: Mapping[str, tuple[str, str]] = {"claude": (".claude/agents", ".md"),
                                                 "goose": (".goose/recipes", ".yaml")}
#: Frameworks whose agents dir sits two levels below the root, so ``wasm_mcp.install_files`` paths resolve.
_INSTALL_FRAMEWORKS = frozenset({"claude", "goose", "codex", "copilot-vscode", "copilot-cli"})
#: The text every file that launches the server carries (its install path), used by :func:`launching_config_paths`.
LAUNCH_MARKER = "agentteams-wasm-mcp.py"
#: Where a launching config can live (``(dir, glob)``, project-relative), for the disk scan.
_SCAN: tuple[tuple[str, str], ...] = (
    (".claude/agents", "*.md"), (".claude", "*.json"), (".", ".mcp.json"), (".goose/recipes", "*.yaml"),
    (".codex", "config.toml"), (".grok", "config.toml"), (".grok/agents", "*.md"), (".vscode", "*.json"),
    (".github", "mcp*.json"), (".github/agents", "*.md"),
)
_MERGE_NOTE = ("Inert example written by agentteams (WASM plan P11): agentteams never writes a live MCP config. "
               "Merge the agentteams_wasm entry by hand into {target}; the instance is compute-only (--agent "
               "_session). Once merged, keep {target} write-protected (METAPLAN R1).")


def wasm_on(manifest: Mapping[str, Any]) -> bool:
    """Return the WASM switch (cheap: no grants built).

    Args:
        manifest: The team manifest.

    Returns:
        ``wasm_config.wasm_switch(manifest)[0]``.

    Raises:
        ValueError: A malformed ``wasm_sandbox`` record or ``--wasm-sandbox`` value.
    """
    from agentteams.wasm_config import wasm_switch

    return wasm_switch(manifest)[0]


def _goose_block(manifest: Mapping[str, Any]) -> str | None:
    """Why Goose is not wired, or None.

    The OS sandbox no longer blocks it: operator spike B (2026-11-01) showed Goose launches stdio MCP servers outside
    its session sandbox, so the server can write its run dirs under ``.agentteams/`` and nests its own profile.
    """
    from agentteams.frameworks.goose_tool_scoping import grant_mode

    if not grant_mode(dict(manifest)):
        return "legacy goose_tool_scoping loads the whole developer extension (no per-recipe allowlist to rely on)"
    return None


#: Operator spike B (2026-11-01, dev wasmtime 36.0.16): how Claude Code and Goose run the server.
_HOST_NOTE = ("the host launches the server outside its own session sandbox (operator spike B, 2026-11-01), so the "
              "server's own nested OS profile around wasmtime and the shell-side denyWrite of every config carrying "
              "its launch flags are what protect it")


def _static_row(manifest: Mapping[str, Any], framework: str) -> dict[str, Any]:
    """The framework's row before any agent is rendered."""
    def row(status: str, scope: str, reason: str, verified: bool, paths: Sequence[str] = ()) -> dict[str, Any]:
        return {"status": status, "agents": [], "grant_scope": scope, "reason": reason, "verified": verified,
                "config_paths": list(paths)}

    if framework == "claude":
        return row("wired-per-agent", "per-agent", "subagent front matter mcpServers; the top-level session is not "
                   f"wired until the operator merges {CLAUDE_EXAMPLE} into .mcp.json; {_HOST_NOTE}", True,
                   [CLAUDE_EXAMPLE])
    if framework == "goose":
        blocked = _goose_block(manifest)
        if blocked:
            return row("not-wired", "none", blocked, False)
        return row("wired-per-agent", "per-agent", "recipe extensions, honoured on a direct `goose run --recipe`; "
                   f"a delegate cannot add agentteams_wasm (route that work to a direct run); {_HOST_NOTE}", True)
    if framework == "codex":
        return row("wired-shared", "session-compute-only", "one compute-only instance in .codex/config.toml (loaded "
                   "only for a trusted project); every call prompts (default_tools_approval_mode = prompt); spawned "
                   "agents under the codex:sandbox role gate reach only agentteams_readfs until P13", True,
                   [CODEX_CONFIG])
    if framework == "copilot-vscode":
        return row("example-only", "session-compute-only", f"inert {VSCODE_EXAMPLE} (servers/stdio, VS Code docs "
                   "2026-10-31); merge into .vscode/mcp.json; run_wasm is not in agent tools (custom-agent MCP "
                   "tool spelling unverified)", True, [VSCODE_EXAMPLE])
    if framework == "copilot-cli":
        return row("example-only", "session-compute-only", f"inert {COPILOT_CLI_EXAMPLE} (mcpServers/local, GitHub "
                   "docs 2026-10-31); merge into .github/mcp.json (loaded after folder trust); per-agent "
                   "mcp-servers in CLI agent profiles unverified", True, [COPILOT_CLI_EXAMPLE])
    if framework == "grok":
        return row("unverified-format", "none", "not wired: whether a custom agent with mcpInheritance: none sees "
                   "project MCP servers, or launches front-matter mcpServers, is unverified on the pinned Grok "
                   "Build (operator check pending)", False)
    if framework == "agents-md":
        return row("instructions-only", "none", "AGENTS.md has no MCP channel; instructions only", True)
    return row("not-wired", "none", f"no agentteams_wasm wiring for {framework}", False)


def wiring_row(manifest: dict[str, Any], framework: str) -> dict[str, Any] | None:
    """Return (creating on first use) ``manifest["wasm_wiring"]["frameworks"][framework]``; None when WASM is off.

    Args:
        manifest: The team manifest (mutated: ``wasm_wiring`` when WASM is on; untouched when off).
        framework: The framework id.

    Returns:
        The row, or None.

    Raises:
        ValueError: As :func:`wasm_on`.
    """
    if not wasm_on(manifest):
        return None
    record = manifest.get(WIRING_KEY)
    if not isinstance(record, dict):
        record = manifest[WIRING_KEY] = {}
    record.setdefault("protected_config_paths", [])
    rows = record.setdefault("frameworks", {})
    if not isinstance(rows, dict) or not isinstance(record["protected_config_paths"], list):
        raise ValueError("wasm_wiring must hold a protected_config_paths list and a frameworks object")
    if framework not in rows:
        rows[framework] = _static_row(manifest, framework)
        _protect(record, rows[framework]["config_paths"])
    return rows[framework]


def _protect(record: dict[str, Any], paths: Iterable[str]) -> None:
    out = record.setdefault("protected_config_paths", [])
    out.extend(p for p in paths if p not in out)


def _grant_for(manifest: Mapping[str, Any], slug: str) -> Any:
    from agentteams.sandbox_state import effective_sandbox_state, wasm_grant_for

    return wasm_grant_for(effective_sandbox_state(dict(manifest)), slug)


def wasm_agent_entry(manifest: dict[str, Any], framework: str, slug: str,
                     tools: Sequence[str] | None) -> list[str] | None:
    """Return the launch args for ``slug``'s own ``agentteams_wasm`` copy, recording it as wired, or None.

    The one place a per-agent copy is granted (C-3): WASM on, a per-agent host whose row is wired, and
    ``wasm_eligible({"slug", "tools"})``. The agent's file is added to ``protected_config_paths`` (R1), so the
    control-plane emission that runs after the agents are rendered write-protects it.

    Args:
        manifest: The team manifest (mutated: the wiring record).
        framework: ``claude`` or ``goose`` (anything else returns None).
        slug: The agent slug.
        tools: The agent's tools as rendered (canonical scopes or Claude names).

    Returns:
        ``wasm_mcp.agent_launch_args`` for the agent, or None.

    Raises:
        ValueError: As :func:`wasm_on` / ``wasm_mcp.launch_args``.
        ImportError: WASM is on and ``agentteams.wasm_exec`` is not importable (fail closed).
    """
    if framework not in PER_AGENT_DIRS or not wasm_on(manifest):
        return None
    from agentteams import wasm_mcp
    from agentteams.wasm_config import wasm_eligible

    row = wiring_row(manifest, framework)
    if row is None or row["status"] != "wired-per-agent":
        return None
    if not wasm_eligible({"slug": slug, "tools": list(tools) if tools is not None else None}):
        return None
    grant = _grant_for(manifest, slug)
    args = wasm_mcp.launch_args(slug, grant, _languages(manifest))
    folder, ext = PER_AGENT_DIRS[framework]
    path = f"{folder}/{slug}{ext}"
    if slug not in row["agents"]:
        row["agents"].append(slug)
    if path not in row["config_paths"]:
        row["config_paths"].append(path)
    _protect(manifest[WIRING_KEY], [path])
    return args


def _languages(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    from agentteams.sandbox_state import effective_sandbox_state

    return effective_sandbox_state(dict(manifest)).wasm_languages


def _session_args(manifest: Mapping[str, Any]) -> list[str] | None:
    from agentteams import wasm_mcp
    from agentteams.sandbox_state import effective_sandbox_state

    return wasm_mcp.session_launch_args(effective_sandbox_state(dict(manifest)))


def _example(target: str, key: str, entry: dict[str, Any]) -> str:
    return json.dumps({"_comment": _MERGE_NOTE.format(target=target), key: {"agentteams_wasm": entry}},
                      indent=2) + "\n"


def output_files(manifest: dict[str, Any], framework: str) -> list[tuple[str, str]]:
    """The text files a framework's ``extra_output_files`` adds for WASM: the server + text bundle, and the example.

    Empty when WASM is off (default teams byte-identical) or when nothing launches the server for this framework
    (Grok, agents-md, Goose not wired). The binary deny probe goes through :func:`install_binary` (the emit phase
    writes text only). Paths are relative to the agents dir (two levels below the root).

    Args:
        manifest: The team manifest (mutated: the wiring record).
        framework: The framework id.

    Returns:
        ``(rel_path, text)`` pairs.

    Raises:
        ValueError: A packaged bundle file differs from its pin, or as :func:`wasm_on`.
        ImportError: As :func:`wasm_agent_entry`.
    """
    row = wiring_row(manifest, framework)
    if row is None or framework not in _INSTALL_FRAMEWORKS or row["status"] == "not-wired":
        return []
    from agentteams import wasm_mcp

    files = list(wasm_mcp.install_files(manifest))
    args = _session_args(manifest)
    if args is None:
        return files
    python = wasm_mcp.interpreter()
    entry = wasm_mcp.table_entry(args, python)
    if framework == "claude":
        files.append(("../mcp.agentteams-wasm.example.json",
                      _example(".mcp.json", "mcpServers", {"type": "stdio", **entry})))
    elif framework == "copilot-vscode":
        files.append((f"../../{VSCODE_EXAMPLE}", _example(".vscode/mcp.json", "servers", {"type": "stdio", **entry})))
    elif framework == "copilot-cli":
        files.append((f"../../{COPILOT_CLI_EXAMPLE}", _example(
            ".github/mcp.json", "mcpServers", {"type": "local", **entry, "tools": list(wasm_mcp.TOOLS)})))
    return files


def codex_first_party_tables(manifest: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    """The ``first_party_tables`` argument for ``codex_mcp_emit.emit_codex_mcp_config`` (Codex renders only).

    Args:
        manifest: The team manifest (``framework`` must be ``codex``).

    Returns:
        ``{"mcp_servers.agentteams_wasm": {command, args, default_tools_approval_mode = "prompt"}}``, or None when
        WASM is off or the framework is not Codex.

    Raises:
        ValueError: As :func:`wasm_on`.
        ImportError: As :func:`wasm_agent_entry`.
    """
    if manifest.get("framework") != "codex" or wiring_row(manifest, "codex") is None:
        return None
    from agentteams import wasm_mcp

    args = _session_args(manifest)
    if args is None:
        return None
    entry = wasm_mcp.table_entry(args, wasm_mcp.interpreter())
    return {f"mcp_servers.{wasm_mcp.SERVER_NAME}": {**entry, "default_tools_approval_mode": "prompt"}}


def install_binary(manifest: dict[str, Any], project_root: Path) -> list[str]:
    """Write the bundle's binary file(s) (the deny probe) byte-exactly, where the server is installed.

    The emit phase writes text only (P10 note), so this runs beside the host MCP artifacts after the emit. The
    bundle goes where the emit just installed the server: ``project_root`` itself, or two levels above it when the
    CLI's ``--output`` named the agents dir (the CLI then hands that dir over as ``project_root``).

    Args:
        manifest: The team manifest (``framework``).
        project_root: The project root (or the agents dir, as above).

    Returns:
        The project-relative paths written (empty when nothing is installed).

    Raises:
        ValueError: A pin mismatch or a symlinked ``.agentteams`` / ``bin`` (``wasm_mcp.write_bundle``).
        OSError: The write failed.
    """
    framework = str(manifest.get("framework") or "")
    row = wiring_row(manifest, framework)
    if row is None or framework not in _INSTALL_FRAMEWORKS or row["status"] == "not-wired":
        return []
    from agentteams import wasm_mcp

    base = Path(project_root)
    root = next((r for r in (base, base.parent.parent) if (r / wasm_mcp.PROTECTED_PATH).is_file()), None)
    if root is None:
        raise ValueError(f"the emitted {wasm_mcp.PROTECTED_PATH} was not found beside {base}")
    return wasm_mcp.write_bundle(root, [(p, wasm_mcp.bundle_content(p)) for p in wasm_mcp.BINARY_FILES])


def unhonoured_grants(manifest: dict[str, Any], framework: str, state: Any) -> list[str]:
    """Agents whose widened WASM grant this framework cannot honour (I3): shared-copy hosts refuse them.

    Args:
        manifest: The team manifest.
        framework: The framework id.
        state: The effective ``SandboxState``.

    Returns:
        The widened agents' slugs on a host without per-agent copies (empty for Claude, Goose and WASM off).

    Raises:
        ValueError: As :func:`wasm_on`.
    """
    row = wiring_row(manifest, framework)
    if row is None or row["grant_scope"] != "session-compute-only":
        return []
    return sorted(a for a, g in state.wasm_grants.items() if g.mounts)


def report_line(manifest: dict[str, Any], framework: str) -> str | None:
    """The ``[WASM-WIRING]`` line for this framework (never an enforcement claim), or None when WASM is off.

    Args:
        manifest: The team manifest.
        framework: The framework id.

    Returns:
        ``"[WASM-WIRING] <fw>: <status> (<reason>)"`` plus ``unverified`` where the row is.

    Raises:
        ValueError: As :func:`wasm_on`.
    """
    row = wiring_row(manifest, framework)
    if row is None:
        return None
    flag = "" if row["verified"] else " [unverified]"
    return f"[WASM-WIRING] {framework}: {row['status']}{flag} ({row['reason']}); enforcement is P13's to claim"


def print_report(manifest: dict[str, Any], framework: str, state: Any) -> None:
    """Print the ``[WASM-WIRING]`` line (stderr), its advisory, and refuse widened grants on shared hosts.

    Args:
        manifest: The team manifest (mutated: the wiring record; ``advisories`` for unverified/not-wired rows).
        framework: The framework id.
        state: The effective ``SandboxState``.

    Raises:
        ValueError: A widened grant on a host that only takes one compute-only copy (refused, never downgraded).
    """
    line = report_line(manifest, framework)
    if line is None:
        return
    widened = unhonoured_grants(manifest, framework, state)
    if widened:
        raise ValueError(f"wasm_sandbox.agents grants mounts to {', '.join(widened)}, but {framework} takes only one "
                         "compute-only agentteams_wasm copy for the whole project (I3); drop the widening or render "
                         "for claude/goose")
    print(f"  {line}", file=sys.stderr)
    row = manifest[WIRING_KEY]["frameworks"][framework]
    if framework == "goose" and row["status"] == "wired-per-agent":
        widened = sorted(a for a, g in state.wasm_grants.items() if g.mounts)
        if widened:
            print(f"  NOTE (WASM): widened grants for {', '.join(widened)} are honoured only on a direct "
                  "`goose run --recipe .goose/recipes/<slug>.yaml`", file=sys.stderr)
    if row["status"] in ("unverified-format", "not-wired"):
        manifest.setdefault("advisories", []).append(
            {"code": f"wasm-wiring-{row['status']}", "message": line.removeprefix("[WASM-WIRING] ")})


def launching_config_paths(root: str | Path) -> tuple[str, ...]:
    """Project-relative paths of every config file on disk that launches ``agentteams_wasm`` (R1 truth source).

    Scans the known MCP config locations for :data:`LAUNCH_MARKER` (the server's install path), regular files
    only. A checker unions this with the manifest's ``protected_config_paths``, so a file the manifest does not
    list (hand-merged ``.mcp.json``, a manifest-less ``--check-wiring``) still has to be write-protected.

    Args:
        root: The project root.

    Returns:
        The paths, sorted.

    Raises:
        Nothing (unreadable files are skipped).
    """
    base = Path(root)
    found: set[str] = set()
    for folder, pattern in _SCAN:
        directory = base / folder
        if directory.is_symlink() or not directory.is_dir():
            continue
        for path in directory.glob(pattern):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            if LAUNCH_MARKER in text:
                found.add(path.relative_to(base).as_posix())
    return tuple(sorted(found))


def covered(path: str, entries: Iterable[str]) -> bool:
    """Return whether ``path`` or one of its ancestor directories is in ``entries`` (a denyWrite set).

    Args:
        path: A project-relative path.
        entries: Project-relative deny entries.

    Returns:
        True when covered.

    Raises:
        Nothing.
    """
    have = {e.strip("/") for e in entries}
    parts = path.split("/")
    return any("/".join(parts[:i]) in have for i in range(1, len(parts) + 1))


def check_wiring_problems(root: str | Path, framework: str, manifest: dict[str, Any]) -> list[str]:
    """``--check-wiring`` problems for the WASM wiring: P10's live checks where this framework installs the server.

    Args:
        root: The project root.
        framework: The framework id.
        manifest: The team manifest.

    Returns:
        One message per problem; empty when WASM is off or nothing is wired for this framework.

    Raises:
        ValueError: As :func:`wasm_on`.
    """
    row = wiring_row(manifest, framework)
    if row is None or framework not in _INSTALL_FRAMEWORKS or row["status"] == "not-wired":
        return []
    from agentteams import wasm_mcp

    return wasm_mcp.wiring_problems(root, framework, manifest)


__all__ = [
    "CLAUDE_EXAMPLE",
    "CODEX_CONFIG",
    "COPILOT_CLI_EXAMPLE",
    "LAUNCH_MARKER",
    "PER_AGENT_DIRS",
    "VSCODE_EXAMPLE",
    "WIRING_KEY",
    "check_wiring_problems",
    "codex_first_party_tables",
    "covered",
    "install_binary",
    "launching_config_paths",
    "output_files",
    "print_report",
    "report_line",
    "unhonoured_grants",
    "wasm_agent_entry",
    "wasm_on",
    "wiring_row",
]
