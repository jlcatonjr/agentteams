# Part IX — Switching sandboxes and WASM  (SB25–SB30)

<!-- skeleton:SB25 SB26 SB27 SB28 SB29 SB30 -->

Two knobs, independent of each other: the **OS sandbox** confines the agent *process*; the **WASM
sandbox** confines the *code an agent runs* (and is the whole runtime for `--framework wasm` teams).

## Which knob do I turn? (SB25)

| You want | Set | Cost |
|---|---|---|
| No OS boundary, keep the rest of the profile | `"os_sandbox": "off"` or `--os-sandbox off` | a signed decision in a governed workspace (below) |
| An OS boundary on a `cooperative` team | `"os_sandbox": "on"` or `--os-sandbox on` | the gate hook becomes fail-closed |
| Agent code run in WASM instead of natively | `"wasm_sandbox": {"enabled": true}` or `--wasm-sandbox on` | eligible agents lose native exec where `run_wasm` is wired; `--wasm-install` once |
| A whole team on agentteams' own WASM runtime | `--framework wasm` | beta, generate-only; a model provider key |
| Tell a model-code consumer (baseAgent) to run WASM-confined | `"wasm_sandbox": {"model_substrate": "on"}` or `--wasm-substrate on` | none; the consumer enforces it |

`os_sandbox` is the dedicated on/off switch for the OS boundary. `privilege_profile: "cooperative"` still
turns it off too, but it also keeps the gate hook fail-open and cannot be used with
`write_policy: orchestrator-only`.

WASM is off unless you turn it on. When on, the default grant is **compute only**: stdio, no files, no
environment, no network, limits 10^10 fuel / 64 MiB / 120 s. Widen one agent at a time:

```json
"wasm_sandbox": {
  "enabled": true,
  "languages": ["module", "python"],
  "agents": {"primary-producer": {"read_paths": ["docs", "src"], "scratch": true}}
}
```

