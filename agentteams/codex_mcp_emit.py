"""
codex_mcp_emit.py — MCP server emission into Codex's ``.codex/config.toml``
(open-items remediation OPEN-6). Verified against OpenAI's Codex MCP docs
(2026-08-10): servers live under ``[mcp_servers.<id>]`` tables; stdio servers
use ``command``/``args``/``env_vars`` (env_vars *forwards* named host env
vars — distinct from ``env``, which sets literal values); http servers use
``url``/``bearer_token_env_var``.

Unlike Claude's inert JSON sidecar (``mcp_emit.py``, never auto-loaded),
``.codex/config.toml`` is a real, live config Codex reads to launch MCP
servers — writing an entry here is activation-adjacent, not just
documentation. This module therefore uses Goose's stricter auto-wire bar
(first-party, every tool read-only, no security review required), not
Claude's more permissive inert-write bar; anything else is skipped and
surfaced, never silently activated (mirrors ``goose.py::_goose_wirable``).

CALIBRATION (2026-08-10 adversarial finding, step E.9; RE-VERIFIED 2026-09-29 against
learn.chatgpt.com/docs/agent-approvals-security.md and .../extend/mcp.md). The 2026-08-10
premise — "``suggest`` is the default approval mode, every action requires explicit
approval" — no longer holds and is withdrawn. Current Codex vocabulary: ``approval_policy`` is
``on-request``, ``never`` or ``{ granular = {...} }``; ``untrusted`` is retired (the docs carry a
migration section). A version-controlled folder starts in the ``Auto`` preset
(``workspace-write`` sandbox + ``on-request``), in which commands the sandbox allows run WITHOUT
approval; the default ``workspace-write`` sandbox still has no network and confines writes to the
workspace. Codex's own gate therefore no longer guarantees a prompt per action, so this module
restores that posture explicitly for what it wires: every emitted server carries
``default_tools_approval_mode = "prompt"`` (supported values ``auto``/``prompt``/``writes``/
``approve``; per-tool overrides live under ``[mcp_servers.<id>.tools.<tool>]`` with
``approval_mode``, which this module does not emit — an operator may relax a specific tool).
The first-party/read-only wiring bar (``_codex_wirable``) is unchanged. No model or provider
keys are emitted (baseAgent's 2026-09-29 research reports Codex ignores provider keys in a
project ``.codex/config.toml``; not independently verified here — the config docs only confirm
that an untrusted project's ``.codex/`` layers are ignored altogether).

PROJECT TRUST GATE (X2, documented 2026-08-15): project-level
``.codex/config.toml`` — the file this module splices into — is loaded by
Codex **only when the project is marked trusted**; layering is system
defaults -> ``~/.codex/config.toml`` -> project ``.codex/config.toml``
(trusted only) -> CLI overrides. An untrusted project's splice is therefore
written to disk correctly but silently inert until the operator trusts the
project — this module has no visibility into (and does not attempt to
change) that trust state; it is a Codex-side gate, not one this codebase
enforces or can query.

``.codex/config.toml`` is a shared, multi-purpose file (sandbox/profile
settings live alongside ``[mcp_servers.*]``) that cannot be blind-overwritten.
No comment-preserving TOML library exists (stdlib ``tomllib`` is read-only
and drops comments/formatting on parse), so this module never parses-and-
reserializes the whole file — it splices at the TEXT level: locate and
remove any existing ``[mcp_servers.*]`` table blocks by line-scanning the
raw text, then append freshly-rendered ones. Everything else in the file
(comments, unrelated tables, formatting) is intended to survive unchanged.
This mirrors this project's own house style for surgical text replacement
(``agentteams/fences.py``'s fenced-region merge, ``yaml_frontmatter.py``'s
line-anchored boundary scan) rather than a general-purpose round-trip.

SECURITY (2026-08-10 @security HALT, resolved same session): the line-scan
regex has no awareness of TOML multi-line string quoting (``\"\"\"``/``'''``)
— a hand-authored file whose *content* (e.g. a string value) contains a line
that merely looks like a table header could make the regex under- or
over-match, silently corrupting or deleting unrelated content. Rather than
hand-roll a string-context-aware scanner (itself a source of new edge-case
bugs), this module verifies the splice's actual effect instead of trusting
the regex's intent: after rendering, both the pre-splice and post-splice
text are parsed with ``tomllib`` and compared key-for-key outside
``mcp_servers`` — any divergence refuses the write entirely (``result.errors``,
file untouched) rather than risking silent data loss. A single-generation
backup of the pre-existing file is also written before any splice, since
this file sits outside ``backup.py``'s ``<output_dir>/.agentteams-backups/``
reach (it lives at the project root, not under a generated-team output dir).
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agentteams.atomicio import _atomic_copy, _atomic_write_text
from agentteams.mcp_emit import _inert_problems
from agentteams.toml_write import render_tables

_CODEX_MCP_TOKEN = "codex:mcp"

_MANAGED_HEADER = (
    "# --- BEGIN agentteams-managed mcp_servers (do not hand-edit this block; "
    "re-running agentteams will replace it) ---\n"
)
_MANAGED_FOOTER = (
    "# --- END agentteams-managed mcp_servers ---\n"
)

# Matches a `[mcp_servers.<anything>]` table header line, non-greedy up to
# the next top-level `[...]` header or end of file — used to strip prior
# agentteams-written (or hand-authored) mcp_servers blocks before splicing
# in a fresh render, so re-runs replace rather than accumulate.
_MCP_TABLE_BLOCK_RE = re.compile(
    r"^\[mcp_servers\.[^\]]*\]\n(?:(?!^\[)[^\n]*\n?)*",
    re.MULTILINE,
)


@dataclass
class CodexMCPEmissionResult:
    written: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    gated_off: bool = False
    wired: list[str] = field(default_factory=list)
    not_wired: dict[str, str] = field(default_factory=dict)
    dropped_unmanaged: list[str] = field(default_factory=list)
    #: security-relevant keys already in a pre-existing config.toml that this splice carries forward (#11)
    preserved_security_keys: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


#: Codex per-server MCP tool approval (``auto`` | ``prompt`` | ``writes`` | ``approve``, per
#: learn.chatgpt.com/docs/extend/mcp.md, verified 2026-09-29). ``prompt`` = every tool call asks.
_DEFAULT_TOOLS_APPROVAL_MODE = "prompt"

#: Opt-in token (WP-C) that sets ``agentteams_wasm`` alone to ``auto``: its only tool, ``run_wasm``, is compute-only
#: (no host writes outside the guest's scratch, no network), so the prompt is the cost being removed.
#: Operator-declared servers never get ``auto``. Turning it on is the Rule 15 ``mcp-approval-auto:codex`` relaxation:
#: in a governed workspace the render-time gate (``cli/sandbox_relaxation_gate``) needs the operator's
#: Ed25519-signed decision for it (WP-C2).
_CODEX_WASM_AUTO_APPROVE_TOKEN = "codex:wasm-auto-approve"
_FIRST_PARTY_AUTO_APPROVAL_MODE = "auto"


def first_party_approval_mode(features: list[str] | None) -> str:
    """The ``default_tools_approval_mode`` the ``agentteams_wasm`` table must carry for ``features``.

    Args:
        features: The manifest's ``host_features`` tokens.

    Returns:
        ``"auto"`` when ``codex:wasm-auto-approve`` is active, else ``"prompt"`` (the default for every server).

    Raises:
        Nothing.
    """
    return (_FIRST_PARTY_AUTO_APPROVAL_MODE if _CODEX_WASM_AUTO_APPROVE_TOKEN in set(features or [])
            else _DEFAULT_TOOLS_APPROVAL_MODE)


def codex_mcp_enabled(features: list[str]) -> bool:
    """True iff the codex:mcp host-feature token is active."""
    return _CODEX_MCP_TOKEN in set(features or [])


def mcp_wirable(server: dict[str, Any]) -> bool:
    """Stricter-than-inert bar: safe to make Codex actually launch this server.

    first-party AND every tool read-only AND no security review required —
    identical bar to goose.py::_goose_wirable, since both targets write a
    live, auto-loaded config rather than an inert sidecar.
    """
    if server.get("trust_tier") != "first-party":
        return False
    if (server.get("security_review") or {}).get("required") is True:
        return False
    tools = server.get("tools")
    if not isinstance(tools, list) or not tools:
        return False
    return all(isinstance(t, dict) and t.get("side_effects") == "read" for t in tools)


#: Pre-2026-10-14 name, kept for existing importers.
_codex_wirable = mcp_wirable


def _codex_entry_for(server: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Map one mcp-server.schema.json entry to a Codex [mcp_servers.<id>] table.

    Every emitted table carries ``default_tools_approval_mode = "prompt"`` (see the module
    docstring's CALIBRATION): each tool call from an agentteams-wired server asks first.

    Returns (fields, None) when wirable, or (None, reason) when in-scope but
    not safely expressible — surfaced, never silently dropped.
    """
    fields, reason = _codex_transport_fields(server)
    if fields is not None:
        fields["default_tools_approval_mode"] = _DEFAULT_TOOLS_APPROVAL_MODE
    return fields, reason


