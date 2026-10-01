# `prompt_roots` — AgentTeamsModule

Prompt-root change detection (follow-up #8 phase 1, 2026-10-01). A prompt root is a file another
harness reads as instructions. Most are writable from a Claude sandbox, so a confined agent could
rewrite what the next unconfined Copilot, Codex or goose session reads. This module detects that. It
never prevents it.

> *Source: `agentteams/prompt_roots.py`*

---

## What is tracked

| Root | Hashed |
|---|---|
| `.github/copilot-instructions.md`, root `AGENTS.md`, `.goosehints`, `CLAUDE.md`, `.claude/CLAUDE.md` (when this team emits them) | per `AGENTTEAMS` fenced region |
| each team's agent files: `.github/agents/*.agent.md`, `.codex/agents/*.toml`, `.goose/recipes/*.yaml`, `.claude/agents/*.md` | per fenced region (whole file when it has no parseable fence) |
| `.github/instructions/**`, `.github/prompts/**` (never emitted) | whole file |

Every generate and `--update` writes the map to this team's `references/build-log.json` as
`prompt_root_hashes` (`{project-relative path: {section id: sha256}}`, `"*"` for a whole-file hash).
A symlinked root is hashed by its link target string. Only the default team layouts are recorded.

Every generate, `--update` and `--check` compares the live tree with the map of every present team
and prints a `PROMPT-ROOT CHANGED since last build` warning to stderr. A team's agent files are
compared with that team's log, a shared root file with the newest log that records it, and the
snapshot dirs with the newest log overall. Edits in USER-EDITABLE (unfenced) regions never warn. A
front-matter edit in an agent file is outside every fence and is not reported here.

The warning is advisory. `--check --strict-prompt-roots` exits `1` on it.

## Limits

- **Detection only.** The build-log is agent-writable outside the launcher, so an agent that can
  edit a prompt root can also rewrite the recorded hashes.
- **A fresh generation re-baselines** (remediation pattern "baseline-at-generation-launders-state").
  Whatever is on disk when the log is written becomes the new baseline. When generate or `--update`
  re-records over a change it has just detected, it prints a `PROMPT-ROOT RE-BASELINE` notice first.
  The converged `--update` heal path rewrites nothing and keeps the old map.
- A build-log written before this feature has no map and is skipped. Printed paths are
  `repr`-escaped. A recorded hash that is not 64 lowercase hex digits counts as a change.

---

## Constants

- `PROMPT_ROOTS` — every prompt root, as glob-style patterns.
- `TEAM_AGENT_GLOBS` — agents dir to agent-file glob.
- `SHARED_ROOT_FILES`, `SNAPSHOT_DIRS` — the shared single-file roots and the whole-file dirs.
- `BUILD_LOG_KEY` (`"prompt_root_hashes"`), `WHOLE_FILE` (`"*"`).

## Functions

### `is_prompt_root(rel: str) -> bool`

Whether a project-relative POSIX path is a prompt root.

### `region_hashes(text: str) -> dict[str, str]`

`{section_id: sha256}` for each fenced region (via `fences._extract_fenced_regions`), or
`{"*": sha256}` when the text has no fence or its fences do not parse.

### `team_project_root(output_dir: Path) -> tuple[Path, str] | None`

Splits a team's agents dir into the project root and the matched team dir, or `None` for a
non-default layout.

### `compute_prompt_root_hashes(project_root: Path, team_dir: str, emitted_abs_paths: list[str], previous: dict[str, Any] | None = None) -> dict[str, dict[str, str]]`

The map for one team's build-log. An incremental `--update` lists only the files it re-rendered, so a
shared root file is also kept when the team's `previous` map recorded it.

### `add_to_build_log(log: dict[str, Any], output_dir: Path, emitted_abs_paths: list[str]) -> None`

Sets `prompt_root_hashes` on a build-log dict about to be written (`build_team._write_run_log`).
Prints the `PROMPT-ROOT RE-BASELINE` notice first when this run's detection found changes.

### `detect_prompt_root_changes(project_root: Path) -> list[str]`

Sorted, de-duplicated findings such as `'.github/prompts/x.md': added` or
`'AGENTS.md': fenced region 'project_overview' changed`.

### `print_prompt_root_warnings(project_root: Path) -> list[str]`

Detects, prints the warning block and remembers the findings for the re-baseline notice and
`--strict-prompt-roots`. It runs from `generate_helpers._apply_sibling_team_denies` on every
generate, `--update` and `--check`.

### `strict_failure() -> bool`

Whether this run's detection found any change. `generate_helpers._handle_check` reads it when
`--strict-prompt-roots` is set.
