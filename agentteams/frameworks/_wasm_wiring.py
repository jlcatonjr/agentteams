"""_wasm_wiring.py — where each framework launches the ``agentteams_wasm`` MCP server (WASM sandbox plan P11, D8).

P10 (:mod:`agentteams.wasm_mcp`) builds the server's entries; this module decides, in one place, **who** gets one and
**where** it is written, so the renderers, the R1 control-plane emission and ``--check-wiring`` can't drift apart:

* only while WASM is on (:func:`agentteams.wasm_config.wasm_switch`), and only for agents P01's
  :func:`~agentteams.wasm_config.wasm_eligible` accepts (an execute-class tool; D12);
* per-agent copies (each launch carries that agent's grant) where the host scopes MCP servers per agent: Claude
  subagent front matter, Goose recipes (grant mode, direct ``goose run --recipe``);
* one compute-only ``--agent _session`` copy where the config is project-wide: Codex ``.codex/config.toml``;
  inert examples the operator merges for the Claude top-level session, VS Code, Copilot CLI and Grok (agentteams
  never writes a live ``.mcp.json`` / ``.vscode/mcp.json`` / ``.github/mcp.json``, or the server's table in
  ``.grok/config.toml``);
* nothing where the channel is absent (agents-md), with an explicit notice.

The record is ``manifest["wasm_wiring"]``: ``protected_config_paths`` (every emitted file that launches the server;
METAPLAN R1 — :mod:`._control_plane_protect` adds each to the session denyWrite and an ``Edit`` deny) and one row
per framework (``status``, ``agents``, ``grant_scope``, ``reason``, ``verified``, ``config_paths``). This module
never claims enforcement (P13 owns ``wasm_enforcement``). It imports only P01/P00 state and ``wasm_mcp`` — never a
P13 or P14 module. Integrity-pinned: it decides which agents receive a tool (C-3).
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from agentteams.sandbox_state import SandboxState

#: The manifest key P11 owns (team-manifest.schema.json ``wasm_wiring``).
WIRING_KEY = "wasm_wiring"
#: Wiring statuses (team-manifest.schema.json ``wasm_wiring.frameworks.*.status``); every module compares against these.
WIRED_PER_AGENT = "wired-per-agent"
WIRED_SHARED = "wired-shared"
EXAMPLE_ONLY = "example-only"
NOT_WIRED = "not-wired"
UNVERIFIED_FORMAT = "unverified-format"
INSTRUCTIONS_ONLY = "instructions-only"
#: Statuses under which an eligible agent receives ``run_wasm``.
WIRED_STATUSES = frozenset({WIRED_PER_AGENT, WIRED_SHARED})
#: Inert examples (never live configs); project-relative.
CLAUDE_EXAMPLE = ".claude/mcp.agentteams-wasm.example.json"
VSCODE_EXAMPLE = ".vscode/mcp.agentteams-wasm.example.json"
COPILOT_CLI_EXAMPLE = ".github/mcp.agentteams-wasm.example.json"
GROK_EXAMPLE = ".grok/mcp.agentteams-wasm.example.toml"
CODEX_CONFIG = ".codex/config.toml"
#: Per-agent MCP config hosts: ``framework -> (agents dir, extension)`` (project-relative, canonical layout).
PER_AGENT_DIRS: Mapping[str, tuple[str, str]] = {"claude": (".claude/agents", ".md"),
                                                 "goose": (".goose/recipes", ".yaml")}
#: Frameworks whose agents dir sits two levels below the root, so ``wasm_mcp.install_files`` paths resolve.
_INSTALL_FRAMEWORKS = frozenset({"claude", "goose", "codex", "copilot-vscode", "copilot-cli", "grok"})
#: The text every file that launches the server carries (its install path), used by :func:`launching_config_paths`.
LAUNCH_MARKER = "agentteams-wasm-mcp.py"
#: Where a launching config can live (``(dir, globs, recursive)``, project-relative), for the disk scan.
_SCAN: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    (".claude/agents", ("*.md",), False), (".claude", ("*.json",), False), (".", (".mcp.json",), False),
    (".goose/recipes", ("*.yaml", "*.yml", "*.json"), True), (".codex", ("config.toml",), False),
    (".grok", ("config.toml", "mcp*.toml"), False), (".grok/agents", ("*.md",), False), (".vscode", ("*.json",), False),
    (".github", ("mcp*.json",), False), (".github/agents", ("*.md",), False),
)
#: Bounds of the scan (fail closed: a file or tree past a bound is reported as unscanned, never half-read).
SCAN_MAX_DEPTH = 4
SCAN_MAX_FILES = 2000
SCAN_MAX_BYTES = 8 * 1024 * 1024
#: Keys whose (parsed) values are a launch command line in JSON / TOML MCP configs (Claude, VS Code, Copilot,
#: Codex, Grok: ``command``/``args``; Goose: ``cmd``/``args``).
_LAUNCH_KEYS = frozenset({"command", "cmd", "args", "argv"})
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
#: Operator spike 07 (2026-11-03, wasmtime 36.0.20, macOS): what the other hosts did with one `run_wasm` call.
_NESTED_UNLESS_SANDBOXED = ("live call nested under Seatbelt with the host's sandbox off, os_confinement=none "
                            "(unavailable:darwin, snapshot read-only mounts) under {flag}, which runs the server "
                            "inside it (operator spike 07, 2026-11-03)")


def _static_row(manifest: Mapping[str, Any], framework: str) -> dict[str, Any]:
    """The framework's row before any agent is rendered."""
    def row(status: str, scope: str, reason: str, verified: bool, paths: Sequence[str] = ()) -> dict[str, Any]:
        return {"status": status, "agents": [], "grant_scope": scope, "reason": reason, "verified": verified,
                "config_paths": list(paths)}

    if framework == "claude":
        return row(WIRED_PER_AGENT, "per-agent", "subagent front matter mcpServers; the top-level session is not "
                   f"wired until the operator merges {CLAUDE_EXAMPLE} into .mcp.json; {_HOST_NOTE}", True,
                   [CLAUDE_EXAMPLE])
    if framework == "goose":
        blocked = _goose_block(manifest)
        if blocked:
            return row(NOT_WIRED, "none", blocked, False)
        return row(WIRED_PER_AGENT, "per-agent", "recipe extensions, honoured on a direct `goose run --recipe`; "
                   f"a delegate cannot add agentteams_wasm (route that work to a direct run); {_HOST_NOTE}", True)
    if framework == "codex":
        return row(WIRED_SHARED, "session-compute-only", "one compute-only instance in .codex/config.toml (loaded "
                   "only for a trusted project), configured with default_tools_approval_mode = \"prompt\" (\"auto\" "
                   "only under the opt-in codex:wasm-auto-approve host feature; a default the host applies, not enforcement agentteams verifies); under the codex:sandbox role gate a "
                   "spawned agent outside the keep list is denied the shell and steered to run_wasm (WASM mode), or "
                   "reaches only agentteams_readfs (write_policy: orchestrator-only)", True,
                   [CODEX_CONFIG])
    if framework == "copilot-vscode":
        return row(EXAMPLE_ONLY, "session-compute-only", f"inert {VSCODE_EXAMPLE} (servers/stdio, VS Code docs "
                   "2026-10-31); merge into .vscode/mcp.json; run_wasm is not in agent tools (custom-agent MCP "
                   "tool spelling unverified); live call nested under Seatbelt, after an approval prompt (operator "
                   "spike 07, 2026-11-03)", True, [VSCODE_EXAMPLE])
    if framework == "copilot-cli":
        return row(EXAMPLE_ONLY, "session-compute-only", f"inert {COPILOT_CLI_EXAMPLE} (mcpServers/local, GitHub "
                   "docs 2026-10-31); merge into .github/mcp.json (loaded after folder trust); per-agent "
                   "mcp-servers in CLI agent profiles unverified; "
                   + _NESTED_UNLESS_SANDBOXED.format(flag="--sandbox"), True, [COPILOT_CLI_EXAMPLE])
    if framework == "grok":
        return row(EXAMPLE_ONLY, "session-compute-only", f"inert {GROK_EXAMPLE} (write-protected only inside the agentteams "
                   "launcher, not in profile mode: check it against a fresh render before merging); merge its table into .grok/config.toml, outside the "
                   "agentteams-managed block (loaded after folder trust); no "
                   "[permission] ask rule covers it, and custom agents carry mcpInheritance: none (their access and "
                   "Grok's approval prompt are untested); "
                   + _NESTED_UNLESS_SANDBOXED.format(flag="--sandbox workspace"), True, [GROK_EXAMPLE])
    if framework == "agents-md":
        return row(INSTRUCTIONS_ONLY, "none", "AGENTS.md has no MCP channel; instructions only", True)
    return row(NOT_WIRED, "none", f"no agentteams_wasm wiring for {framework}", False)


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


