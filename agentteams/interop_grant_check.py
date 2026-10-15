"""interop_grant_check.py — what a projection may emit, checked per agent.

A projection re-renders each source agent for another surface: ``interop.run_interop`` (``--interop-from``),
``multi_sync`` and ``convert_team`` (``--convert-from``). Before this module the rendered grant was never compared
with the source's, and on 2026-10-13 (plan ``tmp/by-week/2026-W42/projection-fixes.plan.md``, Revision 1):

* **goose** recipes got the whole ``developer`` builtin (shell, write, edit) for a ``Read, Grep, Glob`` agent;
* **Copilot** adds ``tools: ['read', 'edit', 'search']`` to a header that has none, whatever the source declared;
* **Codex** and **AGENTS.md** cannot enforce a grant at all, and nothing said so.

So every projected agent is checked after rendering and before anything is written:

1. **No widening** (every projection path). The rendered file is re-read the way that surface reads it
   (:func:`projected_grant`). Its canonical tool scopes must be a subset of the source agent's, and every tool name
   it grants that maps to no canonical scope (``runCommands``, an MCP tool, an unknown goose extension) must be one
   the source itself declares: an unknown name fails closed. On goose only, ``read`` and ``search`` count as one
   class, because its ``agentteams_readfs`` server serves both.
2. **Enforcement class** (``--interop-from`` only: :attr:`ProjectionPolicy.strict`). Each surface is
   :data:`ENFORCED`, :data:`DECLARED` or :data:`NONE` (:func:`enforcement_class`). An agent whose grant is narrower
   than the surface default (:data:`SURFACE_DEFAULT`) is refused on a declared or none surface unless the operator
   names it in ``--interop-allow-cooperative``. Each opt-in is then recorded in the destination team's
   ``references/security-decisions.log.csv`` (Rule 15 trace) and the agent carries a "grant is cooperative on this
   surface" banner.
3. **Copilot size** (strict only). A Copilot body over the platform's 30,000-character limit is refused (native
   generation only warns: ``copilot_vscode._check_body_length``).
4. **goose readfs** (``interop``/``multi_sync``). goose renders grant-scoped; its readers launch the
   ``agentteams_readfs`` server, which must already be installed at the target root and match the pinned
   ``goose_tool_scoping.READFS_SHA256``.
"""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
#: What a surface gives an agent that declares nothing: read, search, edit and shell. An agent missing any of them
#: is narrower than the default.
SURFACE_DEFAULT: frozenset[str] = frozenset({"read", "edit", "search", "execute"})
_READ_CLASS: frozenset[str] = frozenset({"read", "search"})
#: Goose extension names whose tools map to canonical scopes (``capability_map.goose_grants_to_canonical``).
_GOOSE_MAPPED = frozenset({"developer", "analyze", "summon", "agentteams_readfs", "todo", "<unparsed>"})
_SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_TOOLS_LINE_RE = re.compile(r"^(?:tools|allowed-tools):[ \t]*(.*)$", re.MULTILINE)
_BANNER_LEAD = "> **Projection grant note (agentteams):**"
_BANNER_RE = re.compile(r"\n*" + re.escape(_BANNER_LEAD) + r"[^\n]*(?:\n>[^\n]*)*\n?")
_GOOSE_BANNER = (
    f"{_BANNER_LEAD} this recipe's `extensions` bind when Goose runs it directly (`goose run --recipe`). Run as a "
    "`delegate`, it gets the delegating session's extensions instead, narrowed only by the `extensions` argument the "
    "delegator passes. Stay within your declared tools either way."
)
#: The destination log an ``--interop-allow-cooperative`` opt-in is recorded in, relative to the agents dir.
DECISION_LOG_REL = "references/security-decisions.log.csv"
#: The ``action_reviewed`` prefix of an opt-in record: ``interop-allow-cooperative:<framework>:<slug>``.
OPT_IN_ACTION = "interop-allow-cooperative"


@dataclass(frozen=True)
class ProjectionPolicy:
    """Per-run options for the projection checks.

    Attributes:
        allow_cooperative: Slugs the operator lets project onto a surface that can't enforce their grant.
        strict: ``--interop-from``: also refuse by enforcement class and by Copilot size. ``multi_sync`` and
            ``convert_team`` pass ``False``: they keep the no-widening check (and goose grant mode) only.
    """

    allow_cooperative: frozenset[str] = frozenset()
    strict: bool = True


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
    if framework == "grok":
        # Grok honours tools: (with injectDefaultTools false), but no live negative test has shown a Read-only
        # agent's Edit blocked yet; hooks fail open and the sandbox is operator-activated (@security step 6/12b).
        return DECLARED
    return ENFORCED


