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

`exclusive` is a superset of `confined`. P2 is orthogonal (issue grants or don't).
Enforcement runs on Claude Code's two documented sandbox backends — **macOS (Seatbelt)**
and **Linux / WSL2 (bubblewrap)** — from the same emitted config (no per-OS agentteams
logic). The macOS mechanism was additionally verified by a direct Seatbelt spike. On
Linux, a direct `bwrap` kernel-deny was **observed** working once (bwrap 0.11.1,
Ubuntu 26.04 aarch64, one protected path, a hand-built invocation): `python3` ran inside
the sandbox and the protected read returned denied. That confirms only the **kernel
mechanism** — the still-untested link is **argument construction**: whether Claude Code
correctly derives the `bwrap` arguments from agentteams' `denyRead` JSON (a kernel that
denies when handed correct hand-built args says nothing about whether Claude Code's
*derived* args are correct). So Linux is **not** "verified" end-to-end; treat it as
mechanism-observed, translation-unverified (tracked in the remediation log). Native
Windows has no OS sandbox agentteams can configure and stays advisory-only.

## What it does

When enabled, agentteams injects a `sandbox` block into the Claude settings example
it already ships (`.claude/settings.hooks.example.json`):

```json
{
  "sandbox": {
    "enabled": true,
    "filesystem": {
      "allowWrite": ["."],
      "denyWrite": [
        "references/agent-privilege.json",
        ".claude/hooks/constitutional-gate.py"
      ]
    },
    "allowUnsandboxedCommands": false
  }
}
```

Once the operator merges this into their `.claude/settings.json`, Claude Code's
native sandbox (macOS Seatbelt / Linux + WSL2 bubblewrap) confines **every Bash
command and child process** file write to the `allowWrite` roots. Writes outside are
denied by the kernel — not by an agent's judgement, and without agentteams having to
parse shell command lines.

Verified at two levels:

- **Mechanism (2026-08-20):** a bash redirect and a Python child process both fail with
  "Operation not permitted" when writing outside the root (raw Seatbelt).
- **Product, end-to-end (2026-08-26, macOS):** real Claude Code (`claude -p`) launched
  against the *emitted* `sandbox` block enforces it — a write to `$HOME` (outside
  `allowWrite` and outside the session temp dir) is DENIED, a `denyWrite` of
  `references/agent-privilege.json` blocks an agent from flipping the
  `enforce_decision_signing` switch, and a `denyRead` secret cannot be read, while
  legitimate in-root writes/reads still succeed. Encoded as the opt-in regression guard
  `tests/test_os_sandbox_product_enforcement.py` (run with `RUN_CLAUDE_SANDBOX_ITEST=1`).
  *One caveat learned here:* the sandbox allows writes to the working dir **and the
  session temp dir**, so an escape test must target a non-temp path (e.g. `$HOME`) — a
  `/tmp` target is allowed and proves nothing.

**P3-3 resolved (2026-08-26):** the `~`-relative `denyRead` form — the *default* agentteams
emits — was empirically verified to enforce end-to-end. A `denyRead: ["~/.secret"]` blocks a
read of that path while a *different* HOME file (not denied) stays readable, so **Claude Code
expands `~` before the Seatbelt deny** — the tilde form is not a silent no-op. (At the *raw*
Seatbelt level a literal unresolved path still no-ops, which is why the `expanduser` opt-in
remains available as defense-in-depth, but it is not required for correctness in the product.)
Covered by `tests/test_os_sandbox_product_enforcement.py::test_p3_3_tilde_denyread_expands_and_enforces`.

**Linux status (verified 2026-08-26 on a real VM — Ubuntu 26.04 aarch64, bwrap 0.11.1).**
- **Mechanism: verified.** A direct bwrap test denies a write outside the read-write bind, allows
  the in-bind write, and hides an unbound read — the kernel enforces `denyRead`/`allowWrite`-equivalent
  confinement.
