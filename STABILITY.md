# API Stability Policy

Starting with **1.0.0**, `agentteams` follows [Semantic Versioning 2.0.0](https://semver.org/).
This document enumerates the public surface that the version contract applies to
and the surfaces that are explicitly **not** covered.

## What SemVer covers

A change is **breaking** (requires a major version bump) if it removes or
changes the contract of any of the following:

### 1. Command-line interface
- The `agentteams` console-script entry point.
- Any documented flag in [`agentteams.1`](agentteams.1) or `agentteams --help`.
- Exit codes documented in the man page.
- The `build-team` deprecation alias is retained through the **1.x** series and
  will be removed at **2.0** (it emits a stderr deprecation notice on every
  invocation).

### 2. Python API
The supported import surface is everything documented under
[`docs_src/api-reference/`](docs_src/api-reference/). Notable modules:
- `agentteams.ingest`, `agentteams.analyze`, `agentteams.render`, `agentteams.emit`
- `agentteams.security_refs`, `agentteams.model_routing`, `agentteams.drift`
- `agentteams.memory_index`, `agentteams.fence_inject`, `agentteams.liaison_logs`
- `agentteams.frameworks.{copilot_vscode, copilot_cli, claude}`
- `build_team.main`, `build_team.__version__`

> **Framework adapters.** Seven frameworks are registered. Their adapter modules and emitted-artifact shapes are
> covered by the SemVer contract as follows:
>
> | Framework | Module | SemVer status | Notes |
> |---|---|---|---|
> | `copilot-vscode` | `agentteams.frameworks.copilot_vscode` | **Stable** | |
> | `copilot-cli` | `agentteams.frameworks.copilot_cli` | **Stable** | |
> | `claude` | `agentteams.frameworks.claude` | **Stable** | |
> | `goose` | `agentteams.frameworks.goose` | **Beta** (outside SemVer) | Generate, convert, bridge and interop are supported. Converting from a `claude` or `copilot-cli` source gives flat (un-delegated) recipes, because those sources strip handoffs. Bridge stubs still declare the whole `developer` extension. |
> | `agents-md` | `agentteams.frameworks.agents_md` | **Beta** (outside SemVer) | No grant channel. It has no `AGENTS.md` ownership guard, so it overwrites an `AGENTS.md` written by another renderer. |
> | `codex` | `agentteams.frameworks.codex` | **Beta** (outside SemVer) | Grants are enforced only under its `write_policy` role gate (section 6). Codex's repo-root `AGENTS.md` instructions file belongs to this adapter, not to `agents-md`. |
> | `grok` | `agentteams.frameworks.grok` | **Beta** (outside SemVer) | The hook gate is **advisory**: the `grok-gate-shim.py` runs the constitutional gate and fails open. Grants are declared, not enforced (section 6). Grok Build 1.0.52 cannot spawn custom agents as subagents. `convert` and `bridge` refuse grok. |
>
> A beta adapter is shipped, tested and usable. Its adapter API and emitted-artifact shapes may change in a minor
> release while the integration matures. It graduates to the stable contract once its listed gaps close.

> **Beta modules.** `agentteams.term_index` and the `--term-check` gate (issue #548, new in 1.3.0) are **beta**:
> shipped and tested, but their API, register schema and finding codes may change in a minor release.

> **Beta: `agentteams.wasm_session`** (issue #567, new in 1.3.0), the WASM streaming session mode and its
> `python -I -m agentteams.wasm_session` entry point. It is shipped and tested, but its flags, status records
> (schema `agentteams-wasm-session/1`) and exit codes may change in a minor release. Any such change bumps the schema
> major and gets a CHANGELOG `breaking` entry. It joins the supported surface after one real consumer certification.

Symbols not documented in `docs_src/api-reference/` are **internal** and may
change without notice — including modules whose name starts with `_`,
functions starting with `_`, and any helper not listed in the reference docs.

### 3. JSON schemas
The committed [`agentteams/schemas/`](agentteams/schemas/) directory is part of the contract.
Backwards-incompatible changes to any schema (removing a required field,
changing a field's type, narrowing an enum) require a **major** bump.
Additive changes (new optional fields, new enum values that consumers are
expected to ignore-or-pass-through) are **minor**.

**Limitation (known, 1.0):** schemas do not yet carry a per-file
`schema_version` field. The package version is the only schema version.
Per-schema versioning is planned for a future minor release; until then,
schema changes are governed by package SemVer.

### 4. On-disk artifacts
The following file formats produced by `agentteams` are part of the
contract — readers (other tools, CI, downstream automation) may rely on
their structure:
- `_build-description.json`
- `_intake-notes.md` fence markers
- The build-log header / structural-diff format
- The `pre-fencing-snapshot` git-tag protocol used by `--migrate` /
  `--revert-migration`
- Agent file fence markers (`<!-- agentteams:fence ... -->`) — adding new
  fence kinds is minor; renaming or removing existing kinds is major.

### 5. Template contracts
Template *names* under [`agentteams/templates/`](agentteams/templates/) and
their slot variables (`{UPPER_SNAKE_CASE}` tokens) are part of the contract.
Renaming a template file or removing/renaming a slot is breaking.
Adding optional slots with default values is non-breaking.

### 6. Orchestrator-only writes (`write_policy`)

`write_policy: "orchestrator-only"` makes the orchestrator the only agent that
writes. Every other agent is narrowed to read-only tools and returns
proposals. Agents named in `mcp_grants` instead write and run allowlisted
commands through the `agentteams_runner` MCP server, which queues the work for
the out-of-session runner (`agentteams --serve-requests`, started by the
operator outside every agent session).

**It is opt-in, and the default does not change in 1.0.** A brief without
`write_policy`, or with `write_policy: "default"`, renders exactly as before.
The default stays off throughout 1.x. Turning the switch on by default would
change the output for every existing brief, so this policy treats it as a
breaking change.

**Requirements.** The switch needs an explicit `privilege_profile` of
`"confined"` or `"exclusive"`. Render refuses `"cooperative"`, and it refuses
an unset profile, because the default leaves the Claude gate hook fail-open.

**Framework scope.** `write_policy.resolve` refuses the switch on every
framework other than `claude`, `goose` and `codex`, and it refuses Goose too
(see the table below; `AGENTTEAMS_ALLOW_UNENFORCED_GOOSE_SWITCH=1` is for
agentteams' own tests only). The one exception is a
render scoped by `write_policy_frameworks`: a framework not named in that list
renders the same brief **without** the switch, with a notice, instead of being
refused.

**Per-surface status.** Two separate questions apply to each surface: whether
`write_policy` can be turned on there, and whether the surface enforces an
agent's declared tool grant (`interop_grant_check.enforcement_class`).

| Surface | `write_policy: "orchestrator-only"` | Tool grants | Status for 1.0 |
|---|---|---|---|
| **Claude** (`claude`) | Supported. The session sandbox denies the ledger-key directory, and `--check-wiring` verifies the live settings. `--check-wiring` refuses Linux until the `.agentteams-status/` channel has been measured under bwrap. | Enforced | **Enforced**, with documented residuals: Claude Code's built-in general-purpose subagent holds Bash, and a granted agent's runner server is not connected in headless `claude -p` runs. See [known limitations](docs_src/known-limitations.md#orchestrator-only-writes-write_policy-and-the-runner). |
| **Copilot** (`copilot-vscode`, `copilot-cli`) | **Refused at render.** No session sandbox keeps the ledger key from the session. With `write_policy_frameworks` set, a Copilot render of the same brief renders without the switch. | Enforced | **Grants enforced.** In a signing-governed workspace (`enforce_decision_signing`), adding an agent to the orchestrator's `agents:` list under an `--update` merge (`--adopt-orphans`) or `--adopt-only` requires an operator Ed25519-signed `adopt-orphans-merge` decision that grants exactly `agents:<slug>`. `--overwrite --adopt-orphans` is cleared by the `overwrite` clearance instead. |
| **Goose** (`goose`) | **Refused at render.** Goose 1.37 delegates inherit the orchestrator's `write`/`edit`/`shell` extensions, so per-recipe narrowing does not hold. `AGENTTEAMS_ALLOW_UNENFORCED_GOOSE_SWITCH=1` lifts the refusal for agentteams' own tests only, never for a real team. | Enforced for a direct `goose run --recipe`. Under `delegate`, the narrowing is cooperative. | **Refused under the switch** |
| **Codex** (`codex`, including its repo-root `AGENTS.md`) | Supported only when Codex is launched through `.codex/confined-run.example.sh` on Linux or macOS, with an explicit `confined` or `exclusive` profile. The launcher masks the key directory and turns on a generated PreToolUse role gate (`.agentteams/bin/codex-role-gate.py`). Generation cannot verify that launch, and says so. | Enforced under the role gate. Elsewhere they are declared only (`sandbox_mode` and the tool list are self-imposed). | **Orchestrator-only for 1.0** (through the launcher) |
| **agents-md** (`agents-md`, the framework-neutral `AGENTS.md`) | **Refused at render.** With `write_policy_frameworks` set, an `agents-md` render of the same brief renders without the switch. | None: the surface has no grant channel. | **Cooperative.** An agent narrower than the surface default is projected only when named in `--interop-allow-cooperative`. The opt-in is recorded and the agent carries a "cooperative" banner. |
| **Grok** (`grok`) | **Refused at render.** With `write_policy_frameworks` set, a `grok` render of the same brief renders without the switch. | Declared, not enforced. No live negative test has yet shown a read-only agent blocked from editing. Scoped entries and `MCPTool` are dropped from `tools:` (grok discards the whole list otherwise). MCP servers are outside `tools:`: an agent run with `grok --agent` reaches every configured one (`mcpInheritance` limits only subagents; live 2026-11-10). | **Advisory** |

**`mcp_grants` approval modes.** `staged` (the default; the orchestrator
approves each write) and `direct` (the runner applies a write only under a
verified operator-signed grant). Deletes are always staged.

**What SemVer covers here:**
- **Inside SemVer:**
  - the brief keys `write_policy` (enum `default` | `orchestrator-only`),
    `write_policy_frameworks`, `mcp_grants` and `agent_policies`, and their
    schema entries;
  - the default of off;
  - the documented CLI flags of the approval loop and its checks:
    `--serve-requests`, `--issue-dispatch`, `--apply-proposal`,
    `--run-request`, `--list-staged`, `--show-staged`, `--apply-staged`,
    `--reject-staged`, `--verify-proposal-ledger`, `--sign-grant` and
    `--check-wiring`;
  - the set of surfaces on which render refuses the switch. Removing a surface
    from that set (making it accept the switch) is a minor change. Adding one
    is breaking.
- **Outside SemVer:**
  - **The enforcement level each surface achieves.** It depends on the
    third-party runtime (Claude Code, Goose, Codex, Grok Build) and on
    operator-owned settings. A runtime release can strengthen or weaken it
    without any agentteams change.
  - The wording of the generated write-policy sections, and the runner's
    on-disk queue and status layout (`.agentteams-queue/`,
    `.agentteams-status/`).

Known limitations of this feature for 1.0 are listed in
[`docs_src/known-limitations.md`](docs_src/known-limitations.md).

### 7. WASM sandbox core (new in 1.3.0, #568)

The WASM executor that agentteams owns and baseAgent derives from. Supported:

- **`agentteams.wasm_exec`**:
  - `run`, `prepare`, `materialize`, `assert_grant_shape`;
  - `Grant`, `Mount`, `Limits`, `LIMIT_BOUNDS`, `DEFAULT_LIMITS`, `COMPUTE_ONLY`;
  - `Prepared`, `Launch` (with `cleanup()`), `WasmResult`;
  - the `WasmResolver` protocol and the `OsConfine` values;
  - `GRANT_SPEC`, `GRANT_SPEC_SHA256`, `WasmExecError`.
- **`agentteams.wasm_pins`**: `pinned_resolver(...)`, the `WasmResolver` interface it satisfies (with the optional
  `expected_sha256`), and `WasmPinError`. The concrete `PinnedResolver` class, `resolve_tree`, `runtime_mounts` and
  `tree_digest` are not covered.
- **`agentteams/schemas/sandbox-state.schema.json`** (`agentteams-sandbox-state/v1`). See the schema rule below.
- **`agentteams.cli.sandbox_relaxation_gate.spent_off_decision`** and `SandboxRelaxationError` (#574). The signature,
  the `<key_id>@<sig16>` return form and the exception type are frozen. Its checks may only tighten. Its message text
  is not covered.
- **The stdlib-only property.** `wasm_exec.py` and `wasm_pins.py` import nothing beyond the Python standard library
  and nothing from agentteams, at import time or call time, so a bundle can exec-load them on their own.

The following rules apply on top of the general ones above:

- **`GRANT_SPEC` content may change in a minor release.** Every change changes `GRANT_SPEC_SHA256` and gets a
  CHANGELOG entry. Pin the sha and refuse on a mismatch. A structural change (a key removed or renamed, or a type
  changed) bumps `agentteams-wasm-grant/vN` and is breaking.
- **`assert_grant_shape`**: its signature and its "raises `WasmExecError`" contract are frozen. **The set of accepted
  argv is defined by `GRANT_SPEC`, not by SemVer.** Tightening it falls under the security-tightening clause in the
  release cadence (a CHANGELOG `security` entry). Widening it is a `GRANT_SPEC` content change (a new sha, a CHANGELOG
  entry) that needs a recorded `@security` clearance; it is never shipped as a security fix.
- **`profiles=`** (on `run` and `materialize`): the only supported value is `agentteams._sandbox_profiles`, or the
  bundle's exec-loaded copy. The functions that module provides are internal.
- **May change in any release:**
  - refusal lists (`NEST_DENY_READS`, `NEST_PROTECTED_REL`, `REFUSED_COPY_OUT_NAMES`, the credential names) may grow
    and `MAX_*` limits may shrink, under the security-tightening clause; raising a `MAX_*` limit widens and needs a
    recorded `@security` clearance;
  - `trap` and `classify_trap` labels are diagnostic and not covered;
  - the pins data (`wasm_runtime_pins.json`: versions and hashes) may change, and its structure is internal.
- **`sandbox-state.schema.json` v1 is closed** (`additionalProperties: false`, and consumers refuse anything but the
  exact shape). **Any** change to its properties or enums makes it `agentteams-sandbox-state/v2`, even an addition;
  for this file that overrides §3's "additive is minor". These meanings are frozen:
  - `on` is a floor, never a ceiling;
  - `require_enforced` is always true;
  - the file is unsigned data (C-4) and never authorizes a relaxation;
  - an absent file means no projection (`sandbox_projection.read_projection` returns `None`), but a consumer that
    has seen `on` must refuse when the file is then absent (handoff §4.3 item 8).
- **`agentteams.wasm_session` stays beta** (see the note in §2) until a consumer certification run.

## What SemVer does NOT cover

The following may change in any release — pin exactly if you depend on them:
- Behavioral-drift fingerprint algorithm (`FINGERPRINT_ALGO_VERSION` is
  versioned independently; bumps trigger build-log healing, not SemVer).
- Wording of human-readable output (`print(...)` strings, prose in
  diagnostics, progress messages).
- File layout under `references/`, `tmp/`, `workSummaries/`, `docs/`,
  `docs_src/`, `examples/` — these are repository-internal and not shipped.
- Test fixtures and test-only helpers under `tests/`.
- Memory-index relevance evaluation corpus and thresholds (used only for
  developer-side calibration; CI skips the relevance test).
- The set and content of generated agent files for the `agentteams` repo's
  own team (under `.github/agents/`, gitignored).
- Skill files emitted under `.claude/skills/` (their *existence and contract*
  is stable; their internal phrasing is not).
- Defects listed in
  [`docs_src/known-limitations.md`](docs_src/known-limitations.md).
  Fixing a listed limitation may change behaviour in a minor or patch release.
  It is not a contract break, even when the fix blocks something the
  limitation allowed.

## Deprecation policy

A deprecation must:
1. Land in a **minor** release.
2. Emit a runtime warning when the deprecated surface is used.
3. Be documented in the `[Unreleased]` section of [`CHANGELOG.md`](CHANGELOG.md)
   with the planned removal version.
4. Survive at least one full minor cycle before removal.

Removal of a deprecated surface always requires a **major** bump.

## Release cadence

- **Patch** (1.0.x): bugfixes, doc fixes, security fixes. No new public
  surface. No behavior change for any documented input, except the security
  tightening below.
- **Security tightening** (patch or minor; operator ruling 2026-11-20): a security
  fix may tighten a refusal, so that something previously accepted is now refused.
  Examples: a new `WasmExecError`, `WasmPinError` or `SandboxRelaxationError` case, a
  smaller bound, a longer deny list. It must carry a CHANGELOG `security` entry that
  says what is newly refused and how to migrate. Signatures, return types and
  exception classes stay fixed. This clause never loosens a refusal and never changes
  the shape of an output. Like the known-limitation carve-out above, it lets a fix
  block something that used to pass.
- **Minor** (1.x.0): new features, new flags, new templates, new schemas,
  additive changes to existing schemas, new deprecation notices.
- **Major** (x.0.0): removals, contract changes, schema breaking changes,
  removal of any previously-deprecated surface.

## Pre-release versions

`-rc.N` suffixes (`1.0.0rc1`, `1.0.0rc2`, …) are release candidates for the
matching final version. The SemVer contract above does not bind them: an rc
may add features and flags, and may remove or change a surface that no final
release has shipped yet. rc.10, for example, added the `grok` adapter and the
opt-in gate decision log, and removed `research browser --screenshot`. Every
such change is recorded in [`CHANGELOG.md`](CHANGELOG.md), and a breaking one
is marked **BREAKING**. The contract applies from the final release onward.

## Reporting issues

Stability-related issues (a documented behavior changes silently across
a minor version, an undocumented internal becomes load-bearing) should
be filed at <https://github.com/jlcatonjr/agentteams/issues> with the
label `stability`.
