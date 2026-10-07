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
import tomllib
from pathlib import Path
from typing import Any

from agentteams.audit_types import AuditFinding, _agent_slug, _is_agent_file
from agentteams.front_matter_merge import CAPABILITY_FRONT_MATTER_KEYS
from agentteams.frameworks.goose_recipe_read import developer_tools, recipe_extension_grants
from agentteams.frameworks.goose_recipe_validate import _RECIPE_VERSION_RE
from agentteams.frameworks.goose_tool_scoping import (
    DISABLED, READFS_NAME, declared_from_marker, grant_extensions, readfs_extension,
)
from agentteams.write_policy import ORCHESTRATOR_SLUGS as _ORCHESTRATOR_SLUGS
from agentteams.write_policy import READ_ONLY_TOKENS as _READ_ONLY_TOKENS

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
#: coordination server (wired by team configuration; it reads and records, so it writes request/log files).
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


# --- write_policy "orchestrator-only": only the orchestrator writes (pilot P2) ------------------

#: Shell tokens: copilot ``execute``, Claude Code's ``Bash``, Goose ``developer`` ``shell``.
_SHELL_TOKENS = frozenset({"execute", "bash", "shell"})
#: Frameworks with no per-agent sandbox: a shell there is unconfined, so it is an error, not a warning
#: (operator decision 2026-10-06: Copilot drops ``execute`` under the switch).
#: Markdown-agent frameworks where a declared shell is a warning (until P4); for any other markdown framework,
#: unknown ids included, it is an error. Goose recipes take their own branch, where a shell always warns.
_SHELL_WARN_FRAMEWORKS = frozenset({"claude"})
#: Slugs exempt from the policy: only the single shallowest file per slug (a deeper copy is checked like any
#: agent, and two equally shallow copies are both checked).
_WRITER_SLUGS = frozenset({"orchestrator"})
_GOOSE_WRITER_SLUGS = _ORCHESTRATOR_SLUGS  # single source with the generator (write_policy.ORCHESTRATOR_SLUGS)
#: Goose extensions a non-orchestrator recipe may carry: only the read-only file server. The coordination
#: server is not one: it writes request/log files, so the generator withholds it from these recipes
#: (``goose.py``, "restricted"), and the audit agrees. ``developer`` and ``analyze`` are judged by their
#: tools; any other extension is refused.
_GOOSE_READ_EXTENSIONS = frozenset({"agentteams_readfs"})
#: The real type of each built-in name a non-orchestrator recipe may carry (Goose 1.37; as goose_recipe_emit
#: renders them). A matching name with another type is a different extension.
_GOOSE_BUILTIN_TYPES = {"developer": "builtin", "analyze": "platform", "summon": "platform"}
#: The only keys the shipped readfs entry has (goose_tool_scoping.readfs_extension, as rendered).
_READFS_KEYS = frozenset({"type", "name", "cmd", "args", "timeout", "available_tools"})
#: ``tools:`` values YAML reads as null: the key is then effectively absent and the agent inherits every tool.
_NULL_TOOLS = frozenset({"", "~", "null", "''", '""'})
#: Capability front-matter keys a non-orchestrator markdown agent may carry under the switch. Every other key
#: in ``CAPABILITY_FRONT_MATTER_KEYS`` is an error, so a key added there later is refused by default.
#: ``tools`` is judged on its own; ``model`` grants nothing; ``disallowedTools`` only narrows; ``agents`` is
#: Copilot's subagent roster, inert without a dispatch tool (itself an error); ``permissionMode`` is judged
#: on its value.
_WRITE_POLICY_OK_KEYS = frozenset({"tools", "model", "disallowedTools", "agents", "permissionMode"})
_WRITE_POLICY_BAD_KEYS = CAPABILITY_FRONT_MATTER_KEYS - _WRITE_POLICY_OK_KEYS
#: Claude ``permissionMode`` values that relax nothing. Any other value (``acceptEdits``, ``bypassPermissions``,
#: ``dontAsk``, anything unknown) is an error.
_SAFE_PERMISSION_MODES = frozenset({"default", "plan"})
_FM_KEY_RE = re.compile(r"""^["']?([A-Za-z][\w-]*)["']?[ \t]*:[ \t]*(.*)$""", re.MULTILINE)
#: Top-level YAML a key regex can't read: an explicit key (``? k``), a merge key (``<<:``), an anchor, alias
#: or tag before a key, or a quoted key with an escape (``"perm\u0069ssionMode"``). Any of them could hide a
#: capability key, so each fails closed.
_FM_OPAQUE_KEY_RE = re.compile(r"""^(?:[?&*!]|<<|["'][^"'\n]*\\)""", re.MULTILINE)


