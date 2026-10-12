# `signing_switch` — AgentTeamsModule

The one writer of the strict agent-privilege switch, `<agents dir>/references/agent-privilege.json`
(key `enforce_decision_signing`), for both native generation and interop / `multi_sync` projection
(2026-10-11). The security gate reads the switch to decide whether an unsigned authorizing
decision row is refused.

Before this module the projection wrote the switch only when it was absent, so an operator's
`"enforce_decision_signing": false` reached `.github/agents` but never an existing projected
`.codex/agents` (fleet-sync round-3 security review).

> *Source: `agentteams/signing_switch.py`*

---

## Ownership

The pipeline **owns** a switch when the team has a team marker (a regular
`references/build-log.json`, native or `origin: "interop"`) **and** the existing switch is a
regular JSON file whose `note` is one of the pipeline's own notes (`NATIVE_NOTE`,
`PROJECTION_NOTE`). An owned switch tracks the rendered value. Anything else is **unclear**: no
marker, a hand-written note, a malformed file or a symlink. An unclear switch is never changed.

## Constants

- `SWITCH_REL`: `"references/agent-privilege.json"`.
- `TEAM_MARKER_REL`: `"references/build-log.json"`.
- `GOVERNED_MARKER`: `"signing-governed.marker"`. A governed team never gets `false`.
- `RELAXED_PREFIX`: `"SIGNING SWITCH RELAXED"`, printed for an owned `true -> false` change.
- `KEPT_PREFIX`: `"SIGNING SWITCH KEPT"`, printed when a switch is left differing from the
  rendered value: unclear ownership, an unusable file, a signing-governed team, or a failed write.
- `NATIVE_NOTE`, `PROJECTION_NOTE`, `PIPELINE_NOTES`: the notes the two writers stamp.
- `TEAM_DIRS`: `.github/agents`, `.claude/agents`, `.goose/recipes`, `.codex/agents`.
- `TEAM_MARKER_REL`: re-exported from `frameworks/_sandbox_emit`. `projection_marker` and
  `cli/artifacts.AGENT_PRIVILEGE_REL_PATH` import the switch constants from here.

## Functions

### `apply_switch(agents_dir, value, note, *, label="", create_only=False) -> SwitchOutcome`

Makes the switch track `value`:

- an absent switch is created;
- an owned switch is rewritten when its text differs (an identical one is left byte-identical);
- an unclear switch is kept, with a `KEPT_PREFIX` notice when its value differs;
- in a signing-governed team, `false` becomes `true` (written or kept), with a `KEPT_PREFIX`
  notice;
- with `create_only`, an existing switch is never rewritten. A projection whose source has no
  switch passes this, so a missing file never relaxes a switch.

The write never follows a link. `references/` is opened `O_DIRECTORY|O_NOFOLLOW`, and a temp file
is `os.replace`d relative to that descriptor. `SwitchOutcome` carries `path`, `written`, `kept`,
`relaxed` and `notices`. Raises `OSError` when the write fails.

### `sync_projected_switches(agents_dir) -> list[str]`

Called by `cli/generate_helpers._emit_agent_privilege_config` after a native render. The value
propagated is the one now on disk in `agents_dir`, which is what a re-projection would mirror. It
is never the bare description value, so a kept, hand-pinned source switch is not contradicted.

Every sibling team in `TEAM_DIRS` whose interop marker names this team as its `source_dir` is
passed through `apply_switch`. The sibling must be reached through real directories inside the
project, never a symlink. Native siblings and projections of another source are left alone. It
returns the notices to print. A failed sibling write becomes a `KEPT_PREFIX` notice, never an
exception.

### `read_switch(path) -> bool | None`

Returns the switch value, or `None` when nothing is there. Raises `ValueError` for a symlink, a
non-regular file, malformed JSON or a non-boolean value. It never defaults to `false`.
`projection_marker` delegates to it.

### `owns_switch(agents_dir) -> bool`, `is_governed(*dirs) -> bool`, `switch_payload(value, note) -> str`

The ownership test above, the governed-marker test, and the switch file text.

### `review_lines(update_output) -> list[str]`, `review_reason(lines) -> str`

Used by `fleet`. `review_lines` picks the `RELAXED_PREFIX` / `KEPT_PREFIX` lines out of a target's
update output, wherever they sit in the line. A non-empty result makes the target REVIEW, with the
reason from `review_reason` ("signing switch relaxed (n)", "signing switch kept, not tracking the
description (n)"). This applies in git and non-git workspaces. On a FAIL or fence-imbalance result
the reason is appended to the detail.

`--dry-run` does not preview a relax: the switch is written only on a real run. Read the REVIEW
reason of the applied run before pushing.

## Callers

- `cli/artifacts._write_agent_privilege_config`: native generate, update and heal.
- `cli/generate_helpers._emit_agent_privilege_config`: the sibling sync.
- `projection_marker._write_control_plane` and `_track_switch_only`: every projection, including
  one that projected no files. Both are create-only when the source has no switch
  (`multi_sync`'s `.agentteams/canonical`).

An absent `enforce_decision_signing` key in the description still means `true`
(`analyze.build_manifest`).
