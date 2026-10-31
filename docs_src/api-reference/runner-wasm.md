# `runner_wasm`

The runner's `sandbox: wasm` jails (WASM sandbox, plan P12; D2 "strong mode"). Under `write_policy:
"orchestrator-only"` an operator may set `confined_programs.<agent>.sandbox = "wasm"`. That agent then has no native
program list: each queued command names a guest, and the runner (the sole executor) runs it as the pinned wasmtime
inside a fresh Seatbelt/bwrap wrap. Entries without `sandbox` (or with `"os"`) behave exactly as before.

> Source: `agentteams/runner_wasm.py` (integrity-pinned). Used by [`confinement`](confinement.md)`.load_confined`,
> [`proposal_run`](proposal-run.md), [`proposal_runner`](proposal-runner.md) (start check), `--install-confined` and
> the render's sandbox-relaxation gate.

## Entry shape

```json
"confined_programs": {
  "data-analyst": {
    "sandbox": "wasm",
    "wasm": {"languages": ["python"], "read": ["data/inputs"], "scratch": true,
             "limits": {"fuel": 2000000000, "memory_mib": 512, "timeout_s": 120}}
  }
}
```

- `exec`, `write` and `allow_unresolved_launcher_with_package_store` are refused: the only program is the pinned
  wasmtime, and a guest writes only its scratch.
- `languages` ⊆ [`wasm_guests.LANGUAGES`](wasm-guests.md). `read` are literal project-relative directories, preopened
  read-only at `/project/<rel>` (never the ledger, `.git`, the control plane or a secret). `scratch: true` preopens
  `<project>/.agentteams/wasm/scratch/<agent>/` at `/scratch`. `limits` lie inside `wasm_exec.LIMIT_BOUNDS`; the run's
  `timeout_s` never exceeds the command entry's timeout or the MCP channel cap.

## Commands

`argv[0]` is `wasm:module`, `wasm:python` or `wasm:javascript`; `argv[1]` is a `.wasm` / `.py` / `.js|.mjs` file under
a `read` path; the rest are guest arguments. A wasm agent's host `argv[0]` and an `os` agent's `wasm:` form are both
refused, as is a wasm agent's command outside the runner.

## Public Surface

- `SANDBOX_KINDS`, `WASM_ARGV0`, `EXTENSIONS`, `SCRATCH_REL`, `RunnerWasmError`.
- `validate_entry(agent, spec, control_plane_of, *, protected_rel) -> dict` (called by `load_confined`).
- `prepare_command(root, agent, argv, jail, *, timeout_s, resolver=None) -> WasmCommand`: validation and
  `wasm_guests.argv_for` before any ledger row; wasmtime and the interpreter are re-hashed against the pins.
- `launch(cmd, root, *, sandbox) -> wasm_exec.Launch`: `wasm_exec.materialize` with `os_confine="require"` (or `"off"`
  under the logged `allow_unconfined_runs` opt-out, with a `[runner] WASM only` notice). Executables run from the
  install dir, re-hashed just before exec (R3'); a copy, when needed, lands in `.agentteams/wasm/exec/<run-id>/`,
  never TMPDIR. The wasmtime environment is empty.
- `build_grant`, `grant_digest`, `exec_literal`, `check_runtimes(root, confined)` (runner start, fail closed).
- `program_modes(raw)`, `runner_exceptions(raw) -> SandboxExceptions`, `effective_raw(root, brief_confined)`,
  `switch_relaxations`, `check_switch`, `team_dirs`, `gate_policy_change`: the `wasm-to-os-switch` effect through the
  P15 gate.

## Ledger

`run-request-start` / `run-request` rows gain `"sandbox": "wasm"` and `"wasm": {language, module_sha256, source_sha256,
grant_sha256, wasmtime_sha256, os_confinement}` (`nested` | `none`); `confined` becomes `seatbelt+wasm`, `bwrap+wasm`
or `unconfined-opt-out+wasm`. The result gains a diagnostic `trap` (guest stderr is data, C-4).

## Rule 12 carve-outs

1. The wasm branch never calls `_resolve_program`, which refuses programs inside the project: only the pinned,
   re-hashed wasmtime under `.agentteams/wasm/` runs.
2. A wasm jail has no operator exec list; the nested profile's only exec rule is that one literal.
3. Scratch changes count as declared, only when an OS sandbox bounded the run.
4. The scratch is the one write-allow inside a protected path (R11), only for `sandbox: wasm` entries.

## Governance

Moving an entry from `sandbox: wasm` back to `os` is the constraint-relaxing `wasm-to-os-switch:<agent>` effect.
`--install-confined` checks it (an operator Ed25519-signed decision in a governed workspace, else a `SANDBOX
RELAXATION` notice) before installing; the render consumes the decision and registers the standing exception, and
records `sandbox_exceptions` (`wasm_programs`, `os_programs`) once any runner program uses `sandbox: wasm`.
