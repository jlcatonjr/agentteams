"""interop_grant_check.py — what an ``--interop-from`` projection may emit, checked per agent.

A projection (``interop.run_interop``) re-renders each source agent for another surface. Before this module, the
rendered grant was never compared with the source's, and three things went wrong on 2026-10-13 (plan
``tmp/by-week/2026-W42/projection-fixes.plan.md``, Revision 1):

* **goose** recipes got the whole ``developer`` builtin (shell, write, edit) for a ``Read, Grep, Glob`` agent;
* **Copilot** adds ``tools: ['read', 'edit', 'search']`` to a header that has none, whatever the source declared;
* **Codex** and **AGENTS.md** cannot enforce a grant at all, and nothing said so.

So every projected agent is now checked after rendering and before anything is written:

1. **No widening.** The rendered file is re-read the way that surface reads it (:func:`projected_scopes`), and its
   canonical tool scopes must be a subset of the source agent's. ``read`` and ``search`` count as one read-only
   class here: every surface that grants one exposes the other's data (goose's ``agentteams_readfs`` serves both).
2. **Enforcement class.** Each surface is :data:`ENFORCED`, :data:`DECLARED` or :data:`NONE`
   (:func:`enforcement_class`). An agent whose grant is narrower than what the surface gives by default
   (:data:`SURFACE_DEFAULT`) is refused on a declared or none surface, unless the operator names it in
   ``--interop-allow-cooperative``; it then carries a "grant is cooperative on this surface" banner.
3. **Copilot size.** A Copilot body over the platform's 30,000-character limit is refused on this path (native
   generation only warns: ``copilot_vscode._check_body_length``).

Bespoke tool names (``runCommands``, MCP tool names, Claude tools outside the canonical map) are not compared: they
have no canonical meaning, and a raw tools string is only ever carried to a surface of the same vocabulary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from agentteams import capability_map as _cm
from agentteams import write_policy as _write_policy
from agentteams.yaml_frontmatter import parse_yaml_front_matter

#: The surface enforces the declared grant (Claude ``tools``, Copilot ``tools``, a goose recipe run directly,
#: Codex under the ``write_policy: "orchestrator-only"`` role gate).
ENFORCED = "enforced"
#: The grant is written down but not enforced (Codex: ``sandbox_mode`` and the tool list are self-imposed).
DECLARED = "declared"
#: The surface has no grant channel at all (AGENTS.md).
NONE = "none"

_FULL: frozenset[str] = frozenset(_cm.CANONICAL_TOOL_SCOPES)
#: What a surface gives an agent that declares nothing: workspace read/search, edit and shell. An agent missing any
#: of them is narrower than the default.
SURFACE_DEFAULT: frozenset[str] = frozenset({"read", "edit", "search", "execute"})
_READ_CLASS: frozenset[str] = frozenset({"read", "search"})
_SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_TOOLS_LINE_RE = re.compile(r"^(?:tools|allowed-tools):[ \t]*(.*)$", re.MULTILINE)
_BANNER_LEAD = "> **Projection grant note (agentteams):**"
_BANNER_RE = re.compile(r"\n*" + re.escape(_BANNER_LEAD) + r"[^\n]*(?:\n>[^\n]*)*\n?")
_GOOSE_BANNER = (
    f"{_BANNER_LEAD} this recipe's `extensions` bind when Goose runs it directly (`goose run --recipe`). Run as a "
    "`delegate`, it gets the delegating session's extensions instead, narrowed only by the `extensions` argument the "
    "delegator passes. Stay within your declared tools either way."
)


@dataclass(frozen=True)
class ProjectionPolicy:
    """Per-run options for the projection checks; ``interop.run_interop`` builds one.

    Attributes:
        allow_cooperative: Slugs the operator lets project onto a surface that can't enforce their grant.
    """

    allow_cooperative: frozenset[str] = frozenset()


def parse_slug_list(value: str | Iterable[str] | None) -> frozenset[str]:
    """Parse ``--interop-allow-cooperative`` (``SLUG[,SLUG...]``, or an iterable of such strings).

    Args:
        value: The flag value(s), or ``None``.

    Returns:
        The slugs (empty for ``None``).

    Raises:
        ValueError: An entry is not a plain agent slug (there is no wildcard: each agent is named).
    """
    if value is None:
        return frozenset()
    parts = value.split(",") if isinstance(value, str) else [p for v in value for p in str(v).split(",")]
    slugs = frozenset(p.strip() for p in parts if p.strip())
    bad = sorted(s for s in slugs if not _SLUG_RE.fullmatch(s))
    if bad:
        raise ValueError(f"--interop-allow-cooperative: not an agent slug: {', '.join(bad)}")
    return slugs


def enforcement_class(framework: str, manifest: dict[str, Any]) -> str:
    """Return how far *framework* enforces a projected agent's grant.

    Args:
        framework: The target registry framework id.
        manifest: The interop manifest (Codex is enforced only under its role gate).

    Returns:
        :data:`ENFORCED`, :data:`DECLARED` or :data:`NONE`.

    Raises:
        Nothing.
    """
    if framework == "agents-md":
        return NONE
    if framework == "codex":
        from agentteams.frameworks._codex_role_gate_emit import codex_role_gate_enabled

        return ENFORCED if codex_role_gate_enabled(manifest) else DECLARED
    return ENFORCED


def _closure(scopes: Iterable[str]) -> frozenset[str]:
    """The scopes, with ``read`` and ``search`` standing for each other (one read-only class)."""
    found = frozenset(scopes)
    return found | _READ_CLASS if found & _READ_CLASS else found


def narrower_than_default(scopes: Iterable[str]) -> bool:
    """Whether a declared grant withholds part of :data:`SURFACE_DEFAULT`.

    Args:
        scopes: The agent's canonical tool scopes.

    Returns:
        True when the grant lacks ``edit``, ``execute`` or read access.

    Raises:
        Nothing.
    """
    return not SURFACE_DEFAULT <= _closure(scopes)


def _items(value: str) -> list[str]:
    """Split a ``tools:`` value (flow list or comma scalar) into names, quotes removed."""
    v = value.strip()
    if v.startswith("[") and v.endswith("]"):
        v = v[1:-1]
    return [i.strip().strip("'\"").strip() for i in v.split(",") if i.strip().strip("'\"").strip()]


def maps_to_canonical(raw: str) -> bool:
    """Whether a raw ``tools:`` string names only known tools and at least one Claude-style name.

    Codex then gets the canonical tokens instead of the raw string (``sandbox_mode`` and "Reading on Codex" key on
    them); a raw string with a bespoke name, or one already in canonical tokens, stays verbatim.

    Args:
        raw: A captured ``capabilities.raw`` value, e.g. ``Read, Grep, Glob``.

    Returns:
        True when every name is canonical or a known Claude tool, and not all are canonical.

    Raises:
        Nothing.
    """
    items = _items(raw)
    known = all(i in _FULL or i in _cm._CLAUDE_TO_CANONICAL for i in items)
    return bool(items) and known and not all(i in _FULL for i in items)


def _copilot_default() -> frozenset[str]:
    """The tools Copilot's adapter adds to a header without a ``tools`` key."""
    from agentteams.frameworks.copilot_vscode import _YAML_DEFAULTS

    return frozenset(_items(_YAML_DEFAULTS["tools"]))


