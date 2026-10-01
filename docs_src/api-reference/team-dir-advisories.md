# `team_dir_advisories` — AgentTeamsModule

Generation-time advisories for two launcher residuals (follow-up #11, 2026-09-30). They detect and
never act. See [workspace privilege scoping](workspace-privilege-scoping.md) for the launcher these
support.

> *Source: `agentteams/team_dir_advisories.py`*

---

## Functions

### `marker_only_team_dirs(project_root) -> list[str]`

Returns the project-relative agents dirs (`.claude/agents`, `.goose/recipes`, `.github/agents`,
`.codex/agents`) that hold `references/build-log.json` but no switch and no agent file of their
framework. A confined process can plant such a marker, and `sandbox/confine-run.sh` then refuses the
project. The dir stays denied in the Claude block: excluding it would let an agent drop a real team's
deny by deleting that team's agent files. The heuristic is a diagnosis only. An attacker can steer it
with one dummy agent file, which gets the generic "regenerate" hint instead.

### `codex_config_security_keys(project_root) -> list[str]`

Returns the security-relevant keys set in `.codex/config.toml`: `approval_policy`, `sandbox_mode`,
`notify`, `[sandbox_workspace_write]` and `[mcp_servers]`. It returns `["<symlink>"]` for a symlinked
file, and `[]` when the file is absent.

Detection is key-based on every run, because a digest recorded at generation would bless whatever was
planted before it. It is best-effort: it sees plain `key =` lines and `[table]` headers only.

### `print_team_dir_advisories(project_root) -> None`

Prints both advisories to stderr. It runs from `generate_helpers._apply_sibling_team_denies` on every
generate, `--update` and `--check`.
