"""audit_agent_contract.py — checks that a generated agent file honours the agent contract.

Carved out of ``audit`` when adding one check pushed that module past the CH-07 ceiling; it had
been sitting at 999 of 1000 lines with the ratchet holding it there, so any addition forced this.
The seam was already in the file: these checks all read a single agent file and ask whether
it still declares what an agent is required to declare — an Invariant Core, a return handoff,
tools consistent with its own read-only claim, a reachable instruction-authority ordering, and no
write-plus-dispatch pairing outside the roles the templates grant it to.
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
from typing import Any

from agentteams.audit_types import AuditFinding, _agent_slug, _is_agent_file
from agentteams.frameworks.goose_recipe_read import developer_tools, recipe_extension_grants
from agentteams.frameworks.goose_recipe_validate import _RECIPE_VERSION_RE
from agentteams.frameworks.goose_tool_scoping import DISABLED, declared_from_marker, grant_extensions

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




#: Non-orchestrator agents whose templates grant both a write tool and dispatch. Derived from the
#: ``tools:`` front matter in ``agentteams/templates/`` — every other template keeps the two
#: apart. ``tests/test_audit.py`` re-derives this set from the templates, so a template that gains
#: or loses the pairing fails a test until this constant is updated with it.
_WRITER_DISPATCH_ALLOWLIST: frozenset[str] = frozenset({
    "agent-refactor",
    "agent-updater",
    "repo-liaison",
    "work-summarizer",
})

#: Write tokens across frameworks: copilot-vscode ``edit``/``write``/``create`` and Claude Code's
#: ``Edit``/``Write`` (compared lower-cased), plus the Claude editors that also write files.
_WRITE_TOKENS = frozenset({"edit", "write", "create", "multiedit", "notebookedit"})

#: Dispatch tokens: copilot-vscode ``agent`` and Claude Code's ``Task`` (also exposed as ``Agent``).
_DISPATCH_TOKENS = frozenset({"agent", "task"})

_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_TOOLS_LINE_RE = re.compile(r"^tools\s*:[ \t]*(.*)$", re.MULTILINE)


def _declared_tool_tokens(content: str) -> set[str]:
    """Lower-cased tool names from an agent file's ``tools:`` front-matter key.

    Reads all three shapes the frameworks emit or a hand-written agent may use: a flow list
    (``tools: ['read', 'edit']``), Claude Code's comma string (``tools: Read, Edit, Task``) and a
    YAML block list. A scoped grant such as ``Bash(python -m x:*)`` reduces to ``bash``.
    """
    fm = _FRONT_MATTER_RE.match(content)
    if not fm:
        return set()
    m = _TOOLS_LINE_RE.search(fm.group(1))
    if not m:
        return set()
    value = m.group(1).strip()
    if value:
        items = value.strip("[]").split(",")
    else:
        items = []
        for line in fm.group(1)[m.end():].splitlines()[1:]:
            stripped = line.strip()
            if not stripped.startswith("- "):
                break
            items.append(stripped[2:])
    tokens: set[str] = set()
    for item in items:
        name = item.strip().strip("'\"").split("(", 1)[0].strip().lower()
        if name:
            tokens.add(name)
    return tokens


def _check_writer_dispatch_grants(
    file_map: dict[str, str],
    *,
    agent_ext: str,
) -> list[AuditFinding]:
    """Check that no non-orchestrator agent holds both a write tool and dispatch.

    agentteams' own templates keep the two apart for every domain agent: the orchestrator holds
    dispatch, and a writer hands back to it rather than spawning agents itself. Only the roles in
    ``_WRITER_DISPATCH_ALLOWLIST`` combine them. The check applies the same constraint to every
    agent file in the team, including bespoke agents a project adopted, so an adopted agent is
    held to what agentteams requires of the agents it generates.

    ``severity="warning"``, for the reason ``_check_instruction_authority_reachable`` gives:
    existing consumers' adopted agents predate the check, and failing their audit would break
    builds for teams that did nothing wrong when they were written.

    Args:
        file_map: Rendered file content keyed by relative path.
        agent_ext: The framework's agent-file extension.

    Returns:
        List of AuditFinding for agents outside the allowlist that hold both grants.
    """
    findings: list[AuditFinding] = []
    for rel_path, content in file_map.items():
        if not _is_agent_file(rel_path, agent_ext):
            continue
        slug = _agent_slug(rel_path, agent_ext)
        if slug == "orchestrator" or slug in _WRITER_DISPATCH_ALLOWLIST:
            continue
        tokens = _declared_tool_tokens(content)
        writes = tokens & _WRITE_TOKENS
        dispatch = tokens & _DISPATCH_TOKENS
        if writes and dispatch:
            findings.append(AuditFinding(
                category="AGENT_REFACTOR",
                code="AR_WRITER_DISPATCH",
                severity="warning",
                file=rel_path,
                description=(
                    f"'{slug}' holds write tool(s) ({', '.join(sorted(writes))}) and dispatch "
                    f"({', '.join(sorted(dispatch))}). The orchestrator holds dispatch for "
                    "writers; a writer returns to the orchestrator through a handoff instead of "
                    "dispatching agents itself. Drop the dispatch grant."
                ),
            ))
    return findings


# --- Goose recipes: exposure must match the declared tools (goose_tool_scoping) -----------------

#: The ``developer`` tools that change the workspace (``shell`` writes and executes).
_GOOSE_WRITE_TOOLS = frozenset({"write", "edit", "shell"})


#: Extensions a grant recipe may carry besides those its declared tools grant: the first-party
#: coordination server (wired by team configuration, record-only).
_GOOSE_FIRST_PARTY_EXTRAS = frozenset({"agentteams_coordination"})
#: Extension types an operator MCP server is emitted as (goose.py ``_goose_extension_for``).
_GOOSE_OPERATOR_TYPES = frozenset({"stdio", "streamable_http"})
#: Recipes outside grant scoping (bridge entry recipes keep the legacy extensions).
_GOOSE_UNSCOPED_RECIPES = frozenset({"bridge-orchestrator.yaml"})


def _goose_exceeded(
    extensions: list[dict[str, Any]], declared: frozenset[str], extra_names: frozenset[str] = frozenset()
) -> list[str]:
    """List what a parsed recipe exposes beyond what its declared tools grant (empty when it matches)."""
    names, allow, stdio = grant_extensions(declared)
    problems = []
    permitted = set(names) | {e["name"] for e in stdio} | _GOOSE_FIRST_PARTY_EXTRAS
    # An operator MCP server is always emitted as an MCP transport, so its name excuses only an entry of
    # that type: a `type: builtin` (or platform) entry named like an operator server is still a builtin.
    unknown = sorted({
        e["name"] for e in extensions
        if e["name"] not in permitted
        and not (e["name"] in extra_names and e.get("type") in _GOOSE_OPERATOR_TYPES)
    })
    if unknown:
        problems.append(f"extension(s) not granted by the declared tools: {', '.join(unknown)}")
    extra_dev = developer_tools(extensions) - set(allow.get("developer", []))
    if extra_dev:
        problems.append(f"developer {', '.join(sorted(extra_dev))}")
    for ext in extensions:
        if ext["name"] == "summon":
            granted = set(allow.get("summon", []))
            exposed = {"delegate", "load"} if ext["available_tools"] is None else set(ext["available_tools"])
            if exposed - granted:
                problems.append(f"summon {', '.join(sorted(exposed - granted))}")
    if developer_tools(extensions) and not any(
        e["name"] == "analyze" and e["available_tools"] == [DISABLED] for e in extensions
    ):
        problems.append("analyze (auto-added beside developer; must be listed [__none__])")
    return problems


def _check_goose_recipe_grants(
    file_map: dict[str, str],
    *,
    agent_ext: str,
    grant_mode: bool,
    operator_extensions: dict[str, frozenset[str]] | None = None,
) -> list[AuditFinding]:
    """Check Goose recipes' extensions against what their agents are meant to hold.

    * ``AR_GOOSE_GRANT_EXCEEDED`` (error): a grant-mode recipe, identified by its
      ``# agentteams-declared-tools:`` marker, exposes more than its declared tools grant: extra
      ``developer`` tools, ``summon`` without ``agent``, ``analyze`` left on beside ``developer``, or any
      extension the declared tools do not grant (operator MCP servers and the coordination server excepted).
      This keys on declared tools, not prose. Residual: the marker states its own authority, so editing the
      marker and the extensions together passes this check; recipes are hash-tracked in the build log, so
      ``--check`` reports such a hand edit.
    * ``AR_GOOSE_MARKER_MISSING`` (error, grant mode): a recipe without exactly one marker (bridge
      recipes excepted), so the grant check cannot run.
    * ``AR_GOOSE_READONLY_WRITE`` (warning): an unmarked recipe (legacy, bridge or hand-written) whose
      instructions self-declare read-only exposes ``developer`` ``write``, ``edit`` or ``shell``.
    * ``AR_GOOSE_EXTENSIONS_FAIL_OPEN``: a missing, bare or null ``extensions``, which Goose 1.37 reads as
      "load the user's configured extensions". An error in grant mode, a warning in legacy.

    Unparseable ``extensions`` shapes are read as an unscoped ``developer`` (worst case).

    Args:
        file_map: Rendered file content keyed by relative path.
        agent_ext: The framework's agent file extension; only ``.yaml`` (Goose) is checked.
        grant_mode: Whether the team uses ``goose_tool_scoping: "grant"``.
        operator_extensions: Agent slug -> operator MCP extension names scoped to that agent (the adapter
            wires a server only into the agents its ``scope`` lists, so only those may carry it).

    Returns:
        The findings.

    Raises:
        Nothing.
    """
    if agent_ext != ".yaml":
        return []
    advice = "" if grant_mode else ' Set "goose_tool_scoping": "grant" in the brief and re-render.'
    findings: list[AuditFinding] = []
    for rel_path, content in file_map.items():
        if not rel_path.endswith(".yaml") or not _RECIPE_VERSION_RE.search(content):
            continue
        extensions = recipe_extension_grants(content)
        if extensions is None:
            findings.append(AuditFinding(
                category="AGENT_REFACTOR", code="AR_GOOSE_EXTENSIONS_FAIL_OPEN",
                severity="error" if grant_mode else "warning", file=rel_path,
                description=(
                    "Recipe has a missing, empty or null `extensions`, which Goose reads as \"load the user's "
                    "configured extensions\" (usually full developer, including shell). Emit `extensions: []` "
                    "for an agent granted nothing." + advice
                ),
            ))
            continue
        declared = declared_from_marker(content)
        if declared is None and grant_mode and rel_path.rsplit("/", 1)[-1] not in _GOOSE_UNSCOPED_RECIPES:
            # Without exactly one marker a grant recipe cannot be checked against its grant; deleting or
            # doubling the marker must not quietly downgrade it to the prose check.
            findings.append(AuditFinding(
                category="AGENT_REFACTOR", code="AR_GOOSE_MARKER_MISSING", severity="error", file=rel_path,
                description=(
                    "Grant-mode recipe lacks exactly one `# agentteams-declared-tools:` marker, so its "
                    "extensions cannot be checked against the agent's declared tools. Re-render it."
                ),
            ))
            continue
        if declared is not None:
            slug = rel_path.rsplit("/", 1)[-1][: -len(".yaml")]
            problems = _goose_exceeded(extensions, declared, (operator_extensions or {}).get(slug, frozenset()))
            if problems:
                findings.append(AuditFinding(
                    category="AGENT_REFACTOR", code="AR_GOOSE_GRANT_EXCEEDED", severity="error",
                    file=rel_path,
                    description=(
                        f"Recipe exposes more than its declared tools ({', '.join(sorted(declared)) or 'none'}) "
                        f"grant: {'; '.join(problems)}. Re-render it; do not widen recipes by hand."
                    ),
                ))
            continue
        if not _READONLY_BODY_RE.search(content):
            continue
        writes = developer_tools(extensions) & _GOOSE_WRITE_TOOLS
        if writes:
            findings.append(AuditFinding(
                category="AGENT_REFACTOR", code="AR_GOOSE_READONLY_WRITE", severity="warning",
                file=rel_path,
                description=(
                    f"Agent declares itself read-only but its Goose recipe exposes developer "
                    f"{', '.join(sorted(writes))}." + advice
                ),
            ))
    return findings