def _codex_transport_fields(server: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Transport/auth fields for one server (the body of :func:`_codex_entry_for`)."""
    if not mcp_wirable(server):
        return None, "needs operator authorization (not first-party read-only)"

    auth = server.get("auth") or {}
    mechanism = auth.get("mechanism", "none")
    transport = server.get("transport")

    if transport == "stdio":
        command = server.get("command")
        if not command:
            return None, "stdio server has no 'command' to launch"
        fields: dict[str, Any] = {"command": str(command)}
        args = [str(a) for a in (server.get("args") or [])]
        if args:
            fields["args"] = args
        if mechanism == "env":
            ref = auth.get("credential_ref")
            if ref:
                fields["env_vars"] = [str(ref)]
        elif mechanism in ("secret-store", "oauth"):
            return None, f"credential mechanism '{mechanism}' not expressible for a stdio Codex server"
        return fields, None

    if transport == "http":
        uri = auth.get("url")
        if not uri:
            return None, "http server has no auth.url endpoint"
        fields = {"url": str(uri)}
        if mechanism == "env":
            ref = auth.get("credential_ref")
            if ref:
                fields["bearer_token_env_var"] = str(ref)
        elif mechanism in ("secret-store", "oauth"):
            return None, f"credential mechanism '{mechanism}' not expressible for an http Codex server"
        return fields, None

    return None, f"unknown transport {transport!r}"


def _strip_managed_block(text: str) -> str:
    """Remove a prior agentteams-managed mcp_servers block, if present."""
    return strip_marked_block(text, _MANAGED_HEADER, _MANAGED_FOOTER)


def strip_unmanaged_mcp_tables(text: str, keep: Collection[str] = ()) -> str:
    """Remove any [mcp_servers.*] tables left outside a managed block (e.g.
    hand-authored, or from a version of this module predating the managed-
    block marker) so a re-run replaces rather than duplicates them.

    Args:
        text: The config text outside the managed block.
        keep: Server ids whose table survives, matched only on a bare ``[mcp_servers.<id>]`` header line (a quoted
            or sub-table spelling is still stripped; callers re-check what survived).

    Returns:
        *text* without the stripped tables.

    Raises:
        Nothing.
    """
    kept_headers = {f"[mcp_servers.{sid}]" for sid in keep}
    return _MCP_TABLE_BLOCK_RE.sub(
        lambda m: m.group(0) if m.group(0).split("\n", 1)[0].strip() in kept_headers else "", text)


@dataclass
class TomlSplice:
    """Outcome of :func:`splice_managed_toml`."""

    error: str | None = None
    existing: dict[str, Any] = field(default_factory=dict)
    new_text: str = ""
    #: True when a pre-existing file parsed as TOML (False when absent or unparseable).
    existing_parsed: bool = False


def splice_managed_toml(
    out_path: Path,
    block_body: str,
    *,
    owned_keys: tuple[str, ...],
    header: str,
    footer: str,
    strip_unmanaged: Callable[[str], str] | None = None,
    dry_run: bool = False,
) -> TomlSplice:
    """Replace one agentteams-managed block in a TOML config, leaving every other key untouched (or refusing).

    Shared by the Codex (``.codex/config.toml``) and Grok (``.grok/config.toml``) MCP emitters. The prior
    managed block (and, via *strip_unmanaged*, stray copies of the owned tables) is removed, the fresh block is
    appended, and the result is re-parsed: every top-level key outside *owned_keys* must be identical to the
    original, or nothing is written (closes the 2026-08-10 @security HALT on the quoting-unaware line splice).

    Args:
        out_path: The config file (may not exist yet).
        block_body: The rendered TOML for the owned tables (no markers).
        owned_keys: Top-level keys the block owns; excluded from the preservation comparison.
        header: The managed block's opening marker line.
        footer: The managed block's closing marker line.
        strip_unmanaged: Optional ``text -> text`` removing unmanaged copies of the owned tables.
        dry_run: When True, compute without writing.

    Returns:
        :class:`TomlSplice` with ``error`` set when the write was refused.

    Raises:
        OSError: The file cannot be read or written.
    """
    existing_text = ""
    existing: dict[str, Any] = {}
    if out_path.is_file():
        existing_text = out_path.read_text(encoding="utf-8")
        try:
            existing = tomllib.loads(existing_text)
        except tomllib.TOMLDecodeError as exc:
            return TomlSplice(error=f"existing {out_path} is not valid TOML, refusing to splice: {exc}")
    parsed = out_path.is_file()
    base_text = strip_marked_block(existing_text, header, footer)
    if strip_unmanaged is not None:
        base_text = strip_unmanaged(base_text)
    if base_text and not base_text.endswith("\n\n"):
        base_text = base_text.rstrip("\n") + "\n\n" if base_text.strip() else ""
    new_text = base_text + header + block_body + footer
    try:
        new_parsed = tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as exc:
        return TomlSplice(error=f"splice produced invalid TOML, refusing to write {out_path}: {exc}", existing=existing,
                          existing_parsed=parsed)
    if ({k: v for k, v in existing.items() if k not in owned_keys}
            != {k: v for k, v in new_parsed.items() if k not in owned_keys}):
        owned = ", ".join(f"[{k}]" for k in owned_keys)
        return TomlSplice(
            error=(f"splice would alter content outside {owned} in {out_path} (likely a table-boundary-shaped "
                   "line inside an existing string value confusing the line scan) — refusing to write"),
            existing=existing, existing_parsed=parsed,
        )
    if not dry_run:
        if out_path.is_file():
            _atomic_copy(out_path, out_path.with_name(out_path.name + ".bak"))
        _atomic_write_text(out_path, new_text)
    return TomlSplice(existing=existing, new_text=new_text, existing_parsed=parsed)


def strip_marked_block(text: str, header: str, footer: str) -> str:
    """Remove one marker-delimited managed block, if present.

    Args:
        text: The file text.
        header: The block's opening marker line.
        footer: The block's closing marker line.

    Returns:
        *text* without the block (unchanged when the markers are absent or unbalanced).

    Raises:
        Nothing.
    """
    start = text.find(header)
    if start == -1:
        return text
    end = text.find(footer, start)
    if end == -1:
        return text
    return text[:start] + text[end + len(footer):]


#: The only first-party server ids ``first_party_tables`` may carry, and the exact keys of each table.
_FIRST_PARTY_IDS = ("agentteams_wasm",)
_FIRST_PARTY_KEYS = frozenset({"command", "args", "default_tools_approval_mode"})


def _first_party_problems(tables: dict[str, dict[str, Any]],
                          approval_mode: str = _DEFAULT_TOOLS_APPROVAL_MODE) -> list[str]:
    """Why ``first_party_tables`` is not exactly agentteams' canonical shared ``agentteams_wasm`` table (or ``[]``).

    Bound for the carve-out: only ``mcp_servers.agentteams_wasm``; keys exactly ``command``/``args``/
    ``default_tools_approval_mode``; approval exactly ``approval_mode`` (``prompt``, or ``auto`` under the opt-in
    ``codex:wasm-auto-approve`` token; any other value, either way, is refused); ``command``/``args`` exactly a compute-only
    ``--agent _session`` launch of the installed server (``wasm_mcp.is_session_launch``, shared with Grok's
    ``keep_if_exact``). An element-wise rebuild, so ``--read=/``, an argparse prefix (``--rea``), a second
    ``--agent`` or an interpreter outside ``runner_mcp.SYSTEM_PYTHONS`` are all refused.

    Args:
        tables: ``first_party_tables`` as passed to :func:`emit_codex_mcp_config`.
        approval_mode: The required value, from :func:`first_party_approval_mode`.

    Returns:
        One problem string per offending table; ``[]`` when every table is the canonical one.

    Raises:
        Nothing.
    """
    from agentteams import wasm_mcp

    problems: list[str] = []
    for name, fields in tables.items():
        sid = name.split(".", 1)[1] if name.startswith("mcp_servers.") else ""
        if sid not in _FIRST_PARTY_IDS:
            problems.append(f"first_party_tables: {name!r} is not an agentteams first-party server")
            continue
        if (not isinstance(fields, dict) or set(fields) != _FIRST_PARTY_KEYS
                or fields.get("default_tools_approval_mode") != approval_mode
                or not wasm_mcp.is_session_launch(fields.get("command"), fields.get("args"))):
            problems.append(f"first_party_tables: {name} is not the canonical compute-only agentteams_wasm table")
    return problems


def _existing_mcp_ids(path: Path) -> list[str]:
    """The ``[mcp_servers.*]`` ids an existing ``config.toml`` defines (``[]`` when absent or unparsable)."""
    import tomllib

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    servers = data.get("mcp_servers")
    return list(servers) if isinstance(servers, dict) else []


def emit_codex_mcp_config(
    *,
    servers: list[dict[str, Any]],
    features: list[str],
    output_root: Path,
    dry_run: bool = False,
    first_party_tables: dict[str, dict[str, Any]] | None = None,
) -> CodexMCPEmissionResult:
    """Splice wirable MCP servers into ``.codex/config.toml``'s ``[mcp_servers.*]``
    tables. Every other line is intended to survive unchanged; a
    content-preservation check verifies this held before writing and refuses
    the write (``result.errors``) rather than risk silent data loss if not.

    Args:
        servers: mcp-server.schema.json-conformant server definitions.
        features: Active host-feature tokens; no-ops (``gated_off``) unless
            ``codex:mcp`` is present.
        output_root: Project root; writes to ``output_root/.codex/config.toml``.
        dry_run: When True, compute the result without writing.
        first_party_tables: agentteams' own servers (WASM plan P11: ``{"mcp_servers.agentteams_wasm": ...}`` from
            ``frameworks._wasm_wiring.codex_first_party_tables``), merged after the wirable-bar loop. They bypass
            ``mcp_wirable`` and the ``codex:mcp`` gate (turning WASM on is the opt-in), so
            :func:`_first_party_problems` bounds them to the exact canonical shape (Rule-12 carve-out 3). Without
            ``codex:mcp`` an existing config's other ``[mcp_servers.*]`` tables refuse the write rather than be
            dropped (carve-out 4).

    Returns:
        CodexMCPEmissionResult.

    Raises:
        ValueError: A ``first_party_tables`` entry that is not the canonical ``agentteams_wasm`` session table
            (including an approval mode other than :func:`first_party_approval_mode` for ``features``).
    """
    result = CodexMCPEmissionResult()
    first_party = dict(first_party_tables or {})
    problems = _first_party_problems(first_party, first_party_approval_mode(features))
    if problems:
        raise ValueError("; ".join(problems))
    if not codex_mcp_enabled(features):
        if not first_party:
            result.gated_off = True
            return result
        servers = []  # codex:mcp off: no operator server is wired, only agentteams' own
    if not servers and not first_party:
        return result

    valid: list[dict[str, Any]] = []
    for s in servers:
        problems = _inert_problems(s)
        if problems:
            sid = s.get("server_id", "<unknown>") if isinstance(s, dict) else "<non-object>"
            result.errors.append(f"{sid}: {'; '.join(problems)}")
            continue
        valid.append(s)

    tables: dict[str, dict[str, Any]] = {}
    for s in valid:
        sid = s["server_id"]
        fields, reason = _codex_entry_for(s)
        if fields is None:
            result.not_wired[sid] = reason or "not expressible"
            continue
        tables[f"mcp_servers.{sid}"] = fields
        result.wired.append(sid)

    tables.update(first_party)
    result.wired.extend(name.split(".", 1)[1] for name in first_party)
    out_path = Path(output_root) / ".codex" / "config.toml"
    if not tables:
        return result
    if first_party and not codex_mcp_enabled(features):
        others = sorted(set(_existing_mcp_ids(out_path)) - {n.split(".", 1)[1] for n in tables})
        if others:
            result.errors.append(f"{out_path} already defines [mcp_servers.*] {', '.join(others)}; wiring "
                                 "agentteams_wasm without codex:mcp would drop them, so nothing was written (declare "
                                 "them in mcp_servers[] with codex:mcp, or remove them)")
            return result

    from agentteams.team_dir_advisories import codex_config_security_keys

    # #11: read BEFORE the splice rewrites the file, so the keys reported are the ones carried forward.
    security_keys = [k for k in codex_config_security_keys(Path(output_root)) if k not in ("[mcp_servers]", "<symlink>")]
    splice = splice_managed_toml(
        out_path, render_tables(tables), owned_keys=("mcp_servers",), header=_MANAGED_HEADER,
        footer=_MANAGED_FOOTER, strip_unmanaged=strip_unmanaged_mcp_tables, dry_run=dry_run,
    )
    if splice.existing_parsed:
        result.preserved_security_keys = security_keys
    # Surface (never silently drop) any pre-existing [mcp_servers.<id>] table — hand-authored or from a
    # version of this module predating the managed-block marker — that this run replaces. The
    # content-preservation check deliberately excludes mcp_servers (the one table this function rewrites),
    # so without this an operator's own manual entry could be lost with zero signal (2026-08-10 adversarial
    # finding, step E.9).
    this_run_ids = {name.split(".", 1)[1] for name in tables}
    result.dropped_unmanaged = sorted(set((splice.existing.get("mcp_servers") or {}).keys()) - this_run_ids)
    if splice.error:
        result.errors.append(splice.error)
        return result
    result.written.append(str(out_path))
    return result


__all__ = [
    "CodexMCPEmissionResult",
    "TomlSplice",
    "codex_mcp_enabled",
    "emit_codex_mcp_config",
    "mcp_wirable",
    "splice_managed_toml",
    "strip_marked_block",
    "strip_unmanaged_mcp_tables",
]

#: Pre-2026-10-14 private name, kept for existing importers.
_strip_unmanaged_mcp_tables = strip_unmanaged_mcp_tables
