# Part IX — Switching sandboxes and WASM  (SB25–SB30)

<!-- skeleton:SB25 SB26 SB27 SB28 SB29 SB30 -->

Two switches, set independently. The **OS sandbox** confines the *agent process*. The **WASM sandbox**
confines the *code an agent runs*, and it is the whole execution model of agentteams' own WASM-native
agents. Neither replaces the other. This part states what each switch does, how turning one off is
governed, exactly what agentteams claims per provider, how the runtime is downloaded, and what stays open.

## SB25 — Two switches, what each confines, and the 2×2 matrix  ✅/⚙

**The OS switch.** `os_sandbox: "on" | "off"` in the brief, or `--os-sandbox on|off` for one run (the flag
wins). Absent, it follows `privilege_profile` (SB4): `confined` / `exclusive` = on, `cooperative` = off.

- `off` suppresses every OS boundary agentteams emits for the selected framework: the Claude settings
  `sandbox` block, the Goose Seatbelt profile, the Codex / Goose / Grok runners, the Grok profile and the
  neutral launcher `sandbox/confine-run.sh`. The rest of the profile (write roots, read-exclusion knobs) is
  kept for when the switch goes back on, and the control-plane stubs a sibling team's launcher needs are
  still emitted.
- `on` emits the boundary even under `cooperative`, and counts as the explicit opt-in that flips the gate
  hook fail-closed (SB17).
- Precedence: `--os-sandbox` > an explicit `--target-host-features <fw>:sandbox` > the brief's
  `os_sandbox` > the profile > any `*:sandbox` token. `--os-sandbox off` together with an explicit
  `<fw>:sandbox` token is refused.

`os_sandbox` is the dedicated on/off switch for the boundary alone. `cooperative` stays a valid profile and
also leaves the boundary off, but it changes the hook (always fail-open) and is not accepted by the
orchestrator-only write policy (SB24).

**The WASM switch.** `wasm_sandbox.enabled` in the brief, or `--wasm-sandbox on|off` (precedence: flag >
brief > framework > off). It is **off unless something turns it on**. `--framework wasm` turns it on by
itself (reason `framework`) and refuses an explicit off, because that runtime has no non-WASM mode.

When WASM is on, agent code runs as a WebAssembly guest under a pinned wasmtime: prebuilt `.wasm` modules,
Python (the CPython WASI build) and JavaScript (QuickJS-ng), narrowed by `wasm_sandbox.languages`. The
default grant is **compute only**: stdio, no preopened directory, an empty environment, no network, and
fuel / memory / timeout limits (defaults 10^10 fuel, 64 MiB, 120 s; bounds in `wasm_exec.LIMIT_BOUNDS`).
Per-agent widening in `wasm_sandbox.agents.<slug>` is read-only project paths (`read_paths`, mounted at
`/project/<path>`) and/or one private writable scratch dir (`scratch: true`, always
`.agentteams/wasm/scratch/<agent>/`). A grant reaches an agent only through its `agentteams_wasm` copy, and
what happens to one that reaches none depends on the framework's wiring:

- **Claude, and Goose under `goose_tool_scoping: grant`** (`wired-per-agent`): only a WASM-eligible agent (one
  holding an execute-class tool) gets a copy. A grant on any other agent has no effect.
- **Goose under legacy scoping and AGENTS.md** (`not-wired`, `instructions-only`):
  no agent gets a copy, so every widened grant has no effect.
- **Codex, Copilot VS Code, Copilot CLI and Grok** (one shared, compute-only copy for the whole project): a widened
  grant is refused and the render stops (`_wasm_wiring.unhonoured_grants`); it is never silently dropped.

