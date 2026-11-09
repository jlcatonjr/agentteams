# `branch_model` — AgentTeamsModule

The project's branch model, from the brief's optional `git_workflow` block. Under `"trunk"` (the default, and what
an absent block means), feature branches merge to the default branch through PRs. Under `"integration"`, feature
branches merge **locally** into an integration branch (`dev` by default) behind a local gate. The default branch then
advances only through a release PR (merged with a merge commit) or a hotfix PR. Cloud CI and deploys run once per
release instead of once per feature.

> Source: `agentteams/branch_model.py` (stdlib only). For a trunk team, every placeholder it owns is `""`, so the
> render is byte-identical to a team without the block.

## Functions

| Function | Purpose |
|---|---|
| `resolve(description)` | The normalized record `{branch_model, integration_branch, release_cadence}` for an integration brief; `None` for trunk. Raises `ValueError` on a malformed block, an unknown model or cadence, or an invalid branch name, including `main`/`master` |
| `enabled(manifest)` | Whether the manifest carries an integration record |
| `default_placeholders()` | Every owned placeholder set to `""` (what `analyze` seeds) |
| `populate_placeholders(manifest)` | Called by `cli/render_pipeline` before `render_all`. Fills the `{BRANCH_MODEL_*}` rows, `{INTEGRATION_BRANCH}` and `{RELEASE_CADENCE}`; `""` for trunk |
| `rows(branch)` | The `\n`-prefixed rows appended at line ends in the merge reference, `@git-operations`, `@pr-manager` and the branch-lifecycle reference |
| `integration_branch_from_project(root)` | The brief's integration branch (`.agentteams/brief.json`, `brief.json`, `_build-description.json`), or `None` (no brief, a symlinked or outside brief, unreadable JSON, or trunk). Raises `ValueError` on a malformed `git_workflow` block, so a typo can't silently drop the branch's protection |
| `plan_files(description, output_files)` | Called by `analyze`: resolves the block and, for an integration team, appends `references/integration-branch.reference.md` to the output plan; returns the manifest record or `None` |

## What an integration team gets

- `references/integration-branch.reference.md` (feature work, local gate, release PR, hotfix and back-merge,
  trigger alignment, branch tools, and the setup prerequisite that the integration branch be a protected branch
  so a release merge cannot delete it), added to the output plan by `analyze`.
- Rows in the merge-strategy matrix, `@git-operations`' ground truth and output contract (`Local gate`; rule 7
  `N/A — integration branch`), `@pr-manager`'s ground truth and disposition table (merge locally into the
  integration branch; open the release PR), and the branch-lifecycle state table (*Integration*, *Integrated*).
- `branch_inventory` states `integration` (never deleted) and `integrated` (kept until a release), so
  `--branch-post-merge` refuses the integration branch after a release merge.
- Vulnerability-fix PRs target the integration branch unless `vulnerability_scanning.base_branch` is set.
