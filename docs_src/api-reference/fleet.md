# `fleet` — AgentTeamsModule

Safely run `--update --merge` across **every** agent-infrastructure workspace under a parent directory (and its subfolders) in one command — covering both `.github/agents/` (copilot-vscode) and `.claude/` infrastructures — with a git-commit snapshot before each apply and a `git diff` content audit after.

> *Source: `agentteams/fleet.py`*

---

## Command-line usage

The fleet runner is driven entirely from the CLI via the **`--fleet`** option (there is no separate executable):

```bash
# Dry-run preview — discovers workspaces and reports what WOULD change. No writes.
agentteams --fleet /path/to/parent --update --merge

# Apply — snapshot each git workspace, run the merge, then analyse the diff.
agentteams --fleet /path/to/parent --update --merge --yes

# Restrict to one infrastructure, or change the report location.
agentteams --fleet /path/to/parent --update --merge --yes --fleet-frameworks github
agentteams --fleet /path/to/parent --update --merge --yes --fleet-report /tmp/fleet-out
```

`--fleet` **requires** `--update` and `--merge`. The default (without `--yes`) is a **dry-run preview**; pass `--yes` to apply. See the [CLI Reference → Fleet Update](../cli-reference.md#fleet-update-multi-workspace) for the option table and the full exclusivity rules.

### What one run does, per discovered workspace

1. **Discover** — find directories containing `.github/agents/` and/or `.claude/`, pruning `node_modules`, `.git`, `.worktrees`, and `archive`, and never recursing into `.github`/`.claude` internals.
2. **Snapshot (git commit)** — before applying, commit the workspace's agent-infra state as `chore(fleet): pre-update snapshot` (or stay at `HEAD` when already clean). This is the recoverable rollback point and the diff base. Non-git workspaces fall back to the automatic `<output_dir>/.agentteams-backups/<ts>/` snapshot.
3. **Update in-process** — re-enter the standard update path per target (`--update --merge` for `.github/agents/` and native `.claude/agents/` teams; `--bridge-merge` for bridge-consumer `.claude/`). No subprocess is spawned, so a successful merge is never misreported because of an interpreter/exit-code quirk, and a failure in one target is isolated.
4. **Diff analysis** — after applying, `git diff <snapshot>` is classified by the **authoritative content signals** — shrink Notices and deletions inside `USER-EDITABLE` regions — **not** the process exit code. Per-workspace `.diff` files plus `report.json` and `summary.md` are written under `<DIR>/.agentteams-fleet/<run-id>/`.

### Per-target status

Each `(workspace, target)` row is one of:

| Status | Meaning |
|--------|---------|
| `OK` | Merge succeeded; only fenced/generated regeneration and intel churn. |
| `REVIEW` | A shrink Notice or a `USER-EDITABLE`-region deletion was detected — inspect the saved diff before committing. |
| `FAIL` | The merge itself errored (e.g. invalid/absent descriptor, bridge source missing). |
| `SKIP` | Ambiguous `.claude/` (no bridge signal and no resolvable descriptor) — left for manual review. |
| `WOULD-UPDATE` | Dry-run preview only. |

> **Note on the `target` field when parsing `report.json`:** updated rows use `target = "github"`, `"claude-direct"`, or `"claude-bridge"`. A `SKIP` row for an ambiguous `.claude/` carries `target = "claude"` (it could not be classified). Filter for these explicitly so manual-review rows are not dropped.

### Safety guarantees

Fleet mode is **non-destructive by construction**: it is merge-only, and `--overwrite`, `--prune`, `--migrate`, `--bridge-refresh`, `--shrink-policy=allow`, and every single-target flag (`--description`, `--project`, `--output`, `--self`, …) are rejected — see the [full exclusivity list](../cli-reference.md#explicitly-excluded-option-pairs). `.claude/` is only ever **bridge-merged**, never bridge-refreshed. Descriptor resolution prefers `.agentteams/brief.json` over the thin `_build-description.json` stub. The exit code is `1` only when a target genuinely `FAIL`ed; a `REVIEW` exits `0` with a warning so the report can be inspected.

---

## Classes

### `TargetResult`

> *Source: `agentteams/fleet.py`*

Outcome of updating one target (`"github"`, `"claude-direct"`, or `"claude-bridge"`) within a workspace.

**Attributes:**

- `workspace` (`str`) — Absolute path of the workspace.
- `target` (`str`) — Which infrastructure was updated.
- `status` (`str`) — `OK` | `REVIEW` | `FAIL` | `SKIP` | `WOULD-UPDATE`.
- `detail` (`str`) — Human-readable explanation (e.g. the first error, or the review reason).
- `descriptor` (`str`) — The descriptor file used.
- `files_changed`, `added_lines`, `removed_lines` (`int`) — Diff magnitude vs the snapshot.
- `shrink_notices` (`list[str]`) — Shrink-guard Notices emitted during the merge.
- `user_editable_deletions` (`list[str]`) — Deleted lines that fell inside a `USER-EDITABLE` region (the real content-loss signal).
- `rc` (`int | None`) — Raw return code of the in-process update (diagnostic only — status is derived from content, not this).

### `WorkspaceResult`

> *Source: `agentteams/fleet.py`*

Aggregate result for one workspace.

**Attributes:**

- `path` (`str`) — Absolute path of the workspace.
- `is_git` (`bool`) — Whether the workspace is a git repository.
- `snapshot_ref` (`str | None`) — The snapshot commit/HEAD used as the diff base and rollback point.
- `snapshot_committed` (`bool`) — `True` if a snapshot commit was created (workspace was dirty), `False` if `HEAD` was already clean.
- `diff_file` (`str | None`) — Path to the persisted per-workspace `.diff`.
- `targets` (`list[TargetResult]`) — One entry per updated target.

---

## Functions

### `discover_workspaces(parent, frameworks="both")`

> *Source: `agentteams/fleet.py`*

Return the sorted list of workspace directories under `parent` (recursively) that contain `.github/agents/` and/or `.claude/`. `frameworks` (`"github"` | `"claude"` | `"both"`) filters which infrastructure qualifies a directory. Prunes `node_modules`, `.git`, `.worktrees`, and `archive`, and never recurses into `.github`/`.claude` internals.

**Returns:** `list[Path]`

### `run_fleet(args, parser)`

> *Source: `agentteams/fleet.py`*

Entry point dispatched from `build_team.main()` when `--fleet` is set. Discovers workspaces, snapshots and updates each target in-process, classifies the resulting diff, writes the report under `<DIR>/.agentteams-fleet/<run-id>/`, and returns an exit code (`1` if any target `FAIL`ed, else `0`).

**Parameters:**

- `args` (`argparse.Namespace`) — Parsed CLI arguments (reads `fleet`, `fleet_frameworks`, `fleet_report`, `yes`, `dry_run`, `shrink_policy`).
- `parser` (`argparse.ArgumentParser`) — The CLI parser (reserved for error reporting).

**Returns:** `int` — Process exit code.

---

## Rollback

The pre-update snapshot is the recoverable point. To undo a workspace's update:

```bash
git -C /path/to/workspace reset --hard <snapshot_ref>   # snapshot_ref is in report.json
```

Non-git workspaces restore from their `<output_dir>/.agentteams-backups/<ts>/` snapshot (see [`emit.restore_backup`](emit.md)).
