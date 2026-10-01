# `projection_marker` — AgentTeamsModule

The honest team marker an interop or `multi_sync` projection writes (2026-10-01). Native generation
records `<agents dir>/references/build-log.json`; fleet discovery, the framework-freshness scan,
`--check`, `--verify-integrity`, the Claude block's present-sibling denies and the `confine-run.sh`
launcher all key on that file. Before this module, no interop path wrote one, so a projected
`.codex/agents` team was invisible to all of them.

> *Source: `agentteams/projection_marker.py`*

---

## Constants

- `MARKER_SCHEMA_VERSION` — `"1.5"`, the native build-log's schema version.
- `INTEROP_ORIGIN` — `"interop"`, the `origin` value that identifies a projection marker
  (also `drift.INTEROP_ORIGIN`).
- `MARKER_FRAMEWORKS` — `codex`, `copilot-vscode`, `copilot-cli`, `goose`. Claude is excluded:
  a claude marker makes the launcher require the gate hook, which only native generation emits.
  agents-md and canonical have no team-marker convention.

## Functions

### `write_projection_marker(agents_dir, framework, *, source_dir, source_framework, files_written, agent_slugs=None) -> ProjectionMarkerResult`

Writes, or refreshes, the `origin: "interop"` marker of a projected team. The marker carries
`schema_version`, `agentteams_version`, `generated_at`, `project_name` and `agent_slug_list` (from
the source team's build-log when it has one, else `agent_slugs`), `framework`, `files_written`
(project-root-relative), `file_hashes` (16-character SHA-256, agents-dir-relative, the native
format, so `--verify-integrity` reads them), `template_hashes: {}`, `origin`, `source_dir`
(project-relative when inside the project), `source_framework` and `source_build_log_sha256`.

- **It never overwrites a native build-log.** That means one without `origin: "interop"`, an
  unparseable one, or a symlink. An existing interop marker is refreshed. When nothing was
  projected, an existing interop marker is left as it is.
- **The control plane is written first (security condition 12).** Once the marker exists, the
  launcher requires the team's switch, verify-key store and rosters. They are created
  write-if-absent (`O_CREAT|O_EXCL|O_NOFOLLOW`), in this order:
  1. the switch, which mirrors the source team's `enforce_decision_signing`. A missing or
     unreadable source switch gives `false`, which reads like no switch;
  2. the verify-key store sentinel;
  3. the roster stubs (`control_plane_io.write_control_plane_stubs`).

  Each required entry is then checked again: it must be a real file or directory, never a symlink.
- **No marker is written on any failure.** If a write fails, a path is unsafe, the agents dir, its
  parent, `references/` or the store is a symlink, or the marker path is a symlink, `error` is set.
- **The write is atomic.** The marker is a temp file moved into place with `os.replace` inside the
  checked `references/`.

Callers:

- `interop.run_interop` (re-exported as `interop.write_projection_marker`), after a real run with
  no errors. A dry run or a skills-only run writes no marker. A refused marker becomes a
  `result.errors` entry.
- `multi_sync._project_and_rebaseline`, after each framework's projection. A refused marker is
  printed and added to the notices.

Returns a `ProjectionMarkerResult` with these fields:

- `marker`: the marker path, or `None`;
- `control_plane`: the files created before the marker;
- `skipped`: why nothing was written;
- `error`: why the marker was refused;
- `created`: true when no marker existed before.

### `existing_marker_origin(agents_dir) -> str | None`

Returns `None` when there is no build-log, `"interop"` for a projection marker, and `"native"`
for anything else, including an unparseable file or a symlink.

### `codex_session_advisory(project_root) -> str`

The text of security condition 14. Once `.codex` is a recognised team, a Claude sandbox block
generated from then on write-denies `.codex` whole, so an interop into `.codex/agents` run from
Bash inside a Claude session fails. The projection must run outside the session. `run_interop`
adds this as a notice the first time it creates a codex marker. Generation prints it through
`generate_helpers._advise_codex_projection_outside_session` when a Claude sandbox is on and
`.codex/agents` carries an interop marker.

### `print_marker_outcome(result, *, label="") -> None`

Prints one line for a `ProjectionMarkerResult`. An error goes to stderr and says that no marker
was written.

## The marker authorises nothing (security condition 13)

`drift.is_interop_marker(build_log)` identifies the marker, and every reader treats it as follows:

- `drift.detect_drift` sets `DriftReport.unverifiable` to `"unverifiable (interop projection: no
  template hashes)"`, and `--check` fails on it rather than report clean.
- `framework_freshness` reports the same status, and never as "current" or the freshest render.
- `--update` treats the marker as no prior build.
- `emit._unmodified_since_build` returns no "unmodified" files, so the marker's `file_hashes`
  never let a merge overwrite a file.
- `cli/output_target` does not count it as evidence of an agentteams tree.
- `fleet` reports an interop Codex team as a SKIP row with its interop refresh command and never
  runs `--update` on it.
- `bridge.native_team_notice` gives the interop command.
- The launcher's missing-entry message names the interop re-run for `.codex/agents`.