In the first two cases the render prints `NOTE (WASM): the wasm_sandbox.agents grant for <slug> has no effect
on <framework>: ...` and records a `wasm-grant-no-effect` advisory (`_wasm_wiring.inert_grants`), so a grant
that does nothing is never silent. `--framework wasm` honours grants in its own runtime (SB28). There is no
network key. The full key reference is in
[`DESCRIPTION-FORMAT.md`](../../DESCRIPTION-FORMAT.md#wasm_sandbox).

**What each confines.**

| Switch | Confines | Mechanism |
|---|---|---|
| OS sandbox | The agent *process* (a vendor CLI, or agentteams' WASM host) and every command it runs natively | Claude settings block, Goose Seatbelt, the runners, the neutral launcher (SB10–SB13) |
| WASM sandbox | The *code* an agent sends to `run_wasm` (vendor CLIs, SB27), or every step of a WASM-native agent (SB28) | wasmtime with a fixed grant shape (`wasm_exec.assert_grant_shape`) |

WASM never replaces the OS sandbox. When an OS sandbox is available, the host process still runs
inside it.

**The 2×2 matrix.**

| | WASM off | WASM on |
|---|---|---|
| **OS on** | Today's behaviour. The agent process is write-confined once wired (SB14); its code runs natively inside the boundary. | Strongest. The process is OS-confined; eligible agents lose native exec where their host gives them `run_wasm`; the `.agentteams` control plane, `.claude/agents` and every config that launches the WASM server are session-write-denied (METAPLAN R1). Only here can a provider row read `enforced` or `conditional` (SB27). |
| **OS off** | Nothing is confined. | Guest code is WASM-confined, but nothing protects the control plane from the session, so every provider row is `advisory`. Generation prints `wasm-without-os-sandbox` and records it in the manifest's `advisories`. On a host with no OS sandbox (native Windows), WASM runs alone and the notice is `wasm-only-host`, plus `wasm-windows-unsupported` (not supported for now). |

**A third, independent switch.** `wasm_sandbox.model_substrate` (`--wasm-substrate on|off`) is not agent
confinement. It projects a floor into `.agentteams/sandbox-state.json` (schema
`agentteams-sandbox-state/v1`) for consumers that run model code, such as baseAgent's battery: `on` means
the consumer must run model code WASM-confined under an enforcing runner and refuse rather than fall back.
The consumer enforces it; agentteams never runs or confines model code. Unsetting the key leaves the file
in place as the floor. See [`sandbox_projection`](../../api-reference/sandbox-projection.md).

*Source:* `agentteams/sandbox_state.py` `effective_sandbox_state`, `os_switch_state`;
`agentteams/wasm_config.py` `wasm_switch`, `composition_advisories`; `agentteams/wasm_exec.py`
`GRANT_SPEC`, `COMPUTE_ONLY`; `agentteams/sandbox_projection.py`.

## SB26 — Turning a sandbox off is governed (D7)  ✅

Six transitions are **constraint-relaxing** (see the constraint-relaxing-authorizations rule). Each has a gate action id:

| Effect kind | When | Action id |
|---|---|---|
| `sandbox-os-off` | the OS boundary the profile (or an earlier render) would emit is suppressed | `sandbox-os-off:<framework>` |
| `sandbox-wasm-off` | WASM was on and is turned off; or the model-substrate floor goes from `on` to `off` / absent | `sandbox-wasm-off:<framework>`, `sandbox-wasm-off:model-substrate` |
| `wasm-grant-widen` | a per-agent grant gains read paths or a scratch dir | `wasm-grant-widen:<framework>` (tokens `wasm:<agent>:read:<rel>`, `wasm:<agent>:scratch`) |
| `native-exec-keep` | a WASM-eligible agent keeps native exec while WASM is on | `native-exec-keep:<framework>` (tokens `native-exec:<agent>`) |
| `wasm-to-os-switch` | a runner `confined_programs` entry moves from `sandbox: wasm` back to `os` | `wasm-to-os-switch:<agent>` |
| `mcp-approval-auto` | a Codex render with WASM on turns on the opt-in `codex:wasm-auto-approve` host feature, so the shared `agentteams_wasm` table gets `default_tools_approval_mode = "auto"` and `run_wasm` runs without the per-call prompt | `mcp-approval-auto:codex` |

**"Governed" is the default.** A workspace is governed when strict decision signing is on
(`enforce_decision_signing`), and that defaults to true: the first render writes it into
`references/agent-privilege.json`. The gate reads the switch from disk *before* the render, so the first
render of a brand-new team is not yet governed; every render after it is. In a governed workspace each
new relaxation needs a decision row whose scope is exactly the action id and whose effect is exactly this
run's (`effect_relaxes` = the action, or `effect_grants` = exactly the added tokens), signed with the
operator's **Ed25519** key (`--sign-decision`). An HMAC signature is valid but insufficient. An unretracted
HALT on the instance, a lineage ancestor or the whole family blocks it (C-2). All checks run before
anything is consumed.

**Standing exceptions.** OS off, a grant widening, a native-exec keep, a runner switch and MCP auto-approval are
standing:
each is registered in the exception registry with a 90-day sunset (`SANDBOX_EXCEPTION_TTL_DAYS`) and bound
to the decision row that cleared it. A later render carries it over only while that row still verifies;
otherwise it lapses and a fresh signed decision is needed. They share the registry's aggregate cap
(default one active relaxing exception), so a workspace that needs, say, an OS-off and a grant widening at
once must raise the cap in the registry (a visible, git-tracked decision). `sandbox-wasm-off` is not
standing. Whether auto-approval is on is recorded in the build-log's `sandbox_exceptions` (`mcp_approval_auto: "on"`,
written only when on; a record without it reads as off), and dropping the token retires its exception.

**Carve-out: an OS-off state that predates governance.** A continuing `sandbox-os-off` (the previous
render already had the OS boundary off and this one keeps it off) with no exception ever recorded for its
action id is not refused in a governed workspace. It continues without a signature: generation prints
`SANDBOX RELAXATION: sandbox-os-off:<framework> predates sandbox governance (no exception was ever recorded
for it) and continues without a signature; ...` and the build-log `sandbox_state` records `pre-governance`.
The usual way in is the ungoverned first render: a brief that sets `os_sandbox: off` from the start gets a
notice there, and every later render then carries the off state over under this carve-out. Only
`sandbox-os-off` has it. Any other continuing relaxation without a recorded exception is refused, and once an
OS-off exception has been recorded for the action, a lapsed one is refused too (re-sign, or turn the sandbox
back on). Turning the OS sandbox off *after* it was on is a new relaxation and needs a signed decision.

**Ungoverned workspaces** never refuse: generation prints a `SANDBOX RELAXATION` notice and the build-log
`sandbox_state` records `notice-only`. `os_sandbox: off` is refused outright under
`write_policy: orchestrator-only`.

**Carve-out (Rule 12).** The gate engages only when a switch is in play: `os_sandbox` or `--os-sandbox`
set, WASM on, or an earlier render recorded `sandbox_state` (so deleting the key later cannot step around
it). A team that never used the switches and moves `confined` → `cooperative` stays exactly as ungoverned
as before the switches existed.

*Source:* `agentteams/cli/sandbox_effects.py`; `agentteams/cli/sandbox_relaxation_gate.py`;
`agentteams/cli/os_sandbox_switch.py` `gate_engaged`; `agentteams/analyze.py`;
`agentteams/cli/exception_registry.py`.

## SB27 — WASM tools for vendor CLIs (`run_wasm`) and the per-provider verdict  ✅/⚙

Vendor CLIs (Claude Code, Codex, Goose, Grok, Copilot) cannot be compiled to WASM, so they stay native.
With WASM on, agentteams installs a first-party stdio MCP server, `agentteams_wasm`
(`.agentteams/bin/agentteams-wasm-mcp.py`, with a sha-checked bundle beside it), whose one tool `run_wasm`
runs a module, Python or JavaScript in a guest. The grant is fixed by the server's launch flags (one
instance per agent where the host allows it, one compute-only shared instance where it does not); a call
argument naming a grant, a path or a limit is refused. See [`wasm_mcp`](../../api-reference/wasm-mcp.md).

