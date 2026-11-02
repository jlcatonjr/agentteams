# `wasm_pins`

A pure hash check of an installed WASM runtime (wasmtime, guest interpreters) against its pins mapping. Every call
re-hashes the installed files, refusing a symlink anywhere below the install dir. A `TODO-PIN` or any non-hex hash
fails closed (Rule 5). The module imports nothing from agentteams, so the standalone `agentteams_wasm` bundle can load
the same file.

## Public Surface

- `pinned_resolver(install_dir, pins, *, platform=None) -> PinnedResolver`. It satisfies
  [`wasm_exec`](wasm-exec.md)`.WasmResolver`.
- `PinnedResolver.resolve(component) -> Path` returns `<install_dir>/<component>/<version>/<entry>`, re-hashed.
- `PinnedResolver.pinned_version(component)` and `.expected_sha256(component)`.
- `PinnedResolver.resolve_tree(component) -> Path` re-hashes a pinned tree (for example, a CPython stdlib).
- `PinnedResolver.runtime_mounts(component) -> tuple[(host, guest), ...]` returns the pinned read-only interpreter
  mounts.
- `tree_digest(top) -> (sha256, count)` is sha256 over the sorted `"<relpath>\0<sha256>\n"` lines of every regular
  file.
- `host_platform() -> str` returns a key such as `darwin-arm64`.
- `WasmPinError` (a `RuntimeError`).