def _installs(row: Mapping[str, Any] | None, framework: str) -> bool:
    """Whether ``framework`` installs the server (WASM on, an install host, and a row that is not ``not-wired``)."""
    return row is not None and framework in _INSTALL_FRAMEWORKS and row["status"] != NOT_WIRED


def _widened(state: SandboxState) -> list[str]:
    """The agents whose WASM grant carries mounts (a widening past compute-only), sorted."""
    return sorted(a for a, g in state.wasm_grants.items() if g.mounts)


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
    if row is None or row["status"] != WIRED_PER_AGENT:
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


def _toml_example(target: str, entry: Mapping[str, Any]) -> str:
    note = _MERGE_NOTE.format(target=target)
    table = "".join(f"{key} = {json.dumps(value, ensure_ascii=False)}\n" for key, value in entry.items())  # valid TOML
    caveat = ("A render with grok:mcp wiring any server owns [mcp_servers]: it keeps this table only while it equals "
              "this example exactly, and otherwise replaces it (it says so); merge it again after such a render.")
    verify = ("Grok itself does not write-protect this file: before merging, check it matches a fresh render (the "
              "--agent value must be _session).")
    exposure = ("Only the agentteams launcher (.grok/confined-run.example.sh, the default launcher mode) makes .grok "
                "read-only to a Grok session; in profile mode (GROK_CONFINE_MODE=profile) a session's shell can edit this "
                "example and .grok/config.toml alike (the [permission] deny covers only Grok's file tools), so "
                "merge only from a fresh render, after a diff.")
    return f"# {note}\n# {caveat}\n# {verify}\n# {exposure}\n[mcp_servers.agentteams_wasm]\n{table}"


