<!--
SECTION MANIFEST
| section_id         | designation   |
|--------------------|---------------|
| integration_branch | FENCED        |
| project_notes      | USER-EDITABLE |
-->

<!-- AGENTTEAMS:BEGIN integration_branch v=1 -->
# Integration-Branch Model — {PROJECT_NAME}

{PROJECT_NAME} uses the **integration-branch model** (brief `git_workflow.branch_model: "integration"`).
Feature work merges **locally** into `{INTEGRATION_BRANCH}`, which runs no cloud CI. The default branch
advances only through a **release PR** or a **hotfix PR**, so cloud CI and deploys run once per release
instead of once per feature. Release cadence: **{RELEASE_CADENCE}**.

This reference takes precedence over the trunk-based defaults in `references/github-workflows-merge.reference.md`
and `references/branch-lifecycle.reference.md` where they differ. It does not relax any `@security` rule.

```
feature/*  ──(local gate)──▶  {INTEGRATION_BRANCH}  ──(release PR: cloud CI once)──▶  default  ──▶ deploy
                                   ▲                                                   │
                                   └──────────── back-merge after a hotfix ◀───────────┘
hotfix/*  ───────────────────────(PR: cloud CI)──────────────────────────────────────▶ default
```

## 1. Feature work

1. Branch from `{INTEGRATION_BRANCH}`: `git fetch origin && git switch -c <type>/<topic> origin/{INTEGRATION_BRANCH}`
   (in a worktree when the project keeps a shared checkout on the default branch).
2. Commit as usual. Pushing the feature branch is optional and runs no CI, as long as the triggers match §5.
3. **Maintainers and agents do not open PRs into `{INTEGRATION_BRANCH}`:** each `pull_request` run is the cost this
   model removes. Merge locally (§2). The exception is an **outside contributor** working from a fork, who cannot
   merge locally: their PR targets `{INTEGRATION_BRANCH}`, and the project decides whether such PRs run CI (§5).

## 2. Local gate, then merge into `{INTEGRATION_BRANCH}`

The local gate replaces the per-PR cloud run, so it has to be honest. **Run it before every merge**, and
report its command and result in `@git-operations`' output contract (`Local gate`).

1. Run the project's gate: the same tests, linters and builds its CI runs, at least for the platform you are on.
   Record the command in **Project-Specific Notes** below so every agent runs the same one.
2. Merge: `git switch {INTEGRATION_BRANCH} && git pull --ff-only && git merge --no-ff <branch>`. The `--no-ff`
   merge commit per feature is what `git bisect --first-parent` walks later, and what the release-PR body lists.
3. Push `{INTEGRATION_BRANCH}` when convenient (backup and sharing). It should trigger no CI. `@git-operations`
   rule 7 reports `N/A — integration branch` only after confirming that no run exists for the pushed commit
   (`actions/runs?head_sha=<sha>`). A run that did trigger, such as a deploy workflow, is verified as usual.
4. Leave the feature branch alone. The branch tools classify it **Integrated** (kept) until a release carries it
   to the default branch, and then **Merged** (deletable under the usual grant or clearance).

## 3. Release

1. When `{INTEGRATION_BRANCH}` is release-worthy ({RELEASE_CADENCE}), push it and open the **release PR**
   `{INTEGRATION_BRANCH}` → default branch. Its body lists the feature merges since the last release:
   `git log --first-parent --merges --format='- %s' origin/<default>..origin/{INTEGRATION_BRANCH}`.
2. That PR's CI is the one cloud gate. Fix failures **on `{INTEGRATION_BRANCH}`** and batch the fixes, because
   each push re-runs CI. Checks only the cloud runs (other operating systems, container-only jobs) are first
   exercised here.
3. Merge with a **merge commit**. **Never squash or rebase the release PR.** Either one
   leaves the default branch with history `{INTEGRATION_BRANCH}` lacks: every later release PR re-conflicts,
   and no integrated branch is ever recognized as merged.