**Who loses native exec (D12).** Every agent holding an execute-class tool is WASM-eligible
([`wasm_config.wasm_eligible`](../../api-reference/wasm-config.md)); read-only auditors hold none. The
brief's `wasm_sandbox.native_exec` picks the policy:

- `replace` (default): `execute` is removed where the host gives the agent `run_wasm`; where it does not,
  the agent keeps native exec and is listed as a **fallback** in the notice and the record.
- `deny`: `execute` is removed wherever the framework has a tool channel, with or without `run_wasm`.
- `keep`: no tool change; every eligible agent becomes a `native-exec-keep` effect.

Never narrowed: the fixed exemptions (the orchestrator, recognised by its template, and the team builder)
and `native_exec_keep`, whose default is `navigator` and `git-operations` (they need git). The default
keep is one `native-exec-keep:<framework>` decision the operator signs once in a governed workspace; an
explicit list replaces the default (`[]` keeps none).

**Wiring per framework** (`manifest["wasm_wiring"]`):

| Framework | Status | How |
|---|---|---|
| claude | `wired-per-agent` | subagent front matter `mcpServers` + `mcp__agentteams_wasm__run_wasm`; the top-level session only after the operator merges `.claude/mcp.agentteams-wasm.example.json` into `.mcp.json` |
| goose | `wired-per-agent` (grant mode); `not-wired` under legacy scoping | recipe extensions, honoured on a direct `goose run --recipe`; a delegate cannot add the server |
| codex | `wired-shared` | one compute-only instance in `.codex/config.toml` (loaded for a trusted project), approval mode `prompt` |
| copilot-vscode / copilot-cli | `example-only` | inert `.vscode/` / `.github/mcp.agentteams-wasm.example.json`; no `run_wasm` in agent tools |
| grok | `example-only` | inert `.grok/mcp.agentteams-wasm.example.toml`; the operator merges its table into `.grok/config.toml`, outside the agentteams-managed block. Write-protected only inside the agentteams launcher; in profile mode neither file is, so merge from a fresh render after a diff |
| agents-md | `instructions-only` | no MCP channel |

