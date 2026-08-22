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
deliberate C-3 widening. Tool parsing is delegated to
:func:`agentteams.capability_map.canonical_tools_for_claude`, which returns the
canonical 7-token vocabulary (``read, edit, search, execute, todo, agent,
retrieval``) or ``None`` when an agent declares no capability key.

Disposition is **warn-only** (``severity="warning"``): the override list is
unvalidated policy data and the live team predates it, so a finding routes
attention rather than blocking. Remediation of a real over-grant goes to
``@security`` (a C-3 widening) per the constitutional gate.
"""

from __future__ import annotations

from agentteams import analyze
from agentteams.audit_types import AuditFinding, _agent_slug, _is_agent_file
from agentteams.capability_map import CANONICAL_TOOL_SCOPES, canonical_tools_for_claude

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


def check_rank_conformance(file_map: dict[str, str], agent_ext: str) -> list[AuditFinding]:
    """Flag agents whose declared tool surface exceeds their rank ceiling.

    Iterates the agent files in *file_map* (entries the shared audit predicate
    classifies as agent files for *agent_ext*), derives each agent's rank,
    parses its canonical tool tokens, and emits a finding when the declared
    tokens exceed ``ceiling ∪ override``. Agents declaring no capability key
    (``canonical_tools_for_claude`` returns ``None``) are skipped — there is no
    declaration to check.

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

    findings: list[AuditFinding] = []
    for path in sorted(file_map):
        if not _is_agent_file(path, agent_ext):
            continue
        declared = canonical_tools_for_claude(file_map[path])
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