def output_files(manifest: dict[str, Any], framework: str) -> list[tuple[str, str]]:
    """The text files a framework's ``extra_output_files`` adds for WASM: the server + text bundle, and the example.

    Empty when WASM is off (default teams byte-identical) or when nothing launches the server for this framework
    (agents-md, Goose not wired). The binary deny probe goes through :func:`install_binary` (the emit phase
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
    if not _installs(row, framework):
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
    elif framework == "grok":
        files.append((f"../../{GROK_EXAMPLE}", _toml_example(".grok/config.toml", entry)))
    return files


def codex_first_party_tables(manifest: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    """The ``first_party_tables`` argument for ``codex_mcp_emit.emit_codex_mcp_config`` (Codex renders only).

    Args:
        manifest: The team manifest (``framework`` must be ``codex``).

    Returns:
        ``{"mcp_servers.agentteams_wasm": {command, args, default_tools_approval_mode}}`` (``"prompt"``, or
        ``"auto"`` under the opt-in ``codex:wasm-auto-approve`` host feature), or None when
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
    from agentteams import codex_mcp_emit

    entry = wasm_mcp.table_entry(args, wasm_mcp.interpreter())
    mode = codex_mcp_emit.first_party_approval_mode(manifest.get("host_features") or [])
    return {f"mcp_servers.{wasm_mcp.SERVER_NAME}": {**entry, "default_tools_approval_mode": mode}}


