# `env_hygiene`

Checks that every `.env` file is gitignored and kept out of Docker build contexts. It backs security Rule S-1's
env-file bullets and the blocking pre-commit env-file guard that `--install-git-hooks` installs
(see [`git_hooks`](git-hooks.md)).

```
python -m agentteams.env_hygiene REPO [REPO ...] [--fix [--execute]]
```

Prints JSON `{"repos": [{"repo", "findings", "verdict", "fix"?}], "verdict"}` and exits 1 when any repository's
verdict is HALT. Git runs read-only and hardened ([`git_exec`](git-exec.md)).

## Findings

Findings are `scan.ScanFinding` objects, so `scan.verdict_for_findings` maps them to a verdict.

| Category | Severity | Meaning |
|---|---|---|
| `tracked-env-file` | high | git tracks an env file. Untracking is manual: move anything a deploy reads into the deploy config first. |
| `unignored-env-file` | high | an env file on disk that `git add .` would stage |
| `dockerignore-env` | medium | a Dockerfile copies its whole build context (`COPY .`, `COPY *`, the JSON-array form, continuation lines) and neither the root `.dockerignore` nor BuildKit's `<Dockerfile>.dockerignore` excludes every env shape (`.env`, `.env.*`, `*.env`) |

An env file is a basename `.env`, `.env.<suffix>` or `<name>.env`. A name with an `example` / `sample` / `template`
component is a placeholder and exempt. `.envrc`, `environment.yml` and `env/` are never matched.

## `--fix`

Appends only the missing lines (`.env`, `.env.*`, `*.env` and the placeholder negations, which match `is_env_file`)
to the root `.gitignore`, and to the root `.dockerignore` when a Dockerfile is flagged. The build context is assumed
to be the repository root. It is a dry run unless `--execute` is given. It refuses a
file with uncommitted changes, because git is the snapshot, and it **never untracks** a file.

Not checked: archives built from a working tree outside Docker, such as a Terraform `archive_file` `source_dir`.

## Public surface

| Name | Purpose |
|---|---|
| `is_env_file(name) -> bool` | The basename rule above. |
| `audit_repo(repo) -> list[ScanFinding]` | Audit one repository. |
| `plan_fix(repo, findings) -> FixPlan` / `apply_fix(repo, plan) -> FixPlan` | The `--fix` dry run and the `--execute` write. |