def _allowed(scopes: Iterable[str], framework: str) -> frozenset[str]:
    """The scopes a projection onto *framework* may grant: on goose, read and search stand for each other."""
    found = frozenset(scopes)
    return found | _READ_CLASS if framework == "goose" and found & _READ_CLASS else found


def narrower_than_default(scopes: Iterable[str]) -> bool:
    """Whether a declared grant withholds part of :data:`SURFACE_DEFAULT`.

    Args:
        scopes: The agent's canonical tool scopes.

    Returns:
        True when the grant lacks any of ``read``, ``search``, ``edit`` or ``execute``.

    Raises:
        Nothing.
    """
    return not SURFACE_DEFAULT <= frozenset(scopes)


def _items(value: str) -> list[str]:
    """Split a ``tools:`` value (flow list or comma scalar) into names, quotes removed."""
    v = value.strip()
    if v.startswith("[") and v.endswith("]"):
        v = v[1:-1]
    return [i.strip().strip("'\"").strip() for i in v.split(",") if i.strip().strip("'\"").strip()]


def _claude_name(item: str) -> bool:
    """Whether *item* is a Claude tool name ``capability_map`` maps (the one owner of that reverse map)."""
    return bool(_cm.claude_allowed_tools_to_canonical([item]))


def maps_to_canonical(raw: str) -> bool:
    """Whether a raw ``tools:`` string names only known tools and at least one Claude-style name.

    Such a string is re-emitted as canonical tokens (Codex keys ``sandbox_mode`` and "Reading on Codex" on them;
    ``convert_team`` hands them to every adapter); a raw string with a bespoke name, or one already in canonical
    tokens, stays verbatim.

    Args:
        raw: A captured ``capabilities.raw`` value, e.g. ``Read, Grep, Glob``.

    Returns:
        True when every name is canonical or a known Claude tool, and not all are canonical.

    Raises:
        Nothing.
    """
    items = _items(raw)
    known = all(i in _FULL or _claude_name(i) for i in items)
    return bool(items) and known and not all(i in _FULL for i in items)


def _flow(scopes: Iterable[str]) -> str:
    return "tools: [" + ", ".join(f"'{t}'" for t in scopes) + "]"


def tools_line(framework: str, scopes: list[str], raw_caps: dict[str, Any], raw_scopes: Any) -> str | None:
    """Return the ``tools:`` header line an imported agent gets on *framework*, or ``None`` for none.

    A captured raw string is kept only where it stays within the canonical scopes (C-3: a stale raw string never
    widens a narrowed grant): verbatim on Copilot (same vocabulary, bespoke ``runCommands`` kept), and on Codex,
    which states the declared tools as a self-imposed limit, unless it names Claude tools, which become canonical
    tokens. Everything else gets one VS Code-shaped bracket list: ``claude.py`` maps it to Claude names, Copilot
    keeps it, and goose's grant mode derives the recipe's extensions from it.

    Args:
        framework: The target registry framework id.
        scopes: The agent's canonical scopes (after any write-policy narrowing).
        raw_caps: ``capabilities.raw``: source framework -> raw tools string.
        raw_scopes: ``interop_helpers.raw_scopes`` (passed in: that module imports interop's helpers).

    Returns:
        The header line, or ``None``.

    Raises:
        Nothing.
    """
    copilot_raw = next((str(v) for k, v in raw_caps.items()
                        if k in ("copilot-vscode", "copilot-cli") and str(v).strip()), "")
    if copilot_raw and raw_scopes(copilot_raw) != scopes:
        copilot_raw = ""
    if copilot_raw and framework in ("copilot-vscode", "copilot-cli"):
        return f"tools: {copilot_raw}"
    if framework == "codex":
        raw = next((str(v) for v in raw_caps.values() if str(v).strip()), "")
        if raw and (raw_scopes(raw) != scopes or maps_to_canonical(raw)):
            raw = ""
        return f"tools: {raw}" if raw else (_flow(scopes) if scopes else None)
    if scopes and framework in ("copilot-vscode", "copilot-cli", "claude", "goose", "grok"):
        return _flow(scopes)
    return None


