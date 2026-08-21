# Workspace Privilege Scoping

Opt-in **workspace write-confinement** for generated teams. It lets a project
declare that its agents may only write inside their own workspace, enforced by the
host's OS-level sandbox rather than by agents choosing to comply.

This covers the first two stages of the privilege model (see the investigation report
`references/plans/workspace-privilege-scoping.report.md`): **P1**, complete write
confinement on the Claude Code target (below), and **P2**, signed cross-workspace
capability grants (see "Cross-workspace grants" below). Sibling-team exclusion (**P3**)
is deferred.

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

## Cross-workspace grants (P2)

P1 confines a team to its own workspace. **P2** lets one team reach *into another's*
workspace — but only by an explicit, signed, scoped, expiring, use-counted grant the
other team issues. Absent a valid grant, there is no reach.

### The model

A grant authorizes team A (the holder) to write a specific path in team B's workspace.
It is an HMAC-signed row in the **holder's** `references/capability-grants.log.csv` — the
holder holds the grants issued to it (a bearer-capability model), so the holder's own
generation can read them. `issuer_team` records who authorized it. When team A is next
generated or updated with the sandbox on, the granted path (if it permits `write`) is
merged into A's sandbox `allowWrite` — so A's kernel-enforced boundary now includes
exactly that foreign path (verified against Seatbelt: a granted foreign dir is writable,
an ungranted one is still denied).

```bash
# Issue a grant into the HOLDER's workspace (--output points at the holder A);
# needs AGENTTEAMS_GRANT_SIGNING_KEY + an approver on A's roster:
agentteams --issue-grant grant-spec.json --output /path/to/A

# audit a workspace's grant ledger (read-only, never consumes):
agentteams --verify-grants --output /path/to/A
```

A grant spec is JSON: `issuer_team`, `holder_team`, `target_path`, `permitted_ops`,
`expires_at`, `max_uses`, `approver`, `ticket_id`, `reason_code`. Each team's identity
is its `team_id` (the `privilege_profile` sibling field; defaults to the slugified
project name — **keep it unique across your workspaces**).

### Enforcement is generation-time, by design

A freshly-issued grant is **inert until the holder re-runs an update**. Widening
happens when A is (re)generated — there is deliberately no runtime path by which a
running agent widens its own OS boundary. Issuing a grant is thus an explicit,
auditable step, not a silent runtime hole.

### Trust model (read this before relying on it)

Signing is **symmetric HMAC-SHA256** (one shared `AGENTTEAMS_GRANT_SIGNING_KEY`). It
defends against a **keyless** actor — a prompt-injected or buggy agent that cannot read
the key cannot fabricate a grant. It does **not** defend against an actor that holds the
key (an adversarial peer team). That is the same single-trusted-operator model the
security waivers use, and it is the honest ceiling of a stdlib-only implementation:
cross-team *unforgeability* (a holder who cannot forge the issuer's grants) needs
asymmetric signatures, which the Python standard library does not provide.
`agentteams.cli.signed_ledger` is the seam where an asymmetric backend would slot in.

**Preconditions and non-goals:**
- The signing key must be issued out-of-band and **never enter an agent session** (same
  as the waiver key).
- A grant **never overrides a `@security` HALT** — it widens a write boundary, it does
  not lift a stop (C-2 parity).
- Filesystem-local only; no cross-machine grant delivery.
- Holder identity is a name (`team_id`), safe only because grants are consumed at
  operator-run generation time, not at agent runtime.

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
- **Scope: P1 + P2, not P3.** P1 confines an agent to its own workspace; P2 (above)
  grants cross-workspace access by explicit signed request. What remains out of scope is
  **P3** — excluding *other* agent teams or hostile sibling processes from a tree — which
  is OS-account/container work outside agentteams' reach.
- **`allowWrite` mixes relative and absolute entries.** P1's default root is `["."]`
  (project-relative); a P2 grant adds an absolute foreign path. The emitted `allowWrite`
  therefore carries both forms — expected, and honored by the sandbox (verified against
  Seatbelt).

## See also

- [`host_features`](host-features.md) — the `claude:sandbox` token and the gating mechanism.
- [`hooks-emit`](hooks-emit.md) — the sibling constitutional-gate hook and the "ship an example, never clobber settings.json" convention this feature follows.
