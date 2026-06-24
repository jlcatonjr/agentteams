# `bridge` — AgentTeamsModule

Lightweight cross-framework compatibility bridges that preserve source canonical documentation.

> Source: `agentteams/bridge.py`

---

## Public Types

### `BridgeResult`

Summary of bridge generation or bridge check run.

Fields:

1. `written`: written (or would-be-written) paths
2. `skipped`: skipped existing paths
3. `errors`: error messages
4. `dry_run`: whether writes were simulated
5. `check_only`: whether run was check mode
6. `check_ok`: bridge freshness verdict
7. `check_report_path`: report path when check mode runs
8. `manifest_missing` (`bool`): in check mode, `True` when the bridge manifest file does not exist at the expected path (default `False`)
9. `notices` (`list[str]`): human-readable per-file notices accumulated during the run — e.g. merge-mode files skipped because they lack an `AGENTTEAMS-BRIDGE` fence (default empty)

Property:

- `success`: `True` when no errors and check passes (for check mode)

---

## Public Function

### `run_bridge(*, source_dir, target_framework, output_root, source_framework=None, dry_run=False, overwrite=False, check_only=False, merge_only=False, emit_skills=True, host_features=None)`

Generate bridge artifacts or validate bridge freshness.

Args:

1. `source_dir` (`Path`): canonical source agent directory.
2. `target_framework` (`str`): target runtime framework.
3. `output_root` (`Path`): root where bridge artifacts are written.
4. `source_framework` (`str | None`): optional source override.
5. `dry_run` (`bool`): simulate writes.
6. `overwrite` (`bool`): destructively overwrite **existing target-framework entry files** at `output_root` (CLAUDE.md, `.claude/agent-team.md`, etc.) and existing bridge-internal artifacts. Triggered by `--bridge-refresh`.
7. `check_only` (`bool`): run freshness check only; no writes.
8. `merge_only` (`bool`): non-destructive update of target-framework entry files. Bridge-internal artifacts under `references/bridges/.../` are always regenerated. For target entry files, only content inside `<!-- AGENTTEAMS-BRIDGE:BEGIN <region> v=N --> ... <!-- AGENTTEAMS-BRIDGE:END <region> -->` fences is re-rendered; content outside fences is preserved verbatim. Files lacking any bridge fence are skipped with notices in `bridge-merge.report.md`. Triggered by `--bridge-merge`.
9. `emit_skills` (`bool`): emit `.claude/skills/recall.md` skill template (claude target only). Default `True`. Set to `False` (via `--bridge-no-skills`) if your team manages skills separately.
10. `host_features` (`list[str] | None`): opt-in subselectors that gate additional emission paths. When `None` or `[]`, default emission is unchanged. Recognised tokens (claude target):
    - `bridge:copilot-vscode-to-claude:subagents` — emit per-agent stubs via [`bridge_subagents`](bridge-subagents.md).
    - `bridge:copilot-vscode-to-claude:hooks` — emit hook settings + guard via [`hooks_emit`](hooks-emit.md).
    - `bridge:copilot-vscode-to-claude:cache-split` — emit cache-aware `CLAUDE.md` via [`instructions_split`](instructions-split.md) in place of the default pointer file.
    - `bridge:copilot-vscode-to-claude:schedule` — emit `.claude/schedules.agentteams.json` via [`schedule_emit`](schedule-emit.md).
    - `bridge:copilot-vscode-to-claude:todo-projection` — emit `.claude/skills/todo-from-plan.md` (rendered by [`plan_steps_todo.render_skill`](plan-steps-todo.md)).

    Tokens are parsed and validated by [`host_features`](host-features.md). CLI surface: `--target-host-features TOKENS`.

Returns:

- `BridgeResult`

Raises:

1. `FileNotFoundError` when source directory is missing.
2. `ValueError` for unknown framework ids.

---

## Artifacts

Bridge artifacts are generated in:

- `references/bridges/<source>-to-<target>/`

Core files:

1. `bridge-manifest.json`
2. `agent-inventory.md`
3. `quickstart-snippet.md`
4. `entrypoint.md`
5. `domain-boundary.md` — clarifies the memory-index vector-mode boundary vs project-level retrieval-integrator contracts.

Check mode also emits:

- `bridge-check.report.md`

Merge mode also emits:

- `bridge-merge.report.md` — per-file status: merged / skipped (no fence) / skipped (no rendered fence).

Claude target with `emit_skills=True` (default) also emits at `output_root`:

- `.claude/skills/recall.md` — wraps `agentteams --query-index` for in-session retrieval. **Not** under `references/bridges/...`; lives in the consumer's `.claude/skills/` directory.

Goose target emits these fenced entry files at `output_root` (regions
`goose-bridge-entry`/`-hints`/`-readme`):

- `AGENTS.md` — repo-root pointer to the source inventory/quickstart. **Shared multi-tool file** (Cursor/Codex/Cline also read it) — see the warning below.
- `.goosehints` — `@AGENTS.md` integrator so Goose's default context discovery loads the bridged brief.
- `.goose/README.md` — bridge note.

Goose is supported as a bridge **target** only (any of copilot-vscode/copilot-cli/claude → goose); bridging *from* a Goose project is not yet supported.

---

## Notes

1. Designed for runtime interoperability without full cross-framework regeneration.
2. Hash-based freshness checks detect changed, missing, and newly added source files.
3. Target entry files are emitted to help route users through source orchestrator-first flow.

## Mode Selection Guidance

| Situation | Use |
|---|---|
| First-time bridge generation for a project | `--bridge-refresh` (or `--bridge-from` with no mode flag, on a project that has no target entry files yet) |
| Re-running after source agents changed; consumer's `CLAUDE.md`/`.claude/*` have been customized | **`--bridge-merge`** — preserves customization outside the bridge's fenced regions |
| Re-running with no consumer customization to preserve | Either `--bridge-refresh` or `--bridge-merge` works; `--bridge-merge` is the safer default |
| Verifying source has not drifted from manifest | `--bridge-check` |

> **Warning:** `--bridge-refresh` unconditionally overwrites `CLAUDE.md` and `.claude/*` at the consumer's `output_root` with terse stubs. If your team has rich entry files, use `--bridge-merge` for refreshes. Consumer-managed sections should live **outside** the `<!-- AGENTTEAMS-BRIDGE:BEGIN ... -->` fences so the merge logic preserves them.
>
> **Goose target — extra caution:** `AGENTS.md` is a **shared, multi-tool standard file** read by Cursor, Codex, Cline, and others. The bridge writes it (fenced) on first contact in *every* mode — including `--bridge-merge` — when it is absent, and `--bridge-refresh` overwrites the whole file. An existing **unfenced** `AGENTS.md` (another tool's) is safely skipped under `--bridge-merge`. Always prefer `--bridge-merge` for Goose and review `references/bridge-refresh-safety.md` (in the repo) before any `--bridge-refresh`.

## Fence Convention

Target entry files emitted by the bridge contain `AGENTTEAMS-BRIDGE` fences (distinct from the `AGENTTEAMS` fences used by the agent-template emit pipeline):

```markdown
<!-- AGENTTEAMS-BRIDGE:BEGIN <region-id> v=<version> -->
<bridge-managed content>
<!-- AGENTTEAMS-BRIDGE:END <region-id> -->
```

`--bridge-merge` updates only the content inside these fences. Place consumer-side notes, project-specific context, or richer documentation OUTSIDE the fences so they survive every merge.