def grok_kept_tables(manifest: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    """The ``keep_if_exact`` argument for ``grok_config_emit.emit_grok_config``: the inert example's table.

    The Grok config splice is host-scoped (any ``--framework`` with ``grok:mcp``), so this does not require a grok
    render, and it reads the manifest without creating a wiring row. agentteams never writes the table; it only
    stops a ``grok:mcp`` render from stripping an operator-merged copy that equals it.

    Args:
        manifest: The team manifest (read only).

    Returns:
        ``{"mcp_servers.agentteams_wasm": {command, args}}``, or None when WASM is off.

    Raises:
        ValueError: As :func:`wasm_on`, or no system python3 exists.
    """
    if not wasm_on(manifest):
        return None
    from agentteams import wasm_mcp

    args = _session_args(manifest)
    if args is None:
        return None
    return {f"mcp_servers.{wasm_mcp.SERVER_NAME}": wasm_mcp.table_entry(args, wasm_mcp.interpreter())}


def install_root(framework: str, path: str | Path) -> Path:
    """The project root the emit installed the server under, for ``path`` (the CLI's pre-normalisation output).

    Deterministic, never a probe: the emit writes the server two levels above the agents dir, so when ``path`` *is*
    the framework's canonical agents dir (``--output`` named it, e.g. ``.claude/agents``) the root is its grandparent;
    otherwise ``path`` is the root. A non-canonical agents dir is refused before anything is emitted
    (:func:`agents_dir_problem`).

    Args:
        framework: The framework id.
        path: The CLI's project root (the ``--output`` value, resolved).

    Returns:
        The install root.

    Raises:
        Nothing.
    """
    from agentteams.frameworks._sandbox_emit import TEAM_KEY_BY_FRAMEWORK, team_agents_dir

    base = Path(path)
    key = TEAM_KEY_BY_FRAMEWORK.get(framework)
    sub = team_agents_dir(key) if key else ""
    if sub and base.as_posix().endswith("/" + sub):
        return base.parents[sub.count("/")]
    return base


def agents_dir_problem(manifest: Mapping[str, Any], framework: str, team_dir: str | Path | None) -> str | None:
    """Why this render's agents dir cannot carry the WASM wiring, or None.

    The protected paths (``.claude/agents/<slug>.md``, ``.goose/recipes/<slug>.yaml``) and the server's install
    (two levels above the agents dir) assume the framework's canonical agents dir. A custom ``--output`` elsewhere
    would write the launching files where no deny names them, so with WASM on it is refused.

    Args:
        manifest: The team manifest.
        framework: The framework id.
        team_dir: The run's agents dir, or None (nothing to check).

    Returns:
        The refusal text, or None (WASM off, a framework that installs nothing, or the canonical dir).

    Raises:
        ValueError: As :func:`wasm_on`.
    """
    if team_dir is None or framework not in _INSTALL_FRAMEWORKS or not wasm_on(manifest):
        return None
    from agentteams.frameworks._sandbox_emit import TEAM_KEY_BY_FRAMEWORK, team_agents_dir

    key = TEAM_KEY_BY_FRAMEWORK.get(framework)
    if key is None:
        return None
    sub = team_agents_dir(key)
    if Path(team_dir).resolve().as_posix().endswith("/" + sub):
        return None
    return (f"WASM is on, so the {framework} agents must be written to the project's {sub} (this --output writes "
            f"them to {Path(team_dir).name}/, where the control-plane denies of the files that launch "
            "agentteams_wasm would not reach them); render into the project root or its " + sub)


def install_binary(manifest: dict[str, Any], project_root: Path) -> list[str]:
    """Write the bundle's binary file(s) (the deny probe) byte-exactly beside the server installed at ``project_root``.

    The emit phase writes text only (P10 note), so this runs beside the host MCP artifacts after the emit. It never
    searches another directory: the server must already be installed at ``project_root`` (the CLI passes
    :func:`install_root`), else this refuses.

    Args:
        manifest: The team manifest (``framework``).
        project_root: The project root the server was installed under.

    Returns:
        The project-relative paths written (empty when nothing is installed).

    Raises:
        ValueError: The server is not installed at ``project_root``, a pin mismatch, or a symlinked ``.agentteams``
            / ``bin`` (``wasm_mcp.write_bundle``).
        OSError: The write failed.
    """
    framework = str(manifest.get("framework") or "")
    if not _installs(wiring_row(manifest, framework), framework):
        return []
    from agentteams import wasm_mcp

    root = Path(project_root)
    if not (root / wasm_mcp.PROTECTED_PATH).is_file():
        raise ValueError(f"the emitted {wasm_mcp.PROTECTED_PATH} was not found under {root}")
    return wasm_mcp.write_bundle(root, [(p, wasm_mcp.bundle_content(p)) for p in wasm_mcp.BINARY_FILES])


def unhonoured_grants(manifest: dict[str, Any], framework: str, state: SandboxState) -> list[str]:
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
    return _widened(state)


def inert_grants(manifest: dict[str, Any], framework: str, state: SandboxState) -> list[tuple[str, str]]:
    """Widened WASM grants that reach no ``agentteams_wasm`` copy on this framework, each with why (D2: never silent).

    Call after the agents are rendered: on a per-agent host the wiring record's ``agents`` is the ground truth (only
    :func:`wasm_agent_entry` adds to it). Shared-copy hosts refuse a widening outright (:func:`unhonoured_grants`),
    so they never reach here; ``--framework wasm`` honours grants in its own runtime and is skipped.

    Args:
        manifest: The team manifest (its wiring record, filled in while the agents were rendered).
        framework: The framework id.
        state: The effective ``SandboxState``.

    Returns:
        ``(slug, reason)`` per widened agent the framework did not wire, sorted by slug.

    Raises:
        ValueError: As :func:`wasm_on`.
    """
    if framework == "wasm":
        return []
    row = wiring_row(manifest, framework)
    if row is None or row["grant_scope"] == "session-compute-only":
        return []
    wired = set(row["agents"]) if row["status"] == WIRED_PER_AGENT else set()
    out: list[tuple[str, str]] = []
    for slug in _widened(state):
        if slug in wired:
            continue
        if row["status"] == WIRED_PER_AGENT:
            why = (f"{slug} is not WASM-eligible here (only an agent holding an execute tool gets an "
                   "agentteams_wasm copy, R9), so it was not wired")
        else:
            why = f"{framework} does not wire agentteams_wasm per agent ({row['status']}: {row['reason']})"
        out.append((slug, why))
    return out


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


def print_report(manifest: dict[str, Any], framework: str, state: SandboxState) -> None:
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
    if framework == "goose" and row["status"] == WIRED_PER_AGENT:
        widened = _widened(state)
        if widened:
            print(f"  NOTE (WASM): widened grants for {', '.join(widened)} are honoured only on a direct "
                  "`goose run --recipe .goose/recipes/<slug>.yaml`", file=sys.stderr)
    if row["status"] in (UNVERIFIED_FORMAT, NOT_WIRED):
        manifest.setdefault("advisories", []).append(
            {"code": f"wasm-wiring-{row['status']}", "message": line.removeprefix("[WASM-WIRING] ")})


class ConfigScan(NamedTuple):
    """The disk scan's result: what launches the server, and what could not be checked (both are protection targets).

    Attributes:
        launching: Project-relative regular files whose parsed launch command lines name the server.
        unsafe: ``(path, reason)`` for every config location the scan could not read as a plain file: a symlinked
            file or directory (a path deny does not pin what it points at), a non-regular, unparseable or oversized
            file, or a tree past the walk bounds. Callers report each as not protected (fail closed).
    """

    launching: tuple[str, ...]
    unsafe: tuple[tuple[str, str], ...]


_JSONC_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|//[^\n]*|/\*.*?\*/|,(?=\s*[}\]])', re.S)
#: YAML double-quoted escapes that can spell a marker character, and the escaped line break that joins two lines.
_YAML_HEX = re.compile(r"\\x([0-9A-Fa-f]{2})|\\u([0-9A-Fa-f]{4})|\\U([0-9A-Fa-f]{8})")
_YAML_LINE_JOIN = re.compile(r"\\\r?\n[ \t]*")
#: A Markdown front matter's closing fence: a whole line of exactly ``---`` (trailing blanks, CRLF allowed).
_FRONT_MATTER_END = re.compile(r"^---[ \t]*\r?$", re.M)


def _strip_jsonc(text: str) -> str:
    """Drop ``//`` / ``/* */`` comments and trailing commas outside strings (VS Code's JSONC), keeping strings."""
    return _JSONC_TOKEN.sub(lambda m: m.group(0) if m.group(0).startswith('"') else "", text)


def _launch_strings(node: Any, under: bool = False) -> Iterable[str]:
    """Every string under a :data:`_LAUNCH_KEYS` key of a parsed JSON/TOML node (duplicate JSON keys all kept)."""
    if isinstance(node, str):
        if under:
            yield node
    elif isinstance(node, Mapping):
        for key, value in node.items():
            yield from _launch_strings(value, under or str(key).lower() in _LAUNCH_KEYS)
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, tuple) and len(item) == 2:   # a JSON object kept as pairs
                yield from _launch_strings(item[1], under or str(item[0]).lower() in _LAUNCH_KEYS)
            else:
                yield from _launch_strings(item, under)