def _claude_tokens(items: list[str]) -> frozenset[str]:
    """Canonical scopes of a Claude ``tools`` list (a canonical token passes through)."""
    out: set[str] = set()
    for item in items:
        if item == "*":
            return _FULL
        if item in _cm._CLAUDE_TO_CANONICAL:
            out.add(_cm._CLAUDE_TO_CANONICAL[item])
        elif item.lower() in _FULL:
            out.add(item.lower())
        elif item.startswith("Bash("):
            out.add("execute")  # any other scoped shell grant still runs commands
    return frozenset(out)


def projected_scopes(framework: str, rendered: str) -> frozenset[str] | None:
    """Re-read a rendered agent the way its surface does and return the canonical scopes it grants.

    Platform defaults are applied: no ``tools`` on Claude inherits every tool; on Copilot the adapter's default list
    applies; a missing goose ``extensions`` key loads the user's extensions; a Codex agent with no declared tools
    inherits the session. More than one ``tools`` line is ambiguous and reads as every tool.

    Args:
        framework: The target registry framework id.
        rendered: The adapter's output for one agent.

    Returns:
        The granted scopes, or ``None`` for a surface with no grant channel (agents-md).

    Raises:
        ValueError: From the goose/Codex reader on a file it cannot parse.
    """
    if framework == "agents-md":
        return None
    if framework in ("goose", "codex"):
        from agentteams.frameworks.registry import FRAMEWORKS

        caps = (FRAMEWORKS[framework]().parse_agent_source(rendered) or {}).get("capabilities") or {}
        if framework == "codex" and not caps.get("raw"):
            return _FULL  # no declared tools: the agent inherits the session
        return frozenset(caps.get("tool_scopes") or ())
    yaml_text, _ = parse_yaml_front_matter(rendered)
    values = _TOOLS_LINE_RE.findall(yaml_text) if yaml_text is not None else []
    if len(values) > 1 or (values and not values[0].strip()):
        return _FULL
    if framework == "claude":
        return _claude_tokens(_items(values[0])) if values else _FULL
    if not values:
        return _copilot_default()
    return frozenset(i.lower() for i in _items(values[0]) if i.lower() in _FULL)