**Operator spike B.** Run on the operator's macOS host on 2026-11-01 with the development wasmtime 36.0.16
(the Claude Code and Goose versions were not recorded): Claude Code
(with its session sandbox on and off) and Goose launch stdio MCP servers **outside** their own sandbox. So
the session sandbox does not confine the server. The server confines itself: it wraps wasmtime in its own
nested OS profile (Seatbelt, or bwrap on Linux) where one can start, and reports `os_reason`
`nested:seatbelt` or `nested:bwrap`; and every config file that launches it is in the session
`denyWrite` and an `Edit` deny, so an agent cannot rewrite its launch flags.

**The verdict (`manifest["wasm_enforcement"]`, `[WASM-ENFORCEMENT]` lines)** is the one place enforcement
is claimed. Terms, strongest first: `enforced`, `conditional`, `declared`, `advisory`. `enforced` and
`conditional` require the OS sandbox on **and** the R1 control-plane rules merged in the live
configuration; otherwise the cell is `advisory` with the reason.

**What `enforced` covers.** The term is about the native-exec policy only: Bash (or the shell) removed from
the narrowed sub-agents, the session OS sandbox on, and the control-plane rules merged. It says nothing about
whether `run_wasm` itself runs inside an OS sandbox. That is measured on every call and returned as the
result's `os_confinement`: `nested` (Seatbelt or bwrap), `inherited` (only under the `--run-wasm-agent` launcher of WASM-native agents, SB28) or `none`. The MCP
server calls with `os_confine="auto"`, which falls back to `none` when no profile can start
(`wasm_exec._nesting`). Only `nested` counts as OS-enforced. When the render's probe
(`_sandbox_profiles.available()`) finds no usable profile, it prints `NOTE (WASM): run_wasm will run without
OS nesting on this host (...)` and records a `wasm-run-unnested` advisory (WASM then runs alone, with a
notice). A render inside an already-sandboxed session also probes `None`, while the server, which the vendor
CLI launches outside that sandbox, may still nest, so the per-call field is the authority.

| Framework | Narrowed sub-agents | Precondition |
|---|---|---|
| claude | `enforced` (Bash removed from subagent `tools`; Claude Code enforces subagent tools) | the live settings carry the `.agentteams` denyWrite, the `Edit(/.agentteams/**)` and `Edit(/.claude/agents/**)` denies, the shell-side deny on `.claude` (or at least `.claude/agents`) and every launching config (`_sandbox_drift.verify_control_plane_merged`, `wasm_mcp.control_plane_advisory`), and no narrowed agent's live file still grants a shell. A fresh render, before the operator merges the settings, reads `advisory: control-plane rules not merged`. |
| goose (grant) | `enforced` on a direct `goose run --recipe`; `delegates` always `advisory` | `.goose/sandbox.sb` carries the rules **and** `GOOSE_SANDBOX` is on in the live, user-global Goose config (`~/.config/goose/config.yaml`, or `$AGENTTEAMS_GOOSE_CONFIG`; `control_plane_advisory`). Otherwise `advisory`, reason `Seatbelt profile not wired`: the emitted profile is inert until wired, and a `sandbox-exec -f .goose/sandbox.sb` launch is not verifiable at render, so it never counts. |
| goose (legacy) | `advisory` | — |
| codex | `conditional` when the confined launcher emits the role gate in WASM mode (or the write-policy gate); `declared` without it | the launcher's `--protect` + self-probe at each launch; not verifiable at render |
| grok | `declared` under `deny` (Bash absent; no live negative probe); `advisory` under `replace` (no `run_wasm`) | — |
| copilot-vscode / copilot-cli | `advisory`, also under `deny` | no verified control-plane protection |
| agents-md | `advisory` | no tool channel |
| every top-level session | `advisory` | — |