def _yaml_decoded(text: str) -> str:
    """``text`` with every YAML escaped line break joined and every ``\\x``/``\\u``/``\\U`` escape decoded.

    Applied to the whole file, quoted or not, so no quote-pairing trick can keep an escaped marker from being
    decoded; decoding outside a double-quoted scalar can only add a match (fail closed), never remove one.
    """
    def hex_char(m: re.Match[str]) -> str:
        code = int(next(g for g in m.groups() if g), 16)
        return chr(code) if code <= 0x10FFFF else m.group(0)

    return _YAML_HEX.sub(hex_char, _YAML_LINE_JOIN.sub("", text))


def _launches(rel: str, text: str) -> bool:
    """Whether a config's parsed launch command lines name the server.

    Raises:
        ValueError: A JSON or TOML file that does not parse (the caller reports it as unreadable).
    """
    text = text.removeprefix("\ufeff")
    if rel.endswith(".json"):
        data = json.loads(_strip_jsonc(text), object_pairs_hook=list)
        return any(LAUNCH_MARKER in v for v in _launch_strings(data))
    if rel.endswith(".toml"):
        return any(LAUNCH_MARKER in v for v in _launch_strings(tomllib.loads(text)))  # TOMLDecodeError: ValueError
    if rel.endswith(".md"):
        # An agent file declares MCP servers only in its front matter (`mcpServers:`); the body is prose an agent
        # reads (P14's @security rows name the server's path there). No front matter: nothing can be launched. An
        # opened but unclosed block is scanned whole (fail closed).
        # The block ends at the first later line that is exactly a fence (``---`` plus trailing blanks), never at a
        # line that merely starts with ``---`` (a ``---x`` line inside a quoted multi-line scalar).
        if not text.startswith("---"):
            return False
        end = _FRONT_MATTER_END.search(text, text.find("\n") + 1 or len(text))
        text = text if end is None else text[:end.start()]
    # YAML (Goose recipes, agent front matter), read without a YAML dependency like goose_recipe_read: the whole
    # file, escapes decoded, is a superset of every cmd/args scalar that reader returns (it only strips quotes).
    return LAUNCH_MARKER in text or LAUNCH_MARKER in _yaml_decoded(text)


