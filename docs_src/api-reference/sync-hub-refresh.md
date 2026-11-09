# `sync_hub_refresh` — AgentTeamsModule

Refreshes the [canonical hub](canonical.md)'s instruction-file fences from the pin's deployed
instruction file (`--sync-refresh-instructions`).

> *Source: `agentteams/sync_hub_refresh.py`*

The hub's `instructions_binding.content` is seeded by `--sync-init`, and [`multi_sync`](multi-sync.md)
reconciles agents three-way, so the hub used to age while the deployed instruction files were
regenerated from newer templates. The version guard (`interop_helpers.keep_newer_fences`, below) stops
a projection lowering a fence, but cannot catch stale same-version values (`src/`, `templates/`).
`run_sync` therefore calls `refresh_hub_binding` before every projection, and
`--sync-refresh-instructions` runs the same refresh on its own.

## Public types

- `HubRefreshResult`: `pin`, `refreshed` (fences replaced from the pin), `added` (fences only the
  pin had), `notices`, `changed`, `dry_run`, `replaced_fenceless` (the hub's instruction content had no
  fences and was replaced wholesale by the pin's).

## Public functions

### `refresh_hub_instructions`

`refresh_hub_instructions(root, *, dry_run=False) -> HubRefreshResult`

Fence-merges the pin framework's deployed instruction file into the hub. A fence another framework
in the sync set deploys at a higher `v=` is taken from that framework instead (a sync from before the
guard lowered the pin's own file too). Only fenced regions move;
the hub's text outside fences is kept. A hub whose instruction content has no fences at all can't be
fence-merged, so it is replaced by the pin's file; before the save, its `team.cai.json` is backed up to
`agents/.agentteams-backups/<timestamp>/__external__/`, so `--restore-backup` on the hub's agents dir
recovers the old text (a notice names the backup). A hub fence with a higher `v=` than the pin's is kept and
reported, so the hub never lowers either. No shrink-preserve: the operator asked for the pin's
bodies. A hub fence still below the newest shipped template's version afterwards is reported: every
copy is stale, so regenerate the pin natively (`--update`) and refresh again. With `dry_run` nothing is
written. Raises `ValueError` when the project is not pinned, the pin has no instruction file, or
fences do not parse, and `FileNotFoundError` when the hub has no `team.cai.json`.

### `refresh_hub_binding`

`refresh_hub_binding(root, pin_doc, hub, *, dry_run=False, take_peer_fences=True) -> HubRefreshResult`

The same refresh applied in memory to an already-loaded hub (`hub["instructions_binding"]["content"]`
is updated unless `dry_run`); the caller persists it. `run_sync` uses it before projecting, always in
memory (a dry run previews the projection from the refreshed hub and only skips the save), with
`take_peer_fences=False`, so the pin stays authoritative and an edited peer file cannot spread its body
unasked. It turns a `ValueError` into a notice rather than aborting the sync; `pin_instruction_problem`
has already refused the cases that would raise it, apart from a team with no instruction files.

### `pin_instruction_problem`

`pin_instruction_problem(root, pin_doc, hub) -> str`

Why the pin's deployed instruction file is unsafe to sync from, with the remedy, or `""`. Unsafe means:
missing, or with no parseable fences, while the hub has fences to project; or another framework deploys a
fence newer than both the pin and the hub. `run_sync` and `sync_init` raise on it before writing anything.
Running `--sync-refresh-instructions` takes the peer's fence into the hub and reports it for review, which clears
the refusal, and the next sync brings the pin up to it. Stale values at the same fence version can't be detected here.

### `refuse_unsafe_pin_instructions`

`refuse_unsafe_pin_instructions(root, pin_doc, hub) -> None`

Raises `ValueError` carrying `pin_instruction_problem`'s text. `run_sync` and `sync_init` call it first.

### `refresh_hub_for_projection`

`refresh_hub_for_projection(root, pin_doc, hub, canonical_dir, *, dry_run, notices) -> None`

`run_sync`'s pre-projection step: `refresh_hub_binding` from the pin alone, reported in `notices`, saved unless
`dry_run`.

## The projection guard

`interop_helpers.keep_newer_fences(rendered, existing)` puts back every existing fence whose `v=`
is higher than the incoming one and returns one notice per kept fence (starting
`FENCE_DOWNGRADE_NOTICE`). A version above the newest shipped template's
(`template_fence_versions()`) is not kept: no release wrote it, and keeping it would pin that body
against every later update. `merge_instruction_file`, which every `preserve_existing` projection of
an instruction file uses (pinned `--sync` and the Grok path included), runs it first. The guard cannot
tell a stale same-version body from a deliberate one, and it protects only fences that already exist;
the refresh and `pin_instruction_problem` cover the rest. `template_fence_versions()` reads literal
`AGENTTEAMS:BEGIN <id> v=N` markers only, so the fences code builds by f-string (`wasm_sandbox`,
`codex_translation`, `grok_runtime`, `constitutional_rules_baseline`, `codex_write_policy`, and
`fence_inject`'s runtime `content`/`content_N`) have no ceiling. `tests/test_sync_low_severity.py` lists
each site with its `v=`, so a new site or a version bump fails until it is listed and its ceiling decided.
