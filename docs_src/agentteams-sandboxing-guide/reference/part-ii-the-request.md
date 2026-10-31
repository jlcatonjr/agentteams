# Part II — The request  (SB4–SB6)

<!-- skeleton:SB4 SB5 SB6 -->

## SB4 — Three privilege profiles  ✅

`privilege_profile` has three values, each a superset of the last:

- **`cooperative`** (the opt-out) — no boundary emitted; the agent is trusted, hook fail-open (SB3).
- **`confined`** (default as of 2026-W39) — workspace **write-confinement**: the agent writes only inside
  `workspace_write_roots` (default `["."]`, the generated tree). *Network* deny-by-default is a property
  of the mechanisms that emit an egress directive — goose Seatbelt (`deny network*`) and the Linux
  launcher (`--unshare-net`); the **claude** mechanism emits **no** egress directive, so claude network
  confinement is Claude Code's own product default (see SB10, same unverified caveat).
- **`exclusive`** — `confined` **plus read-exclusion** (P3a) for the **claude** (`denyRead`) and
  **goose** (`deny file-read*`) mechanisms: OS-deny reads of a curated credential set + operator-supplied
  `protected_read_paths` (sibling workspaces), plus an operator inbound-hardening advisory (P3b).
  **Nuance for the Linux launcher (SB12):** it masks the default *credential* set (`~/.ssh …`) on
  **every** wrap regardless of profile; only the extra sibling-workspace `--exclude` reads are
  `exclusive`-specific there.

An **unknown** profile value **fails closed** — a hard error at parse (`build_manifest` calls
`validate_privilege_profile`), never a silent downgrade to `cooperative`. A typo cannot ship an
unconfined team that looks confined.

*Source:* `agentteams/host_features.py` `validate_privilege_profile`;
`agentteams/frameworks/_sandbox_emit.py` `_exclusive_read_deny_paths`, `_DEFAULT_PROTECTED_READ_PATHS`;
`agentteams/schemas/project-description.schema.json`.

**The dedicated OS switch (`os_sandbox`, P00).** `os_sandbox: "on" | "off"` in the brief (or
`--os-sandbox on|off`, which overrides it) switches the OS boundary independently of the profile.
Absent, the boundary follows the profile exactly as above. `off` suppresses every boundary agentteams
emits for the framework (settings `sandbox` block, Seatbelt profile, runners, Grok profile, neutral
launcher) while the rest of the profile (read-exclusion knobs, write roots) is kept for when it is turned
back on; the control-plane stubs a sibling team's launcher needs are still emitted. `on` emits the
boundary under `cooperative` and counts as the explicit opt-in that flips the gate hook fail-closed.
Precedence: `--os-sandbox` > an explicit `--target-host-features <fw>:sandbox` > the brief's
`os_sandbox` > the profile > any `*:sandbox` token; `--os-sandbox off` with an explicit token is refused.
Turning the boundary off is constraint-relaxing (Rule 15): in a governed workspace (strict decision
signing, on by default) it needs an operator Ed25519-signed decision `sandbox-os-off:<framework>`;
elsewhere it prints `SANDBOX RELAXATION` and is recorded in the build-log `sandbox_state`. It is refused
with `write_policy: orchestrator-only`. A typo fails closed, like the profile.

## SB5 — Host-feature tokens and profile expansion  ✅

The request is recorded — and read — two ways: the `privilege_profile` field **and** a `*:sandbox`
host-feature token (`claude:sandbox`, `goose:sandbox`). A confined/exclusive profile **expands** to the
framework-appropriate token via `expand_privilege_profile`: goose → `goose:sandbox`; every other
framework → `claude:sandbox`. The two enablement checks (`_sandbox_feature_enabled` and the
framework-neutral `_sandbox_confinement_requested`) treat **either** source as "confinement requested,"
so a confined manifest emits on the `convert`/`render` paths too, not only interactive `generate`.

A `:sandbox` token is **rejected** for a namespace that has no emitter (e.g. bridge namespaces): a
*validating* token that confined nothing would be a silent false-confidence signal, and rejecting it is
how that is prevented.

*Source:* `agentteams/host_features.py` `expand_privilege_profile`, `merge_profile_features`, `validate`;
`agentteams/frameworks/_linux_sandbox_emit.py` `_sandbox_confinement_requested`.

## SB6 — The manifest is the request record  ✅

`build_manifest` carries `privilege_profile`, `workspace_write_roots`, `protected_read_paths`, and the
resolved `host_features` into the render/emit pipeline. The manifest is the single object the decision
(Part III) and every emitter (Part IV) read; there is no out-of-band sandbox state.

*Source:* `agentteams/analyze.py` `build_manifest`; `agentteams/schemas/team-manifest.schema.json`.

> **Next:** [Part III — The decision](part-iii-the-decision.md).