def source_names(raw_caps: dict[str, Any] | Iterable[str]) -> frozenset[str]:
    """The tool names a source agent declares, lower-cased (raw strings split into names).

    Args:
        raw_caps: ``capabilities.raw`` (a dict of raw strings) or the raw strings themselves.

    Returns:
        The names.

    Raises:
        Nothing.
    """
    values = raw_caps.values() if isinstance(raw_caps, dict) else raw_caps
    return frozenset(i.lower() for v in values for i in _items(str(v)))


def source_grant(content: str) -> tuple[list[str], frozenset[str]] | None:
    """Read the grant a Markdown source agent declares (``convert_team``: the source framework isn't recorded).

    Args:
        content: A Copilot or Claude agent file.

    Returns:
        ``(canonical scopes, declared names)``, or ``None`` when it has no ``tools``/``allowed-tools`` line.

    Raises:
        Nothing.
    """
    yaml_text, _ = parse_yaml_front_matter(content)
    values = _TOOLS_LINE_RE.findall(yaml_text) if yaml_text is not None else []
    if not values:
        return None
    scopes = (_cm.canonical_tools_for_copilot_vscode(content) or _cm.canonical_tools_for_claude(content) or [])
    return list(scopes), source_names(values)


def canonical_tools_header(content: str) -> str:
    """Rewrite a Claude-named ``tools:`` line (``Read, Grep, Glob``) as canonical tokens (``['read', 'search']``).

    ``convert_team`` passes source files to the target adapter as they are; every adapter but Claude's own reads only
    the canonical bracket form (Claude's adapter reads that too), so a Claude scalar fell back to defaults. A line
    with a bespoke name is left alone.

    Args:
        content: A source agent file.

    Returns:
        The content, with at most its first ``tools`` line rewritten.

    Raises:
        Nothing.
    """
    yaml_text, body = parse_yaml_front_matter(content)
    if yaml_text is None:
        return content
    m = _TOOLS_LINE_RE.search(yaml_text)
    if not m or not maps_to_canonical(m.group(1)):
        return content
    scopes = _cm.claude_allowed_tools_to_canonical(_items(m.group(1)))
    head = content[: len(content) - len(body)]
    return head.replace(m.group(0), _flow(scopes), 1) + body


def _copilot_default() -> frozenset[str]:
    """The tools Copilot's adapter adds to a header without a ``tools`` key."""
    from agentteams.frameworks.copilot_vscode import _YAML_DEFAULTS

    return frozenset(_items(_YAML_DEFAULTS["tools"]))


def _claude_grant(items: list[str]) -> tuple[frozenset[str], frozenset[str]]:
    """Canonical scopes and unmapped names of a Claude ``tools`` list (wraps ``capability_map``'s reverse map).

    Extra cases: a canonical token passes through, ``*`` is every tool, and any other scoped ``Bash(...)`` still runs
    commands (``execute``).
    """
    if "*" in items:
        return _FULL, frozenset()
    scopes = set(_cm.claude_allowed_tools_to_canonical(items))
    unknown: set[str] = set()
    for item in items:
        if _claude_name(item):
            continue
        if item.lower() in _FULL:
            scopes.add(item.lower())
        elif item.startswith("Bash("):
            scopes.add("execute")
        else:
            unknown.add(item.lower())
    return frozenset(scopes), frozenset(unknown)