- **Product arm (Claude Code deriving+enforcing the emitted config): blocked on a stock VM.** Claude
  Code's Linux sandbox needs **nested unprivileged user namespaces**, which stock Ubuntu 26.04 restricts
  (`apply-seccomp: setgroups nested userns`), so the sandbox cannot initialize there. This is a Claude
  Code + host-kernel matter, not an agentteams config defect.
- **Fail-closed confirmed.** When the sandbox cannot initialize, Claude Code **refuses to run the Bash
  command regardless of `allowUnsandboxedCommands`** — a confined/exclusive team fails closed on Linux
  (no unconfined execution), consistent with the fail-closed default. A positive Linux *product* run
  requires a host that permits Claude Code's nested-userns sandbox.

Do not read the above as "Linux product arm verified" — it is not; the mechanism is verified and the
failure mode is fail-closed. Full evidence:
`tmp/by-week/2026-W35/security-followups/linux-verification-verdict-2026-08-26.md`.

Two properties matter for the privilege model:

- **`.claude/` is auto-protected** even inside `allowWrite`, so a confined agent
  cannot edit its own boundary. A human editing outside an agent session is
  unaffected — the "preserve normal human access" requirement is met for free.
- **The control plane is `denyWrite`-protected (D-3).** The `enforce_decision_signing`
  switch (`references/agent-privilege.json`) lives inside the write root and is *not*
  under `.claude/`, so the auto-protection above does not cover it — a `denyWrite`
  entry does. `denyWrite` takes precedence over `allowWrite` (deny-over-allow; the
  Seatbelt write-deny mechanism is verified in `tests/test_os_sandbox_enforcement.py`).
  The gate hook is included belt-and-suspenders — the `.claude/` auto-protection above
  is itself not yet empirically verified. Globs are unsupported, so these are exact
  paths; the switch and hook are written by the *operator* at generate/update time
  (outside any sandbox), never by the in-sandbox agent, so denying them costs no
  legitimate workflow.
  > **Linux runtime note (D-3).** The `denyWrite` binding is conditional on two things at
  > *runtime*: (a) the operator has merged this example into the live `.claude/settings.json`
  > (the example is inert on its own), and (b) each `denyWrite` path resolves to a file that
  > **exists** — on Linux, Claude Code's bwrap backend *binds* each deny path and a missing
  > one makes the whole sandbox fail to initialize (macOS Seatbelt tolerates a missing deny
  > path). A normally-generated `confined`/`exclusive` team emits both control files, so both
  > exist; a hand-assembled or partial team must ensure they are present.
  > `tests/test_workspace_privilege_scoping.py::test_every_denywrite_control_file_is_emitted`
  > guards that generation keeps emitting every path the `denyWrite` list names.
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

> **Typos fail closed.** Only `cooperative`, `confined`, and `exclusive` are accepted.
> A misspelled profile (e.g. `"exclusve"`) is **rejected at build with a non-zero exit** —
> it is never silently downgraded to unconfined, because a value that *looks* like a
> confinement request while granting none is the worst outcome. A missing profile is not
> a typo: it defaults to `cooperative`.

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
generation can read them. Rows are **hash-chained**: each carries a signed `prev_digest`
linking it to its predecessor, so deleting or reordering a signed row is detected at every
read (per-row signatures stop forgery; the chain stops silent deletion). A broken chain
fails closed — `--verify-grants` reports it, and generation-time widening applies no grants. `issuer_team` records who authorized it. When team A is next
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
`expires_at`, `max_uses`, `approver`, `ticket_id`, `reason_code`, and the optional
`issuer_root`. Each team's identity is its `team_id` (the `privilege_profile` sibling
field; defaults to the slugified project name — **keep it unique across your workspaces**).

