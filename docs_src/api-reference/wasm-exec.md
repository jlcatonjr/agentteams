# `wasm_exec`

The one place agentteams starts `wasmtime` (WASM sandbox, plan P02). A guest runs under a pinned compute-only grant
(no preopened directory, an empty environment, no sockets) with fuel, memory and wall-clock limits. When an OS sandbox
is usable, wasmtime runs nested inside it ([`confinement`](confinement.md)'s Seatbelt/bwrap builders, via
`agentteams._sandbox_profiles`). The module imports nothing from agentteams, so the standalone `agentteams_wasm`
bundle can load the same file. Provisioning and nesting are injected (`resolver=`, `profiles=`, both required).
The `profiles` module supplies `available`, `seatbelt_profile`, `bwrap_argv`, `snapshot_tree`, `refuse_credentials`,
`remove_tree` (run-directory cleanup) and `ConfinementError`.

## Public Surface

STABILITY.md §7 (from 1.3.0) covers these, and only these: `run`, `prepare`, `materialize`, `assert_grant_shape`,
`Grant`, `Mount`, `Limits`, `LIMIT_BOUNDS`, `DEFAULT_LIMITS`, `COMPUTE_ONLY`, `Prepared`, `Launch` (with `cleanup()`),
`WasmResult`, `WasmResolver`, the `OsConfine` values, `GRANT_SPEC`, `GRANT_SPEC_SHA256` and `WasmExecError`. Everything
else below is documented but internal: `build_argv`, `execute_launch`, `classify_trap` and `trap` labels,
`copy_out_refused`, `probe`, `ProbeReport`, `available`, the refusal lists and the `MAX_*` limits.

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
- `execute_launch(launch, stdin, *, stdout_cap, stderr_cap) -> WasmResult`: runs an already-materialized launch with
  the same stdin-size (8 MiB) and output-cap checks as `run`, which calls it; the caller releases the launch. The WASM
  agent host's brokers use it for launches Stage 0 prepared.
- `WasmResult.os_confinement` is `nested`, `inherited` or `none`. Only `nested` counts as enforced.
  `read_only_mode` is `os`, `snapshot` or `none`. `trap` is diagnostic only, because the guest can forge stderr.
- `classify_trap(exit_code, stderr, *, host_label=None)`, `copy_out_refused(name)`.
- `probe(...) -> ProbeReport` and `available(...) -> str | None`: a real deny run, a control run, the fuel and memory
  traps, and a read-only write attempt. They never answer just "a binary exists".
- `WasmResolver` (protocol), `WasmExecError`.

## Canonical argv tail

For a core module, the canonical wasmtime command line ends with
`--argv0 <name> -- <absolute module> [guest args...]` (#570):

```
[wasmtime, "run", "-C", <C>, "-W", <W>, "-S", <S>, ("--dir", <host>::<guest>)*, "--argv0", <name>, "--", <abs module>, <guest args>...]
```

- `--` ends wasmtime's option parsing, so no guest argument can be read as a wasmtime flag under any wasmtime
  version's parser.
- The module path must be absolute, so it never depends on the working directory.
- `<name>` must match `[A-Za-z0-9_.-]`, at most 64 characters (`_argv0` falls back to `guest`).
- Guest arguments are capped by `MAX_ARGS` and `MAX_ARGS_BYTES`.

`build_argv` emits this form, and `assert_grant_shape` refuses any other, in grant mode and in parity mode alike.
baseAgent's `--argv0 <name> <module>` (no `--`) is the one recorded difference (`INTENTIONAL_DIFFERENCES["argv-tail"]`
in `tests/test_wasm_grant_spec.py`). `GRANT_SPEC` and the shared corpus did not change for this, so their sha256 values
are unchanged. Once baseAgent runs on this executor, the executor builds the argv and the difference goes away.

Component guests are refused in grant mode. agentteams' `@security` ruled on 2026-11-19 (#569) to allow them under
nine conditions, C1–C9, which have not landed yet. Among them: each memory's limit is halved so that two memories fit
in one grant, the table size is limited, imports are checked against an allowlist before launch, and the host builds
`--invoke` from a closed grammar. The component tail will be
`--invoke <wave> -- <absolute component>`, with no `--argv0`.

## Notes

- wasmtime 36 has no read-only preopen (`--dir H::G::readonly` is accepted and writable). `ro` is therefore enforced
  by the nested OS profile, or by a hardened per-run snapshot copy.
- Verified copies live in `<project>/.agentteams/wasm/exec/<run-id>/`, never TMPDIR. A run executes in place from the
  install area only when the caller passes `control_plane_protected=True`.
- Before a run, `materialize` refuses a `/project` mount that overlaps a protected path (compared case-folded, so
  `.GITHUB` is `.github`) or holds a credential-named entry anywhere below it: the bounded scan
  `profiles.refuse_credentials` from the injected `_sandbox_profiles` module (the one shared credential list).
- The nested profile read-denies `NEST_DENY_READS` (`~/.ssh`, `~/.aws`, `~/.config/gh`, `~/.config/gcloud`,
  `~/.netrc`, `~/Library/Keychains`, `~/.docker`, `~/.kube`, `~/.gnupg`, `~/.npmrc`, `~/.pypirc`); the WASM agent's
  Stage 1 host profile reuses the list. These are file denies only: Keychain items stay reachable through the
  Security framework (`securityd`), which the profile does not filter.

## See also

- [`wasm_pins`](wasm-pins.md): the pins check behind `resolver`.
- [`confinement`](confinement.md): the runner's OS sandbox (same profile builders).
