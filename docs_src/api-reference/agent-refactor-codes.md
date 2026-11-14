# `agent_refactor_codes` — AgentTeamsModule

The opt-in typed violation codes for `@agent-refactor`, from the brief's optional `agent_refactor` block. A brief
that sets `agent_refactor.violation_codes: true` gives the agent-refactor file a "Violation Codes" section with the
book's Chapter 13 codes, AR-01 to AR-10, each with the check that fires it. AR-05 fires above
`agent_refactor.line_budget` lines (default 200). Findings are reported as code / file / evidence rows plus one
`Counts:` line per code.

> Source: `agentteams/agent_refactor_codes.py` (stdlib only). With the codes off, `{AGENT_REFACTOR_VIOLATION_CODES}`
> resolves to `""`, so the render is byte-identical to a team without the block.

## Constants

| Name | Value |
|---|---|
| `PLACEHOLDER` | `"AGENT_REFACTOR_VIOLATION_CODES"`, the placeholder at the end of the template's Part 2 |
| `DEFAULT_LINE_BUDGET` | `200`, the AR-05 line budget when the brief gives none |

## Functions

| Function | Purpose |
|---|---|
| `manifest_fields(description)` | Called by `analyze`: `{"agent_refactor": {"violation_codes": True, "line_budget": n}}` when the codes are on, `{}` otherwise. Raises `ValueError` when the codes are on and `line_budget` is a bool, not an integer, or below 1 |
| `section(description)` | Called by `analyze` to fill the placeholder: the Violation Codes section with the budget substituted, or `""` when the codes are off. Goes through `manifest_fields`, so it raises on the same bad budgets |

## Merge behaviour

The section has no fence of its own. The agent-refactor template is unfenced, so the emitter wraps the whole body in
the `content` fence, and `--update --merge` replaces that fence in full. Turning the switch on adds the section on the
next update. Turning it off removes the section, and the merge reports a shrink notice for `content`.
