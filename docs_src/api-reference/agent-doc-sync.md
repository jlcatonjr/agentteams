# `agent_doc_sync` — AgentTeamsModule

Propagates each agent's **learned block** (`<!-- AGENTTEAMS-LEARNED:BEGIN -->` …
`<!-- AGENTTEAMS-LEARNED:END -->`) between the copies of that agent across frameworks:
`.github/agents/<slug>.agent.md`, `.claude/agents/<slug>.md` and `.goose/recipes/<slug>.yaml`.
The CLI entry point is [`--sync-agent-docs`](../cli-reference.md#agent-doc-sync-learned-blocks).

> *Source: `agentteams/agent_doc_sync.py`* (integrity-pinned: it writes agent files unsandboxed)

**Why it exists.** An agent inside a Claude Code sandbox cannot write `.claude/agents` (Claude Code
read-only binds it, with or without agentteams), so cross-framework propagation of what agents
learn runs **outside every agent session** — by hand, or from the operator-installed systemd user
unit (`scripts/install-agent-doc-sync.sh`). The text-level parse/compose/verify half lives in
[`learned_blocks`](learned-blocks.md).

## Rules

- **Only the learned block moves.** Front matter, template fences, recipe keys other than the
  block inside `instructions`, settings, build-logs and trust roots are never propagated; every
  composed file is proven byte-identical outside the block before it is written.
- **Direction.** A baseline digest per (slug, framework) lives in
  `$XDG_STATE_HOME/agentteams/<sha256(realpath project)[:16]>/agent-doc-sync.json` (default
  `~/.local/state`), never in the project. The copy that changed since the baseline is the source;
  copies that changed differently are a **conflict** (`conflicts.json` + stdout, nothing written).
  A removed block is never propagated; an emptied block is, after the overwritten text is backed up
  to `backups/` in the state dir.
- **Modes.** Default is check (report only; writes nothing, not even state). `--apply` writes
  `.github/agents` and `.goose/recipes` targets. `.claude/agents` targets are staged in
  `pending-claude.json` unless `--include-claude` is also given, which prints each diff and writes.
- **Content gates.** Each propagated block is scanned with `scan.scan_content`; any finding
  quarantines it (`quarantine/` in the state dir) and nothing is written. Fence/learned marker
  tokens and line breaks other than `\n` are refused.
- **Filesystem.** No symlinked agent-dir component; regular single-link files only, opened
  `O_NOFOLLOW` relative to a directory fd and under the project's realpath; existing files only
  (never created); temp file + rename with a digest re-check of the target just before the rename
  (a concurrent agent edit wins); an exclusive `flock` in the state dir serialises runs.
- **No configuration input.** No brief, pin or project config is read.
- **Idempotent.** A run with nothing to do writes nothing.

## Public constants

- `FRAMEWORK_DIRS`: `(framework id, agents dir, file suffix, host format)` for `github`, `claude`, `goose`.
- `BASELINE_NAME`, `CONFLICTS_NAME`, `PENDING_NAME`, `LOCK_NAME`: state-dir file names.
- `EXIT_OK` (0), `EXIT_ATTENTION` (1: conflict, quarantine, refusal or concurrent-edit skip), `EXIT_FATAL` (2).

## Public classes

### `SyncError`

A fatal precondition failed: the project is not a directory, the state dir is unsafe (inside the
project, or not a real directory), `include_claude` without `apply`, or the lock is held.

### `SyncReport`

What one run did: `written`, `would_write`, `staged`, `conflicts`, `quarantined`, `refused`,
`skipped_concurrent`, the printable `lines`, and the derived `exit_code`.

## Public functions

### `project_hash(project)`

The 16-hex-character id of a project: `sha256(os.path.realpath(project))[:16]`.

### `state_dir_for(project)`

The per-project state dir. An absolute `XDG_STATE_HOME` is honoured; a relative one is ignored.

### `sync_agent_docs(project, *, apply, include_claude)`

Run one sync and return a `SyncReport`. Raises `SyncError` on a fatal precondition.

### `run_sync_agent_docs(project, *, apply, include_claude, out)`

CLI wrapper: run the sync, print the report, return the exit code (`EXIT_FATAL` on `SyncError`).