def projected_grant(framework: str, rendered: str) -> tuple[frozenset[str], frozenset[str]] | None:
    """Re-read a rendered agent the way its surface does: the canonical scopes it grants, and its unmapped names.

    Platform defaults apply: no ``tools`` on Claude inherits every tool; on Copilot the adapter's default list
    applies; a missing goose ``extensions`` key loads the user's extensions; a Codex agent with no declared tools
    inherits the session. More than one ``tools`` line is ambiguous and reads as every tool.

    Args:
        framework: The target registry framework id.
        rendered: The adapter's output for one agent.

    Returns:
        ``(scopes, unmapped lower-cased names)``, or ``None`` for a surface with no grant channel (agents-md).

    Raises:
        ValueError: From the goose/Codex reader on a file it cannot parse.
    """
    if framework == "agents-md":
        return None
    if framework in ("goose", "codex"):
        from agentteams.frameworks.registry import FRAMEWORKS

        caps = (FRAMEWORKS[framework]().parse_agent_source(rendered) or {}).get("capabilities") or {}
        scopes = frozenset(caps.get("tool_scopes") or ())
        if framework == "codex":
            if not caps.get("raw"):
                return _FULL, frozenset()  # no declared tools: the agent inherits the session
            return scopes, frozenset(n for n in source_names(caps["raw"]) if n not in _FULL)
        from agentteams.frameworks.goose_recipe_read import recipe_extension_grants

        names = {str(e.get("name", "")).strip().lower() for e in recipe_extension_grants(rendered) or ()}
        return scopes, frozenset(names - _GOOSE_MAPPED)
    yaml_text, _ = parse_yaml_front_matter(rendered)
    values = _TOOLS_LINE_RE.findall(yaml_text) if yaml_text is not None else []
    if len(values) > 1 or (values and not values[0].strip()):
        return _FULL, frozenset()
    if framework == "claude":
        return _claude_grant(_items(values[0])) if values else (_FULL, frozenset())
    if framework == "grok":
        # Grok widens a declared list unless both exact-toolset keys are set (Phase 3 cond. 3): read it as full then.
        if not values or not _cm.grok_exact_toolset(rendered):
            return _FULL, frozenset()
        return _claude_grant(_items(values[0]))
    if not values:
        return _copilot_default(), frozenset()
    items = _items(values[0])
    scopes = frozenset(_cm.canonical_tools_for_copilot_vscode(rendered) or (i.lower() for i in items if i.lower() in _FULL))
    return scopes, frozenset(i.lower() for i in items if i.lower() not in _FULL)


def projected_scopes(framework: str, rendered: str) -> frozenset[str] | None:
    """The canonical scopes of :func:`projected_grant` (``None`` for agents-md).

    Args:
        framework: The target registry framework id.
        rendered: The adapter's output for one agent.

    Returns:
        The granted scopes, or ``None``.

    Raises:
        ValueError: From :func:`projected_grant`.
    """
    grant = projected_grant(framework, rendered)
    return None if grant is None else grant[0]


def _order(tokens: Iterable[str]) -> list[str]:
    found = set(tokens)
    return [t for t in _cm.CANONICAL_TOOL_SCOPES if t in found]


def _grant_text(scopes: Iterable[str]) -> str:
    return ", ".join(f"`{t}`" for t in _order(scopes)) or "no tools"


def _target_granted(slug: str, framework: str, manifest: dict[str, Any]) -> frozenset[str]:
    """Names the TARGET's own configuration grants (the ``mcp_grants`` runner under the switch), not the source."""
    grant = (manifest.get("mcp_grants") or {}).get(slug) if _write_policy.enabled(manifest) else None
    if not isinstance(grant, dict) or not grant.get("tools"):
        return frozenset()
    from agentteams import runner_mcp

    if framework == "goose":
        return frozenset({runner_mcp.SERVER_NAME})
    return frozenset(n.lower() for n in runner_mcp.tool_names(grant["tools"]))


