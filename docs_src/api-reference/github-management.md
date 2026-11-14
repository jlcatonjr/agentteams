# `github_management` — AgentTeamsModule

The constitution's *GitHub management* section, from the brief's `github_management` (default `true`). Every
generated team's instruction file carries the fenced `github_management_rule` section, whose body this module
fills through the `{GITHUB_MANAGEMENT_RULE}` placeholder.

- **On (the default).** Routine GitHub work through `gh` and `git` is authorized for the project's own repository,
  allocated by role: `@git-operations` writes, `@pr-manager` owns the PR lifecycle (never merges), and the orchestrator
  may run read-only `gh` queries. Agents ask when an action looks risky, and always for the C-5 classes, the default
  branch, `release/*`, tags, signing and cross-repository pins. The section is never consent to a gate prompt.
- **Off** (`false`): a notice that every GitHub write goes to the operator first.

Nothing is widened: no `tools:` line changes, no MCP server is activated and every gate prompt stays. The one merge
without the gate's prompt is `agentteams --merge-pr` under the operator-signed integration-branch merge
authorization (`agentteams/cli/dev_merge.py`; see the CLI reference).

> Source: `agentteams/github_management.py` (stdlib only).

## Functions

| Function | Purpose |
|----------|---------|
| `enabled(description)` | Whether the brief authorizes GitHub management; `ValueError` for a non-boolean value. |
| `section_body(description)` | The section text for this brief (the rules, or the off notice). |
| `placeholders(description)` | `{"GITHUB_MANAGEMENT_RULE": section_body(description)}`. |
