"""rank_conformance.py — AP-2 rank-conformance validator.

Enforces the C-3 capability surface *against agent taxonomy rank*: an agent's
declared ``tools:`` must not exceed what its rank permits. Rank is **derived**,
not stored — from three signals mirrored from :mod:`agentteams.analyze`:

* slug ``"orchestrator"`` → ``orchestrator`` (no ceiling; holds all 7 tokens)
* slug in :data:`analyze.GOVERNANCE_AGENTS` → ``governance``
* slug ending ``"-expert"`` → ``workstream-expert``
* everything else → ``domain``

Each rank carries a default tool *ceiling* (:data:`TIER_CEILINGS`). Individual
agents may hold tokens beyond their ceiling only when recorded in
:data:`PER_AGENT_OVERRIDES` — every override entry is the auditable record of a
deliberate C-3 widening. Tool parsing is delegated to a framework-dispatched parser
(:func:`~agentteams.capability_map.canonical_tools_for_claude` for claude,
:func:`~agentteams.capability_map.canonical_tools_for_copilot_vscode` for
copilot-vscode/copilot-cli), each returning the canonical 7-token vocabulary
(``read, edit, search, execute, todo, agent, retrieval``) or ``None`` when an agent
declares no capability key. Frameworks with no per-agent tool-scope channel
(codex, goose, agents-md) are **not checkable** and say so (an exit-neutral notice),
rather than passing silently (C-3, 2026-08-26).

Disposition is **warn-only** (``severity="warning"``): the override list is
unvalidated policy data and the live team predates it, so a finding routes
attention rather than blocking. Remediation of a real over-grant goes to
``@security`` (a C-3 widening) per the constitutional gate.
"""

from __future__ import annotations

from agentteams import analyze
from agentteams.audit_types import AuditFinding, _agent_slug, _is_agent_file
from agentteams.capability_map import (
    CANONICAL_TOOL_SCOPES,
    canonical_tools_for_claude,
    canonical_tools_for_copilot_vscode,
)

#: Frameworks whose per-agent capability surface a canonical-tools parser can read. Everything
#: else (codex, goose, agents-md) has **no tool-scope channel**, so rank conformance is structurally
#: NOT CHECKABLE there — it must be *said* (an exit-neutral advisory), never silently passed, or a
#: strict run would look enforced where it is blind (C-3, 2026-08-26). copilot-cli shares the
#: copilot-vscode `.agent.md` YAML surface, so it reuses that parser.
_TOOL_PARSERS = {
    "claude": canonical_tools_for_claude,
    "copilot-vscode": canonical_tools_for_copilot_vscode,
    "copilot-cli": canonical_tools_for_copilot_vscode,
}


def _parser_for(framework: str | None, agent_ext: str):
    """Return ``(parser, checkable)`` for a team.

    Dispatch by ``framework`` when known. A framework with a parser → that parser, checkable.
    A framework WITHOUT one (codex/goose/agents-md) → ``(None, False)`` — the honest not-checkable
    signal. When ``framework`` is ``None`` (hand-built manifests / older tests), fall back to the
    ``agent_ext`` heuristic (``.agent.md`` → copilot-vscode, ``.md`` → claude) so existing callers
    keep working.
    """
    if framework is not None:
        parser = _TOOL_PARSERS.get(framework)
        return parser, parser is not None
    if agent_ext == ".agent.md":
        return canonical_tools_for_copilot_vscode, True
    if agent_ext == ".md":
        return canonical_tools_for_claude, True
    return None, False


def rank_check_not_checkable_notice(framework: str | None, agent_ext: str) -> str | None:
    """An exit-neutral advisory message when a team's framework has no tool-scope parser, else None.

    Surfaced by the CLI (like the W19 keyless note) so a `--check-rank[-strict]` run over a
    codex/goose/agents-md team reports "not checkable" rather than a misleading clean pass. It does
    NOT contribute a finding, so it never flips the strict exit code — a framework lacking a parser
    is not an agent-level defect to block on.
    """
    _, checkable = _parser_for(framework, agent_ext)
    if checkable:
        return None
    fw = framework or f"ext {agent_ext!r}"
    return (
        f"rank conformance NOT CHECKABLE for framework {fw}: no per-agent tool-scope channel to "
        "parse, so declared-vs-rank cannot be evaluated (enforcement applies to claude / "
        "copilot-vscode / copilot-cli only)."
    )