Codex's `default_tools_approval_mode = "prompt"` never counts as enforcement. `--convert-from`,
`--interop-from` and multi-sync render agents from their source tools without this policy or the
`run_wasm` wiring, so for a WASM team they print `[WASM-ENFORCEMENT] <framework>: ADVISORY ONLY` instead of
any claim. Details: [`wasm_enforcement`](../../api-reference/wasm-enforcement.md).

*Source:* `agentteams/wasm_mcp.py`; `agentteams/data/agentteams-wasm-mcp.py`;
`agentteams/frameworks/_wasm_wiring.py` `_static_row`, `scan_launching_configs`;
`agentteams/wasm_enforcement.py` `_MECHANISM`, `enforcement_report`, `projection_notice`;
`agentteams/wasm_config.py` `NATIVE_EXEC_EXEMPT`, `NATIVE_EXEC_KEEP_DEFAULT`.

## SB28 — WASM-native agents (`--framework wasm`) and delegation  ✅/⚙

`--framework wasm` (**beta**, generate-only) renders a team for agentteams' own runtime instead of a vendor
CLI: `.agentteams-wasm/agents/<slug>.md`, `TEAM.md`, `grants.json`, `runtime.json`, `README.md`, all hashed
into `.agentteams/wasm-team.lock.json`. Each agent runs as a sequence of WASM guest steps (one event in, one
op out, then exit); the host owns the conversation and calls the chosen provider (Anthropic,
OpenAI-compatible services, Gemini, Ollama / LM Studio, or the offline `stub`) through
[`model_providers`](../../api-reference/model-providers.md). Keys are read from the operator's environment
by the launcher and handed to the host over a pipe; a guest never sees one.

**Host confinement.** With the OS switch on, `runtime.json` says `host_os_confine: auto` and the launcher
wraps the host in a fresh OS sandbox (Seatbelt or bwrap) where the machine allows it. The guests then
report `os_confinement: inherited`, which means "inside the host's profile", not a fresh guest sandbox.
Where no OS sandbox can start (inside another sandbox, native Windows) the run says
`WASM host not OS-confined`. With the OS switch off the host is never confined (and that is a
`sandbox-os-off` effect).

**The lock** proves the team files match the last render. It holds no key, so its integrity rests on R1
(the session cannot write `.agentteams/` when OS-confined with WASM on) and on the launch-time grant
authorization: before every run the agent's grant is re-checked, and in a governed workspace each read
path or scratch dir must be covered by an active, still-verifying `wasm-grant-widen:wasm` exception.

**Delegation (D11).** A sub-agent's grant is never wider than its delegator's, with no amplification
exception: each child read mount must lie inside a parent mount, a child gets scratch only if the parent
has it, and child limits and languages are at most the parent's. A declared mount, limit or tool the
delegator lacks is narrowed (listed in `delegate-start.dropped`), never silently kept. A target outside the
agent's `delegates`, a cycle, the depth / per-run fan-out / tree caps (defaults 2 / 8 / 16, hard caps 4 / 32
/ 64) or an exhausted budget refuse the delegation. Stage 0 opens every child and re-checks all of this
itself; the grant never crosses the pipe from the confined host. `--verify-wasm-run <root> --tree`
verifies every transcript and parent/child binding; `--revoke-wasm-run <root>` stops the whole tree.

Operator guide: [WASM agent runtime](../../wasm-agent-runtime.md).

*Source:* `agentteams/frameworks/wasm.py`; `agentteams/wasm_agent/launch.py`, `host.py`, `team.py`,
`delegation.py`, `child_runs.py`; `agentteams/cli/wasm_agent_commands.py`.

## SB29 — Downloads and pins (D4, D9, D10)  ✅/⚙

- **Explicit only.** `--wasm-install [COMPONENTS]` (`wasmtime`, `python`, `javascript`, `all`; default
  `wasmtime`) downloads into `<project>/.agentteams/wasm/`. It prints the plan and asks `y/N` unless
  `--yes`. Rendering never downloads: the provisioning flags are refused alongside `--description`,
  `--self`, `--update` and `--output`.
