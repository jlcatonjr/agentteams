# `confinement` — AgentTeamsModule

Orchestrator-only-writes pilot, phase P4b. This module runs each command and gate that the out-of-session
runner starts inside an OS sandbox.

> Source: `agentteams/confinement.py` (integrity-pinned). Used by `proposals.run_request` and
> `proposals.apply_proposal` with `confine=True`, which the [`proposal_runner`](proposal-runner.md) always
> sets.

---

## Configuration (operator file or brief)

```json
"confined_programs": {"lean-prover": {"exec": ["~/.elan"], "write": [".lake"]}},
"allow_unconfined_runs": false
```

- **`exec`.** Absolute or `~/` paths. Programs or toolchain roots, matched as subpaths.
  - The command's own program must lie under one.
  - Give the toolchain root, not only its `bin`: Homebrew Python, for one, re-executes a binary inside its
    framework.
- **`write`.** Project-relative directories.
  - Never the project root, `.agentteams`, `.agentteams-queue`, `.git` or the control plane.
  - Never containing an `exec` path, or a command could build a binary there and then run it.
  - Changes inside a write root count as declared, so a build needn't list every output.
- **Gates.** A gate may set `exec`. The default is its own program plus a shebang interpreter. Gates write
  only their temp dir.
- **Where it lives (P5b).** Prefer the operator-owned file `~/.config/agentteams/confined/<project>-<hash>.json`
  (`agentteams --confined-path`) over the brief, so machine paths stay out of the committed brief.
  - Install it with `agentteams --install-confined FILE --description BRIEF`. That validates it against the
    brief's gates and prints the exact JSON it would install with its sha256. Nothing is written until you
    rerun it with `--confirm-review-sha256 <sha256>`. It then writes those bytes with mode 0600 in a 0700
    directory. A hash that doesn't match, because the file changed after review, is refused.
  - Write roots are literal directories. A `*`, `?` or `[` is refused, because the runner's containment checks
    treat write roots as paths.
  - The runner reads it at start and pins its hash. A change stops the runner, like a brief change does.
  - It refuses a file that is a symlink, not yours, group- or world-writable, inside the project (case-folded on
    macOS), over 64 KiB, or not a JSON object. It also refuses when the brief sets `confined_programs` too.
  - The file is opened with `O_NOFOLLOW | O_NONBLOCK` and re-checked with `fstat`. A file swapped in after the
    checks (same name, different inode) is refused, and a FIFO can't hang the runner.
  - Agents can't write it. Session sandboxes write-confine to the workspace, and the emitted
    `permissions.deny` carries `Edit(~/.config/agentteams/confined)` and `Edit(~/.config/agentteams/confined/**)`
    for the built-in Edit/Write tools. Under the switch the orchestrator writes only the candidate JSON and gives
    the user the one `--install-confined` command. It writes no script.
  - Residual: whether Claude Code applies those deny rules to case variants (`~/.Config/...` on APFS) or to a
    symlink alias made inside the project is not verified on the product. The custody checks can't tell an
    agent from the operator (same uid). The session sandbox and the deny rules are the boundary.
- **`allow_unconfined_runs`.** Decision A: off by default. With no usable sandbox the runner refuses;
  with this set, it runs unconfined and logs each run in the ledger as `"confined": "unconfined-opt-out"`.

## What the sandbox enforces

| | macOS (Seatbelt, `sandbox-exec -p`) | Linux (bwrap) |
|---|---|---|
| Writes | Only the write roots, a private `TMPDIR` and `/dev` nodes | The root is read-only; the write roots and `TMPDIR` are bind-mounted writable |
| Inside a write root | `.agentteams`, the queue, `.git` and the control plane are denied | Re-mounted read-only |
| Key directory `~/.config/agentteams/keys` | Read-denied | Masked with an empty tmpfs |
| Programs | Only under `exec` (`process-exec` deny-by-default) | Not restricted (decision C) |
| Leftover processes | `killpg` after exit; any survivor fails the run. A `setsid` escapee isn't caught | New PID namespace and `--die-with-parent`, so nothing survives |

