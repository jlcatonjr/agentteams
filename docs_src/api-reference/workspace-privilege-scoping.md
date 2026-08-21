# Workspace Privilege Scoping

Opt-in **workspace write-confinement** for generated teams. It lets a project
declare that its agents may only write inside their own workspace, enforced by the
host's OS-level sandbox rather than by agents choosing to comply.

This covers the privilege model (see the investigation report
`references/plans/workspace-privilege-scoping.report.md`): **P1**, complete write
confinement on the Claude Code target (below); **P2**, signed cross-workspace capability
grants (see "Cross-workspace grants"); and **P3**, read-exclusion hardening + inbound
hardening guidance (see "Read-exclusion & cross-team exclusion (P3)"). All three are
**independently optional** — see the selection matrix directly below.

## Selecting P1 / P2 / P3 (all optional)

| You want | Select | What you get |
|---|---|---|
| nothing (default) | `privilege_profile: cooperative` | no OS boundary — agents trusted to respect the workspace |
| **P1** — confine my team's writes | `privilege_profile: confined` | OS write-confinement to the workspace (Bash + subprocesses) |
| **P2** — cross-workspace reach by grant | issue grants (any sandbox profile) | holder's `allowWrite` widens to signed-granted paths |
| **P3** — read-exclusion + inbound hardening | `privilege_profile: exclusive` | P1 **plus** OS read-exclusion of protected paths (P3a) **plus** an operator inbound-hardening advisory (P3b) |

`exclusive` is a superset of `confined`. P2 is orthogonal (issue grants or don't). macOS
(Seatbelt) is the enforced target today; Linux (bubblewrap) is a follow-out.

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
| `exclusive` | `confined` **plus P3**: OS read-exclusion (`denyRead`) of a default deny set + your `protected_read_paths`, and a P3b inbound-hardening advisory. See "Read-exclusion & cross-team exclusion (P3)" below. |

> **Note on `exclusive`:** its emitted enforcement is OUTBOUND — it seals *your* team's
> reads. It does not by itself stop *other* teams from reading your workspace; that
> inbound property is operator filesystem hardening (P3b).

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

## Read-exclusion & cross-team exclusion (P3)

The user goal behind "P3" is an **inbound** property: *only my team accesses my
workspace.* Be clear about what agentteams can and cannot do here, because the mechanism
it emits is the **outbound dual**.

### P3a — read-exclusion (emitted, OS-enforced)

Selecting `privilege_profile: exclusive` adds `sandbox.filesystem.denyRead` to the
emitted sandbox block: the team (and its Bash/child processes) is **OS-denied from
reading** a curated default deny set (SSH keys + cloud-provider creds) plus any
`protected_read_paths` you list. The default set is deliberately conservative — it
excludes registry-auth files (`~/.npmrc`, `~/.pypirc`, `~/.netrc`, `~/.docker/config.json`)
because denying those **breaks authenticated `npm`/`pip`/`git`/`docker`** against private
registries; add them yourself if your team does not use authenticated registries.

`allowRead` re-opens the write roots (which include any P2-granted paths) so a granted
write target stays readable, and — since "more specific wins" — the workspace stays
readable even if a `protected_read_paths` entry denies one of its ancestors.

The denylist shape is what makes this practical: a read-*allowlist* starves the toolchain
(a direct Seatbelt spike had python abort under one), whereas a read-denylist keeps the
toolchain and denies the listed paths — that Seatbelt deny mechanism was verified directly
(with absolute paths). The `~/`-relative deny entries rely on Claude Code's documented
sandbox path syntax (`~/` = home); operators who prefer no ambiguity may list absolute
paths instead.

This seals **your** team's reads (it cannot snoop on or exfiltrate the listed paths /
sibling workspaces). When *every* team runs `exclusive` and lists the others in
`protected_read_paths`, teams cannot read each other → mutual isolation.

### P3b — inbound exclusion (operator-run, advisory only)

What P3a does **not** do: stop *another* team from reading *your* workspace. Nothing
agentteams emits into your config can constrain a different process. Selecting
`exclusive` therefore also prints and records a
`privilege-profile-exclusive-inbound-hardening` advisory pointing you at the OS steps
that *do* deliver inbound exclusion, which you run yourself:

- **Restrict the workspace to your user:** `chmod 700 <workspace>` + `chown` it to you,
  so other-uid processes are kernel-denied.
- **(Stronger) dedicated macOS user:** run the owning team's harness under a separate
  user account (`dscl`/System Settings), and own the workspace as that user — the kernel
  then excludes every other user, while your admin/human account retains access.

agentteams **cannot verify** you ran these, so P3b is emitted as an honest advisory, not
a claimed guarantee — the same discipline as the unenforced-host warning.

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
- **P3 is read-exclusion (outbound), not enforced inbound exclusion.** The emitted
  `exclusive` config seals *your* team's reads; the "only my team touches my tree"
  inbound property is operator filesystem hardening (P3b advisory), which agentteams
  documents but cannot enforce. See the P3 section below.
- **`allowWrite` mixes relative and absolute entries.** P1's default root is `["."]`
  (project-relative); a P2 grant adds an absolute foreign path. The emitted `allowWrite`
  therefore carries both forms — expected, and honored by the sandbox (verified against
  Seatbelt).

## See also

- [`host_features`](host-features.md) — the `claude:sandbox` token and the gating mechanism.
- [`hooks-emit`](hooks-emit.md) — the sibling constitutional-gate hook and the "ship an example, never clobber settings.json" convention this feature follows.
