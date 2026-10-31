# `wasm_mcp` — AgentTeamsModule

The `agentteams_wasm` MCP server (WASM sandbox plan P10, decision D8). It gives an agent on any MCP-capable vendor CLI
exactly one tool, `run_wasm`, which runs a prebuilt core module, Python source or JavaScript source inside the pinned
wasmtime, under that agent's WASM grant. The default grant is compute-only (D5): no files, no environment, no network.
The server is direct: it needs no out-of-session runner, and `agentteams_runner` grants keep working unchanged.

> Source: `agentteams/wasm_mcp.py` (install and launch facts) and `agentteams/data/agentteams-wasm-mcp.py` (the
> server: stdlib only, no `agentteams` import). Both are integrity-pinned. The per-framework wiring that calls these
> builders is P11.

## Identity and grant: bound at launch

MCP gives a server no caller identity, so **the instance is the identity and its launch flags are the grant**:

```
python3 -I -S -B .agentteams/bin/agentteams-wasm-mcp.py
    --agent <slug|_session> --languages module,python[,javascript]
    [--read <project-rel dir>]... [--scratch]
    --fuel <n> --max-memory-bytes <n> --timeout-s <n>
    --pins-sha256 <hex> --grant-sha256 <hex>
```

- **Per-agent instance** where the host scopes MCP servers per agent: Claude subagent `mcpServers:` front matter and
  Goose recipes. The flags come from `sandbox_state.wasm_grant_for(state, agent)`.
- **One shared `--agent _session` instance**, always compute-only, where the host only has a project-wide MCP config
  (Codex, Grok, VS Code). `launch_args` refuses a widened grant for it.
- `--scratch` mounts `/scratch` at `<project>/.agentteams/wasm/scratch/<agent>/`. The brief never chooses where.
- `--grant-sha256` is recomputed by the server, so a hand-edited flag that forgot the digest refuses to start. It is
  not a security boundary: grant integrity rests on the control plane (below).
- The tool takes **no grant argument**. An unknown argument (`grant`, `read`, `fuel`, ...) is refused, and
  `timeout_s` may only lower the grant's timeout. A grant timeout above 240 s is refused at render (Goose's extension
  timeout is 300 s).

## The bundle

The server runs as `python3 -I -S -B` and cannot import `agentteams`. Generation installs, beside it under
`.agentteams/bin/`, byte-identical copies of the package files it runs (METAPLAN R2/R10):

| Installed | Packaged source | Checked against |
|---|---|---|
| `_sandbox_profiles.py`, `wasm_pins.py`, `wasm_exec.py`, `wasm_guests.py` | the package modules of the same name | `BUNDLE_SHA256` (the server carries the same constants) |
| `wasm_deny_probe.wasm`, `wasm_probe_python.py`, `wasm_probe_javascript.mjs` | `data/wasm_deny_probe.wasm`, `data/wasm_probes/probe.{py,mjs}` | `BUNDLE_SHA256` |
| `wasm_runtime_pins.json` | `data/wasm_runtime_pins.json` | `--pins-sha256` (`wasm_runtime.PINS_SHA256`) |

At start the server reads each file once (no symlink, regular, size-capped) and checks its sha256. It then
`compile()`-s and `exec()`-s the modules from those same bytes into fresh module objects. Nothing is imported by
path, and no bytecode is written (`-B` plus `sys.dont_write_bytecode`). Any mismatch exits with status 2 before
`initialize` is answered. The pins file is checked against a launch flag, not a constant, so a `--wasm-pin-update`
needs no server change.

## `run_wasm`

| Argument | Meaning |
|---|---|
| `language` | `module`, `python` or `javascript`, limited to the instance's `--languages`. |
| `source` | Python or JavaScript source, at most 512 KiB. Exactly one of `source` and `path`. |
| `path` | A project-relative `.wasm` module (`language=module`) or script file under a granted `--read` dir. A compute-only instance cannot name a path. |
| `args` | Up to 64 strings of at most 4 KiB each. |
| `stdin` | UTF-8 text piped to the guest (data, never program text), at most 1 MiB. |
| `timeout_s` | Lowers the grant's timeout for this call. |

The result (`content[0].text`, JSON) holds `exit_code`, `trap`, `timed_out`, `stdout`/`stderr` (each at most 256 KiB,
with `*_truncated` and `*_encoding` set to `utf-8` or `utf-8-replaced`), `os_confinement`, `os_reason`,
`read_only_mode`, `wall_s`, `agent`, `grant_sha256`, `runtime`, and the C-4 `notice`. The whole result is at most
600 KiB. A trap or timeout is a normal result. `isError` is used only for refusals: bad shape, a path outside the
grant, a runtime that is missing or doesn't match its pins, or a failed deny probe.