def _capability_key_problems(content: str) -> list[str]:
    """Capability-granting front-matter keys, other than ``tools:``, that defeat the read-only narrowing.

    An inline ``mcpServers`` entry starts a process when the subagent starts, ``hooks`` run shell at tool
    events, ``skills`` preload instructions and scripts, ``memory`` turns on Read/Write/Edit, ``allowed-tools``
    is a legacy grant some hosts still read, and a relaxed ``permissionMode`` skips permission checks. None of
    these is visible in ``tools:``. YAML key forms the key regex can't read fail closed.
    """
    fm = _FRONT_MATTER_RE.match(content)
    if not fm:
        return []
    problems: list[str] = []
    opaque = _FM_OPAQUE_KEY_RE.search(fm.group(1))
    if opaque:
        line = fm.group(1)[opaque.start():].split("\n", 1)[0][:40]
        problems.append(f"has a front-matter key this check can't read ({line!r}: an explicit, merge, anchored, "
                        "tagged or escaped key); write plain `key: value` lines")
    for m in _FM_KEY_RE.finditer(fm.group(1)):
        key, value = m.group(1), m.group(2).split(" #", 1)[0].strip().strip("'\"")
        if key in _WRITE_POLICY_BAD_KEYS:
            problems.append(f"declares `{key}:`, a capability-granting key the read-only narrowing doesn't cover")
        elif key == "permissionMode" and value not in _SAFE_PERMISSION_MODES:
            problems.append(f"declares `permissionMode: {value or '(empty)'}`; only "
                            f"{' or '.join(sorted(_SAFE_PERMISSION_MODES))} is allowed under the switch")
    return problems


def _tools_key_problem(content: str) -> str | None:
    """Why the front matter's ``tools:`` cannot be trusted, else ``None``.

    Fails closed on every shape :func:`_declared_tool_tokens` might misread: absent or null (the agent
    inherits every tool), duplicated, a flow list spanning lines, a block scalar, or a block list with
    anything but ``- item`` lines.
    """
    fm = _FRONT_MATTER_RE.match(content)
    matches = list(_TOOLS_LINE_RE.finditer(fm.group(1))) if fm else []
    if len(matches) > 1:
        return "declares `tools:` more than once (YAML keeps the last; this audit can't tell which one runs)"
    if not matches:
        return "has no `tools:` declaration, so the agent inherits every tool, writes included"
    value = matches[0].group(1).split(" #", 1)[0].strip()
    if value[:1] in (">", "|", "{", "&", "*", "!"):
        return f"has a `tools:` value of a shape this check can't read ({value[:12]!r})"
    if value.replace(" ", "") == "[]":
        return "has an empty `tools:` list, which a host may read as 'inherit every tool'; declare read tools"
    # The lines up to the next top-level key belong to this value. Only two shapes are read the same way by
    # YAML and by _declared_tool_tokens: a one-line value with nothing after it, or a block list of
    # consecutive `- item` lines. Anything else (a continued scalar, a multi-line flow list, a blank or
    # comment line inside the list) is refused rather than guessed at.
    tail = []
    for line in fm.group(1)[matches[0].end():].splitlines()[1:]:
        if line.strip() and not line[:1].isspace() and not line.startswith(("-", "#")):
            break  # the next key (a column-0 comment is not one: YAML skips it and keeps reading the list)
        tail.append(line)
    while tail and not tail[-1].strip():
        tail.pop()
    if value.lower() not in _NULL_TOOLS:
        if tail:
            return "has a `tools:` value continued on later lines, which this check can't read; use one line"
        return None
    if not tail:
        return "has a null `tools:` value, so the agent inherits every tool, writes included"
    if any(not line.strip().startswith("- ") for line in tail):
        return "has a `tools:` block list with lines other than consecutive `- item`, which this check can't read"
    return None


