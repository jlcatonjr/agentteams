# `sandbox_projection`

Projects the model-substrate WASM switch (WASM sandbox, plan P20; operator decision D1 "both") into a consumer
project. A brief sets `wasm_sandbox.model_substrate: "on" | "off"` (or the operator passes `--wasm-substrate`), and
generation writes `<project>/.agentteams/sandbox-state.json` (schema `agentteams-sandbox-state/v1`,
`agentteams/schemas/sandbox-state.schema.json`). A consumer that runs model code, such as baseAgent's battery, reads it
as the default and the floor of its own `--wasm` switch and enforces it. agentteams never runs or confines model code
(Layer D emits; Layers A-C enforce). The switch is independent of `wasm_sandbox.enabled`, which confines agent code.

## The file

```json
{
  "schema": "agentteams-sandbox-state/v1",
  "producer": "agentteams",
  "agentteams_version": "<version>",
  "team": ".claude/agents",
  "os_sandbox": "on",
  "os_sandbox_reason": "profile:confined",
  "wasm_sandbox": "off",
  "wasm_sandbox_reason": "default-off",
  "model_substrate_wasm": "on",
  "model_substrate_wasm_source": "brief",
  "require_enforced": true
}
```

Every value is enumerated (C-4): no brief text, limits, paths, grants or provider names are copied in, so a brief
cannot loosen the consumer's certified grant. `os_sandbox` is the render-time intent, not proof that the consumer's
process is confined. `require_enforced` is always `true`; a consumer must refuse a file without it.

## Public surface

- `PROJECTION_REL`, `SCHEMA_ID`, `PRODUCER`, `FIELDS` (file order = the schema's `required`).
- `TEAM_DIR_KEY`: a transient manifest key, the render's agents dir (set by
  `cli.wasm_sandbox_switch.apply_wasm_switch` on every render with a project root). Like the `_wasm_substrate_*`
  keys it is never persisted: the build-log records only `sandbox_state`.
- `projection_record(manifest, *, team) -> dict | None`: the record, or `None` when neither the brief nor argv sets
  the switch (a kept floor is never re-projected).
- `validate_record(obj) -> dict`: the stdlib twin of the schema (exact keys, enums, `require_enforced is True`);
  raises `ProjectionError`.
- `projection_text(record) -> str`: canonical two-space JSON plus one newline.
- `write_projection(project_root, record, *, dry_run) -> ProjectionOutcome` (`status`: `written`, `unchanged`,
  `kept-foreign`, `dry-run`; `notices`). `.agentteams` must be a real directory and the file regular or absent; the
  write is temp-then-`os.replace` relative to the directory fd (`atomicio.replace_no_follow`); the read is
  `atomicio.read_regular_nofollow`, capped at 4 KiB.
- `read_projection(project_root) -> dict | None`: the reference reader (absent: `None`; anything malformed raises).
- `kept_value(project_root) -> str | None`: the floor an existing file holds (only a readable object saying `"off"`
  is `"off"`).
- `preflight(project_root)`: refuses a symlinked directory or file before anything is emitted.
- `emit_projection(manifest, project_root, team_dir, *, dry_run=False)`: the generate and multi-sync call site.
- `wiring_lines(manifest, project_root, team_dir)`: the `--check-wiring` line (`exists` / `parses` / `matches`).
- `verify_projection(team_dir, project_root)`: the `--verify-integrity` check against the build-log
  `sandbox_state.model_substrate`; a lowered floor exits 1.
- `project_root_for(team_dir)`: strips a default agents-dir suffix.

## Rules

- **Opt-in.** Written only when the switch is set; every other render is byte-identical.
- **Ownership.** Only a file with `producer: "agentteams"` is rewritten. A foreign or unreadable file is kept, with
  `SANDBOX STATE KEPT`. A difference only in `team` is not rewritten; a value change from another team prints
  `SANDBOX STATE CHANGED by <team>: <field> <old> -> <new>` (several teams: the last writer wins).
- **No silent drop.** When the switch becomes unset, the file stays and its value is carried as the kept floor
  (`wasm_config.WASM_SUBSTRATE_KEPT_KEY`), so the relaxation gate sees no change. Lowering it to `off`, or deleting
  the file, is `sandbox-wasm-off:model-substrate`: an operator Ed25519-signed decision in a governed workspace, a
  `SANDBOX RELAXATION` notice elsewhere.
- **Protection.** Where the control plane is not already write-protected (`write_policy: orchestrator-only` or WASM
  on), the file alone gets a Claude `denyWrite` entry and an `Edit(/.agentteams/sandbox-state.json)` rule, a Goose
  Seatbelt deny and a Linux-runner `--protect`. The Codex and Grok runners already protect all of `.agentteams`.
- **Multi-sync.** `--sync` carries the switch with the brief's privilege posture and rewrites the file; its gate check
  never consumes a clearance.

## Residuals (Rule 12)

The kept floor can only hold a stricter value longer. With the OS sandbox off nothing stops a session from editing
the file; `--verify-integrity` and the gate's OR with the build-log record catch a lowered floor afterwards. The
Copilot surfaces (copilot-cli, copilot-vscode) emit no session sandbox, so they never write-protect the file
(`known-limitations.md`). A consumer must also refuse when the file is absent after an `on`: handoff §4.3 item 8.

## See also

- [`sandbox_state`](sandbox-state.md), [`wasm_config`](wasm-config.md), [`--wasm-substrate`](../cli-reference.md).
- The baseAgent handoff: `references/cross-orchestrator-requests/2026-W44-wasm-grant-spec-and-substrate.md` §4.