def _order(tokens: Iterable[str]) -> list[str]:
    found = set(tokens)
    return [t for t in _cm.CANONICAL_TOOL_SCOPES if t in found]


def _grant_text(scopes: Iterable[str]) -> str:
    return ", ".join(f"`{t}`" for t in _order(scopes)) or "no tools"


def check_agent(slug: str, source_scopes: list[str] | None, framework: str, rendered: str,
                manifest: dict[str, Any], policy: ProjectionPolicy) -> list[str]:
    """Return why one projected agent must not be written (empty when it may).

    Args:
        slug: The agent's slug.
        source_scopes: Its canonical scopes after any write-policy narrowing, or ``None`` when the source declared
            no grant at all (nothing to narrow or widen).
        framework: The target registry framework id.
        rendered: The adapter's output.
        manifest: The interop manifest.
        policy: The run's :class:`ProjectionPolicy`.

    Returns:
        One message per refusal, each naming the agent and the surface.

    Raises:
        ValueError: From :func:`projected_scopes`.
    """
    problems: list[str] = []
    if framework in ("copilot-vscode", "copilot-cli"):
        from agentteams.frameworks.copilot_vscode import _MAX_BODY_CHARS

        yaml_text, body = parse_yaml_front_matter(rendered)
        size = len(body if yaml_text is not None else rendered)
        if size > _MAX_BODY_CHARS:
            problems.append(f"{slug}: its {framework} body is {size:,} characters, over the platform's "
                            f"{_MAX_BODY_CHARS:,}-character limit (it could be truncated); shorten the source agent")
    if source_scopes is None:
        return problems
    allowed = _closure(source_scopes)
    projected = projected_scopes(framework, rendered)
    if projected is not None and not projected <= allowed:
        problems.append(f"{slug}: projecting onto {framework} would widen its grant to {_grant_text(projected)} "
                        f"(the source grants {_grant_text(source_scopes)})")
    cls = enforcement_class(framework, manifest)
    if cls != ENFORCED and narrower_than_default(source_scopes) and slug not in policy.allow_cooperative:
        problems.append(f"{slug}: its grant ({_grant_text(source_scopes)}) is narrower than {framework}'s default, "
                        f"and {framework} does not enforce grants ({cls}). Pass --interop-allow-cooperative {slug} "
                        "to project it with a cooperative-grant banner, or leave it out of the source")
    return problems


