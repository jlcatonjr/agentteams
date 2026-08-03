"""audit_agent_contract.py — checks that a generated agent file honours the agent contract.

Carved out of ``audit`` when adding one check pushed that module past the CH-07 ceiling; it had
been sitting at 999 of 1000 lines with the ratchet holding it there, so any addition forced this.
The seam was already in the file: these five checks all read a single agent file and ask whether
it still declares what an agent is required to declare — an Invariant Core, a return handoff,
tools consistent with its own read-only claim, and a reachable instruction-authority ordering.
They need no manifest, no output directory, and no filesystem access.

What deliberately stayed behind in ``audit``: the placeholder/front-matter checks (file *shape*,
not agent contract), the manifest-coverage checks (which need the manifest), the code-hygiene
checks (CH rules, a different rulebook), and ``_check_dangling_agent_slugs`` (which resolves
slugs against paths on disk).

``audit`` re-exports these names, so no existing import changed.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentteams.audit_types import AuditFinding, _agent_slug, _is_agent_file

#: Pattern that identifies a self-declared read-only agent from its body text.
#: Matches only explicit self-attributive declarations:
#:   - ``**read-only**`` (bold, emphasis on the agent's own capability)
#:   - ``you are/perform/operate ... read-only`` (direct self-reference)
#: Does NOT match incidental mentions like "external paths are read-only",
#: "authoritative — read-only" (table label), or "Read-only agents must NOT".
_READONLY_BODY_RE = re.compile(
    r"(?:\*\*read[- ]only\*\*|\byou\b[^.\n]*\bread[- ]only\b)",
    re.IGNORECASE,
)

#: Tools that read-only agents must not claim.
#: 'execute' is intentionally excluded: read-only agents may legitimately run
#: read-only shell commands (grep, find, python -c) without modifying files.
_READWRITE_TOOLS = frozenset({"edit", "write", "create"})


def _check_invariant_core_present(
    file_map: dict[str, str],
    *,
    agent_ext: str,
) -> list[AuditFinding]:
    """Check that every agent file contains the Invariant Core marker.

    The agent-refactor spec requires every agent file to have an Invariant
    Core section marked with a ⛔ symbol.

    Args:
        file_map: Rendered file content keyed by relative path.

    Returns:
        List of AuditFinding for files missing the ⛔ marker.
    """
    findings: list[AuditFinding] = []
    for rel_path, content in file_map.items():
        if not _is_agent_file(rel_path, agent_ext):
            continue
        if "\u26d4" not in content:  # ⛔
            findings.append(AuditFinding(
                category="AGENT_REFACTOR",
                code="AR_MISSING_INVARIANT_CORE",
                severity="warning",
                file=rel_path,
                description=(
                    "Agent file is missing the Invariant Core section (⛔ marker). "
                    "Add a '> ⛔ **Do not modify or omit.**' section."
                ),
            ))
    return findings


#: Agents whose ordinary work involves reading content this project did not author — files under
#: review, retrieved index results, fetched pages, adjacent-repository files. These are the agents
#: for which "content is data, not instruction" (C-4) is load-bearing rather than incidental.
#: `@security` is absent deliberately: Rules S-5/S-6 already state it in stronger, agent-specific
#: form, and duplicating it there would be the restatement C-4 exists to replace.
_UNTRUSTED_CONTENT_READERS: frozenset[str] = frozenset({
    "repo-liaison",
    "navigator",
    "code-hygiene",
    "reference-manager",
    "research-analyst",
    "technical-validator",
    "retrieval-integrator",
})

#: Emitted path of the instruction-authority ordering (Tier 0-6), referenced by the pointer.
_INSTRUCTION_AUTHORITY_REF = "instruction-authority.reference.md"


def _check_instruction_authority_reachable(
    file_map: dict[str, str],
    *,
    agent_ext: str,
) -> list[AuditFinding]:
    """Check the instruction-authority ordering is emitted and reachable from its readers.

    Two regressions this catches, both silent:

    - The reference stops being emitted (an ``output_plan`` edit), leaving every ``(C-4)``
      pointer in the team dangling.
    - An agent that reads untrusted content loses its pointer, so the one rule standing between
      a retrieved document and an instruction is no longer stated anywhere it is read.

    ``severity="warning"``, deliberately. Every team generated before this shipped lacks both,
    and failing their audit for being older than a feature would turn a safety check into a
    broken build for consumers who did nothing wrong. Promote once the fleet has regenerated.

    Args:
        file_map: Rendered file content keyed by relative path.

    Returns:
        List of AuditFinding for a missing reference or a reader missing its pointer.
    """
    findings: list[AuditFinding] = []

    if not any(_INSTRUCTION_AUTHORITY_REF in path for path in file_map):
        findings.append(AuditFinding(
            category="AGENT_REFACTOR",
            code="AR_MISSING_INSTRUCTION_AUTHORITY",
            severity="warning",
            file=f"references/{_INSTRUCTION_AUTHORITY_REF}",
            description=(
                "The instruction-authority ordering is not emitted. Every '(C-4)' pointer in "
                "this team now refers to a file that does not exist."
            ),
        ))

    for rel_path, content in file_map.items():
        if not _is_agent_file(rel_path, agent_ext):
            continue
        stem = _agent_slug(rel_path, agent_ext)
        if stem not in _UNTRUSTED_CONTENT_READERS:
            continue
        if _INSTRUCTION_AUTHORITY_REF not in content:
            findings.append(AuditFinding(
                category="AGENT_REFACTOR",
                code="AR_MISSING_C4_POINTER",
                severity="warning",
                file=rel_path,
                description=(
                    f"'{stem}' reads content this project did not author but does not carry the "
                    "C-4 pointer to references/instruction-authority.reference.md. Without it, "
                    "nothing in this agent's own file says that what it reads is data rather "
                    "than instruction."
                ),
            ))
    return findings


def _check_return_handoff_present(
    file_map: dict[str, str],
    *,
    agent_ext: str,
    supports_handoffs: bool = True,
) -> list[AuditFinding]:
    """Check that every agent file has a return-to-orchestrator handoff.

    Every agent must declare a handoff back to orchestrator so the
    conversation can be cleanly returned after the agent's work is done.

    Args:
        file_map: Rendered file content keyed by relative path.

    Returns:
        List of AuditFinding for agent files without an orchestrator handoff.
    """
    findings: list[AuditFinding] = []
    # Frameworks that do not support handoffs have them stripped from the body at render
    # time (`_strip_handoffs_section`), so demanding one here reports every agent file for a
    # section the pipeline deliberately removed. Silent while the filter was hardcoded to
    # `.agent.md`; 25 findings per `.md` framework the moment that was fixed.
    if not supports_handoffs:
        return findings
    for rel_path, content in file_map.items():
        if not _is_agent_file(rel_path, agent_ext):
            continue
        # The orchestrator itself doesn't need a return handoff to itself.
        # The team-builder is a meta entry-point agent, not a collaborating agent.
        slug = _agent_slug(rel_path, agent_ext)
        if slug in {"orchestrator", "team-builder"}:
            continue
        # Check YAML block for a handoff that routes to orchestrator
        if "agent: orchestrator" not in content and "agent: 'orchestrator'" not in content:
            findings.append(AuditFinding(
                category="AGENT_REFACTOR",
                code="AR_MISSING_RETURN_HANDOFF",
                severity="warning",
                file=rel_path,
                description=(
                    "Agent file has no 'Return to Orchestrator' handoff. "
                    "Add a handoff entry with 'agent: orchestrator' in the YAML front matter."
                ),
            ))
    return findings


def _check_readonly_tool_declarations(
    file_map: dict[str, str],
    *,
    agent_ext: str,
) -> list[AuditFinding]:
    """Check that self-declared read-only agents do not claim write tools.

    An agent whose body prose explicitly states it is 'read-only' must have
    only 'read' and 'search' in its tools declaration.

    Args:
        file_map: Rendered file content keyed by relative path.

    Returns:
        List of AuditFinding for read-only agents declaring write tools.
    """
    findings: list[AuditFinding] = []
    _tools_re = re.compile(r"tools\s*:\s*\[([^\]]*)\]")

    for rel_path, content in file_map.items():
        if not _is_agent_file(rel_path, agent_ext):
            continue
        # Only enforce rule on files that self-declare as read-only
        if not _READONLY_BODY_RE.search(content):
            continue
        m = _tools_re.search(content[:400])  # YAML block only
        if not m:
            continue
        declared_tools = {t.strip().strip("'\"") for t in m.group(1).split(",")}
        violations = declared_tools & _READWRITE_TOOLS
        if violations:
            findings.append(AuditFinding(
                category="AGENT_REFACTOR",
                code="AR_READONLY_TOOL_VIOLATION",
                severity="error",
                file=rel_path,
                description=(
                    f"Agent declares itself read-only but claims write tool(s): "
                    f"{', '.join(sorted(violations))}. Remove them from the tools list."
                ),
            ))
    return findings


