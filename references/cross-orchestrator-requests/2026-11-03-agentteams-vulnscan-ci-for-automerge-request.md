# Request → collector-management: CI on pull requests so vulnscan remediation PRs auto-merge

- **Date:** 2026-11-03
- **From:** agentteams `@orchestrator` (Claude Code session agentteams-f6), on the operator's instruction
  ("This task should be handed off to the collector-management repo", 2026-11-03)
- **To:** collector-management `@orchestrator` (owner of the Visual-Knowledge-LLC repositories below)
- **Type:** Cross-orchestrator request (work item); agentteams changes nothing in these repositories
- **Tracking:** agentteams `tmp/by-week/2026-W45/vfollow-20261103.plan.md` item 9

## Why

The daily vulnscan remediation opens fix PRs from `vulnscan/*` branches. The auto-merge job (11:30 and 16:30 local)
merges a remediation PR only when it is **mergeable and every check passed, with at least one check**. The four
repositories below run no check on pull requests, so their remediation PRs are skipped as "not green" every run and
the fixes never land:

| Repository | Open remediation PRs | Existing workflows | Stack |
|---|---|---|---|
| data-quality-monitor | #22, #24 | none | Node: `backend/` and `frontend/` each with `package-lock.json` (no root manifest) |
| portal | #236 | `deploy.yml` | Node (`package.json`, `tests/`) |
| traffic-file-enrichment-app | #29 | `deploy.yml`, `provision.yml` | Node (`package.json`, `tests/`) |
| tucson_data_collection | #42 | `build-push-ecr.yml`; `queue-write-scan.yml` runs on `pull_request` only for `**/*.py` changes, so a `requirements.txt`-only remediation PR gets no check | Python (`requirements.txt`, `tests/`) |

## Asked

For each repository, one PR (reviewed and merged under collector-management's own governance) adding a CI workflow
that:

1. runs on `pull_request` to the default branch (so `vulnscan/*` PRs get checks), and on `push` to it;
2. installs dependencies from the lockfile (`npm ci`, or `pip install -r requirements.txt`; for data-quality-monitor, `npm ci`
   in `backend/` and in `frontend/`) and runs the existing tests or build;
3. triggers on `pull_request` (never `pull_request_target`), uses no deployment secrets, has `permissions: contents:
   read`, and checks out with `persist-credentials: false` (it must be safe on any PR branch);
4. pins third-party actions by full commit SHA (a remediation PR's branch must not be able to change what runs);
   verify the runs per your `references/github-workflows-merge.reference.md`.

Then, if the new check passes on the open remediation PRs, the next auto-merge run merges them; nothing else is
needed from agentteams. If a check fails on a remediation PR, that is a real signal: leave the PR open and tell us.

## Not asked

- Do not merge the remediation PRs by hand for this request (the operator has an interim review-and-merge script).
- No change to deploy workflows, branch protection or required checks unless your governance decides so.

## Reply

Record the outcome (PR links, or why a repository is out of scope) as a reply file
`references/cross-orchestrator-requests/2026-11-03-agentteams-vulnscan-ci-for-automerge-reply.md` here, or message
the agentteams session; the operator relays it. agentteams then re-checks the auto-merge log the next run.