> **Anchor an absolute grant to the issuer's tree (`issuer_root`, optional but recommended).**
> When the spec declares `issuer_root` (an absolute path), it is **signed into the grant** and
> an absolute `target_path` that resolves outside it is refused — at issue *and* on the
> generation-time widening path (P2-4 containment). Because the anchor is signed, a tampered
> or widened `issuer_root` invalidates the grant. Omit it and the grant still works, bounded by
> the `target_path` shape checks below (backward compatible).

> **An approver roster is mandatory.** A grant is only honoured when its `approver` is on
> the holder workspace's `references/security-approvers.txt`. If that roster is **absent
> or empty**, issuing, verifying, and generation-time widening all **fail closed** — the
> grant is refused rather than cleared by a built-in `security`/`@security` default.
> (Without this, a grant naming `@security` as its own approver could self-clear.) Create
> the roster with at least one named approver before issuing or honouring any grant.
>
> `target_path` is also validated at issue: an empty path, `/`, a home-rooted `~/…` path,
> or a `..`-escaping relative path is refused before it can enter the ledger and widen a
> boundary. A malformed `expires_at` is likewise rejected at issue, not left to fail later.
>
> **Honest ceiling — grant signing is symmetric (HMAC-SHA256).** The signature defends against a
> **keyless, injected, or buggy** agent (it cannot mint a valid grant without the shared
> `AGENTTEAMS_GRANT_SIGNING_KEY`), but it does **not** defend against an adversarial **peer team
> that holds the same key** — with a symmetric key, whoever can *verify* a grant can also *forge*
> one. This is a deliberate ceiling for the single-operator / both-repos model these grants are
> built for (there is no second trust principal). If you ever exchange grants **across
> organizations or mutually-distrusting operators**, this model is insufficient — an asymmetric
> (public-key) backend behind the `signed_ledger` seam would be required (a gated future option;
> decision `references/plans/decision-c1-grant-signing.report.md`).

### Enforcement is generation-time, by design

A freshly-issued grant is **inert until the holder re-runs an update**. Widening
happens when A is (re)generated — there is deliberately no runtime path by which a
running agent widens its own OS boundary. Issuing a grant is thus an explicit,
auditable step, not a silent runtime hole.

Two consequences of the generation-time model to plan around:

- **Revocation has latency.** There is no revocation list and no runtime revocation
  between generations. Removing a grant row (or dropping its approver from the roster)
  stops it applying only at the holder's **next** generation; until then an
  already-widened `allowWrite` persists. Prefer a **short `expires_at`** to bound the
  window rather than relying on after-the-fact removal.
- **`max_uses` is validated, not consumed.** It is checked at each generation (an
  exhausted grant is rejected), but generation-time widening does **not** decrement the
  counter — expiry is the only active bound. An operator who sets `max_uses=1` expecting
  a single use gets a grant that behaves as reusable-until-expiry; the CLI prints a NOTE
  to that effect at issue. Again: use `expires_at` for a tight window.

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
reading** a curated default deny set plus any `protected_read_paths` you list. The
default set is the credential stores an agent rarely needs to act *as* during its build
work — `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.kube`, `~/.config/gcloud`, and `~/.azure`
(cloud-provider creds).

It **deliberately excludes** files that routine agent tooling authenticates with, because
denying them breaks the toolchain: registry-auth files (`~/.npmrc`, `~/.pypirc`,
`~/.netrc`, `~/.docker/config.json` — authenticated `npm`/`pip`/`git`/`docker`) and
`~/.config/gh` (the GitHub CLI token, read by `gh` on every call — and the generated PR
agents shell out to `gh`). Add any of these via `protected_read_paths` if your team does
not use that toolchain.

> **Files, not environment variables.** `denyRead` denies *filesystem reads*. A secret
> already exported into the agent's environment (e.g. a signing key placed in an env var)
> is **not** covered — this control keeps a credential *file* off-limits, it does not
> scrub the process environment.

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

### Verifying enforcement on your machine