`read_paths` are mounted read-only; `scratch` is one private writable dir at
`.agentteams/wasm/scratch/<agent>/`. There is no network key. Key reference:
[`DESCRIPTION-FORMAT.md`](../../DESCRIPTION-FORMAT.md#wasm_sandbox).

**The four combinations:**

| | WASM off | WASM on |
|---|---|---|
| **OS on** | today's behaviour; code runs natively inside the wired boundary | strongest: process OS-confined, code in WASM, control plane write-denied; the only cell where a provider can read `enforced` |
| **OS off** | nothing confined | code in WASM, but every provider row is `advisory`; notice `wasm-without-os-sandbox` (`wasm-only-host` on native Windows) |

WASM never replaces the OS sandbox: when one is available, the host process still runs inside it.

## Turning something off (SB26)

Five changes are constraint-relaxing: `sandbox-os-off`, `sandbox-wasm-off` (WASM off after on, or the
model-substrate floor lowered), `wasm-grant-widen`, `native-exec-keep`, `wasm-to-os-switch` (a runner
program back from `sandbox: wasm` to `os`).

**Your workspace is probably governed.** "Governed" means strict decision signing
(`enforce_decision_signing`) is on, and it defaults to true from the first render. The gate reads it from
disk before rendering, so only the very first render of a new team is ungoverned. In a governed workspace
the render refuses until you record an operator **Ed25519**-signed decision whose scope is the exact
action id (for example `sandbox-os-off:claude`) and whose effect matches exactly; the refusal prints the
shape it needs. Mint it outside the agent's session (the spec format is in the
[CLI reference](../../cli-reference.md#-sign-decision)):

```
AGENTTEAMS_DECISION_ED25519_KEYFILE=~/.config/agentteams/keys/<key>.pem \
  agentteams --sign-decision spec.json --output <team dir>
```

An HMAC-signed row is not enough, and a HALT blocks it. Standing relaxations (OS off, a widening, a keep,
a runner switch) get a 90-day sunset and count against the exception registry's aggregate cap (default
**one** active relaxing exception): if you need two at once, raise the cap in the registry first.
In an ungoverned workspace you get a `SANDBOX RELAXATION` notice instead of a refusal. `os_sandbox: off` is
always refused under `write_policy: orchestrator-only`.

Teams that never used the switches keep their old behaviour: moving such a team from `confined` to
`cooperative` is not gated (it never was).

## WASM for vendor CLIs: what you actually get (SB27)

With WASM on, agentteams installs the `agentteams_wasm` MCP server (`.agentteams/bin/`) and offers its
`run_wasm` tool. Every agent with an execute-class tool is eligible. Under the default
`native_exec: replace` an eligible agent loses its native shell where its host wires `run_wasm`, and keeps
it (listed as a *fallback*) where the host does not. Always kept: the orchestrator and team builder
(fixed), plus `native_exec_keep`, which defaults to `navigator` and `git-operations` because they need git
(one `native-exec-keep:<framework>` decision covers both; set `[]` to keep none).

Read the verdict the render prints as `[WASM-ENFORCEMENT]` (also in the manifest's `wasm_enforcement`):

| Framework | Best case | When you get it |
|---|---|---|
| claude | `enforced` | OS sandbox on **and** you merged the emitted settings into the live config, including the `.agentteams` denyWrite, the `.claude` / `.claude/agents` deny and the deny on every config that launches the server; and no narrowed agent file grants a shell. Until you merge: `advisory`. |
| goose | `enforced` for a direct `goose run --recipe` | OS sandbox on and `.goose/sandbox.sb` carries the rules. Delegates: always `advisory`. Legacy scoping: `advisory`. |
| codex | `conditional` | launched through the confined launcher (its role gate runs in WASM mode); otherwise `declared` |
| grok | `declared` | `native_exec: deny`; under `replace` it is `advisory` (no `run_wasm` yet) |
| copilot-vscode, copilot-cli | `advisory` | always (only an inert MCP example is emitted) |
| agents-md | `advisory` | always |
| any top-level session | `advisory` | always |

Why the config denies matter: in the operator's spike (macOS, development wasmtime 36.0.16), Claude Code
(sandbox on and off) and Goose ran stdio MCP servers **outside** their own sandbox. The server therefore
wraps wasmtime in its own OS profile, and what stops an agent from rewriting its launch flags is the
write-deny on those config files.

Check after merging: `agentteams --description brief.json --framework <fw> --check-wiring` re-checks
the control-plane denies and the verdict against the live files.
`--convert-from` / `--interop-from` / multi-sync never narrow and always print `ADVISORY ONLY` for a WASM
team.

## WASM-native agents (SB28)

```
agentteams --description brief.json --framework wasm --project .
agentteams --wasm-install all --project .
ANTHROPIC_API_KEY=... agentteams --run-wasm-agent orchestrator --wasm-task "Summarise the open issues." --project .
agentteams --verify-wasm-run <run-id> --tree --project .
agentteams --revoke-wasm-run <run-id> --revoke-reason "stop" --project .
```

Each agent runs as WASM guest steps; the host calls your provider (`--wasm-provider`, `--wasm-base-url`;
keys only from the environment). With the OS switch on, the host itself is OS-confined where the machine
allows it, and guests report `inherited`. A delegated sub-agent never gets a wider grant than its
delegator (no exception); give an agent a wider grant by running it directly. Full guide:
[WASM agent runtime](../../wasm-agent-runtime.md).

## Downloads (SB29)

Nothing is downloaded while rendering. You run:

```
agentteams --wasm-install all --project .     # wasmtime, CPython WASI, QuickJS-ng; asks y/N unless --yes
agentteams --wasm-status --project .          # offline re-verify
```

Pinned: wasmtime 36.0.20 (adopted inside the 14-day cooldown under a recorded `@security` waiver for its
security fixes), CPython WASI 3.15.0, QuickJS-ng 0.17.0. Any size or sha256 mismatch fails closed; a bad
install is quarantined, not deleted. To move a pin (on a host that can download, then `@security`
reviews the printed diff before you commit it and run `--write-integrity-manifest`):

```
agentteams --wasm-pin-update wasmtime <VERSION> [--remediation GHSA-...] [--pins-out PATH]
```

## What it costs you, honestly (SB30)

- Guests run `.wasm`, Python and JavaScript only: no Bash, git or npm.
- The vendor CLI is not confined by WASM, only the code it sends to `run_wasm`.
- `advisory` rows are guidance, not a boundary.
- Native Windows: WASM without an OS sandbox, and no read-only mounts.
- Residuals (name-based config scan, point-in-time scans, Keychain via the Security framework, the
  model-substrate file deletable when nothing write-protects it) are listed in
  [Known limitations](../../known-limitations.md).

*Full detail:* [Reference Part IX](../reference/part-ix-switching-sandboxes-and-wasm.md).
