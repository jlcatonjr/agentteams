# `source_provenance` — AgentTeamsModule

Which agentteams code is running. It is recorded in every `references/build-log.json` as `agentteams_source`,
and printed by `agentteams --version`.

> Source: `agentteams/source_provenance.py`. Added after CA-033, where a consumer's render silently ran a
> stale, unrecorded snapshot and lost its adopted routing rows. Its build log said only `1.0.0rc8`.

| `kind` | Meaning | `commit` | `branch` | `dirty` |
|---|---|---|---|---|
| `checkout` | The package runs from a git work tree, directly or through an editable install | `HEAD` | the current branch (`None` when detached) | `git status --porcelain` is non-empty, **untracked files included** |
| `vcs-pin` | Installed from a VCS URL | from `direct_url.json` | `None` | `False` |
| `local-snapshot` | Installed from a local directory, not editable: a frozen copy | `None` | `None` | `None` |
| `package` | Anything else, e.g. an index install | `None` | `None` | `None` |

- **No path.** No absolute path is recorded, because consumers commit their build logs.
- **Recording only.** Refusing an off-main or dirty source is the consumer's policy, as mathAgents' refresh
  guard does.
- **`--version` output:** `agentteams 1.0.0rc8 (checkout 729a0add38f1 main, dirty)`.

| Name | Purpose |
|---|---|
| `source_provenance() -> dict` | `{kind, commit, branch, dirty}`; cached; never raises. |
| `describe(prov=None) -> str` | The parenthesized one-line summary used by `--version`. |
