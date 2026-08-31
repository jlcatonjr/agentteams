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

CALIBRATION (2026-08-10 adversarial finding, step E.9): Goose's bar is
justified relative to a specific fact — every Goose agent defaults to the
``developer`` builtin extension (unconditional local shell, always on,
goose.py's own docstring). Borrowing that same threshold for Codex without
checking Codex's own baseline would be an unverified analogy, not evidence.
Checked via live web search (2026-08-10, not assumed from training
knowledge): Codex's own default posture is markedly *more* conservative than
Goose's, not less — ``suggest`` is the default approval mode, every action
requires explicit operator approval before execution, ``sandbox_mode``
defaults to no network access with filesystem writes confined to the active
workspace, and this is a separate, independent runtime gate Codex applies at
tool-call time regardless of what ``config.toml`` declares. Writing a
first-party/read-only server into ``[mcp_servers.*]`` therefore does not, by
itself, grant Codex any capability its own default sandbox/approval layer
wouldn't still gate — the borrowed bar is not under-calibrated in the
direction originally worried about (a server getting network reach Codex's
default posture wouldn't otherwise allow). Sources (X4, re-verified
2026-08-15 — the old developers.openai.com citation 308-redirects, so cited
directly at its current location): learn.chatgpt.com/docs/extend/mcp and
learn.chatgpt.com/docs/extend/agent-approvals-security.

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


def codex_mcp_enabled(features: list[str]) -> bool:
    """True iff the codex:mcp host-feature token is active."""
    return _CODEX_MCP_TOKEN in set(features or [])


def _codex_wirable(server: dict[str, Any]) -> bool:
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


def _codex_entry_for(server: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Map one mcp-server.schema.json entry to a Codex [mcp_servers.<id>] table.

    Returns (fields, None) when wirable, or (None, reason) when in-scope but
    not safely expressible — surfaced, never silently dropped.
    """
    if not _codex_wirable(server):
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
    start = text.find(_MANAGED_HEADER)
    if start == -1:
        return text
    end = text.find(_MANAGED_FOOTER, start)
    if end == -1:
        return text
    end += len(_MANAGED_FOOTER)
    return text[:start] + text[end:]


def _strip_unmanaged_mcp_tables(text: str) -> str:
    """Remove any [mcp_servers.*] tables left outside a managed block (e.g.
    hand-authored, or from a version of this module predating the managed-
    block marker) so a re-run replaces rather than duplicates them."""
    return _MCP_TABLE_BLOCK_RE.sub("", text)


def emit_codex_mcp_config(
    *,
    servers: list[dict[str, Any]],
    features: list[str],
    output_root: Path,
    dry_run: bool = False,
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

    Returns:
        CodexMCPEmissionResult.
    """
    result = CodexMCPEmissionResult()
    if not codex_mcp_enabled(features):
        result.gated_off = True
        return result
    if not servers:
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

    out_path = Path(output_root) / ".codex" / "config.toml"
    if not tables:
        return result

    existing_text = ""
    existing_parsed: dict[str, Any] = {}
    if out_path.is_file():
        existing_text = out_path.read_text(encoding="utf-8")
        try:
            existing_parsed = tomllib.loads(existing_text)
        except tomllib.TOMLDecodeError as exc:
            result.errors.append(f"existing {out_path} is not valid TOML, refusing to splice: {exc}")
            return result

    # Surface (never silently drop) any pre-existing [mcp_servers.<id>] table —
    # hand-authored or from a version of this module predating the managed-
    # block marker — that this run is about to replace. The content-
    # preservation check above deliberately excludes mcp_servers from
    # comparison (that's the one table this function exists to rewrite), so
    # without this, an operator's own manual entry could be lost with zero
    # signal — worse than the corruption case above since it's silent by
    # design, not a bug (2026-08-10 adversarial finding, step E.9).
    existing_server_ids = set((existing_parsed.get("mcp_servers") or {}).keys())
    this_run_ids = {name.split(".", 1)[1] for name in tables}
    result.dropped_unmanaged = sorted(existing_server_ids - this_run_ids)

    base_text = _strip_unmanaged_mcp_tables(_strip_managed_block(existing_text))
    if base_text and not base_text.endswith("\n\n"):
        base_text = base_text.rstrip("\n") + "\n\n" if base_text.strip() else ""
    rendered = _MANAGED_HEADER + render_tables(tables) + _MANAGED_FOOTER
    new_text = base_text + rendered

    # Content-preservation check (closes the 2026-08-10 @security HALT): the
    # line-scan splice above has no TOML string-quoting awareness, so verify
    # its actual effect rather than trust its intent. Every key outside
    # mcp_servers must be untouched, or refuse the write entirely.
    try:
        new_parsed = tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as exc:
        result.errors.append(
            f"splice produced invalid TOML, refusing to write {out_path}: {exc}"
        )
        return result
    existing_other = {k: v for k, v in existing_parsed.items() if k != "mcp_servers"}
    new_other = {k: v for k, v in new_parsed.items() if k != "mcp_servers"}
    if existing_other != new_other:
        result.errors.append(
            f"splice would alter content outside [mcp_servers.*] in {out_path} "
            "(likely a table-boundary-shaped line inside an existing string value "
            "confusing the line scan) — refusing to write"
        )
        return result

    if not dry_run:
        if out_path.is_file():
            _atomic_copy(out_path, out_path.with_name(out_path.name + ".bak"))
        _atomic_write_text(out_path, new_text)
    result.written.append(str(out_path))
    return result


__all__ = [
    "CodexMCPEmissionResult",
    "codex_mcp_enabled",
    "emit_codex_mcp_config",
]
