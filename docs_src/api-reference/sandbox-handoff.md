# `sandbox_handoff`

Writes and checks **operator handoff scripts**. When an agent sandbox refuses an operation the task needs (a
denied network host, `Operation not permitted` on a `.git` gitfile or outside the granted roots, an unreachable
credential socket), the agent writes a reviewable `.sh` script and gives the operator the complete command to
run it in a terminal outside the sandbox. It then reads the result back from the script's log. The protocol is
in the generated `references/sandbox-handoff.reference.md` and the orchestrator's **Sandbox Operator Handoff**
sub-protocol.

```
python -m agentteams.sandbox_handoff detect [--json]
python -m agentteams.sandbox_handoff write SPEC.json [--root DIR] [--week YYYY-Www] [--overwrite] [--json]
python -m agentteams.sandbox_handoff status SCRIPT_OR_LOG [--json]
```

`write` prints the script path, the log path, the script's SHA-256 and the complete command. `status` exits 0
only when the handoff reports `ok`.

## The spec

A JSON object with these keys. Any other key is refused, and so is a boolean field given as a string.

| Key | Meaning |
|---|---|
| `slug` | Lowercase-hyphenated name. The script is written to `tmp/by-week/YYYY-Www/<slug>.handoff.sh`. |
| `purpose`, `blocked_by` | Required, one line each: what the script accomplishes, and the operation the sandbox refused. |
| `steps`, `verify` | `[{title, run}]`. Each `run` is a command line written verbatim, and `verify` runs after `steps`. |
| `requires` | Executables checked on the operator's `PATH` before anything runs. |
| `touches` | Everything the script changes outside the sandbox's grant (shown in the header). |
| `destructive`, `clearance` | `destructive: true` requires a `clearance` citing the recorded authorization (C-5), and forces the confirmation prompt. |
| `confirm` | Ask `Proceed? [y/N]` before running. Defaults to `false`: running a non-destructive handoff is the operator's approval. Destructive scripts always ask once. `AGENTTEAMS_HANDOFF_YES=1` answers it. |
| `cwd`, `plan` | Optional: a directory inside the root to run in, and the plan slug for the header. |

**Refused:**

- a spec whose commands or `touches` name a trust root or capability grant (`cli/governance_targets`
  stems plus settings/hook files, `management-authority.json`, `AGENTS.md` and signing-key names);
- credential-shaped text (`scan`'s credential patterns plus literal `TOKEN=`/`--token` values; `$VAR`
  references are fine);
- a script that fails `bash -n`.

## The script

The script:

- starts with a header the operator reads before anything else;
- sets `set -euo pipefail`, uses an absolute `cd`, and checks that each required tool is present;
- echoes every command before running it, and runs the `verify` commands after the steps;
- exits 64 when a sandbox marker (`SANDBOX_RUNTIME`, `CODEX_SANDBOX`) is set, unless
  `AGENTTEAMS_HANDOFF_ALLOW_SANDBOX=1`.

Output is appended to `<slug>.handoff.log`, together with the script's own SHA-256 and a final
`HANDOFF-RESULT: ok` or `HANDOFF-RESULT: failed rc=<n> step=<k>` line.

The complete command checks the script's SHA-256 before running it, so an edited script refuses to start.

## Public surface

| Name | Purpose |
|---|---|
| `detect_sandbox(env=None) -> SandboxInfo` | Sandbox markers present in the environment. This is advisory: a refused operation is the authoritative signal. |
| `spec_from_dict(data) -> HandoffSpec` / `validate_spec(spec)` | Parse and validate a spec. Raises `HandoffError`. |
| `render_script(spec, *, root, log, script) -> str` | The script text. |
| `write_handoff(spec, root, *, week=None, overwrite=False) -> HandoffResult` | Write the script atomically (mode 0755), run `bash -n`, and return `script`, `log`, `sha256` and `command`. |
| `run_command(script, sha256) -> str` | The hash-checked command line. |
| `handoff_status(path) -> HandoffStatus` | `not-run`, `incomplete`, `ok`, `failed` (with `rc` and `step`) or `stale`. |
| `find_secrets(text)`, `trust_roots_named(spec)` | The two refusal checks, exposed for callers that pre-screen. |

A plan step that runs a handoff has `agent` = `operator`. [`parallel_plan`](parallel-plan.md) always schedules
such a step as a singleton.
