# `sync_hub_refresh` — AgentTeamsModule

Refreshes the [canonical hub](canonical.md)'s instruction-file fences from the pin's deployed
instruction file (`--sync-refresh-instructions`).

> *Source: `agentteams/sync_hub_refresh.py`*

[`multi_sync`](multi-sync.md) reconciles agents only. The hub's `instructions_binding.content` is
seeded by `--sync-init` and never refreshed by `--sync`, so it ages while the deployed instruction
files are regenerated from newer templates. Projection no longer lowers a fence version (see
`interop_helpers.keep_newer_fences` below), but a stale hub still carries stale same-version
values (`src/`, `templates/`). This module brings the hub up to date.

## Public types

- `HubRefreshResult`: `pin`, `refreshed` (fences replaced from the pin), `added` (fences only the
  pin had), `notices`, `changed`, `dry_run`.

## Public functions

### `refresh_hub_instructions`

`refresh_hub_instructions(root, *, dry_run=False) -> HubRefreshResult`

Fence-merges the pin framework's deployed instruction file into the hub. A fence another framework
in the sync set deploys at a higher `v=` is taken from that framework instead (a sync from before the
guard lowered the pin's own file too). Only fenced regions move;
the hub's text outside fences is kept. A hub fence with a higher `v=` than the pin's is kept and
reported, so the hub never lowers either. No shrink-preserve: the operator asked for the pin's
bodies. A hub fence still below the newest shipped template's version afterwards is reported: every
copy is stale, so regenerate the pin natively (`--update`) and refresh again. With `dry_run` nothing is
written. Raises `ValueError` when the project is not pinned, the pin has no instruction file, or
fences do not parse, and `FileNotFoundError` when the hub has no `team.cai.json`.

## The projection guard

`interop_helpers.keep_newer_fences(rendered, existing)` puts back every existing fence whose `v=`
is higher than the incoming one and returns one notice per kept fence (starting
`FENCE_DOWNGRADE_NOTICE`). A version above the newest shipped template's
(`template_fence_versions()`) is not kept: no release wrote it, and keeping it would pin that body
against every later update. `merge_instruction_file`, which every `preserve_existing` projection of
an instruction file uses (pinned `--sync` and the Grok path included), runs it first. When it fires
during `--sync`, `SyncResult.notices` also carries a hint to run `--sync-refresh-instructions`.
The guard cannot tell a stale same-version body from a deliberate one; the refresh handles those.