`available()` checks for a sandbox by applying a trivial profile. Inside an already-sandboxed process
`sandbox-exec` can't nest, so it reports none and the runner refuses, unless opted out.

## Run-time checks (before anything runs)

- **Write roots.** Each must be an existing directory inside the project, with no symlink anywhere below the
  project root. A root swapped for a link to `~` is refused.
- **No overlap.** Exec paths and writable places (write roots and the run's `TMPDIR`) must not overlap in
  either direction. `/`, home and ancestors of the project are refused as exec paths.
- **Protected paths.** A write root may not cover a `protected_paths` entry or the brief, since changes
  inside roots count as declared.
- **The exemption only counts under a sandbox.** Changes inside roots count as declared only when a sandbox
  actually ran. Under the `allow_unconfined_runs` opt-out every change must be declared. Gates run under the
  opt-out are marked `"confined": "unconfined-opt-out"` in the ledger.
- **No environment key.** The runner refuses to start while a ledger-key environment variable is set in its
  own environment.

## Residual risk (named, accepted for the pilot)

- **Reads and network are open.** Only the key directory is read-denied, and Seatbelt keeps
  `(allow default)` for network and `mach-lookup`. Confined code can read the workspace and the home
  directory, and can reach the network.
- **Agent-supplied code runs inside the bounds.** `lake env lean --stdin` with agent-supplied input is
  arbitrary code: `#eval` can read any readable file, and its output returns to the agent as stdout. It
  needs no exec right. Lean FFI or `dlopen` of a library built in a write root also gets around the exec
  allowlist. The write and key limits still hold.
- **A Python toolchain in `exec`** lets any Python run. The argv allowlist still pins which script the
  orchestrator asks for.
- **macOS `setsid` escapee.** A grandchild that calls `setsid` and closes its pipes escapes `killpg`. It
  stays sandboxed, but can keep writing inside the write roots after the final snapshot. On Linux the PID
  namespace prevents this.
- **Linux gaps.** No program allowlist (decision C), no network isolation, and nested `.git` directories
  inside a write root aren't write-denied.
- **Gates with `#!/usr/bin/env` or `#!/bin/sh` shebangs** need an explicit `exec` that includes the real
  interpreter.

## Verified live (macOS, 2026-10-06)

- A declared program writes its root.
- Writes to the project, the ledger and outside the project are refused by the kernel.
- An undeclared program (`/bin/ls`) is refused.
- A detached survivor fails the run.
- A gate can't write the project.
- A nested `.git` inside a write root is write-denied.
- A symlinked write root, an over-broad exec path and a root covering a protected path are refused before
  anything runs.
- A real `lake build` succeeds with `exec: ["~/.elan"]` and `write: [".lake"]`, once `lake-manifest.json` is
  committed.

## Public surface

| Name | Purpose |
|---|---|
| `available() -> str \| None` | `"seatbelt"`, `"bwrap"` or `None`, found by actually applying a trivial profile. |
| `kind() -> str \| None` | The platform's sandbox, without probing. |
| `wrap(argv, *, sandbox, root, cwd, exec_paths, write_roots, tmp_dir) -> list[str]` | Wrap a command. |
| `seatbelt_profile(...)` / `bwrap_argv(...)` | The profile text or argv prefix. |
| `load_confined(raw, gates, control_plane_of)` | Validate `confined_programs` and gates' `exec`. |
| `confined_file_for(root) -> Path` | P5b: the operator-owned file's path for a project. |
| `read_confined_file(root) -> tuple[dict, str] \| None` | P5b: read it with custody checks, as `(confined_programs, sha256)`. |
| `confined_bytes(data) -> bytes` | P5b: the exact bytes installed (sorted, indented JSON): what the operator reviews and hashes. |
| `install_confined_file(root, data) -> Path` | P5b: write those bytes atomically, mode 0600. Callers validate and confirm the review hash first. |
| `exec_allows(program, exec_paths)` / `gate_exec_paths(gate, program)` | Exec allowlist helpers. |
| `ConfinementError` | An unsafe path, or a bad entry. |