def annotate(body: str, slug: str, source_scopes: list[str] | None, framework: str, manifest: dict[str, Any],
             policy: ProjectionPolicy) -> tuple[str, list[str]]:
    """Replace any earlier projection banner in *body* with the one this surface needs.

    Args:
        body: The agent body about to be rendered.
        slug: The agent's slug.
        source_scopes: As for :func:`check_agent`.
        framework: The target registry framework id.
        manifest: The interop manifest.
        policy: The run's :class:`ProjectionPolicy`.

    Returns:
        ``(body, notices)``: goose bodies get the delegate caveat; an opted-in agent on a declared/none surface gets
        the cooperative banner and a notice.

    Raises:
        Nothing.
    """
    if _BANNER_LEAD in body:  # an earlier projection's banner: stale on this surface
        body = _BANNER_RE.sub("\n", body).rstrip("\n") + "\n"
    if source_scopes is None:
        return body, []
    if framework == "goose":
        return f"{body.rstrip(chr(10))}\n\n{_GOOSE_BANNER}\n", []
    cls = enforcement_class(framework, manifest)
    if cls == ENFORCED or not narrower_than_default(source_scopes) or slug not in policy.allow_cooperative:
        return body, []
    banner = (f"{_BANNER_LEAD} grant is cooperative on this surface. {framework} does not enforce this agent's "
              f"grant ({_grant_text(source_scopes)}); the operator projected it with `--interop-allow-cooperative`. "
              "Use no capability outside that grant, whatever the session allows.")
    notice = f"{slug}: projected onto {framework} with a cooperative grant (--interop-allow-cooperative): NOT ENFORCED"
    return f"{body.rstrip(chr(10))}\n\n{banner}\n", [notice]


def prepare(manifest: dict[str, Any], cai: dict[str, Any], framework: str, target_dir: Path,
            policy: ProjectionPolicy) -> list[str]:
    """Set up the interop manifest for the checks, and refuse what can't work, before any agent is rendered.

    goose renders in ``goose_tool_scoping: "grant"`` mode, whose readers launch the ``agentteams_readfs`` server
    from the project root; interop does not install it, so it must already be there.

    Args:
        manifest: The interop manifest (mutated for goose).
        cai: The CAI document being imported.
        framework: The target registry framework id.
        target_dir: The target agents directory (goose: ``<root>/.goose/recipes``).
        policy: The run's :class:`ProjectionPolicy`.

    Returns:
        Notices, e.g. an opt-in naming an agent the source team doesn't have.

    Raises:
        ValueError: goose, an agent would read through ``agentteams_readfs``, and the server script is missing.
    """
    agents = [a for a in cai.get("agents", []) if str(a.get("slug", "")).strip()]
    unknown = sorted(policy.allow_cooperative - {str(a["slug"]).strip() for a in agents})
    notices: list[str] = []
    if unknown:
        notices.append(f"--interop-allow-cooperative names {', '.join(unknown)}, not in the source team; ignored")
    if framework != "goose":
        return notices
    from agentteams.frameworks.goose_tool_scoping import READFS_PROTECTED_PATH, READFS_SCRIPT

    manifest["goose_tool_scoping"] = "grant"
    scopes = {str(a["slug"]).strip(): set((a.get("capabilities") or {}).get("tool_scopes") or ()) for a in agents}
    readers = sorted(slug for slug, granted in scopes.items() if granted & _READ_CLASS)
    ungranted = sorted(slug for slug, granted in scopes.items() if not granted)
    if ungranted:
        notices.append(f"goose: {', '.join(ungranted)} declare no canonical tools, so their recipes get none "
                       "(extensions: [])")
    rel = READFS_PROTECTED_PATH if _write_policy.enabled(manifest) else READFS_SCRIPT
    script = Path(target_dir).parent.parent / rel
    if readers and not script.is_file():
        raise ValueError(f"goose: {', '.join(readers)} would read through the agentteams_readfs server, but {script} "
                         "is missing. Generate the goose team natively once (agentteams --description <brief> "
                         "--framework goose), which installs it, then re-run the projection")
    return notices


__all__ = [
    "DECLARED",
    "ENFORCED",
    "NONE",
    "SURFACE_DEFAULT",
    "ProjectionPolicy",
    "annotate",
    "check_agent",
    "enforcement_class",
    "maps_to_canonical",
    "narrower_than_default",
    "parse_slug_list",
    "prepare",
    "projected_scopes",
]