def _write_policy_problems(content: str, agent_ext: str, framework: str) -> tuple[list[str], list[str]]:
    """Return ``(errors, warnings)`` for one non-orchestrator agent file under the switch."""
    errors: list[str] = []
    warnings: list[str] = []
    if agent_ext == ".toml":
        try:
            data = tomllib.loads(content)
        except tomllib.TOMLDecodeError as exc:
            return [f"is not valid TOML ({exc}), so its sandbox_mode cannot be read"], warnings
        mode = data.get("sandbox_mode")
        if data.get("mcp_servers"):
            errors.append("declares mcp_servers, which sandbox_mode does not confine")
        if mode != "read-only":
            errors.append(f"sandbox_mode is {mode!r}, not 'read-only' (a missing key inherits the parent's mode)")
        return errors, warnings
    if agent_ext == ".yaml":
        # Judge what the recipe actually exposes, not only its marker (the marker states its own authority).
        extensions = recipe_extension_grants(content)
        if extensions is None:
            return ["recipe has missing or null `extensions`, so Goose loads the user's extensions"], warnings
        dev = developer_tools(extensions)
        declared = declared_from_marker(content) or frozenset()
        writes = (dev & (_GOOSE_WRITE_TOOLS - {"shell"})) | (declared & _READWRITE_TOOLS)
        if writes:
            errors.append(f"recipe grants {', '.join(sorted(writes))}")
        summon = [e for e in extensions if e["name"] == "summon" and e["available_tools"] != [DISABLED]]
        if summon or "agent" in declared:
            errors.append("recipe grants summon (dispatch), which can start a sub-recipe that writes")
        if re.search(r"^[\"']?sub_recipes[\"']?\s*:", content, re.MULTILINE):
            errors.append("recipe declares sub_recipes (dispatch), which can run a sub-recipe that writes")
        others = sorted({e["name"] for e in extensions}
                        - _GOOSE_READ_EXTENSIONS - {"developer", "analyze", "summon"})
        if others:
            errors.append(f"recipe carries extension(s) this check can't classify as read-only: {', '.join(others)}")
        # Built-in names are trusted only with their real type: an entry named `analyze` but of type `stdio` would
        # make Goose start whatever `cmd` it names.
        for ext in extensions:
            want = _GOOSE_BUILTIN_TYPES.get(ext["name"])
            if want and ext["type"] != want:
                errors.append(f"recipe's `{ext['name']}` is type {ext['type'] or '(none)'!r}, not {want!r}; "
                              "Goose would start a different extension under that name")
        # The read server is allowed by what it runs, not by its name: a recipe could reuse the name for any
        # program. It must match the shipped entry (type, cmd, args), carry no other keys (`env`, `envs`,
        # `env_keys` or `cwd` could make the same script run other code) and grant only read tools.
        shipped = readfs_extension(protected=True)  # this check runs only under the switch
        for ext in (e for e in extensions if e["name"] == READFS_NAME):
            extra = sorted(set(ext["keys"]) - _READFS_KEYS)
            if (ext["type"], ext["cmd"], ext["args"]) != (shipped["type"], shipped["cmd"], shipped["args"]):
                errors.append(f"recipe's {READFS_NAME} doesn't launch the shipped server "
                              f"({shipped['cmd']} {' '.join(shipped['args'])}); another program could run under its name")
            elif extra or len(ext["keys"]) != len(set(ext["keys"])):
                errors.append(f"recipe's {READFS_NAME} carries keys the shipped entry doesn't "
                              f"({', '.join(extra) or 'a duplicated key'}), which could change what it runs")
            elif ext["available_tools"] and set(ext["available_tools"]) - set(shipped["available_tools"]):
                errors.append(f"recipe's {READFS_NAME} lists tools the shipped server doesn't have")
        # Goose adds `analyze` beside `developer` unless the recipe lists it, `[]` means unrestricted, and
        # `analyze` reads outside the workspace (Goose 1.37 spike B1). Only a non-matching allowlist turns it
        # off, and every `analyze` entry must do so (a reader may take either of two).
        analyzes = [e for e in extensions if e["name"] == "analyze"]
        if ("developer" in {e["name"] for e in extensions} or analyzes) and (
                not analyzes or any(e["available_tools"] != [DISABLED] for e in analyzes)):
            errors.append(f"recipe leaves `analyze` on (Goose adds it beside developer), which reads outside the "
                          f"workspace; list it, once, with the flow-style available_tools: [{DISABLED}]")
        if "shell" in dev or "execute" in declared:
            warnings.append("recipe grants a shell, which is unconfined until the P4 sandbox profiles")
        return errors, warnings
    errors.extend(_capability_key_problems(content))
    problem = _tools_key_problem(content)
    if problem:
        return errors + [problem], warnings
    tokens = _declared_tool_tokens(content)
    writes, shell, dispatch = tokens & _WRITE_TOKENS, tokens & _SHELL_TOKENS, tokens & _DISPATCH_TOKENS
    unknown = tokens - _READ_ONLY_TOKENS - _WRITE_TOKENS - _SHELL_TOKENS - _DISPATCH_TOKENS
    if writes:
        errors.append(f"declares write tool(s) {', '.join(sorted(writes))}")
    if dispatch:
        errors.append(f"declares dispatch ({', '.join(sorted(dispatch))}), which can start a subagent that writes")
    if unknown:
        errors.append(f"declares tool(s) this check can't classify as read-only: {', '.join(sorted(unknown))}")
    if shell and framework not in _SHELL_WARN_FRAMEWORKS:
        errors.append(f"declares {', '.join(sorted(shell))}, and {framework} has no per-agent sandbox")
    elif shell:
        warnings.append(f"declares {', '.join(sorted(shell))}, which is unconfined until the P4 sandbox profiles")
    return errors, warnings


