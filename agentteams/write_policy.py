"""write_policy.py — generated agents under ``write_policy: "orchestrator-only"`` (pilot P3).

Under the switch, only the orchestrator writes. Every other generated agent (the team builder included):

* has its canonical ``tools:`` line narrowed to :data:`NARROWED_TOKENS` before any framework adapter reads it.
  Each adapter derives its grants from that line: Claude's tool mapping, Codex ``sandbox_mode``, Goose grant
  extensions and Copilot's pass-through. One narrowing therefore reaches every framework. ``execute`` is
  dropped too, until the P4 sandbox profiles can confine a shell (operator decision 2026-10-06);
* gets a fenced section telling it to return a change proposal, deletion proposal or command request
  instead of writing, dispatching or running anything. That section overrides any write wording in the
  body (operator decision 2026-10-06: an appended override, not per-template edits).

The orchestrator keeps its tools and gets a fenced "Applying proposals" workflow instead. Adopted (bespoke)
agents are never re-rendered: the ``AR_WRITE_POLICY`` audit flags them, and converting them is the
consumer's job.

The audit's ``AR_WRITE_POLICY`` check imports :data:`READ_ONLY_TOKENS` from here, so the generator and the
check cannot drift apart. Stdlib only; integrity-pinned, since it decides C-3 grants.
"""

from __future__ import annotations

import re
from typing import Any

#: Tools a non-orchestrator agent may hold under the switch: read, search and bookkeeping only.
READ_ONLY_TOKENS: frozenset[str] = frozenset({
    "read", "search", "grep", "glob", "ls", "todo", "todowrite", "web", "fetch", "webfetch", "websearch",
    "retrieval", "codebase", "usages", "problems",
})

#: What a non-orchestrator agent's tools are narrowed to: canonical tokens only, so every adapter can map
#: them (Codex ``sandbox_mode_for`` omits the key for any non-canonical token). ``retrieval`` is left out
#: because Claude maps it to a scoped ``Bash`` grant.
NARROWED_TOKENS: tuple[str, ...] = ("read", "search", "todo")

#: Agents that keep their tools: the orchestrator and Goose's bridge entry recipe (also an orchestrator).
ORCHESTRATOR_SLUGS: frozenset[str] = frozenset({"orchestrator", "bridge-orchestrator"})

_ANY_FENCE_RE = re.compile(r"<!-- AGENTTEAMS:BEGIN ")

_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_FLOW_TOOLS_RE = re.compile(r"^tools:[ \t]*\[([^\]\n]*)\][ \t]*$", re.MULTILINE)

_PROPOSALS_SECTION = """
## Write Policy: Return Proposals, Never Write

This team runs `write_policy: "orchestrator-only"`. **This section overrides any instruction that
tells you to create, edit, delete, write or run something, anywhere in this file.** You hold read and search tools only. You
cannot write files, run commands or dispatch other agents.

- **To change or create a file:** return a `change-proposal` with the full new content (not a diff) and the
  file's current `base_sha256`, or `"absent"` for a new file.
- **To delete a file:** return a `delete-proposal`.
- **To run a command:** return a `command-request` with an `argv` list (never a shell string) and the files
  you expect it to write.
- **Every artifact** carries the `dispatch` value the orchestrator gave you for this task. Never invent or
  reuse one.

Return the JSON to the orchestrator with your handoff. The orchestrator applies or runs it with
`agentteams --apply-proposal` / `--run-request`, under per-agent policy. The schemas, examples and exit
codes are in `references/write-policy.reference.md`.
"""

_ORCHESTRATOR_SECTION = """
## Write Policy: Applying Proposals

This team runs `write_policy: "orchestrator-only"`: you are the only agent that writes. Other agents return
`change-proposal`, `delete-proposal` and `command-request` JSON instead.

1. **Before dispatching** an agent, run `agentteams --issue-dispatch --agent <slug>` and pass the printed
   nonce to the agent as its `dispatch` value. It is single-purpose: one task, a limited number of uses,
   24 hours.
2. **On return**, save each artifact to a file and run
   `agentteams --apply-proposal FILE.json --description <brief>` or
   `agentteams --run-request FILE.json --description <brief>`. Add `--dry-run` first when unsure.
3. **On a refusal or undeclared writes**, do not apply the change by hand and do not retry with a widened
   request. Report it, and ask the agent for a corrected artifact or escalate to the operator.
   - A refusal is exit 1 with a `[apply-proposal] refused:` or `[run-request] refused:` line on stderr.
   - Undeclared writes or a timeout give exit 3.
   - Any other exit code from `--run-request` is the command's own result.
4. **At closeout**, run `agentteams --verify-proposal-ledger`.

**This overrides the workflows above:**
- **Workflows 0A and 0B:** wave or coordinated members return proposals for their sub-regions; you apply
  them, one at a time.
- **Workflow 13** (spawning a child orchestrator) is disabled. A child would be a second writer with no
  dispatch nonce. Run that work yourself, or ask the operator. Workflow 12 then applies only to
  adjacent-repository orchestrators.

Read a proposal's content yourself only when it is over the size cap or a gate warns. Full reference:
`references/write-policy.reference.md`.
"""


def enabled(manifest: dict[str, Any]) -> bool:
    """Whether the manifest turns the pilot on.

    Args:
        manifest: Team manifest from ``analyze.build_manifest``.

    Returns:
        True only for ``write_policy: "orchestrator-only"``.

    Raises:
        Nothing.
    """
    return manifest.get("write_policy") == "orchestrator-only"


