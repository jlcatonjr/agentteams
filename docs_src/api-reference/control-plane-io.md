# `control_plane_io` — AgentTeamsModule

Write-if-absent control-plane stubs and the in-sandbox write preflight (PR-D, 2026-09-30). See
[workspace privilege scoping](workspace-privilege-scoping.md) for the boundary these support.

> *Source: `agentteams/control_plane_io.py`*

---

## Functions

### `stubs_enabled(framework, manifest) -> bool`

True iff that framework's own sandbox predicate is on: `_sandbox_feature_enabled` for `claude`,
`_goose_sandbox_feature_enabled` for `goose`, False for every other framework.

### `write_control_plane_stubs(output_dir, framework, manifest, *, dry_run=False) -> list[Path]`

Writes comment-only stubs of `references/security-approvers.txt`,
`references/authorized-managers.txt` and `references/management-authority.json` into a sandboxed
team, **only when absent**. Each file is created with `O_CREAT|O_EXCL|O_NOFOLLOW`, so an existing
roster or a planted symlink (even a dangling one) is never touched. The stub texts are
`_sandbox_emit.CONTROL_PLANE_STUB_TEXT`; a stub reads exactly like an absent file in every reader.
Deliberately not routed through `extra_output_files`/`emit_all`, which overwrite. Called from the
single `generate_helpers._emit_privilege_artifacts` wrapper and from `multi_sync` projection.
Returns the paths created (or that would be). Raises `OSError` on a create failure other than
"already exists".

### `probe_team_write_denied(output_dir) -> tuple[Path, OSError] | None`

Detects a write-denied team dir before an `--update` writes anything: a `mkstemp`+unlink probe
of the team dir and its `references/` (EROFS/EACCES/EPERM = denied), then a write-only,
non-truncating, non-blocking, no-follow open of each existing **regular** control-plane file
(EROFS/EPERM = denied; a mode-444 file is not, since the writers replace it by atomic rename).
FIFOs, sockets and symlinks are never opened, so a planted FIFO cannot hang the run. A missing
path (ENOENT, e.g. under Seatbelt) is "can't tell". Nothing is created or modified.
`generate_helpers._preflight_sandboxed_write` turns a denial into one exit-2 refusal (`--dry-run`
only reports it).