`os_confinement` is reported as measured: `nested` (Seatbelt or bwrap nested around wasmtime) or `none` (no
nestable OS sandbox here, for example inside a sandboxed session). Only `nested` counts as enforced. The server
never passes the inherited mode (R5).

Before the first run, the server runs the executor's deny probe (`wasm_exec.probe`) in this project. Before the first
run of each interpreter it runs that language's probe (`wasm_guests.probe_language`). Nothing runs until they pass.
Only passes are cached, so a runtime installed later is picked up without a restart. When the runtime is absent,
`tools/list` still lists `run_wasm`, and each call says to run `agentteams --wasm-install`.

## Safety model

- **A trust boundary.** Unlike `agentteams_runner`, no runner re-checks what this server runs. It runs only bundle
  code it verified, and every wasmtime and interpreter resolve re-hashes against the pins.
- **Grant integrity rests on the control plane (R1, S2).** The flags live in agent files and MCP configs, and the
  bundle lives in `.agentteams/bin/`. They are session-write-denied only when the OS sandbox is on and R1's rules
  are emitted and merged into the live host config. `control_plane_advisory` reports anything less as
  `WASM grant integrity: advisory (control plane is agent-writable: <reason>)`, never a silent pass.
- **Read grants exclude secrets (S4).** `--read` paths and `path` arguments use the runner server's deny lists plus
  every agent-config dir. A granted directory that holds a denied name or a symlink refuses to start, because a WASI
  preopen is a whole directory. A file added after start is caught at the next start (residual R4).
- **Verified copies never go to TMPDIR (R3).** wasmtime and the module are copied into
  `.agentteams/wasm/exec/<run-id>/` and re-hashed there. A server that cannot write there refuses the call.
- **Content is data (C-4).** Guest output is returned verbatim, capped and labelled. Nothing is logged to disk. Each
  call writes one stderr line for the host's MCP log.

## Public surface (`wasm_mcp`)

| Name | Purpose |
|---|---|
| `SERVER_NAME`, `TOOLS`, `MAP_VERSION` | `agentteams_wasm`, `("run_wasm",)`, and the tool-surface version. |
| `PROTECTED_PATH`, `BIN_DIR`, `PYTHON_FLAGS`, `SHA256` | Install path and dir, the `-I -S -B` launch flags, and the server's pinned hash. |
| `BUNDLE`, `BUNDLE_SHA256`, `BINARY_FILES`, `PINS_INSTALLED` | The bundle (installed path, packaged source), its pins, the one binary file, and the pins copy. |
| `SESSION_AGENT`, `GOOSE_TIMEOUT`, `MAX_TIMEOUT_S`, `GRANT_SCHEMA` | `_session`, 300 s, 240 s, and the canonical grant-JSON schema id. |
| `server_content() -> str`, `bundle_content(installed_path) -> bytes`, `bundle_files() -> list`, `pins_sha256() -> str` | Packaged content, each re-hashed against its pin (`ValueError` on a mismatch). |
| `grant_parts(grant)`, `grant_digest(grant) -> str`, `grant_args(grant) -> list[str]` | A `Grant`'s read paths and scratch flag, its canonical digest, and its flags. |
| `launch_args(agent, grant, languages, pins_sha=None) -> list[str]` | The canonical arguments for one instance. There is no `--root`: the server derives the root from its install location. |
| `agent_launch_args(state, agent, pins_sha=None)`, `session_launch_args(state, pins_sha=None)` | The same from a `SandboxState`. Each returns `None` when WASM is off. |
| `interpreter() -> str`, `tool_names() -> list[str]` | The system `python3` (shared with `runner_mcp`), and `["mcp__agentteams_wasm__run_wasm"]`. |
| `claude_entry(args, python) -> str`, `goose_extension(args, python) -> dict`, `table_entry(args, python) -> dict` | Entry builders P11 composes into front matter, recipes and TOML/JSON configs. |
| `install_files(manifest)`, `install_binary_files(manifest)` | The text and binary install files (`../../.agentteams/bin/...`) when WASM is on, else `[]`. |
| `write_bundle(project_root, files=None) -> list[str]` | Writes the server and bundle byte-exactly (atomic replace, symlinked dirs refused). |
| `control_plane_advisory(root, framework, manifest) -> str \| None` | The R1 check. P13 records the same answer as `wasm_enforcement`. |
| `wiring_problems(root, framework, manifest, *, home=None) -> list[str]` | Install hashes, stray bytecode, runtime status, shadowing (`runner_mcp._shadowing(..., name)`), and the R1 advisory. |