# ---------------------------------------------------------------------------
# Policy as data (canonical tokens only)
# ---------------------------------------------------------------------------

#: Default tool ceiling per derived rank. Orchestrator has no ceiling (all 7).
#: No ceiling grants ``edit`` except the orchestrator's — write capability is an
#: override that must be recorded per-agent, never a rank default.
TIER_CEILINGS: dict[str, frozenset[str]] = {
    "orchestrator": frozenset(CANONICAL_TOOL_SCOPES),
    "governance": frozenset({"read", "search", "execute", "agent"}),
    "domain": frozenset({"read", "search", "execute", "retrieval"}),
    "workstream-expert": frozenset({"read", "search", "agent"}),
}

#: Extra tokens granted to a specific agent beyond its rank ceiling. Each entry
#: is the auditable record of a C-3 widening for that agent. Keyed by slug.
PER_AGENT_OVERRIDES: dict[str, frozenset[str]] = {
    # governance widenings
    "cleanup": frozenset({"edit"}),
    "agent-updater": frozenset({"edit", "agent"}),
    "agent-refactor": frozenset({"edit", "agent"}),
    "conflict-resolution": frozenset({"edit"}),
    "repo-liaison": frozenset({"edit", "execute", "agent"}),
    # domain widenings
    "primary-producer": frozenset({"edit"}),
    "output-compiler": frozenset({"edit", "execute"}),
    "reference-manager": frozenset({"edit", "execute", "retrieval"}),
    "format-converter": frozenset({"edit", "execute"}),
    "work-summarizer": frozenset({"edit", "execute", "agent"}),
    "content-enricher": frozenset({"edit"}),
    "cohesion-repairer": frozenset({"edit"}),
    # team-builder: interactive intake agent that writes brief.json and drives the
    # build pipeline; edit is a recorded widening (execute is already in-ceiling).
    "team-builder": frozenset({"edit"}),
}

CATEGORY = "RANK_CONFORMANCE"
CODE = "AP2_RANK_CAPABILITY_EXCEEDED"
SEVERITY = "warning"


def rank_for(slug: str) -> str:
    """Return the derived taxonomy rank for an agent slug.

    Args:
        slug: The agent slug (filename minus its agent extension), e.g.
            ``"navigator"`` or ``"pipeline-core-expert"``.

    Returns:
        One of ``"orchestrator"``, ``"governance"``, ``"workstream-expert"``,
        or ``"domain"`` — the fallthrough rank.

    Raises:
        TypeError: If *slug* is not a string.
    """
    if not isinstance(slug, str):
        raise TypeError("slug must be str")
    if slug == "orchestrator":
        return "orchestrator"
    if slug in analyze.GOVERNANCE_AGENTS:
        return "governance"
    if slug.endswith("-expert"):
        return "workstream-expert"
    return "domain"


def _allowed_tokens(slug: str, rank: str) -> frozenset[str]:
    """The union of the rank ceiling and this agent's per-agent override."""
    return TIER_CEILINGS.get(rank, frozenset()) | PER_AGENT_OVERRIDES.get(slug, frozenset())


def agents_missing_capability_key(
    file_map: dict[str, str], agent_ext: str, framework: str | None = None
) -> list[str]:
    """Return the slugs of agent files that declare NO capability (``tools:``) key (W19).

    The complement of :func:`check_rank_conformance`, which *skips* an agent with no
    declared key (there is nothing to check). This surfaces exactly those agents: with
    no key, an agent inherits every tool, so a missing declaration is an implicit
    maximal grant — the opposite failure mode from an over-grant, and one the rank
    validator is structurally blind to. Reporting-only: it names the agents; it does
    **not** invent a ``tools:`` value for them (choosing each agent's real capability
    surface is an operator decision, routed through ``@security`` as a C-3 grant).

    Args:
        file_map: Mapping of relative path → file content.
        agent_ext: The agent-file extension for this team (e.g. ``".md"``).

    Returns:
        Sorted slugs of agent files whose capability key is absent. Empty when every
        agent declares one.

    Raises:
        TypeError: If *file_map* is not a dict or *agent_ext* is not a string.
    """
    if not isinstance(file_map, dict):
        raise TypeError("file_map must be dict")
    if not isinstance(agent_ext, str):
        raise TypeError("agent_ext must be str")
    parser, checkable = _parser_for(framework, agent_ext)
    if not checkable:
        # No tool-scope parser for this framework — a None result would be "no parser", NOT
        # "keyless", so reporting every agent as keyless here would be a false alarm. The
        # not-checkable status is surfaced separately (rank_check_not_checkable_notice).
        return []
    missing: list[str] = []
    for path in sorted(file_map):
        if not _is_agent_file(path, agent_ext):
            continue
        if parser(file_map[path]) is None:
            missing.append(_agent_slug(path, agent_ext))
    return missing