def check_agent(slug: str, source_scopes: list[str] | None, framework: str, rendered: str,
                manifest: dict[str, Any], policy: ProjectionPolicy,
                declared_names: Iterable[str] = ()) -> list[str]:
    """Return why one projected agent must not be written (empty when it may).

    Args:
        slug: The agent's slug.
        source_scopes: Its canonical scopes after any write-policy narrowing, or ``None`` when the source declared
            no grant at all (nothing to narrow or widen).
        framework: The target registry framework id.
        rendered: The adapter's output.
        manifest: The projection manifest.
        policy: The run's :class:`ProjectionPolicy`.
        declared_names: The tool names the source declares (:func:`source_names`): an unmapped projected name is
            allowed only when it is one of them.

    Returns:
        One message per refusal, each naming the agent and the surface.

    Raises:
        ValueError: From :func:`projected_grant`.
    """
    problems: list[str] = []
    if policy.strict and framework in ("copilot-vscode", "copilot-cli"):
        from agentteams.frameworks.copilot_vscode import _MAX_BODY_CHARS

        yaml_text, body = parse_yaml_front_matter(rendered)
        size = len(body if yaml_text is not None else rendered)
        if size > _MAX_BODY_CHARS:
            problems.append(f"{slug}: its {framework} body is {size:,} characters, over the platform's "
                            f"{_MAX_BODY_CHARS:,}-character limit (it could be truncated); shorten the source agent")
    if source_scopes is None:
        return problems
    grant = projected_grant(framework, rendered)
    if grant is not None:
        projected, unmapped = grant
        if not projected <= _allowed(source_scopes, framework):
            problems.append(f"{slug}: projecting onto {framework} would widen its grant to {_grant_text(projected)} "
                            f"(the source grants {_grant_text(source_scopes)})")
        unknown = sorted(unmapped - {n.lower() for n in declared_names} - _target_granted(slug, framework, manifest))
        if unknown:
            problems.append(f"{slug}: projecting onto {framework} would grant {', '.join(unknown)}, which maps to no "
                            "canonical scope and which the source does not declare (an unknown tool fails closed)")
    cls = enforcement_class(framework, manifest)
    if policy.strict and cls != ENFORCED and narrower_than_default(source_scopes) \
            and slug not in policy.allow_cooperative:
        problems.append(f"{slug}: its grant ({_grant_text(source_scopes)}) is narrower than {framework}'s default, "
                        f"and {framework} does not enforce grants ({cls}). Pass --interop-allow-cooperative {slug} "
                        "to project it with a cooperative-grant banner, or leave it out of the source")
    return problems


def _cooperative(slug: str, source_scopes: list[str] | None, framework: str, manifest: dict[str, Any],
                 policy: ProjectionPolicy) -> bool:
    return (source_scopes is not None and policy.strict and slug in policy.allow_cooperative
            and enforcement_class(framework, manifest) != ENFORCED and narrower_than_default(source_scopes))


def annotate(body: str, slug: str, source_scopes: list[str] | None, framework: str, manifest: dict[str, Any],
             policy: ProjectionPolicy) -> tuple[str, list[str]]:
    """Replace any earlier projection banner in *body* with the one this surface needs.

    Args:
        body: The agent body about to be rendered.
        slug: The agent's slug.
        source_scopes: As for :func:`check_agent`.
        framework: The target registry framework id.
        manifest: The projection manifest.
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
    if not _cooperative(slug, source_scopes, framework, manifest, policy):
        return body, []
    banner = (f"{_BANNER_LEAD} grant is cooperative on this surface. {framework} does not enforce this agent's "
              f"grant ({_grant_text(source_scopes)}); the operator projected it with `--interop-allow-cooperative`. "
              "Use no capability outside that grant, whatever the session allows.")
    notice = f"{slug}: projected onto {framework} with a cooperative grant (--interop-allow-cooperative): NOT ENFORCED"
    return f"{body.rstrip(chr(10))}\n\n{banner}\n", [notice]


@dataclass
class ProjectionRun:
    """One projection's checks: built by :func:`prepare`, fed per agent, closed by :meth:`conclude`.

    Attributes:
        policy: The run's :class:`ProjectionPolicy`.
        framework: The target registry framework id.
        target_dir: The target agents directory.
        manifest: The projection manifest.
        refusals: Collected refusals (raised together by :meth:`conclude`).
        cooperative: ``(slug, scopes)`` of each opted-in agent actually projected.
        notices: Operator notices.
    """

    policy: ProjectionPolicy
    framework: str
    target_dir: Path
    manifest: dict[str, Any]
    refusals: list[str] = field(default_factory=list)
    cooperative: list[tuple[str, list[str]]] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)

    def annotate(self, body: str, slug: str, source_scopes: list[str] | None) -> str:
        """:func:`annotate` for this run; an applied opt-in is remembered for :meth:`conclude`.

        Args:
            body: The agent body.
            slug: The agent's slug.
            source_scopes: As for :func:`check_agent`.

        Returns:
            The body with this surface's banner.

        Raises:
            Nothing.
        """
        body, notes = annotate(body, slug, source_scopes, self.framework, self.manifest, self.policy)
        if notes and source_scopes is not None:
            self.cooperative.append((slug, list(source_scopes)))
        self.notices += notes
        return body

    def check(self, slug: str, source_scopes: list[str] | None, rendered: str, raw_caps: Any = ()) -> None:
        """Collect :func:`check_agent`'s refusals for one rendered agent.

        Args:
            slug: The agent's slug.
            source_scopes: As for :func:`check_agent`.
            rendered: The adapter's output.
            raw_caps: The source's raw tool strings (``capabilities.raw``, or the declared names).

        Returns:
            None.

        Raises:
            ValueError: From :func:`projected_grant`.
        """
        self.refusals += check_agent(slug, source_scopes, self.framework, rendered, self.manifest, self.policy,
                                     source_names(raw_caps))

    def conclude(self, *, dry_run: bool) -> list[str]:
        """Refuse the run if any agent was refused; otherwise record each opt-in, then return the notices.

        Args:
            dry_run: Record nothing.

        Returns:
            The run's notices.

        Raises:
            ValueError: Any refusal (nothing has been written), or an opt-in that cannot be recorded.
        """
        if self.refusals:
            raise ValueError("projection refused, nothing written:\n  " + "\n  ".join(self.refusals))
        if self.cooperative and not dry_run:
            record_opt_ins(self.target_dir, self.framework, self.cooperative, self.manifest)
        return self.notices


def _readfs_problem(script: Path) -> str:
    """Why the installed readfs server can't be trusted (``""`` when it matches ``READFS_SHA256``)."""
    from agentteams.frameworks.goose_tool_scoping import READFS_SHA256

    if not script.exists() and not script.is_symlink():
        return f"{script} is missing"
    if script.is_symlink() or not script.is_file():
        return f"{script} is a symlink or not a regular file"
    try:
        data = script.read_bytes()
    except OSError as exc:
        return f"{script} is unreadable ({exc})"
    if hashlib.sha256(data).hexdigest() != READFS_SHA256:
        return f"{script} doesn't match the shipped read-only file server (READFS_SHA256)"
    return ""


