# `wasm_guests`

Runs Python, JavaScript or a prebuilt core module as a WASM guest on the caller's grant (WASM sandbox, plan P04). One
call becomes one [`wasm_exec`](wasm-exec.md) run. The interpreter's stdlib reaches the guest read-only through runtime
mounts whose guest names come from the pins file (CPython: `/lib`). The source reaches it as a private read-only
`/src/main.<ext>` file, so stdin stays free for data. The grant is never widened, and an unsupported language refuses
(there is no native fallback). The module imports nothing from agentteams, so the standalone `agentteams_wasm` bundle
can load the same file. The executor, the resolver and the OS-profile module are injected (`executor=`, `resolver=`,
`profiles=`).

## Public Surface

- `LANGUAGES = ("module", "python", "javascript")`, `MAX_SOURCE_BYTES` (1 MiB), `PYTHON_FLAGS` (`-I -S -B`).
- `argv_for(language, source, module_path, *, args=(), grant, project_root, resolver, executor) -> (Prepared,
  guest_args)`: validation only (no spawn, no writes), for callers that own the run (the runner).
- `run_code(language, source, module_path, *, args=(), stdin=b"", grant, project_root, os_confine="auto", resolver,
  executor, profiles, ...) -> WasmResult`.
- `LANGUAGE_DEFAULT_LIMITS` and `limit_notices(language, limits)`: the measured startup floors. A grant below a floor
  still runs, with a notice. Limits are never raised.
- `probe_language(language, project_root, *, resolver, executor, profiles, ...) -> GuestProbeReport`: a canary deny run,
  a liveness control that must see a widened grant, the fuel and memory traps, and an unchanged host runtime tree.
- `parse_probe(stdout)`, `check_module_path(...)`, `js_source_name(source)`, `GuestResolver` (protocol),
  `WasmGuestError` (not a `WasmExecError`).

## Notes

- `language="module"` paths must be regular, non-symlinked files inside the project and outside its protected paths.
- The probe scripts live in `agentteams/data/wasm_probes/`. The JavaScript probe and argv are unverified until
  QuickJS-ng is pinned and installed.