Because the emitted `denyRead` uses home-relative (`~/`) entries and agentteams does not
run Claude Code, it cannot itself prove the paths resolve and deny on your host. Confirm
it yourself after merging the block and enabling the sandbox: create a throwaway marker
file under one of the denied roots, then attempt to read it from inside a sandboxed Bash
command. A denial (operation not permitted) confirms the boundary is live on your machine;
a successful read means the entry did not resolve — switch that entry to an absolute path.
Do this once per host/OS, since the home path resolves per user at sandbox-apply time.
(The `~/` form is Claude Code's documented sandbox path syntax; the underlying kernel deny
mechanism was verified directly — macOS Seatbelt with absolute paths — but the config-layer
`~` resolution is Claude Code's, so a one-time self-check is the honest confirmation.)

#### Opt-in: emit absolute paths instead (`resolve_deny_read_abspath`)

If you cannot run — or do not want to rely on — that per-host `~`-expansion self-check, set
`resolve_deny_read_abspath: true` in the project description. Every `~/` `denyRead` entry is
then resolved to an `expanduser`'d **absolute** path at generation time, so enforcement no
longer depends on Claude Code expanding `~` before the OS deny. This closes the silent-no-op
risk (P3-3) directly: an unresolved entry can no longer look protective while denying nothing.

The trade-off is portability. An absolute path names *the generating machine's home*, so a
team generated on one host will not carry its deny paths to another — **regenerate on the
host that runs the team**. This fits the same single-operator/both-repos trust model P2
already assumes. The default (`false`) keeps the portable `~/` form and a byte-identical
emitted block, so nothing changes for existing teams. Note this removes the `~` dependency;
it does **not** by itself prove the OS denies the path — that still owes the in-sandbox
escape test above (or the RH-4 harness).

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

## Strict decision-signing enforcement (`enforce_decision_signing`)

An **agent-position** control, distinct from the workspace axes above. The security gate
clears a destructive/privileged action only against an **authorizing** decision row (a
`PASS`/`CONDITIONAL PASS`, or a `HALT-RETRACTED`). When decision signing is *inactive*, that
row is trusted on its **author name** alone — and the author is gated by the
`security-approvers.txt` roster, an operator-authored file that lives inside the workspace.
So in the signing-inactive configuration, an agent that can edit the roster could add itself
and author its own clearance.

`enforce_decision_signing` (project-description field, **default `true`**) closes that path:
when on, an **unsigned authorizing row is refused (fail-closed)** rather than honoured on its
author name. It applies to both authorization paths (PASS and HALT-RETRACTED).

- **Default-on at update, notify-after, opt-out.** The switch is emitted into the team as
  `references/agent-privilege.json` and defaults on whenever the team is generated or
  updated; a notice prints at generation naming the opt-out. Turn it off with
  `"enforce_decision_signing": false` in the brief, then re-run `--update`.
- **Absent means off.** A workspace that has never been updated to this feature (no
  `agent-privilege.json`) keeps the legacy behavior, so updating is what turns the switch on
  — no team breaks by surprise. A present-but-unreadable switch fails **closed** (it will
  not silently disable itself).
- **Migration edge:** turning it on also stops any **existing** unsigned authorizations in
  that workspace's decisions log from clearing until signing is activated (add a `signature`
  column, or set `AGENTTEAMS_DECISION_SIGNING_KEY`).

> **Known residual (honest ceiling).** `references/agent-privilege.json` sits inside the
> agent write root, so an agent with Write could disable the control by setting it `false`
> or deleting the file (absent → off). This is the same "the referee plays in the same
> sandbox as the players" limitation documented for `integrity.py` and accepted for the
> capability-grant roster. The durable fix is harness-level enforcement plus integrity
> coverage of the switch value; it is tracked in the remediation log. Today the switch
> raises the bar (author-name-only → signature-required) and makes tampering a recorded,
> git-visible act, but it is not a boundary an in-sandbox agent cannot cross.

## Limits (be honest about these)

- **Inert until merged.** agentteams never writes `settings.json` directly
  (clobbering operator config is a worse failure than an unwired boundary). The
  emitted block is wire-ready but does nothing until the operator merges it. The
  emitted example's comment states this.
