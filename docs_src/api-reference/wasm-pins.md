# `wasm_pins`

A pure hash check of an installed WASM runtime (wasmtime, guest interpreters) against its pins mapping. Every call
re-hashes the installed files, refusing a symlink anywhere below the install dir. A `TODO-PIN` or any non-hex hash
fails closed (Rule 5). The module imports nothing from agentteams, so the standalone `agentteams_wasm` bundle can load
the same file.

## Public Surface

Supported (STABILITY.md §7, from 1.3.0):

- `pinned_resolver(install_dir, pins, *, platform=None)`. It returns an object that satisfies
  [`wasm_exec`](wasm-exec.md)`.WasmResolver`. Only that interface is covered, not the concrete class it returns.
- The `WasmResolver` methods on that object:
  - `resolve(component) -> Path` returns `<install_dir>/<component>/<version>/<entry>`, re-hashed;
  - `pinned_version(component)`;
  - the optional `expected_sha256(component)`.
- `WasmPinError` (a `RuntimeError`).

Documented but internal (not covered by SemVer; STABILITY.md §7):

- the concrete `PinnedResolver` class;
- `PinnedResolver.resolve_tree(component) -> Path` re-hashes a pinned tree (for example, a CPython stdlib);
- `PinnedResolver.runtime_mounts(component) -> tuple[(host, guest), ...]` returns the pinned read-only interpreter
  mounts;
- `tree_digest(top) -> (sha256, count)` is sha256 over the sorted `"<relpath>\0<sha256>\n"` lines of every regular
  file;
- `host_platform() -> str` returns a key such as `darwin-arm64`.