def prepare(manifest: dict[str, Any], cai: dict[str, Any], framework: str, target_dir: Path,
            policy: ProjectionPolicy) -> ProjectionRun:
    """Set up the projection manifest and refuse what can't work, before any agent is rendered.

    goose renders in ``goose_tool_scoping: "grant"`` mode; its readers launch the ``agentteams_readfs`` server from
    the project root, which this path does not install: it must be there and match ``READFS_SHA256`` (the pin the
    runner checks, ``proposal_runner.check_installed_readfs``). An opt-in onto a surface that can't enforce it needs
    the destination's decision log, where :meth:`ProjectionRun.conclude` records it.

    Args:
        manifest: The projection manifest (mutated for goose).
        cai: The CAI document being imported.
        framework: The target registry framework id.
        target_dir: The target agents directory (goose: ``<root>/.goose/recipes``).
        policy: The run's :class:`ProjectionPolicy`.

    Returns:
        The :class:`ProjectionRun`, with notices such as an opt-in naming an agent the source team doesn't have.

    Raises:
        ValueError: goose readers and a missing, linked or mismatched server script; or an opt-in onto a declared /
            none surface whose destination has no decision log.
    """
    run = ProjectionRun(policy, framework, Path(target_dir), manifest)
    agents = [a for a in cai.get("agents", []) if str(a.get("slug", "")).strip()]
    unknown = sorted(policy.allow_cooperative - {str(a["slug"]).strip() for a in agents})
    if unknown:
        run.notices.append(f"--interop-allow-cooperative names {', '.join(unknown)}, not in the source team; ignored")
    log = Path(target_dir) / DECISION_LOG_REL
    if policy.allow_cooperative and enforcement_class(framework, manifest) != ENFORCED and not log.is_file():
        raise ValueError(f"--interop-allow-cooperative: {log} is missing, so the opt-in for "
                         f"{', '.join(sorted(policy.allow_cooperative))} cannot be recorded (Rule 15). Create the "
                         "destination team's decision log first")
    if framework != "goose":
        return run
    from agentteams.frameworks.goose_tool_scoping import READFS_PROTECTED_PATH, READFS_SCRIPT

    manifest["goose_tool_scoping"] = "grant"
    scopes = {str(a["slug"]).strip(): set((a.get("capabilities") or {}).get("tool_scopes") or ()) for a in agents}
    readers = sorted(slug for slug, granted in scopes.items() if granted & _READ_CLASS)
    ungranted = sorted(slug for slug, granted in scopes.items() if not granted)
    if ungranted:
        run.notices.append(f"goose: {', '.join(ungranted)} declare no canonical tools, so their recipes get none "
                           "(extensions: [])")
    rel = READFS_PROTECTED_PATH if _write_policy.enabled(manifest) else READFS_SCRIPT
    problem = _readfs_problem(Path(target_dir).parent.parent / rel) if readers else ""
    if problem:
        raise ValueError(f"goose: {', '.join(readers)} would read through the agentteams_readfs server, but "
                         f"{problem}. Generate the goose team natively once (agentteams --description <brief> "
                         "--framework goose), which installs it, then re-run the projection")
    return run