def _read_config(path: Path) -> str:
    """A regular file's UTF-8 text, never following a symlink.

    Raises:
        ValueError: Not a regular file, larger than :data:`SCAN_MAX_BYTES`, or not UTF-8.
        OSError: The open or read failed (a symlink swapped in: ``O_NOFOLLOW``).
    """
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as fh:
        info = os.fstat(fh.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("not a regular file")
        if info.st_size > SCAN_MAX_BYTES:
            raise ValueError(f"larger than {SCAN_MAX_BYTES} bytes")
        return fh.read(SCAN_MAX_BYTES + 1).decode("utf-8")


def _symlinked_component(base: Path, folder: str) -> str | None:
    """The first symlinked component of ``folder`` under ``base`` (``base`` itself excepted), or None."""
    current = base
    for part in Path(folder).parts:
        if part == ".":
            continue
        current = current / part
        if current.is_symlink():
            return current.relative_to(base).as_posix()
    return None


def _candidates(base: Path, folder: str, patterns: tuple[str, ...], recursive: bool,
                unsafe: dict[str, str]) -> list[Path]:
    """The non-symlink entries under ``folder`` matching ``patterns`` (a bounded walk when ``recursive``).

    A matching symlink, a symlinked subdirectory of a walked tree, and a bound hit go to ``unsafe`` instead.
    """
    found: list[Path] = []
    stack: list[tuple[Path, int]] = [(base / folder, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            unsafe[current.relative_to(base).as_posix()] = "is an unreadable directory"
            continue
        for entry in entries:
            rel = entry.relative_to(base).as_posix()
            matches = any(entry.match(p) for p in patterns)
            if entry.is_symlink():
                if matches or (recursive and entry.is_dir()):
                    unsafe[rel] = "is a symlinked directory" if entry.is_dir() else "is a symlink"
            elif entry.is_dir():
                if recursive and depth + 1 >= SCAN_MAX_DEPTH:
                    unsafe[rel] = f"is nested deeper than the {SCAN_MAX_DEPTH}-level scan bound"
                elif recursive:
                    stack.append((entry, depth + 1))
            elif matches:
                found.append(entry)
                if len(found) > SCAN_MAX_FILES:
                    unsafe[folder] = f"holds more than {SCAN_MAX_FILES} config files (the scan bound)"
                    return found[:SCAN_MAX_FILES]
    return found


def scan_launching_configs(root: str | Path) -> ConfigScan:
    """Scan every known MCP config location for files that launch ``agentteams_wasm`` (R1 truth source).

    JSON (JSONC comments allowed) and TOML are parsed and matched by the strings under their ``command`` / ``cmd`` /
    ``args`` keys, so an escape cannot hide the server's path; YAML (Goose recipes: ``*.yaml``/``*.yml``/``*.json`` in
    nested dirs, a bounded walk) and Markdown front matter are decoded (escaped line breaks, ``\\x``/``\\u``/``\\U``)
    and searched whole, a superset of their ``cmd``/``args`` scalars. Nothing is skipped silently: a
    symlinked file or directory, a non-regular, unparseable, undecodable or oversized file, and a tree past the
    bounds all land in ``unsafe``.

    Args:
        root: The project root.

    Returns:
        The :class:`ConfigScan`, each part sorted.

    Raises:
        Nothing.
    """
    base = Path(root)
    launching: set[str] = set()
    unsafe: dict[str, str] = {}
    for folder, patterns, recursive in _SCAN:
        linked = _symlinked_component(base, folder)
        if linked is not None:
            unsafe.setdefault(linked, "is a symlinked directory")
            continue
        if not (base / folder).is_dir():
            continue
        for path in _candidates(base, folder, patterns, recursive, unsafe):
            rel = path.relative_to(base).as_posix()
            try:
                if _launches(rel, _read_config(path)):
                    launching.add(rel)
            except (OSError, ValueError) as exc:   # UnicodeDecodeError and JSON errors are ValueErrors
                unsafe[rel] = f"is unreadable or does not parse ({type(exc).__name__})"
    return ConfigScan(tuple(sorted(launching)), tuple(sorted(unsafe.items())))


def launching_config_paths(root: str | Path) -> tuple[str, ...]:
    """Project-relative paths of every config on disk that launches ``agentteams_wasm``, or could not be checked.

    The union of :func:`scan_launching_configs`'s ``launching`` and ``unsafe`` paths (fail closed: an unreadable or
    symlinked config is a protection target, never skipped). A checker unions this with the manifest's
    ``protected_config_paths``, so a file the manifest does not list (hand-merged ``.mcp.json``, a manifest-less
    ``--check-wiring``) still has to be write-protected; :func:`unprotected_config_problems` reports the ``unsafe``
    ones whatever the denies say.

    Args:
        root: The project root.

    Returns:
        The paths, sorted.

    Raises:
        Nothing.
    """
    scan = scan_launching_configs(root)
    return tuple(sorted({*scan.launching, *(p for p, _ in scan.unsafe)}))


def unprotected_config_problems(root: str | Path, off_reason: str | None = None,
                                scan: ConfigScan | None = None) -> list[str]:
    """The scan's hits that are not protected whatever the live denies say.

    Every ``unsafe`` path (it cannot be checked); and, when ``off_reason`` is given (WASM is off, or this framework
    wires nothing, so no R1 deny was emitted for it), every launching file.

    Args:
        root: The project root.
        off_reason: Why nothing protects a launching file here, or None (WASM on: the denies are checked instead).
        scan: A scan already taken of ``root``, or None to take one.

    Returns:
        One message per path.

    Raises:
        Nothing.
    """
    scan = scan if scan is not None else scan_launching_configs(root)
    problems = [f"{p} {why}, so it may launch agentteams_wasm unchecked: not protected (R1); replace it with a "
                "regular, parseable file" for p, why in scan.unsafe]
    if off_reason:
        problems += [f"{p} launches agentteams_wasm, but {off_reason}, so nothing write-protects it (R1); remove the "
                     "entry or render the team with --wasm-sandbox on" for p in scan.launching]
    return problems


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
    """``--check-wiring`` problems for the WASM wiring, whatever the WASM switch.

    Where this framework installs the server: P10's live checks (``wasm_mcp.wiring_problems``, which with WASM off
    reports every launching config on disk). Elsewhere (WASM on but nothing installed here): every launching or
    uncheckable config on disk, since no deny was emitted for it (@security C1).

    Args:
        root: The project root.
        framework: The framework id.
        manifest: The team manifest.

    Returns:
        One message per problem; empty when nothing on disk launches the server and the wiring is sound.

    Raises:
        ValueError: As :func:`wasm_on`.
    """
    from agentteams import wasm_mcp

    row = wiring_row(manifest, framework)
    if row is None or _installs(row, framework):
        return wasm_mcp.wiring_problems(root, framework, manifest)
    return unprotected_config_problems(root, f"{framework} does not wire agentteams_wasm ({row['status']})")


__all__ = [
    "CLAUDE_EXAMPLE",
    "CODEX_CONFIG",
    "COPILOT_CLI_EXAMPLE",
    "GROK_EXAMPLE",
    "ConfigScan",
    "LAUNCH_MARKER",
    "PER_AGENT_DIRS",
    "SCAN_MAX_BYTES",
    "SCAN_MAX_DEPTH",
    "SCAN_MAX_FILES",
    "VSCODE_EXAMPLE",
    "WIRING_KEY",
    "agents_dir_problem",
    "check_wiring_problems",
    "codex_first_party_tables",
    "grok_kept_tables",
    "covered",
    "install_binary",
    "inert_grants",
    "install_root",
    "launching_config_paths",
    "output_files",
    "print_report",
    "report_line",
    "scan_launching_configs",
    "unhonoured_grants",
    "unprotected_config_problems",
    "wasm_agent_entry",
    "wasm_on",
    "wiring_row",
]