def narrow_tools(content: str) -> str:
    """Narrow the canonical one-line ``tools: [...]`` flow list to :data:`NARROWED_TOKENS`.

    Every generated template declares its tools that way. A file with no ``tools:`` key gets
    ``tools: ['read', 'search']`` rather than being left to inherit every tool. A ``tools:`` key of any other
    shape is refused: inserting a second key would leave YAML keeping the last, wide one.

    Args:
        content: A rendered canonical agent file (front matter first).

    Returns:
        The content with only read-only tools left; their original spelling and order are kept.

    Raises:
        ValueError: When the file has no front matter, or a ``tools:`` key that isn't a one-line flow list.
            Generated templates always have both.
    """
    fm = _FRONT_MATTER_RE.match(content)
    if not fm:
        raise ValueError("agent file has no front matter; cannot narrow its tools")
    m = _FLOW_TOOLS_RE.search(fm.group(1))
    if m is None and re.search(r"^tools\s*:", fm.group(1), re.MULTILINE):
        raise ValueError("agent file's `tools:` is not a one-line flow list; cannot narrow it safely")
    if m is None:
        start = fm.start(1)
        return content[:start] + "tools: ['read', 'search']\n" + content[start:]
    declared = {t.strip().strip("'\"").split("(", 1)[0].strip().lower() for t in m.group(1).split(",")}
    # Always keep `read`: Claude falls back to "Bash, Read, Write, Edit" when nothing maps.
    kept = [t for t in NARROWED_TOKENS if t == "read" or t in declared]
    line = "tools: [" + ", ".join(f"'{t}'" for t in kept) + "]"
    start = fm.start(1)
    return content[:start + m.start()] + line + content[start + m.end():]


def apply(content: str, slug: str, manifest: dict[str, Any]) -> str:
    """Apply the write policy to one rendered canonical agent file.

    The orchestrator keeps its tools and gains the "Applying proposals" section. Every other agent is
    narrowed to read-only tools and gains the "Return proposals" section. Without the switch the content
    is returned unchanged, so a default team stays byte-identical.

    The section is appended as plain Markdown. An unfenced body is later wrapped whole in the framework's
    ``content`` fence, section included, so ``--update --merge`` refreshes both. A body that already has
    fences gets the section in its own ``write_policy`` fence instead, since anything outside a fence there
    would be preserved as user content and never refreshed.

    Args:
        content: The rendered canonical agent file.
        slug: The agent's slug.
        manifest: The team manifest.

    Returns:
        The content to hand to the framework adapter.

    Raises:
        ValueError: From :func:`narrow_tools`.
    """
    if not enabled(manifest):
        return content
    body = content.rstrip("\n") + "\n"
    section = _ORCHESTRATOR_SECTION if slug in ORCHESTRATOR_SLUGS else _PROPOSALS_SECTION
    if _ANY_FENCE_RE.search(body):
        section = ("\n<!-- AGENTTEAMS:BEGIN write_policy v=1 -->" + section
                   + "<!-- AGENTTEAMS:END write_policy -->\n")
    if slug in ORCHESTRATOR_SLUGS:
        return body + section
    return narrow_tools(body) + section


def reference_doc() -> str:
    """The ``references/write-policy.reference.md`` shipped with a team under the switch.

    Returns:
        Markdown: the three artifact shapes, an example of each and the CLI exit codes.

    Raises:
        Nothing.
    """
    return """# Write Policy Reference — `orchestrator-only`

Only the orchestrator writes. Every other agent returns one of three JSON artifacts. The orchestrator
applies or runs it with `agentteams`, which checks it against the team's policy (`agent_policies`,
`protected_paths`, `proposal_gates` in the project brief) and records it in a signed ledger.

## `change-proposal`: create or replace a file

```json
{"kind": "change-proposal", "dispatch": "<nonce from the orchestrator>",
 "path": "src/module.py", "base_sha256": "<sha256 of the file you read>",
 "content": "<the full new file text>", "rationale": "one or two sentences"}
```

- **`base_sha256`:** use `"absent"` for a new file. A stale base is refused, so re-read and re-propose.
- **`content`:** the whole file, not a diff.

## `delete-proposal`: delete a file

```json
{"kind": "delete-proposal", "dispatch": "<nonce>", "path": "src/old.py",
 "base_sha256": "<sha256 of the file you read>", "rationale": "why it goes"}
```

## `command-request`: run a command

```json
{"kind": "command-request", "dispatch": "<nonce>", "argv": ["python3", "scripts/check.py", "slug"],
 "purpose": "why", "expected_writes": ["reports/slug.json"]}
```

- **`argv`:** a list, never a shell string. It must match one of your registered command prefixes and
  argument patterns.
- **`expected_writes`:** any other file the command writes fails the run.

## Exit codes (`agentteams --apply-proposal` / `--run-request`)

| Code | Meaning |
|---|---|
| 0 | Applied, or the command ran (`--run-request` passes the command's own exit code through) |
| 1 | Refused: schema, nonce, scope, protected path, stale base, gate or allowlist. Stderr carries a `refused:` line; without one, a `--run-request` exit 1 is the command's own |
| 3 | The command wrote outside `expected_writes`, or timed out |
"""