def _chain_tip(team_dir: Path) -> str:
    """The ``prev_digest`` the next row of a hash-chained decision log must carry (``cli.decision_log``'s chain)."""
    from agentteams.cli.decision_chain import chain_tip
    from agentteams.cli.decision_log import _decision_signing_active, _read_decision_rows

    try:
        read = _read_decision_rows(team_dir)
        if read is None:
            return ""
        return chain_tip(read[0], read[1], signing_active=_decision_signing_active(read[1]))
    except RuntimeError as exc:  # an unreadable log or a broken chain: refuse rather than extend it
        raise ValueError(f"--interop-allow-cooperative: cannot extend the decision log's hash chain: {exc}") from exc


def record_opt_ins(team_dir: Path, framework: str, entries: list[tuple[str, list[str]]],
                   manifest: dict[str, Any]) -> list[str]:
    """Append one ``RECORDED`` row per opted-in agent to the destination team's decision log (Rule 15 trace).

    Written in the log's own header (legacy ``timestamp,…,action_reviewed,verdict,…`` or the repository schema
    ``date,…,decision,status,…``) through ``governance_logs.append_row``. ``RECORDED`` is not an authorizing
    verdict, so the row clears nothing; a hash-chained log gets the ``prev_digest`` its chain requires.

    Args:
        team_dir: The destination agents directory (the log is ``references/security-decisions.log.csv``).
        framework: The target framework.
        entries: ``(slug, scopes)`` per opted-in agent.
        manifest: The projection manifest (for the enforcement class).

    Returns:
        The ``action_reviewed`` ids written.

    Raises:
        ValueError: The log is missing, malformed, or has no action column (``append_row``'s own refusals too).
        OSError: The log cannot be appended to.
    """
    from agentteams.governance_logs import append_row

    log = team_dir / DECISION_LOG_REL
    if not log.is_file():
        raise ValueError(f"--interop-allow-cooperative: {log} is missing; the opt-in cannot be recorded")
    with log.open(encoding="utf-8", newline="") as fh:
        header = next(csv.reader(fh), [])
    by_name = {h.strip(): h for h in header}
    if not ({"action_reviewed", "decision"} & set(by_name)):
        raise ValueError(f"{log} has no action_reviewed/decision column; the opt-in cannot be recorded")
    cls = enforcement_class(framework, manifest)
    written: list[str] = []
    for slug, scopes in entries:
        now = datetime.now(timezone.utc)
        action = f"{OPT_IN_ACTION}:{framework}:{slug}"
        values = {
            "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "date": now.strftime("%Y-%m-%d"),
            "requesting_agent": "operator", "author": "operator", "owner": "operator",
            "action_reviewed": action, "decision": action, "verdict": "RECORDED", "status": "RECORDED",
            "conditions": (f"operator opt-in: {slug}'s grant ({', '.join(_order(scopes)) or 'no tools'}) is "
                           f"cooperative on {framework} ({cls}); NOT ENFORCED"),
            "conditions_verified": "n/a", "consumed": "n/a", "evidence": "agentteams --interop-from projection",
        }
        if "prev_digest" in by_name:
            values["prev_digest"] = _chain_tip(team_dir)
        append_row(log, {by_name[k]: v for k, v in values.items() if k in by_name})
        written.append(action)
    return written


__all__ = [
    "DECISION_LOG_REL",
    "DECLARED",
    "ENFORCED",
    "NONE",
    "OPT_IN_ACTION",
    "SURFACE_DEFAULT",
    "ProjectionPolicy",
    "ProjectionRun",
    "annotate",
    "canonical_tools_header",
    "check_agent",
    "enforcement_class",
    "maps_to_canonical",
    "narrower_than_default",
    "parse_slug_list",
    "prepare",
    "projected_grant",
    "projected_scopes",
    "record_opt_ins",
    "source_grant",
    "source_names",
    "tools_line",
]
