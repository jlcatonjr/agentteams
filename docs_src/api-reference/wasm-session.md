# `wasm_session`

**Beta (1.3.0).** Streaming session mode for [`wasm_exec`](wasm-exec.md). It runs one long-lived guest whose stdin
stays open across turns, and relays stdio frame by frame. It was built for consumers whose guest asks the host a
question and blocks in `fd_read(0)` for the reply, hundreds of thousands of times per process.

The guest runs under `wasm_exec`'s own `prepare` and `materialize`: the same pinned grant, limits, empty
environment, verified copies, re-hash and OS nesting as `run()`. Session mode adds no wasmtime flag. The full
contract, with the consumer requirements it answers, is on
[#567](https://github.com/jlcatonjr/agentteams/issues/567#issuecomment-7026367447).

## Entry point

```
python -I -m agentteams.wasm_session run --project-root DIR [options] -- MODULE [guest args...]
```

- `-I` is required; without it the entry point exits 64.
- `MODULE` must be an absolute path to a core module.
- Guest arguments are passed through unchanged after `--argv0 <basename> --`, so arguments beginning with `--` (for
  example `--augmented`) reach the guest.
- wasmtime is the pinned build installed by `agentteams --wasm-install wasmtime --project DIR` (36.0.20 at 1.3.0). It
  is re-hashed when it is resolved and again when it is copied for the run.
- POSIX only (macOS, Linux). The module imports `fcntl`, so it doesn't load on Windows.

| Option | Default | Bounds |
|---|---|---|
| `--status-fd N` | 3 | ≥ 3. Must be open for writing and must not be the same file as fd 0, 1 or 2. |
| `--os-confine` | `auto` | `auto`, `require` or `off` |
| `--fuel`, `--max-memory`, `--timeout` (s) | the grant defaults | `LIMIT_BOUNDS`; whole seconds only |
| `--grace` (s) | 15 | 1–120. The host timeout is `timeout + grace`. |
| `--deadline` (s) | none | Refuses the run when `timeout + grace` exceeds it. |
| `--frame-cap` | 1 MiB | 262,145 B – 16 MiB (guest → parent line) |
| `--in-frame-cap` | 64 KiB | 16 B – 16 MiB (parent → guest line) |
| `--output-cap`, `--input-cap` | 256 MiB, 64 MiB | 1 MiB – 4 GiB, or `0` for no total cap. Memory is still bounded by the frame caps, and time by the always-on host timeout. |
| `--stderr-cap` | 256 KiB | 4 KiB – 16 MiB. Limits the stderr kept in memory; going over it truncates and never kills. |
| `--stdin-eof` | `kill` | `kill` or `keep` |
| `--eof-grace` (s) | 5 | 0–60 |
| `--idle-timeout` (s) | off | 10–3600. Counts only while both directions are silent. |

### Channels

| fd | Content |
|---|---|
| 0 | Parent → guest stdin, relayed unchanged, one frame at a time. |
| 1 | Guest stdout → parent, byte for byte, one complete line at a time. Closed before the `end` record is written. |
| 2 | wasmtime stderr, forwarded best effort. It never blocks the guest. |
| N | Status records: JSON Lines, `schema: "agentteams-wasm-session/1"`, ASCII-escaped. Each record is one complete line, written only by the entry point. |

The records are:

- **`start`**: provenance. It is written after `materialize` and before the guest spawns. It carries `agentteams_version` and `agentteams_commit`, `wasmtime_version` (the pin)
  with `wasmtime_sha256` (the re-hash of the binary that ran), `module_sha256`, `grant_spec_sha256`, `argv_flags`,
  `argv_sha256`, `limits`, `caps`, `os_confinement`, `os_reason`, `read_only_mode` and `run_dir_bytes`.
- **`refused`**: a pre-start refusal, with a `reason`.
- **`end`**: the outcome and its counters.

Exactly one of `refused` or `end` is written, and it is always last.

**`end.outcome`** is one of:

- `exit`: the guest ended on its own;
- `host-timeout`, with `detail` `wall` or `idle`;
- `frame-cap`, with `detail` `out` or `in`;
- `output-cap` or `input-cap`;
- `stdin-eof-kill`;
- `parent-gone`;
- `signal:<NAME>`;
- `internal`.

Every value except `exit` is host-set, and only those may drive a decision. When two happen together, the first
wins.

`guest_exit_code` is the guest's own code. It is null when the host killed the guest, and then `guest_signal` names
the signal. `trap` is diagnostic only, because a guest can print any engine text and choose any exit code. The
remaining fields are:

- `frames_out`, `frames_in`, `frames_undelivered` and `frames_dropped`;
- `bytes_out` and `bytes_in`;
- `partial_final_line`, `last_frame_incomplete` and `guest_stdin_closed`;
- `stderr_dropped`, `stderr_truncated` and `stderr_tail`;
- `timings`, the overhead report: `import`, `prepare`, `materialize` and `cleanup`;
- `spawn_to_first_frame`, `relay_latency_p50` and `relay_latency_p99`, which sit at the top level of `end`, not
  under `timings`;
- `wall_s` and `cleanup`.

**Entry-point exit codes:**

- 0: an `end` record was written;
- 3: a `refused` record was written;
- 64: a usage error. No record is written; the reason goes to stderr;
- 70: an internal error. A best-effort `end` record is written, with `outcome: "internal"` and only the error's
  type.

A parent knows the session is over when it sees **EOF on fd 1 and the `end` record**.

A guest that closes its own stdout and stderr is treated as finished: it is killed, and the outcome is still `exit`,
with `guest_signal` naming the signal.

**Records may carry local paths.** A `refused` reason and `stderr_tail` can contain them. Treat records as local
diagnostics, and don't commit them raw.

### Ending the guest

- **The parent closes stdin, or dies while the entry point lives.** Under `--stdin-eof kill`, the guest's stdin is
  closed. A guest that exits within `--eof-grace` reports `exit`; otherwise its process group is killed and the
  outcome is `stdin-eof-kill`.
- **The parent stops taking stdout.** EPIPE on fd 1 kills the group, with outcome `parent-gone`.
- **SIGTERM, SIGINT or SIGHUP.** The group is killed and the outcome is `signal:<NAME>`.
- **Cleanup.** The run directory is removed on every path.
- **Under bwrap**, wasmtime ends with its sandbox through `--die-with-parent` and the PID namespace.
- **On Linux**, the child also gets `PR_SET_PDEATHSIG`, so even a SIGKILLed entry point takes the guest with it.
- **Residual risk (macOS, and `--os-confine off`).** A SIGKILLed entry point can leave a guest that is computing,
  not blocked in `fd_read`, running until its engine timeout. Its run directory stays behind until the reaper
  removes it.

**The reaper.** Every session begins by removing at most 16 abandoned directories under
`<root>/.agentteams/wasm/exec/`. A directory qualifies only when all of these hold:

- its name is 16 hex digits;
- `lstat` shows it is a real directory;
- it is older than 3840 s;
- its `owner` pid is gone.

It removes them through `remove_tree`, which never follows symlinks, and it never sends a signal.

### `bench`

```
python -I -m agentteams.wasm_session bench --project-root DIR [--asks N] [--os-confine auto] [--unconfined-baseline] MODULE
```

`bench` sends `x\n` and waits for its echo, *N* times, through a session. With `--unconfined-baseline` it does the
same through a bare `wasmtime run`. Both legs use the same `materialize` and the same grant; the baseline has no OS
sandbox, and the entry point prints a warning when it runs one. Results are printed as JSON, and no status records
are written.

Measured at 3×10⁵ requests, macOS arm64, wasmtime 36.0.20, `--os-confine off`: the bare leg took 22–55 µs per request
and the session leg 92–124 µs. The relay costs about 70 µs per request.

## Library

- `relay_session(launch, *, stdin_fd=0, stdout_fd=1, stderr_fd=2, caps=SessionCaps(), stop=..., parent_death=False)
  -> SessionResult`: relays one materialized launch over the given fds, using a single-threaded `selectors` loop.
  It closes `stdout_fd` before returning. The caller owns `launch.cleanup()`.
- `SessionCaps`, `check_caps(caps)`, `CAP_BOUNDS`, `SessionResult`.
- `StatusChannel(fd)`, with `StatusChannel.verify(fd)` and `.write(record, terminal=...)`.
- `reap_stale_runs(project_root, profiles)`: every component of `.agentteams/wasm/exec` must be a real directory
  (checked with `lstat`; nothing is created). Constants: `REAP_MIN_AGE_S`, `REAP_MAX` and `OWNER_FILE`.
- `agentteams_commit()`: from PEP 610 `direct_url.json`, otherwise from the checkout's git files, otherwise None.
- `SCHEMA`; `EXIT_OK`, `EXIT_REFUSED`, `EXIT_USAGE`, `EXIT_INTERNAL`; `DRAIN_S`, `STDERR_TAIL`; `GRACE_BOUNDS`,
  `EOF_GRACE_BOUNDS`, `IDLE_BOUNDS`.
- The `bench` subcommand lives in the internal `agentteams._wasm_session_bench`.
- `main(argv)`.

## See also

- [`wasm_exec`](wasm-exec.md): the grant, `prepare` and `materialize` this builds on.
- [`wasm_runtime`](wasm-runtime.md): `--wasm-install` and the pinned resolver.