4. Verify the triggered CI **and deploy** runs (`@git-operations` rule 7). The release is not done while they are red.
5. Then `git switch {INTEGRATION_BRANCH} && git merge --ff-only origin/<default>` keeps the two in step.

## 4. Hotfix

1. `git switch -c hotfix/<topic> origin/<default>`, fix, open a PR to the default branch (cloud CI runs, which
   is acceptable because hotfixes are rare), and merge it.
2. **At once** merge the default branch back: `git switch {INTEGRATION_BRANCH} && git merge origin/<default>`,
   then push. Skipping this lets the branches diverge, and the next release PR re-applies or conflicts with the fix.

## 5. Keep the triggers aligned

The model saves money only while no workflow runs on `{INTEGRATION_BRANCH}`. When a workflow is added or changed:

- `push` triggers name the default branch, release branches and tags only. Never `'**'`, never `{INTEGRATION_BRANCH}`.
- `pull_request` is filtered to the default branch and release lines (`branches: [<default>, "release/**"]`). Add
  `{INTEGRATION_BRANCH}` only when outside contributors open fork PRs into it and should get CI feedback. Since
  maintainers never open such PRs, only contributor PRs and automation PRs (below) then cost a run.
- Automation that opens PRs (dependency or update bots, vulnerability-fix PRs) targets `{INTEGRATION_BRANCH}`, so the
  change rides the next release. These are the second accepted kind of PR into `{INTEGRATION_BRANCH}`. A
  vulnerability whose fix cannot wait for the release takes the hotfix path (§4).
- Branch protection on the default branch requires a PR and the CI status checks. `{INTEGRATION_BRANCH}` needs
  no required checks, because its gate is local.
- **`{INTEGRATION_BRANCH}` must itself be a protected branch** (setup prerequisite): no deletion and no force push,
  with no required checks and no required reviews, so the local merges in §2 still push. When the repository's
  "Automatically delete head branches" setting (`delete_branch_on_merge`) is on, GitHub deletes a release PR's
  head branch, `{INTEGRATION_BRANCH}`, the moment the release merges (§3), and only branch protection exempts it.
  Check with `gh api repos/<owner>/<repo> -q .delete_branch_on_merge` and
  `gh api repos/<owner>/<repo>/branches/{INTEGRATION_BRANCH}/protection`. A minimal rule is a `PUT` to that
  protection endpoint with `allow_deletions`, `allow_force_pushes` and `enforce_admins` false and
  `required_status_checks`, `required_pull_request_reviews` and `restrictions` null (GitHub requires all four of
  `required_status_checks`, `enforce_admins`, `required_pull_request_reviews` and `restrictions` in the body). If the branch was already deleted, recreate it at the
  default branch (`git push origin origin/<default>:refs/heads/{INTEGRATION_BRANCH}`): once its release PR has merged,
  everything it held is in the default branch.
- See also the CI Cost Checklist in `references/github-workflows-merge.reference.md`.

## 6. Branch tools

`agentteams --branch-inventory`, `--branch-post-merge` and `--branch-cleanup` read the integration branch from the
brief (or `--integration-branch`):

- `{INTEGRATION_BRANCH}` itself is **Integration**: never proposed for deletion, even though a release merge makes it
  an ancestor of the default branch.
- A branch merged only into `{INTEGRATION_BRANCH}` is **Integrated**: kept until the release.
- `--branch-inventory` warns when GitHub would delete `{INTEGRATION_BRANCH}` on a release merge (auto-delete on
  and the branch unprotected, §5), and says so when it could not check.
<!-- AGENTTEAMS:END integration_branch -->

## Project-Specific Notes

> ⚙️ **USER-EDITABLE** — the local gate command, release checklist, deploy workflows to verify. This section lies
> outside every `AGENTTEAMS` fence and is preserved verbatim across `agentteams --update --merge`.
