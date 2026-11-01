# Part II — The request  (SB4–SB6)

<!-- skeleton:SB4 SB5 SB6 -->

**The knobs, in your brief** (`agentteams/schemas/project-description.schema.json`):

```json
{ "privilege_profile": "confined",
  "workspace_write_roots": ["."],
  "protected_read_paths": ["/scratch/agent-b"] }
```

- **`privilege_profile`** — `cooperative` (no boundary; `os_sandbox: "off"` is the dedicated switch) · `confined` (write-confinement,
  default as of 2026-W39) · `exclusive` (adds read-exclusion of credentials + `protected_read_paths`).
  A **typo fails closed** at parse — it never silently downgrades to `cooperative`.
- **`workspace_write_roots`** — where the agent may write (default `["."]`, the whole generated tree).
- **`protected_read_paths`** — extra paths to deny reading under `exclusive` (sibling workspaces).

- **`os_sandbox`** — `"on"` / `"off"` (or `--os-sandbox on|off`, which wins): switches the OS boundary
  independently of the profile. Absent, it follows the profile. `off` emits no boundary (sandbox block,
  Seatbelt profile, runners, launcher) and, in a governed workspace (strict signing, on by default),
  needs an operator Ed25519-signed decision `sandbox-os-off:<framework>`; otherwise it prints
  `SANDBOX RELAXATION`. Refused with `write_policy: orchestrator-only`. `on` emits a boundary under
  `cooperative` and flips the gate hook fail-closed.

You may instead pass a token directly (`--target-host-features claude:sandbox`); a confined profile
expands to the framework's token automatically (goose→`goose:sandbox`, else `claude:sandbox`). Either
source counts as "confinement requested," including on `--convert-from`/`--fleet`.

> **Cost:** none to *request*; the cost is at **wiring** (Part V) — a confined team you never wire is
> just a cooperative team with extra files.

*Full detail:* [Reference Part II](../reference/part-ii-the-request.md).
