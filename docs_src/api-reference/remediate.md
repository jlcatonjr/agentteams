# `remediate` — AgentTeamsModule

Auto-correction support via standalone Copilot CLI.

Provides an optional remediation pass that runs after [post-audit](audit.md) finds issues in generated agent team files. Uses the standalone [Copilot CLI](../interoperability.md) in non-interactive mode, scopes file access to the generated team directory, then returns control to the main pipeline for a verification rerun.

> *Source: `agentteams/remediate.py`*

---

## Classes

### `RemediationResult`

> *Source: `agentteams/remediate.py`*

Outcome of a Copilot CLI remediation attempt.

**Attributes:**

- `attempted` (`bool`) — `True` if the Copilot CLI was invoked.
- `succeeded` (`bool`) — `True` if the CLI exited with code 0.
- `message` (`str`) — Human-readable status message.
- `command` (`list[str]`) — The exact command that was run.
- `stdout` (`str`) — Standard output captured from the CLI.
- `stderr` (`str`) — Standard error captured from the CLI.

---

## Functions

### `run_copilot_autocorrect(*, output_dir, manifest, audit_result)`

> *Source: `agentteams/remediate.py`*

Invoke the standalone Copilot CLI to repair generated team files.

**Args:**

- `output_dir` (`Path`, keyword-only) — Root directory containing generated team files.
- `manifest` (`dict[str, Any]`, keyword-only) — Team manifest from [`analyze.build_manifest()`](analyze.md).
- `audit_result` (keyword-only) — `AuditResult`-like object containing findings.

**Returns:** `RemediationResult` — Three possible outcomes: if `audit_result` carries no findings at all, returns immediately with `attempted=False, succeeded=True` — without even checking whether the Copilot CLI is on `PATH`, since there is nothing to remediate; if the Copilot CLI is absent, returns `attempted=False, succeeded=False`; if the CLI runs but exits non-zero (or times out), returns `attempted=True, succeeded=False`. No exception is raised for any of these cases.

**Raises:** `OSError` — Only for unexpected OS-level subprocess failures (e.g. permission errors on the executable path). A missing Copilot CLI returns `succeeded=False`, not `OSError`.

---

### `print_remediation_summary(result)`

> *Source: `agentteams/remediate.py`*

Print a human-readable remediation summary to stdout.

**Args:**

- `result` (`RemediationResult`) — Result from `run_copilot_autocorrect()`.