- **Fail closed.** Each archive is checked against its pinned size and sha256 before extraction; unsafe
  members are refused; the extracted files are verified before an atomic move. Installed files are
  re-hashed on every use. A failing install is moved to `.quarantine/`, never deleted. A component whose
  pin reads `TODO-PIN` is refused before any network access. `--wasm-status` re-verifies offline.
- **Shipped pins** (filled from verified downloads on the operator's host, 2026-11-01): wasmtime
  **36.0.20**, CPython WASI **3.15.0**, QuickJS-ng **0.17.0**. wasmtime 36.0.20 was adopted inside the
  14-day S-10 cooldown under a recorded `@security` remediation waiver; it carries the 36.0.17–36.0.20
  fixes (eleven GHSA ids, among them a fuel bypass, a symlink-rename filesystem escape and an aarch64
  Cranelift miscompile).
- **Updating a pin.** On a host that can reach `api.github.com` and the release asset host:

  ```
  agentteams --wasm-pin-update wasmtime 36.0.21
  agentteams --wasm-pin-update wasmtime 36.0.21 --remediation GHSA-xxxx-xxxx-xxxx   # inside the cooldown
  ```

  S-10 applies (at least 14 days old and the newest such release on its major line) unless `--remediation`
  names GHSA ids from a recorded `@security` decision. Every platform asset is checked against its API
  digest, extracted and hashed; the pins file is written only after every platform verifies; the diff is
  printed and nothing is committed. `--tofu` accepts an asset without a digest (recorded as
  `provenance: tofu`, needs a `@security` decision); `--pins-out` names the file to rewrite. Review the
  diff with `@security`, commit it, then run `--write-integrity-manifest`.

*Source:* `agentteams/wasm_runtime.py`; `agentteams/_wasm_archive.py`; `agentteams/_wasm_pin_update.py`;
`agentteams/data/wasm_runtime_pins.json`; `agentteams/cli/wasm_commands.py`.

## SB30 — What the WASM layer does not close  ✅/⚙

1. **WASM runs no native tools.** A guest runs `.wasm` modules, Python and JavaScript only: no Bash, git,
   npm or anything that needs a subprocess. Agents that need those keep native exec (`native_exec_keep`)
   or lose the ability.
2. **Vendor CLIs are not confined by WASM.** Only the code they send to `run_wasm` is. The CLI process is
   confined only by the OS sandbox, and only once wired (SB14).
3. **Advisory rows are guidance.** Wherever the verdict is `advisory` (Copilot, AGENTS.md, Grok under
   `replace`, every top-level session, and every OS-off combination), WASM is a tool offer plus
   instructions, not a boundary.
4. **Windows: not supported for now** (`wasm-windows-unsupported`). Native Windows has no OS sandbox
   (`wasm-only-host`), and read-only mounts there are refused: the un-nested read-only snapshot needs
   `dir_fd` support. That refuses the Python guest too, even with a compute-only grant, because its `/lib`
   runtime is a read-only mount. Only JavaScript and raw modules with compute-only grants run.
5. **Residuals.**
   - The launching-config scan is name-based: it finds configs whose launch strings name
     `agentteams-wasm-mcp.py`. A shell wrapper that builds the server path at run time evades it, so that
     config is not required to be write-protected.
   - The credential scan of a granted read directory, and the config scan, are point-in-time (TOCTOU): a
     file created after the check is not seen until the next check.
   - The nested profiles' file denies (`~/Library/Keychains` among them) do not stop Keychain access
     through the macOS Security framework (a Mach service).
   - With the OS sandbox off, or on Copilot (which emits no session sandbox), a session can delete or
     edit `.agentteams/sandbox-state.json`; agentteams detects a lowered floor only at
     `--verify-integrity` or the next render.
6. **Ceiling #4 still holds.** A same-host operator or key-holder is out of scope.

The full list, with workarounds: [Known limitations](../../known-limitations.md).

*Source:* `agentteams/wasm_guests.py` `LANGUAGES`; `agentteams/_sandbox_profiles.py` `snapshot_tree`;
`agentteams/wasm_exec.py` `NEST_DENY_READS`; `agentteams/frameworks/_wasm_wiring.py` `_launches`;
`agentteams/sandbox_projection.py`.