- **`claude` everywhere; `goose` on macOS only; fails closed elsewhere.** OS enforcement
  is available on the `claude` framework (Claude Code's Seatbelt/bubblewrap sandbox), and —
  as of P1-1 — on the `goose` framework **on macOS only** (Apple Seatbelt via
  `GOOSE_SANDBOX`/`sandbox-exec`; see the goose-privileges reference). The choice is
  **platform-aware** (`host_features.is_sandbox_capable`): a confined/exclusive **goose**
  team on macOS emits a `sandbox.sb` profile + an inert `config.yaml.agentteams.example`,
  while the **same selection on Linux/Windows** has no boundary to emit. Selecting
  `confined`/`exclusive` for `codex`, `copilot-*`, on **native Windows**, or for **goose on
  Linux/Windows** therefore **exits non-zero by default** rather than ship a config that
  silently does nothing. Pass `--allow-unenforced-confinement` to proceed anyway; the
  request then degrades to a visible advisory and a `privilege-profile-unenforced-host`
  manifest advisory — advisory only there, and never silently "on". (Whether Claude Code
  enforces on native Windows is itself unverified; treat Windows as advisory.) For targets
  with no emittable boundary (goose on Linux/Windows, codex, copilot, native Windows),
  confine from **outside** the process: a container plus **seccomp-bpf + Landlock** (Linux)
  and **egress filtering**. This fail-closed default is on the **`generate`** path; the
  `--convert-from`/`--fleet`/render paths still emit the sandbox block (via
  `_sandbox_feature_enabled`/`_goose_sandbox_feature_enabled` reading `privilege_profile`
  directly) but keep the advisory-not-raise default, so an unenforceable target there
  degrades to a warning rather than a non-zero exit.
- **P3 is read-exclusion (outbound), not enforced inbound exclusion.** The emitted
  `exclusive` config seals *your* team's reads; the "only my team touches my tree"
  inbound property is operator filesystem hardening (P3b advisory), which agentteams
  documents but cannot enforce. See the P3 section below.
- **`allowWrite` mixes relative and absolute entries.** P1's default root is `["."]`
  (project-relative); a P2 grant adds an absolute foreign path. The emitted `allowWrite`
  therefore carries both forms — expected, and honored by the sandbox (verified against
  Seatbelt).

## Design foundations

The mechanisms here rest on a few established security principles — cited as *design
rationale*, not as a claim that this implementation formally realizes any of them:

- **Least privilege** — a team writes only where it must (`workspace_write_roots`, default
  `["."]`); `exclusive` additionally denies reading secrets it has no business touching.
- **Fail-safe defaults** — `cooperative` is the default and emits *no* boundary: the
  system does not silently claim protection it is not enforcing. Where a boundary *is*
  requested but cannot be enforced (non-sandbox host, unknown profile), generation **fails
  closed** rather than ship an inert config that looks protective.
- **Complete mediation** — the P2-4 target-path guard runs on the real generation-time
  widening path, not only at issue, so an unsafe grant cannot reach `allowWrite` by a path
  that skipped the check.
- **Confused-deputy avoidance** — a grant widens a boundary at operator-run generation
  time, never at agent runtime, so a running agent cannot be tricked into widening its own
  reach; and it never overrides a `@security` HALT (C-2 parity).
- **Keyed integrity (HMAC)** — grants are signed so a keyless (prompt-injected) agent
  cannot fabricate one. The honest ceiling of a stdlib-only HMAC scheme — no defense
  against a *key-holding* peer team — is stated under "Trust model" above; this is not a
  claim of cross-team unforgeability.

## See also

- [`host_features`](host-features.md) — the `claude:sandbox` token and the gating mechanism.
- [`hooks-emit`](hooks-emit.md) — the sibling constitutional-gate hook and the "ship an example, never clobber settings.json" convention this feature follows.
