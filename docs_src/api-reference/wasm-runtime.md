# `wasm_runtime` — AgentTeamsModule

Pinned provisioning and verify-on-use for the WASM runtime (wasmtime) and the guest interpreters
(CPython-WASI, QuickJS-ng WASI).

> Source: `agentteams/wasm_runtime.py`, with private helpers `agentteams/_wasm_archive.py` (hashing, path checks,
> safe extraction; stdlib-only) and `agentteams/_wasm_pin_update.py` (the pin updater); pins:
> `agentteams/data/wasm_runtime_pins.json` (the trust anchor).
> CLI: [`--wasm-install`, `--wasm-status`, `--wasm-pin-update`](../cli-reference.md#wasm-runtime-provisioning),
> handled by `agentteams/cli/wasm_commands.py`, the only caller of `install()` and `pin_update()`.

---

## What it guarantees

- **Explicit operator act.** Downloads happen only behind `--wasm-install` / `--wasm-pin-update`. Rendering never
  touches the network: `urllib` is imported lazily inside the downloader, and render code may call `status()` only.
- **Pinned by URL, size and sha256.** The archive is checked before extraction. Extracted files are pinned by
  sha256, and the CPython stdlib by a tree digest (sha256 over sorted `"<relpath>\0<sha256>\n"` lines).
- **Fail closed on `TODO-PIN`.** A pin not yet verified holds the literal `TODO-PIN`. `install()` and `resolve()`
  refuse that component before any network access and name the fix (`agentteams --wasm-pin-update …`).
- **Verify on every use.** `resolve()` and `resolve_tree()` re-hash on every call. Nothing is cached by size or
  mtime. `installed.json` in the project is informational, never trusted.
- **Safe extraction.** Member by member, no `extractall`. Refused: absolute paths, `..`/`.` segments, backslashes,
  drive letters, NUL, symlinks, hardlinks, devices, FIFOs, sparse members, encrypted or non-deflate zip entries,
  duplicates, members outside the pinned prefix, more than 20,000 members, a file over 256 MiB, 512 MiB in total,
  or a zip entry that expands more than 200×. Archive modes are ignored; only `exec: true` in the pins gives
  `0o555`. Members not in the pinned `keep` list are skipped and reported in `InstallReport.notices`.
- **Quarantine, never delete.** A download or install that fails verification is moved under
  `.agentteams/wasm/.quarantine/`.

## Install layout

```
<project>/.agentteams/wasm/
  .gitignore                      "*" (install refuses when the dir is tracked by git)
  wasmtime/<ver>/wasmtime         0o555
  python/<ver>/python.wasm, lib/  files 0o444, dirs 0o555
  javascript/<ver>/qjs-wasi.wasm  0o444
  installed.json                  informational only
  exec/, scratch/                 owned by the executor; install never touches them
  .quarantine/                    failed downloads and installs, kept for inspection
```

## API

| Name | Purpose |
|---|---|
| `COMPONENTS` | `("wasmtime", "python", "javascript")` |
| `INSTALL_REL` | `".agentteams/wasm"` |
| `PINS_SHA256` | sha256 of the packaged pins file |
| `WasmRuntimeError`, `WasmNetworkError` | refusal / verification failure; network failure (CLI exit 3) |
| `InstallReport` | `installed`, `already_ok`, `quarantined`, `notices`, `root` |
| `host_platform()` | `darwin-arm64`, `darwin-x86_64`, `linux-x86_64`, `linux-aarch64` or `windows-x86_64` |
| `load_pins(path=None)`, `validate_pins(doc)`, `pinned(component)` | read and check the pins |
| `install_plan(root, components)` | network-free description of what `install` would fetch |
| `install(root, components, *, platform=None, dry_run=False, fetch=None)` | download, verify, install |
| `resolve(root, component)` | verified entry file, re-hashed now |
| `resolve_tree(root, component)` | verified version dir (the CPython stdlib tree re-digested) |
| `status(root)` | `ok <ver>`, `absent`, `MISMATCH: …` or `pin incomplete` per component; read-only |
| `Resolver(root)` | the object the executor and guest dispatch receive: `resolve`, `pinned_version`, `resolve_tree`, `runtime_mounts` |
| `tree_digest(root)` | the tree digest and file count |
| `default_pins_out()` | the packaged pins file when it sits in a git work tree, else `None` |
| `pin_update(component, version, *, remediation=(), tofu=False, pins_path, …)` | re-pin from release metadata and a verified download; returns the diff |

## Updating pins

`agentteams --wasm-pin-update COMPONENT VERSION` reads `api.github.com` release metadata and applies S-10:
the release must be at least 14 days old and the newest such release on its major line, unless `--remediation`
names GHSA ids (from a recorded `@security` decision) that appear in the line's release notes. It downloads every
platform asset, checks it against the API `digest` (an asset without one needs `--tofu`, recorded as
`"provenance": "tofu"`), extracts it with the same safe extractor, and computes the file and tree hashes. The pins
file is written only after every platform verified, and the diff is printed. Nothing is committed: the diff needs
`@security` review, then the integrity manifest is re-pinned.

CPython-WASI comes from `brettcannon/cpython-wasi-build`, an unofficial third-party build. Its trust rests on the
digest pin and the maintainer's public CI.
