# `bridge_retire` — AgentTeamsModule

Governed removal of what one bridge generated, behind
[`--retire-bridge`](../cli-reference.md#--retire-bridge-pair). Use it when a project moves from a
bridge to a native team. `--revert-migration` only undoes `--migrate`, and the `--bridge-*` modes
only check, refresh or merge.

> *Source: `agentteams/bridge_retire.py`* (integrity-pinned)

## Removal set

`build_plan(project, pair)` is read-only and enumerates the set. It never removes a whole directory.

| Removed | Kept and reported |
|---|---|
| The `PAIR_ARTIFACTS` in `references/bridges/<pair>/` | Any other file in that directory, which then stays |
| `.claude/agents/*.md` and `.claude/skills/*/SKILL.md` whose `bridge:` value equals the pair (`bridge_marker`) | Agent and skill files with no marker or another pair's marker, including the recall skills |
| | Entry files (`CLAUDE.md`, `.claude/agent-team.md`, …), listed with the `AGENTTEAMS-BRIDGE` regions they still hold |
| | Anything reached through a symlink below the project root |

Only `copilot-vscode-to-claude` is supported (`SUPPORTED_PAIRS`). Any other pair raises
`RetireError`, and so does a pair with nothing to retire.

`bridge_marker(path)` returns the raw `bridge:` front-matter value (`""` when the key is empty, None when
absent). It is the one predicate for "a bridge generated this file": this module and the write-policy
render refusal both use it, and it lives here so the integrity manifest pins it.

A candidate must resolve inside the project with no symlink on its path below the project root, so a
`--project` pointing at a scratch clone whose `.claude/skills` links elsewhere reaches nothing.

## Authorization

`run_retire(project, pair, team_dir=..., apply=...)` is a dry run unless `apply`. With `apply`:

1. **HALT:** an unretracted HALT on `retire-bridge` (`RETIRE_FAMILY`), on `retire-bridge:<pair>`, or on
   the exact id refuses the run (C-2). All three are checked: a retraction of any `retire-bridge:…` id
   clears a family-level HALT, but not a pair-level one.
2. **Clearance:** a `@security` PASS for `RetirePlan.action`, which is `retire-bridge:<pair>:<sha256>`.
   The sha covers every (path, content sha256) in the set and is recomputed on apply, so a file changed
   after the dry run leaves no matching clearance. The PASS is consumed (C-5). A bare `retire-bridge`
   PASS authorizes nothing.
3. **Backup:** every file is copied to `<project>/.agentteams-backups/<timestamp>/<path>.agentteams-bak`
   before it is removed. Restore by copying it back; `--restore-backup` restores team dirs, not this.
4. **Last-moment check:** each file is re-hashed and re-checked for containment just before it is
   unlinked. A mismatch stops the run with exit `1`; files already removed are in the backup.

A skill directory left empty goes, and so does the pair directory once empty.

Signing works as for `branch_cleanup`: see the branch-lifecycle reference §4.

The write-policy render gate (`cli.write_policy_warning.bridge_agent_refusal`) refuses a render under
`write_policy: "orchestrator-only"` while a wide marked agent remains, and it names this command.

## Public API

`bridge_marker`, `build_plan`, `run_retire`, `RetirePlan`, `RetireError`, `SUPPORTED_PAIRS`, `PAIR_ARTIFACTS`,
`RETIRE_FAMILY`, and the exit codes `EXIT_OK` (0), `EXIT_FAIL` (1) and `EXIT_UNAUTHORIZED` (3).
