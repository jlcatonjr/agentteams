# `source_provenance` — AgentTeamsModule

Which agentteams code is running. It is recorded in every `references/build-log.json` as `agentteams_source`,
and printed by `agentteams --version`.

> Source: `agentteams/source_provenance.py`. Added after CA-033, where a consumer's render silently ran a
> stale, unrecorded snapshot and lost its adopted routing rows. Its build log said only `1.0.0rc8`.

| `kind` | Meaning | `commit` | `branch` | `dirty` | `on_remote` |
|---|---|---|---|---|---|
| `checkout` | The package runs from a git work tree, directly or through an editable install | `HEAD` | the current branch (`None` when detached) | `git status --porcelain` is non-empty, **untracked files included** | a remote-tracking branch contains the commit (`git branch -r --contains`); `None` when git can't tell |
| `vcs-pin` | Installed from a VCS URL | from `direct_url.json` | `None` | `False` | `None` |
| `local-snapshot` | Installed from a local directory, not editable: a frozen copy | `None` | `None` | `None` | `None` |
| `package` | Anything else, e.g. an index install | `None` | `None` | `None` | `None` |

- **No path.** No absolute path is recorded, because consumers commit their build logs.
- **Recording only.** Refusing an off-main or dirty source is the consumer's policy, as mathAgents' refresh
  guard does.
- **`--version` output:** `agentteams 1.0.0rc8 (checkout 729a0add38f1 main, dirty)`, with `, not on a remote` appended when `on_remote` is `False`.
- **Not on a remote (IM item 29).** A render from a local-only branch records a pin no CI can fetch (Tracers and MarketForge got `09dcf0d5` / `820514ac` this way). Every run that writes a build log (a generate or an `--update`, never `--dry-run`) warns, and `--doctor` reports it, re-checking live whenever the running checkout knows the commit. `on_remote` reads the local remote-tracking refs and never fetches: a commit pushed since the last fetch reads as not on a remote until the next `git fetch`.

| Name | Purpose |
|---|---|
| `source_provenance() -> dict` | `{kind, commit, branch, dirty, on_remote}`; cached; never raises. |
| `unpushed_line(prov) -> str \| None` | The warning for a `checkout` whose `on_remote` is `False`, naming the commit and branch; `None` otherwise. Takes a live record or a build log's `agentteams_source`. |
| `warn_if_unpushed(prov) -> dict` | Prints that warning to stderr when it applies and returns `prov`; the build-log writer calls it. |
| `commit_on_remote(commit) -> bool \| None` | Asks the running checkout whether a remote-tracking branch contains `commit`, for a log written before `on_remote` existed; `None` when the running agentteams is not a checkout or doesn't know the commit. |
| `describe(prov=None) -> str` | The parenthesized one-line summary used by `--version`. |
