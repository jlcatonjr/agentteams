# `git_hooks` — AgentTeamsModule

Commit-triggered refresh of the repository maps. Installs a `pre-commit` hook
that regenerates the maps from the *staged* files and stages the result into the
same commit — so the committed maps are always in step with the committed source.

Two maps are refreshed, each under its own guard:

- agent files staged → `<agent-dir>/references/pipeline-graph.md` (agent topology, kept *with the team* — same location `--update`/emit writes — via [`graph`](graph.md))
- `*.py` files staged → `references/architecture-graph.md` (repo-level module architecture, via [`architecture`](architecture.md))

> *Source: `agentteams/git_hooks.py`*

---

## Determinism contract

The refresh reads source files from disk while `--update` builds the same maps
from the in-memory render. Both go through the deterministic serialisers in
[`graph`](graph.md) / [`architecture`](architecture.md), so a disk-built refresh
reproduces the pipeline output **byte-for-byte**. Without that guarantee the hook
would rewrite the maps with meaningless reorderings on every commit and never
agree with `--update`.

---

## Key functions

### `refresh_pipeline_graph(repo_root, *, agents_dir=None, dry_run=False, stage=False) -> RefreshResult`

Rebuild the agent dir's `references/pipeline-graph.md` (+ `pipeline-graph.svg` and
`pipeline-handoffs.svg`) from the agent files on disk (`.github/agents/` or
`.claude/agents/`) — the same location `--update`/emit writes, so there is a single
copy (no repo-root duplicate). Fence-normalised, written only when the topology
changed. Backup/ghost agents under dot-prefixed directories are excluded. When
`stage=True` (the hook path) the written files are `git add`-ed.

### `refresh_architecture_graph(repo_root, *, package_dir=None, dry_run=False, stage=False) -> RefreshResult`

Rebuild `references/architecture-graph.md` from the repo's primary Python package
(auto-detected). Same write-if-changed contract.

### `install_pre_commit_hook(repo_root, *, agentteams_path=None, hooks_dir=None) -> InstallResult`

Write (or sentinel-merge) the refresh block into the repo's `pre-commit` hook,
preserving any pre-existing hook body. Idempotent. The block is non-blocking
(`|| true`) so a refresh failure never aborts a commit, and each guard fires only
when relevant files are staged. Resolves the hooks directory via
`git rev-parse --git-path hooks` (honours `core.hooksPath`, worktrees, submodules).

### `maybe_install_git_hooks(args, project_root) -> None`

Default-on auto-install called from the generate/update success path; opt out
with `--no-git-hooks`. No-op outside a git repository.

---

## CLI

```
agentteams --install-git-hooks [--project DIR]     # install the hook
agentteams --refresh-graph        [--project DIR]  # refresh agent topology map
agentteams --refresh-architecture [--project DIR]  # refresh module architecture map
agentteams --update --no-git-hooks                 # opt out of auto-install
```

The installed hook calls `python -m agentteams.git_hooks --refresh` /
`--refresh-architecture`.
