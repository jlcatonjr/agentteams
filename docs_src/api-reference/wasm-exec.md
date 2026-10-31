# `wasm_exec`

The one place agentteams starts `wasmtime` (WASM sandbox, plan P02). A guest runs under a pinned compute-only grant
(no preopened directory, an empty environment, no sockets) with fuel, memory and wall-clock limits. When an OS sandbox
is usable, wasmtime runs nested inside it ([`confinement`](confinement.md)'s Seatbelt/bwrap builders, via
`agentteams._sandbox_profiles`). The module imports nothing from agentteams, so the standalone `agentteams_wasm`
bundle can load the same file. Provisioning and nesting are injected (`resolver=`, `profiles=`, both required).

## Public Surface

- `GRANT_SPEC`, `GRANT_SPEC_SHA256`: the canonical grant (shared with baseAgent). `python -m agentteams.wasm_exec
  --print-spec` prints it as canonical JSON.
- `Limits(fuel, max_memory_bytes, timeout_s)`, `DEFAULT_LIMITS`, `LIMIT_BOUNDS`.
- `Mount(host, guest, mode)`: guest names `/project/<rel>` (ro), `/scratch` (rw), `/src` and `/lib[/...]` (ro, runtime
  only). Host paths with `::` are refused. `RUN_SCRATCH` requests a one-off scratch inside the run directory.
- `Grant(mounts, limits)`, `COMPUTE_ONLY`.
- `build_argv(...)`, `assert_grant_shape(argv, grant | None, *, runtime_mounts=(), profile="module")`: an exact-shape
  check. `grant=None` is baseAgent parity mode, where any `--dir` is refused.
- `prepare(module, *, grant, runtime_mounts=(), src_files=None, compile_grace_s=15) -> Prepared`: validation only.
  The module must have the binary `\0asm` v1 header, so `.wat` text and components are refused.
- `materialize(...) -> Launch` (for a caller that owns the `Popen`) and `run(...) -> WasmResult`.
- `WasmResult.os_confinement` is `nested`, `inherited` or `none`. Only `nested` counts as enforced.
  `read_only_mode` is `os`, `snapshot` or `none`. `trap` is diagnostic only, because the guest can forge stderr.
- `classify_trap(exit_code, stderr, *, host_label=None)`, `copy_out_refused(name)`.
- `probe(...) -> ProbeReport` and `available(...) -> str | None`: a real deny run, a control run, the fuel and memory
  traps, and a read-only write attempt. They never answer just "a binary exists".
- `WasmResolver` (protocol), `WasmExecError`.

## Notes

- wasmtime 36 has no read-only preopen (`--dir H::G::readonly` is accepted and writable). `ro` is therefore enforced
  by the nested OS profile, or by a hardened per-run snapshot copy.
- Verified copies live in `<project>/.agentteams/wasm/exec/<run-id>/`, never TMPDIR. A run executes in place from the
  install area only when the caller passes `control_plane_protected=True`.
- Before a run, `materialize` refuses a `/project` mount that overlaps a protected path (compared case-folded, so
  `.GITHUB` is `.github`) or holds a credential-named entry anywhere below it: the bounded scan
  `profiles.refuse_credentials` from the injected `_sandbox_profiles` module (the one shared credential list).

## See also

- [`wasm_pins`](wasm-pins.md): the pins check behind `resolver`.
- [`confinement`](confinement.md): the runner's OS sandbox (same profile builders).
