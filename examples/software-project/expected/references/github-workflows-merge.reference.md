<!-- AGENTTEAMS:BEGIN content v=1 -->
# GitHub Workflows and Merge Strategy Reference

This reference defines safe GitHub interaction and merge procedures for WebAppBackend.

## Official Sources

- About Git: https://docs.github.com/en/get-started/using-git/about-git
- About pull requests: https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/proposing-changes-with-pull-requests/about-pull-requests
- About pull request merges: https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/incorporating-changes-from-a-pull-request/about-pull-request-merges
- Merge methods on GitHub: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/about-merge-methods-on-github
- Resolving merge conflicts on GitHub: https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/addressing-merge-conflicts/resolving-a-merge-conflict-on-github
- About protected branches: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches
- About rulesets: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets
- About required status checks: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-required-status-checks

## Merge Strategy Matrix

| Strategy | Use When | Risk Profile | Required Checks |
|---|---|---|---|
| Merge commit | Preserve full branch history | Medium (history noise) | CI green, reviewer approval, branch protections satisfied |
| Squash merge | Keep linear and compact history | Low/Medium (context compression) | CI green, PR description captures scope |
| Rebase merge | Keep linear history and individual commits | Medium/High (history rewrite complexity) | CI green, no policy conflict with branch/rulesets |
| Fast-forward (local) | Branch is directly ahead with no divergence | Low | Verify no hidden divergence (`git fetch`, compare refs) |

## Careful Merge Protocol

1. Confirm branch and ruleset constraints before selecting merge method.
2. Verify required status checks are passing.
3. Confirm review requirements and approval state are satisfied.
4. Check for merge conflicts and resolve using GitHub or local workflow as appropriate.
5. Complete merge according to project policy (merge/squash/rebase).
6. Run post-merge verification — including **CI/CD deployment verification** (see the next section) when the push/merge triggers Actions — and document noteworthy decisions for auditability.

## Post-Merge / Post-Push CI/CD Deployment Verification

**Distinct from pre-merge required status checks** (which *gate* the merge, above): this verifies the GitHub Actions runs that the push/merge itself **triggers** on the target branch — CI **and** deployment workflows (Pages, release, container/package publish, environment deploys). Those runs only come into existence *after* the ref updates, so a green pre-merge PR does not imply a green deployment.

**Applies only when** the operation pushed or merged to a branch, **and** the repository has workflows (`.github/workflows/*.yml`), **and** `gh` is available and authenticated. Otherwise skip and record `N/A`.

**Procedure:**

1. **Find the triggered run(s)** for the new HEAD on the target branch:
   - `gh run list --branch <branch> --limit 10` (note the run(s) whose `headSha` matches the pushed commit; a merge to `main` typically starts one run per workflow — e.g. a CI run *and* a deploy run).
2. **Wait for completion** and read the verdict — do not declare the operation done while a run is `queued`/`in_progress`:
   - `gh run view <run-id> --json status,conclusion,jobs` → require `status == "completed"` and `conclusion == "success"`.
   - (`gh run watch <run-id>` blocks until completion when interactive.)
3. **On `failure`/`cancelled`/`timed_out`:**
   - `gh run view <run-id> --log-failed` → read the failing step(s); diagnose the root cause.
   - Fix the cause (code, config, or workflow), commit, and re-push; the new push triggers a fresh run — re-verify from step 1. Iterate until green.
   - Use `gh run rerun <run-id>` (or `--failed`) **only** for a confirmed transient/infrastructure flake, never to mask a real failure.
   - Escalate to the orchestrator / `@security` if the fix is risky, ambiguous, or out of scope. **Deployment** workflow success (Pages/release/publish) is the binding outcome — a failed deploy means the change is not live and the operation is **not complete**.
4. **Cross-repo guard:** if a fix requires pushing to a repository other than `src/`, that re-push is a cross-repository write — route it through `@repo-liaison` + `@security` (orchestrator Rule 11) before pushing.

## Conflict Handling Decision Tree

1. If conflict is simple and fully represented in changed files, resolve in GitHub UI.
2. If conflict spans generated files, templates, or multi-step refactors, resolve locally with full test and lint checks.
3. If conflict resolution changes architecture or policies, route through orchestrator + adversarial + conflict-auditor before final merge.

## Audit Logging Requirements

For each merge or rebase event, record:

- Source branch and target branch
- Merge method selected and rationale
- Conflict status (none / resolved in UI / resolved locally)
- Required status check snapshot (pass/fail) — the pre-merge gating checks
- Triggered CI/CD run id + conclusion (the post-merge deployment run, `success`/`failure`, or `N/A — no workflows`)
- Resulting commit hash(es)

These records support ex-post audit and incident reconstruction.
<!-- AGENTTEAMS:END content -->