def disposition_exit_code(findings: list[AuditFinding], strict: bool) -> int:
    """Return the CLI exit code for a rank-conformance run.

    Disposition is kept out of :func:`check_rank_conformance` (which only
    *describes* findings, always at ``severity="warning"``) so the same scan
    can back both the default warn-only mode and the opt-in blocking mode
    (SP-04 / AP-2). The finding severity is unchanged; only the exit code
    differs.

    Args:
        findings: The findings from :func:`check_rank_conformance`.
        strict: When ``True`` (``--check-rank-strict``), a non-empty finding
            list yields a non-zero exit. When ``False`` (``--check-rank``),
            the exit is always ``0`` — the finding routes attention, not a
            build failure, while the override policy beds in.

    Returns:
        ``1`` iff *strict* is true and *findings* is non-empty; otherwise ``0``.

    Raises:
        TypeError: If *findings* is not a list.
    """
    if not isinstance(findings, list):
        raise TypeError("findings must be a list")
    return 1 if (strict and findings) else 0


def check_rank_conformance(
    file_map: dict[str, str], agent_ext: str, framework: str | None = None
) -> list[AuditFinding]:
    """Flag agents whose declared tool surface exceeds their rank ceiling.

    Iterates the agent files in *file_map* (entries the shared audit predicate
    classifies as agent files for *agent_ext*), derives each agent's rank,
    parses its canonical tool tokens with the framework-dispatched parser
    (:func:`_parser_for`), and emits a finding when the declared tokens exceed
    ``ceiling ∪ override``. Agents declaring no capability key (the parser returns
    ``None``) are skipped — there is no declaration to check. A framework with no
    tool-scope parser (codex/goose/agents-md) yields **no findings** here; its
    not-checkable status is surfaced separately by :func:`rank_check_not_checkable_notice`.

    Args:
        file_map: Mapping of relative path → file content. Non-agent entries
            (references, ``SETUP-REQUIRED.md``, wrong extension) are ignored.
        agent_ext: The agent-file extension for this team (e.g. ``".md"`` for
            Claude, ``".agent.md"`` for copilot-vscode).

    Returns:
        A list of :class:`~agentteams.audit_types.AuditFinding`, one per
        offending agent file, sorted by path. Empty when every agent is within
        its allowed surface.

    Raises:
        TypeError: If *file_map* is not a dict or *agent_ext* is not a string.
    """
    if not isinstance(file_map, dict):
        raise TypeError("file_map must be dict")
    if not isinstance(agent_ext, str):
        raise TypeError("agent_ext must be str")

    parser, checkable = _parser_for(framework, agent_ext)
    if not checkable:
        # No tool-scope parser for this framework (codex/goose/agents-md): there is nothing to
        # parse, so emit NO findings — a strict run must not block a team for a gap that is the
        # tool's, not the agent's. The not-checkable status is surfaced by
        # rank_check_not_checkable_notice (exit-neutral), never as a blocking finding here.
        return []

    findings: list[AuditFinding] = []
    for path in sorted(file_map):
        if not _is_agent_file(path, agent_ext):
            continue
        declared = parser(file_map[path])
        if declared is None:
            continue  # no capability declaration to check
        slug = _agent_slug(path, agent_ext)
        rank = rank_for(slug)
        allowed = _allowed_tokens(slug, rank)
        offending = [t for t in declared if t not in allowed]
        if not offending:
            continue
        findings.append(
            AuditFinding(
                category=CATEGORY,
                code=CODE,
                severity=SEVERITY,
                file=path,
                description=(
                    f"agent {slug!r} (rank {rank}) declares tools "
                    f"{declared} but its rank permits {sorted(allowed)}; "
                    f"offending tokens {offending} exceed the rank ceiling "
                    f"without a recorded per-agent override (C-3 widening)."
                ),
            )
        )
    return findings