def _check_write_policy(
    file_map: dict[str, str],
    *,
    agent_ext: str,
    framework: str,
    enabled: bool,
    unreadable: list[str] | None = None,
) -> list[AuditFinding]:
    """Under ``write_policy: "orchestrator-only"``, check that only the orchestrator can write.

    ``AR_WRITE_POLICY`` findings, per non-orchestrator agent file (generated or adopted, when present in
    *file_map*):

    * **error (markdown agents):** a write tool; dispatch (``Task``/``agent`` can start a subagent that
      writes); a tool not known to be read-only; a ``tools:`` key that is absent, null, empty, duplicated or
      of any shape other than one line or a clean ``- item`` block list; a shell on any framework but claude;
      a capability key other than ``tools`` (``mcpServers``, ``hooks``, ``skills``, ``memory``,
      ``allowed-tools``, ``capabilities``, or any later key in ``CAPABILITY_FRONT_MATTER_KEYS``) or a ``permissionMode`` other
      than ``default``/``plan``; a front-matter key the check can't read (explicit, merge, anchored, tagged or
      escaped).
    * **error (Codex):** ``sandbox_mode``, parsed as TOML, other than ``"read-only"`` (missing included); any
      ``mcp_servers``; invalid TOML. Every ``.toml`` is checked, ``references/`` included, and the exemption
      needs ``name`` to match.
    * **error (Goose):** missing ``extensions``; ``developer`` ``edit``/``write`` (or a marker declaring them);
      ``summon`` or ``sub_recipes`` (dispatch); any extension other than ``developer``, ``analyze``,
      ``summon`` and ``agentteams_readfs`` (the coordination server writes files, so it is refused too); a
      built-in name with the wrong type (``developer`` builtin; ``analyze``/``summon`` platform); an
      ``agentteams_readfs`` that doesn't launch the shipped server (type, ``cmd``, ``args``), carries other keys
      (``env``, ``envs``, ``env_keys``, ``cwd`` ...) or lists tools it lacks; any ``analyze`` entry not listed
      ``[__none__]``, or none at all beside ``developer``. Residue: ``python3`` resolves via PATH and
      ``scripts/`` is editable, so the exact match pins the entry, not the program it runs.
    * **error (disk audit):** a symlink or an unreadable agent file in the team directory.
    * **warning:** a shell elsewhere (Claude ``Bash``, Goose ``shell``). Nothing confines it before P4, so
      no brief field silences this.
    * **error, once:** ``agents-md``, which declares no per-agent tools and so cannot be checked.

    Only the shallowest ``orchestrator`` and ``bridge-orchestrator`` file is exempt; a deeper or equally
    shallow second copy is checked like any agent, so naming a file ``orchestrator`` elsewhere exempts nothing.

    Args:
        file_map: Team file content keyed by relative path.
        agent_ext: The framework's agent-file extension.
        framework: The framework id from the manifest.
        enabled: Whether the manifest carries ``write_policy: "orchestrator-only"``.
        unreadable: Team paths the disk loader could not read (symlinks, non-UTF-8). A host may still load
            them as agents, so each is an error rather than a silent skip.

    Returns:
        The findings (none when the switch is off).

    Raises:
        Nothing.
    """
    if not enabled:
        return []
    if framework == "agents-md":
        return [AuditFinding(
            category="AGENT_REFACTOR", code="AR_WRITE_POLICY", severity="error", file="AGENTS.md",
            description=("write_policy \"orchestrator-only\" cannot be enforced on agents-md: it declares no "
                         "per-agent tools. Use a framework with per-agent tool declarations."),
        )]
    def is_agent(rel_path: str, content: str) -> bool:
        if agent_ext == ".yaml":
            return rel_path.endswith(".yaml")  # every recipe, not only those with an exact version line
        if agent_ext == ".toml":
            return rel_path.endswith(".toml")  # Codex loads every .toml under the agents dir, references/ too
        return _is_agent_file(rel_path, agent_ext)

    def codex_name(content: str) -> object:
        try:
            return tomllib.loads(content).get("name")
        except tomllib.TOMLDecodeError:
            return None

    agents = sorted((p, c) for p, c in file_map.items() if is_agent(p, c))
    exempt: set[str] = set()
    for slug in _GOOSE_WRITER_SLUGS if framework == "goose" else _WRITER_SLUGS:
        # Codex identifies an agent by its `name`, not its filename, so an exempt file must carry its own slug.
        depths = sorted((Path(p).as_posix().count("/"), p) for p, c in agents if _agent_slug(p, agent_ext) == slug
                        and (agent_ext != ".toml" or codex_name(c) == slug))
        if depths and (len(depths) == 1 or depths[0][0] < depths[1][0]):
            exempt.add(depths[0][1])
    findings: list[AuditFinding] = [
        AuditFinding(
            category="AGENT_REFACTOR", code="AR_WRITE_POLICY", severity="error", file=rel_path,
            description=("write_policy \"orchestrator-only\": this path is a symlink or not UTF-8, so the audit "
                         "can't read it, but the host may still load it as an agent. Replace it with a regular "
                         "UTF-8 file."),
        )
        for rel_path in sorted(unreadable or [])
    ]
    for rel_path in sorted(p for p in exempt if _agent_slug(p, agent_ext) == "orchestrator"):
        # The exempt orchestrator must carry the duties that make it the only writer (P5a): a render or a hand
        # edit that loses the "Applying Proposals" section leaves the team with a writer that has no workflow.
        text = file_map.get(rel_path, "")
        notes = re.search(r"^[ \t]*## Project-Specific Notes[ \t]*$", text, re.MULTILINE)
        duties = re.search(r"^[ \t]*## Write Policy: Applying Proposals[ \t]*$", text, re.MULTILINE)
        notes_at = notes.start() if notes else -1
        duties_at = duties.start() if duties else -1
        # Anchored: the heading itself, before any user notes (text a user typed in the notes can't count).
        if duties_at == -1 or (notes_at != -1 and duties_at > notes_at):
            findings.append(AuditFinding(
                category="AGENT_REFACTOR", code="AR_WRITE_POLICY", severity="error", file=rel_path,
                description=('write_policy "orchestrator-only": the orchestrator lacks its "Write Policy: '
                             'Applying Proposals" section (its duties as the only writer). Re-render it.'),
            ))
    for rel_path, content in agents:
        if rel_path in exempt:
            continue
        errors, warnings = _write_policy_problems(content, agent_ext, framework)
        for severity, problems in (("error", errors), ("warning", warnings)):
            for problem in problems:
                findings.append(AuditFinding(
                    category="AGENT_REFACTOR", code="AR_WRITE_POLICY", severity=severity, file=rel_path,
                    description=(f"write_policy \"orchestrator-only\": '{_agent_slug(rel_path, agent_ext)}' "
                                 f"{problem}. Non-orchestrator agents return a change proposal or command "
                                 "request instead."),
                ))
    return findings
