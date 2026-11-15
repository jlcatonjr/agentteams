# `github_management` — AgentTeamsModule

The constitution's *GitHub management* section, from the brief's `github_management` (default `true`). Every
generated team's instruction file carries the fenced `github_management_rule` section, whose body this module
fills through the `{GITHUB_MANAGEMENT_RULE}` placeholder.

- **On (the default).** Routine GitHub work through `gh` and `git` is authorized for the project's own repository,
  allocated by role: `@git-operations` writes, `@pr-manager` owns the PR lifecycle (never merges), and the orchestrator
  may run read-only `gh` queries. Agents ask when an action looks risky, and always for the C-5 classes, the default
  branch, `release/*`, tags, signing and cross-repository pins. The section is never consent to a gate prompt.
- **Off** (`false`): a notice that every GitHub write goes to the operator first.

When on, the section also says **what counts as the operator's approval** for the actions that guard the default
branch. A merge into the default branch, `main`, `master`, `release/*` or a tag needs a live, in-session answer naming
the PR number and head SHA, and never a file, grant or directive. Switching the default branch's required checks runs
only as `python -m agentteams.release_required_checks --apply <fingerprint>` (see
[`release_required_checks`](release-required-checks.md)). Three more placeholders carry that rule into fenced regions:
`{GITHUB_MGMT_PR_CLEARANCE}` (`@pr-manager` rule 2), `{GITHUB_MGMT_SECURITY_ROWS}` (a `@security` trigger row) and
`{GITHUB_MGMT_RELEASE_STEP}` (integration-branch reference §3). Each is `""` when off.

Nothing is widened: no `tools:` line changes, no MCP server is activated and every gate prompt stays. The one merge
without the gate's prompt is `agentteams --merge-pr` under the operator-signed integration-branch merge
authorization (`agentteams/cli/dev_merge.py`; see the CLI reference).

> Source: `agentteams/github_management.py` (stdlib only).

## Functions

| Function | Purpose |
|----------|---------|
| `enabled(description)` | Whether the brief authorizes GitHub management; `ValueError` for a non-boolean value. |
| `section_body(description)` | The section text for this brief (the rules, or the off notice). |
| `placeholders(description)` | `{"GITHUB_MANAGEMENT_RULE": section_body(description)}` plus the three approval rows (`""` when off). |
| `update_notice(rendered, output_dir)` | The one-line notice `--update` prints the first time a render delivers the approval rules, else `None`. |
