"""MCP server-definition emission (opt-in, inert).

Emits ``.claude/mcp-servers.agentteams.json`` — a list of MCP server
definitions (each conforming to ``schemas/mcp-server.schema.json``). This file
is the **inert definition artifact** from the report (§5.4/§6.1): it documents
the servers a team intends to use and provisions NOTHING. It is not a live
host config (it is deliberately NOT named ``.mcp.json``, the name Claude Code
auto-loads); agentteams writes no credentials and enrolls no server.
Credentialed *activation* — writing a real ``.mcp.json`` that wires secrets to
a network boundary, and wiring this emitter into the build pipeline — is a
separate, operator-authorized step that is intentionally NOT done here.

Gating mirrors the live host-feature consumers in ``bridge.py`` (literal
membership tests), not ``host_features.is_enabled`` which no emitter calls.
Both the direct ``claude:mcp`` token and the canonical bridge
``bridge:copilot-vscode-to-claude:mcp`` token enable emission (report §6.2).

Server entries are written **verbatim and schema-conformant** — the
authorization annotation lives in a sibling ``activation_status`` map, never
inside the server object (which is ``additionalProperties:false``). Inertness
is *enforced*, not assumed: each server is checked for non-dict shape and for
an inline-secret-shaped ``credential_ref`` before being written; failures are
routed to ``result.errors`` and skipped. The emitter never silently activates
anything — it reports which servers still require operator authorization.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_MCP_FEATURE_TOKENS = (
    "claude:mcp",
    "bridge:copilot-vscode-to-claude:mcp",
)

_MANAGED_NOTICE = (
    "Inert MCP server definitions emitted by agentteams. This file provisions "
    "NOTHING: it is documentation/configuration only, contains no secrets, and "
    "is not a live host config (never rename it to .mcp.json). Servers listed "
    "true in activation_status need explicit operator security authorization "
    "before any credentialed activation. See "
    "references/mcp-auto-detection-report.md."
)

# Anything that is NOT exactly first-party / read|write fails closed (requires
# authorization), matching the fail-closed posture in mcp_detect.py.
_FIRST_PARTY = "first-party"
_SAFE_SIDE_EFFECTS = frozenset({"read", "write"})
# credential_ref must be an identifier/key shape — mirrors the schema pattern;
# a raw 'scheme://user:pass@host' connection string must NOT pass.
_CREDENTIAL_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]*$")
_ALLOWED_AUTH_KEYS = frozenset({"mechanism", "credential_ref", "url"})


@dataclass
class MCPEmissionResult:
    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    gated_off: bool = False
    activation_blocked: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


def mcp_enabled(features: list[str]) -> bool:
    """True iff an MCP host-feature token is active (direct or bridge)."""
    feats = set(features or [])
    return any(tok in feats for tok in _MCP_FEATURE_TOKENS)


def _requires_authorization(server: dict[str, Any]) -> bool:
    """Fail-closed: a server needs operator sign-off before activation unless it
    is unambiguously first-party with only read/write tools and no explicit
    review flag. Unknown/missing trust or side-effect values count as blocked."""
    if server.get("trust_tier") != _FIRST_PARTY:
        return True
    if (server.get("security_review") or {}).get("required") is True:
        return True
    tools = server.get("tools")
    if not isinstance(tools, list):
        return True
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("side_effects") not in _SAFE_SIDE_EFFECTS:
            return True
    return False


_VALIDATOR_CACHE: list[Any] = []  # [validator] or [None] once resolved


def _schema_validator() -> Any | None:
    """Lazily load a Draft7 validator for mcp-server.schema.json.

    Returns None (and is cached) when jsonschema or the schema file is
    unavailable, so a minimal environment falls back to the lightweight inert
    checks rather than crashing.
    """
    if _VALIDATOR_CACHE:
        return _VALIDATOR_CACHE[0]
    validator: Any | None = None
    try:
        from jsonschema import Draft7Validator

        schema_path = Path(__file__).resolve().parent.parent / "schemas" / "mcp-server.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = Draft7Validator(schema)
    except (OSError, ValueError):
        # CH-24: bundled schema unreadable / invalid JSON -> degrade to no validation.
        validator = None
    _VALIDATOR_CACHE.append(validator)
    return validator


def _inert_problems(server: Any) -> list[str]:
    """Return reasons this entry is not safe to write as an inert definition.

    Combines a lightweight always-on inline-secret/shape check with full
    schema validation when jsonschema is available.
    """
    problems: list[str] = []
    if not isinstance(server, dict):
        return [f"entry is not an object ({type(server).__name__})"]
    if not server.get("server_id"):
        problems.append("missing server_id")
    auth = server.get("auth")
    if auth is not None:
        if not isinstance(auth, dict):
            problems.append("auth is not an object")
        else:
            extra = set(auth) - _ALLOWED_AUTH_KEYS
            if extra:
                problems.append(f"auth has unexpected keys {sorted(extra)}")
            ref = auth.get("credential_ref")
            if ref is not None and (
                not isinstance(ref, str)
                or "://" in ref
                or not _CREDENTIAL_REF_RE.match(ref)
            ):
                problems.append(
                    "credential_ref looks like an inline secret, not a reference"
                )
    validator = _schema_validator()
    if validator is not None:
        for err in sorted(validator.iter_errors(server), key=lambda e: list(e.path)):
            loc = "/".join(str(p) for p in err.path) or "<root>"
            problems.append(f"schema: {loc}: {err.message}")
    return problems


def emit_mcp_artifact(
    *,
    servers: list[dict[str, Any]],
    features: list[str],
    output_root: Path,
    dry_run: bool = False,
    overwrite: bool = False,
) -> MCPEmissionResult:
    """Write ``.claude/mcp-servers.agentteams.json`` when MCP is enabled.

    ``servers`` is a list of dicts each conforming to mcp-server.schema.json.
    No-op (``gated_off``) when no MCP host-feature token is active. No-op when
    ``servers`` is empty. Non-conforming entries are skipped into
    ``result.errors``. ``overwrite`` defaults to False because the emitted file
    carries operator authorization records (security_review.authorized_by/at)
    that must not be silently clobbered on re-run.
    """
    result = MCPEmissionResult()
    if not mcp_enabled(features):
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

    if not valid:
        return result

    out_path = Path(output_root) / ".claude" / "mcp-servers.agentteams.json"
    if out_path.exists() and not overwrite:
        result.skipped.append(str(out_path))
        return result

    activation_status = {s["server_id"]: _requires_authorization(s) for s in valid}
    result.activation_blocked = sorted(sid for sid, blocked in activation_status.items() if blocked)

    payload = {
        "_agentteams_managed": _MANAGED_NOTICE,
        "schema_version": "1.0",
        "servers": valid,
        "activation_status": activation_status,
    }
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if not dry_run:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
    result.written.append(str(out_path))
    return result


__all__ = [
    "MCPEmissionResult",
    "mcp_enabled",
    "emit_mcp_artifact",
]
