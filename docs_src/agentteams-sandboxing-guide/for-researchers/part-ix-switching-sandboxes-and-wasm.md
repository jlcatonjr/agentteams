# Part IX — Switching sandboxes and WASM  (SB25–SB30)

<!-- skeleton:SB25 SB26 SB27 SB28 SB29 SB30 -->

## The new claim under review

Earlier parts box the agent *process*. This part adds a second, independent box around the *code an
agent runs*: a WebAssembly guest under a pinned wasmtime. The threat it targets is the same Excessive
Agency case (an injected or mis-followed instruction), narrowed to "the agent executes something it
should not". A reviewer should hold three questions: what does each box contain, who can turn a box off,
and where does agentteams *claim* the box holds versus merely offer it.

## Two boxes, not one (SB25)

| Box | Contains | Switch | Default |
|---|---|---|---|
| OS sandbox | the agent process (vendor CLI, or agentteams' own WASM host) and everything it runs natively | `os_sandbox` / `--os-sandbox` | follows `privilege_profile` (`confined` default = on) |
| WASM sandbox | the code the agent sends to `run_wasm`, or every step of a WASM-native agent | `wasm_sandbox.enabled` / `--wasm-sandbox` | off (opt-in); `--framework wasm` forces it on |

The WASM grant is **compute only** by default: stdio, no preopened directory, an empty environment, no
network, and fuel / memory / timeout limits. Widening is per agent and limited to read-only project paths
and one private scratch dir; network is never grantable. Read paths are refused when they reach the
control plane, credential material, a trust root or a symlink.

The two compose; neither replaces the other (D6). The matrix a reviewer should keep:

| | WASM off | WASM on |
|---|---|---|
| **OS on** | process confined (once wired); code native inside it | process confined; code in a guest; control plane session-write-denied — the only cell that can carry an `enforced` claim |
| **OS off** | nothing confined | code in a guest, but the session can rewrite the control plane that configures the guest, so every claim degrades to `advisory` (`wasm-without-os-sandbox`) |

The *OS off, WASM on* cell is the instructive one: the guest is real confinement for the code, but an
agent that can edit the server's launch config or the grant files can widen its own next guest. That is
why agentteams refuses to call it enforced. Native Windows lands in that cell by force (`wasm-only-host`).

A separate switch, `wasm_sandbox.model_substrate`, projects a floor to consumers that run *model* code
(`.agentteams/sandbox-state.json`). It is a signal the consumer enforces, not agent confinement.

## Who can turn a box off (SB26)

Every weakening is a **derived** constraint-relaxing effect (Rule 15), never a self-declared flag:
`sandbox-os-off`, `sandbox-wasm-off`, `wasm-grant-widen`, `native-exec-keep`, `wasm-to-os-switch`.

The governance property to check: **"governed" is the default.** Strict decision signing
(`enforce_decision_signing`) defaults to true and is written at the first render, so from the second
render on, each relaxation needs a decision row signed with the operator's **asymmetric (Ed25519)** key,
scoped to the exact action id and matching the exact effect. A symmetric (HMAC) signature is valid but
insufficient, so neither an agent nor a compromised runner holding a shared secret can mint one; a HALT
on the instance, its lineage or the family blocks it (C-2). Standing relaxations carry a 90-day sunset,
stay bound to the signed row, and share a small aggregate cap (default one), so exceptions cannot
silently proliferate.

Stated carve-outs (Rule 12): the very first render of a new team is ungoverned (the switch is not on disk
yet); ungoverned workspaces get a `SANDBOX RELAXATION` notice rather than a refusal; and a team that never
used the switches keeps the pre-switch behaviour when it moves `confined` → `cooperative`.

## Where agentteams claims enforcement, and where it only offers (SB27)

Vendor CLIs cannot run inside WASM, so for them agentteams offers a tool, `run_wasm`, through its own MCP
server (`agentteams_wasm`), and removes native exec from eligible agents (those holding an execute-class
tool) where the host both wires the tool and enforces per-agent tool lists. Fixed exemptions: the
orchestrator and the team builder. Default keep list: `navigator`, `git-operations` (they need git),
which is itself a signed `native-exec-keep` effect in a governed workspace.

**A trust-boundary fact the operator measured (spike B):** Claude Code (session sandbox on and off) and
Goose launch stdio MCP servers *outside* their session sandbox. The session sandbox therefore does not
contain the server. The server contains itself: it wraps wasmtime in its own nested OS profile where one
can start, and every config that launches it is session-write-denied so an agent cannot change its flags.

The verdict vocabulary is deliberately graded — `enforced`, `conditional`, `declared`, `advisory` — and
`enforced` / `conditional` require **both** the OS sandbox on **and** the control-plane write rules merged
in the *live* configuration (checked on disk, not assumed from the render):

| Provider | Strongest verdict | What it rests on |
|---|---|---|
| Claude Code | `enforced` (sub-agents) | Claude Code enforces subagent `tools`; Bash removed; live settings carry the control-plane denies (including `.claude` / `.claude/agents`) |
| Goose | `enforced` for a direct recipe run; delegates `advisory` | the recipe's tool list is enforced at dispatch; `.goose/sandbox.sb` carries the rules |
| Codex | `conditional` | a role gate the confined launcher protects and self-probes at each launch; not verifiable at render (Codex ignores role tool limits, so without the launcher only `declared`) |
| Grok | `declared` | Bash absent from tools; no live negative probe |
| Copilot (VS Code, CLI), AGENTS.md | `advisory` | no verified per-agent `run_wasm`, or no tool channel |
| Any top-level session | `advisory` | the operator's own session is never narrowed |

The top-level session being `advisory` everywhere matters: a narrowed sub-agent can ask the orchestrator
(which keeps exec) to run something natively. That laundering path is stated in every notice, not closed.
Format conversions and multi-sync apply no narrowing and say `ADVISORY ONLY`.

## WASM-native agents (SB28)

With `--framework wasm`, there is no vendor CLI at all: each agent step is a WASM guest, and the host
process (itself OS-confined when the OS switch is on and the machine allows it) calls the model provider.
Provider keys stay in the host, delivered by the launcher over a pipe. Team files are hash-locked, but the
lock holds no key; it is trustworthy only as far as the control plane is write-protected and the
launch-time grant check passes (a widened grant needs a still-verifying signed exception in a governed
workspace). Delegation is **attenuation only** (D11): a sub-agent's grant is a subset of its delegator's
by capability class, there is no amplification exception, and the parent re-checks target, cycle, depth
and caps itself.

## Supply chain (SB29)

The runtime and interpreters are downloaded only by an explicit operator command, verified against a
pinned size and sha256 before extraction, and re-hashed on every use; any mismatch fails closed. The
shipped wasmtime 36.0.20 was adopted inside agentteams' 14-day release cooldown under a recorded
`@security` remediation waiver, because it carries fixes for, among others, a fuel bypass and a
symlink-rename filesystem escape. Pin updates write nothing until every platform verifies and are
reviewed before commit.

## The honest verdicts for this layer (SB30)

- **Scope:** guests run modules, Python and JavaScript; no Bash, git or npm. The vendor CLI process is not
  WASM-confined.
- **Advisory is not a boundary:** Copilot, AGENTS.md, Grok under `replace`, every top-level session, and
  every OS-off combination.
- **Windows:** WASM without an OS sandbox, and no read-only mounts (the snapshot copy needs `dir_fd`).
- **Residuals a red team should probe:** the launching-config scan is name-based (a wrapper that builds
  the server path at run time evades it); credential and config scans are point-in-time (TOCTOU); file
  denies do not stop Keychain access through the macOS Security framework; the model-substrate file can be
  deleted wherever nothing write-protects it (OS sandbox off, Copilot), detected only at
  `--verify-integrity` or the next render.
- **Ceiling #4 is unchanged:** a same-host operator or key-holder is out of scope.

*Detail:* [Reference Part IX](../reference/part-ix-switching-sandboxes-and-wasm.md) ·
[Known limitations](../../known-limitations.md).
