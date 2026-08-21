# Workspace Privilege Scoping

Opt-in **workspace write-confinement** for generated teams. It lets a project
declare that its agents may only write inside their own workspace, enforced by the
host's OS-level sandbox rather than by agents choosing to comply.

This is Stage 1 of the privilege model (see the investigation report
`references/plans/workspace-privilege-scoping.report.md`): complete P1 write
confinement on the Claude Code target. Cross-workspace capability grants (P2) and
sibling-team exclusion (P3) are deferred.

## What it does

When enabled, agentteams injects a `sandbox` block into the Claude settings example
it already ships (`.claude/settings.hooks.example.json`):

```json
{
  "sandbox": {
    "enabled": true,
    "filesystem": { "allowWrite": ["."] },
    "allowUnsandboxedCommands": false
  }
}
```

Once the operator merges this into their `.claude/settings.json`, Claude Code's
native sandbox (macOS Seatbelt / Linux + WSL2 bubblewrap) confines **every Bash
command and child process** file write to the `allowWrite` roots. Writes outside are
denied by the kernel — not by an agent's judgement, and without agentteams having to
parse shell command lines. Verified empirically (2026-08-20): a bash redirect and a
Python child process both fail with "Operation not permitted" when writing outside
the root.

Two properties matter for the privilege model:

- **`.claude/` is auto-protected** even inside `allowWrite`, so a confined agent
  cannot edit its own boundary. A human editing outside an agent session is
  unaffected — the "preserve normal human access" requirement is met for free.
- **`allowUnsandboxedCommands: false`** closes the `dangerouslyDisableSandbox`
  escape hatch.

## How to enable it

### Via the `privilege_profile` field (recommended)

In the project description:

```json
{
  "project_goal": "…",
  "privilege_profile": "confined",
  "workspace_write_roots": ["."]
}
```

| Profile | Meaning |
|---|---|
| `cooperative` (default) | Today's behavior. Agents are trusted to respect the workspace; no OS boundary emitted. |
| `confined` | Expands to the `claude:sandbox` host feature — emits the sandbox block confining writes to `workspace_write_roots`. |
| `exclusive` | **Currently identical to `confined`.** Reserved for Stage 2 (excluding *other* agent teams / sibling processes from the workspace — an OS-account/container concern). That stronger semantics is **not wired yet**; no code today treats `exclusive` differently from `confined`. The value exists so a project can declare the intent now. |

> **Note on `exclusive`:** it is a forward-declaration, not a distinct behavior. The
> `.claude/` self-protection that both profiles rely on is Claude Code's own sandbox
> behavior (applies to `confined` too), not something `exclusive` adds.

`workspace_write_roots` (optional, default `["."]`) overrides the confined roots.
`.` means the whole generated project tree is the workspace.

### Via the host-feature token directly

```bash
agentteams generate brief.json --framework claude \
  --target-host-features claude:sandbox
```

The effective feature set is the **union** of `--target-host-features` and the
`privilege_profile` expansion, so the two are consistent when both are given.
`cooperative` never strips an explicitly-passed `claude:sandbox` token.

## Limits (be honest about these)

- **Inert until merged.** agentteams never writes `settings.json` directly
  (clobbering operator config is a worse failure than an unwired boundary). The
  emitted block is wire-ready but does nothing until the operator merges it. The
  emitted example's comment states this.
- **Claude Code only.** OS enforcement is available only on the `claude` framework.
  Selecting `confined`/`exclusive` for `goose`, `codex`, `copilot-*`, or on native
  Windows emits a visible advisory and records a
  `privilege-profile-unenforced-host` manifest advisory — the profile is advisory
  only there, never silently "on".
- **This is P1 only.** It confines an agent to *its own* workspace. It does not
  grant cross-workspace access by request (P2), nor exclude *other* agent teams or
  hostile sibling processes from a tree (P3) — that is OS-account/container work
  outside agentteams' reach.

## See also

- [`host_features`](host-features.md) — the `claude:sandbox` token and the gating mechanism.
- [`hooks-emit`](hooks-emit.md) — the sibling constitutional-gate hook and the "ship an example, never clobber settings.json" convention this feature follows.
