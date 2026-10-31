# `wasm_config`

The brief's `wasm_sandbox` block, the `--wasm-sandbox on|off` switch, and WASM eligibility (WASM sandbox, plan
P01). One validated, fail-closed place turns WASM confinement on and shapes it. `analyze.build_manifest` carries the
normalised block as `manifest["wasm_sandbox"]` only when the brief has it, so every other manifest is unchanged.
[`sandbox_state.effective_sandbox_state`](sandbox-state.md) reads the WASM half from here.

## Public Surface

- `WasmConfig(enabled, languages, limits, agents, native_exec, native_exec_keep, model_substrate)` (frozen) and
  `AgentWasmGrant(read_paths, scratch)`; `WasmConfig.to_record()` is the team-manifest form (`limits` in
  `fuel` / `max_memory_bytes` / `timeout_s`).
- `normalize_wasm_sandbox(raw, *, agent_slugs, project_root=None) -> WasmConfig`: validation. Every refusal is a
  `ValueError` naming the brief key path, such as `wasm_sandbox.agents.analyst.read_paths[1]`.
- `default_wasm_config()`, `config_from_record(rec, *, agent_slugs)`, `manifest_wasm_config(manifest)`.
- `validate_read_path(raw, path, *, project_root)` and `check_inside_project(rel, project_root, path)`: the read-path
  rules (project-relative, guest-name grammar, no control plane or its ancestors, no credential material, no
  governance root, no symlink).
- `WASM_SANDBOX_CLI_KEY = "_wasm_sandbox_cli"` (transient, never from input), `validate_wasm_sandbox_cli(value)`,
  `wasm_switch(manifest) -> (bool, reason)` (cheap: what `_control_plane_protect.protect_control_plane` reads, so the
  R1 control-plane write protection follows WASM on every render path), `wasm_switch_set(manifest)`.
- `wasm_state_fields(manifest) -> (wasm, wasm_reason, wasm_languages, wasm_grants)`: brief `read_paths` become
  `Mount(<project>/<rel>, "/project/<rel>", "ro")`; `scratch: true` becomes
  `Mount(<project>/.agentteams/wasm/scratch/<agent>, "/scratch", "rw")`, built through
  `sandbox_state.grant_from_tokens`. Non-default limits give every agent an entry; limits mint no token.
- `wasm_eligible(agent_meta) -> bool`: true iff the canonical tools hold `execute` and `edit`. Read-only auditors
  (no `edit`) are never eligible, nor are agents with missing or unknown tools.
- `composition_advisories(state, framework_id, *, platform=None, mechanism=None)`: the D6 notes
  `wasm-without-os-sandbox` and `wasm-only-host`.
- `NATIVE_EXEC_VALUES`, `NATIVE_EXEC_DEFAULT`, `NATIVE_EXEC_EXEMPT` (`orchestrator`, `team-builder`).
- `WASM_SUBSTRATE_CLI_KEY = "_wasm_substrate_cli"`, `WASM_SUBSTRATE_KEPT_KEY = "_wasm_substrate_kept"` (transient,
  never from input), `validate_wasm_substrate_cli(value)`, and `substrate_switch(manifest) -> (value, source)`:
  `--wasm-substrate` > `wasm_sandbox.model_substrate` > a kept projection file > `(None, None)` (P20; see
  [`sandbox_projection`](sandbox-projection.md)).

## Notes

- `cli/wasm_sandbox_switch.py` carries `--wasm-sandbox` and prints the notices during generation
  (`apply_wasm_switch`, `advise_wasm`). The relaxation gate sees the WASM fields through the effective state: on to
  off is `sandbox-wasm-off:<framework>`, new grant tokens are `wasm-grant-widen:<framework>`.
- Grants are carried while WASM is off but stay inert. When WASM turns on, the gate treats them as a widening over
  compute-only.
- Read-only agents that hold `execute` (`navigator`) are not eligible and keep native shell (an accepted limitation).
