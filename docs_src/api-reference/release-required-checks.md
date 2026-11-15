# `release_required_checks` — AgentTeamsModule

Switches the default branch's required status checks when a release PR renames a CI job the default branch requires
(a runner bump such as `test (ubuntu-24.04, 3.11)` → `test (ubuntu-26.04, 3.11)`, or a matrix change). Without the
switch the release PR can never merge, because GitHub waits for checks its workflows no longer produce.

```bash
python -m agentteams.release_required_checks --pr <n>                      # dry run: the change and a fingerprint
python -m agentteams.release_required_checks --pr <n> --apply <fingerprint>
python -m agentteams.release_required_checks --restore <backup file>       # shows the restore
python -m agentteams.release_required_checks --restore <backup file> --apply <fingerprint>
```

- **Proposal.** A required check the PR still produces is kept. One it no longer produces is replaced by the PR's
  checks with the same name once version numbers are masked, keeping the old check's app pin. Anything else needs
  `--add NAME` or `--drop NAME`. The set is never emptied.
- **Guards.** The PR is open, targets the repository's default branch (read, never assumed) and comes from the
  integration branch (`--integration-branch`, else the brief's `git_workflow`, else `dev`) or `hotfix/*`, never a fork.
  Every proposed check is green on the PR head. It refuses when a repository ruleset requires the checks, and when the
  token cannot read branch protection (no admin rights).
- **Apply exactly what was shown.** `--apply` takes the dry run's fingerprint (PR head, current and proposed sets,
  `strict`) and refuses if any moved. It saves a backup under `~/.local/state/agentteams/branch-protection/` and
  verifies the result. `--restore` takes only such a backup, shows it, and applies it only with its own fingerprint.
- **Approval.** An agent runs `--apply` only after the operator approves that fingerprint live and `@security` records
  `release-required-checks:<fingerprint>` (the instruction file's GitHub management section; brief
  `github_management`).

> Source: `agentteams/release_required_checks.py` (stdlib only; calls `gh`, chosen with `GH`).

## Functions

| Function | Purpose |
|----------|---------|
| `mask_versions(name)` | The check name with every version number replaced by `#`. |
| `propose(required, pr_checks, add=None, drop=None)` | The `Proposal` (old, new, renamed, not green); raises `Refused` when a guard fails. |
| `fingerprint(proposal, *, strict, pr, head_sha)` | 12-hex fingerprint of exactly what `--apply` would do. |
| `run(argv=None)` | Command-line entry point: 0 done or nothing to do, 1 refused. |
