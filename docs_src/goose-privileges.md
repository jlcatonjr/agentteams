# Goose Privilege Configuration Reference

> **Goose is refused under `write_policy: "orchestrator-only"`** (biographies pilot 1, finding G1, 2026-10-12): Goose 1.37 delegates inherit the orchestrator's `write`/`edit`/`shell` extensions instead of their sub-recipe's narrowed `extensions:`, so the policy can't be enforced there. `write_policy.resolve` and `--check-wiring` refuse it; use the switch on Claude (or Codex through its launcher).

How to control what Goose is allowed to do: tool execution, file writes, shell access, and extension capabilities.

---

## Configuration Files

| File | Scope |
|---|---|
| `~/.config/goose/config.yaml` | All sessions for this user |

All privilege settings for Goose live in this single file. There is no project-level override file.

---

## Session Mode (`GOOSE_MODE`)

The mode is the coarsest privilege control. It governs whether Goose asks for confirmation before using tools.

```yaml
# ~/.config/goose/config.yaml
GOOSE_MODE: auto
```

| Value | Behaviour |
|---|---|
| `auto` | Fully autonomous. No confirmation prompts. Edits, shell commands, and extension calls run immediately. |
| `smart_approve` | Prompts before file edits, destructive shell commands, and extension calls. Read-only operations proceed silently. |
| `approve` | Prompts before every tool call without exception. |
| `chat` | Conversation only. All tool use is disabled. No file access, no shell. |

**Override for one session without changing the file:**

```bash
GOOSE_MODE=approve goose session
GOOSE_MODE=smart_approve goose run --recipe .goose/recipes/orchestrator.yaml
```

**Set interactively:**

```bash
goose configure
# → "goose mode" → choose mode
```

---

## Per-Tool Permissions

Individual tools within an extension can be configured independently of the global mode.

**Set interactively:**

```bash
goose configure
# → "Tool Permission" → choose extension → choose tool → set permission
```

| Value | Behaviour |
|---|---|
| `always_allow` | Tool runs without prompting, regardless of `GOOSE_MODE` |
| `ask_before` | Always prompts before this tool runs, regardless of `GOOSE_MODE` |
| `never_allow` | Tool is blocked entirely |

Per-tool permissions take precedence over the session mode. Setting a tool to `never_allow` blocks it even when `GOOSE_MODE: auto`.

---

## Extension Enable / Disable

Extensions are the source of all Goose tools. Disabling an extension removes all of its tools.

```yaml
# ~/.config/goose/config.yaml
extensions:
  developer:
    enabled: true        # ← set to false to disable entirely
    type: builtin
    name: developer
    timeout: 300
    bundled: true

  memory:
    enabled: false       # disabled — no memory tools available
    type: builtin
    name: memory
    bundled: true
```

To disable the developer extension (no file read/write, no shell):

```yaml
extensions:
  developer:
    enabled: false
    type: builtin
    name: developer
    bundled: true
```

---

## Restricting Which Tools an Extension Exposes

The `available_tools` key is an allowlist of the tools an extension exposes. Goose enforces it at
dispatch: a withheld tool is not offered to the model, and a call to it fails with
`-32002: Tool '<name>' not found`. This was verified against Goose 1.37.0 with a stub model, for
builtin, platform and stdio extensions (`references/goose-tool-scoping-spike.md`).

Facts that change how you use it (Goose 1.37.0):

- **`developer` exposes exactly `write`, `edit`, `shell` and `tree`.** It has no `read_file` or
  `list_directory`, so an allowlist naming those matches nothing. The only `developer` tool that can
  print a file's contents is `shell`, which can also write and execute.
- **An empty list means unrestricted.** `available_tools: []` does **not** disable an extension. To
  switch one off, use a list that matches no tool, such as `[__none__]`. agentteams' recipe validator
  rejects `[]`.
- **`analyze` comes with `developer`.** Goose adds the `analyze` platform extension beside
  `developer` unless the recipe lists it, so list it with `[__none__]` to keep it off.
- **`tree` and `analyze` are not workspace-confined.** Both report structure (names, line counts,
  function names) for any path the user can read.
- **An allowlist fails closed.** A future Goose that renames a tool removes it; the agent never gains
  one.

```yaml
extensions:
  - type: builtin
    name: developer
    bundled: true
    timeout: 300
    available_tools: [write, edit, tree]   # a writer without shell
  - type: platform
    name: analyze
    available_tools: [__none__]            # off (an empty list would mean unrestricted)
```

---

## Common Privilege Profiles

### Read-only (no writes, no shell)

`developer` cannot be both read-only and able to read file contents. A read-only agent should drop
`developer` entirely and read through `agentteams_readfs`
(`scripts/goose-readfs-mcp.py`, or `.agentteams/bin/goose-readfs-mcp.py` under the orchestrator-only switch;
see the Goose runtime guide). It is a stdlib stdio server whose only
tools are `read_file`, `list_dir`, `find`, `grep` and `stat`. It has no write tool, is
realpath-confined to the workspace, and refuses secrets inside it.

```yaml
GOOSE_MODE: auto
extensions:
  - type: stdio
    name: agentteams_readfs
    cmd: python3
    args: ["-I", "-S", "scripts/goose-readfs-mcp.py", "--root", "."]
    timeout: 300
    available_tools: [read_file, list_dir, find, grep, stat]
```

agentteams emits this automatically when the brief sets `"goose_tool_scoping": "grant"`. Each recipe's
extensions are then derived from the agent's declared `tools:`:

| Declared | Goose grant |
|---|---|
| `read`, `search` | `agentteams_readfs` (all five read tools) |
| `edit` | `developer`: `write`, `edit`, `tree` |
| `execute` | `developer`: `shell`, `tree` |
| `agent` | `summon`: `delegate`, `load` (orchestrator); `load` (every other agent) |

Any recipe that has `developer` also lists `analyze` as `[__none__]`. Under `"legacy"`, the default,
every recipe keeps the whole `developer`. A merge keeps a recipe's on-disk `extensions:`, so switching an
existing team to `grant` needs a full re-render (`--update --overwrite`, under the usual clearance).

Grant recipes carry a `# agentteams-declared-tools: …` comment, which Goose ignores. `--post-audit`
compares what each marked recipe exposes with what those tools grant:

- **`AR_GOOSE_GRANT_EXCEEDED` (error):** extra `developer` tools, `summon` without `agent`, `analyze` left
  on, or any extension the declared tools don't grant (declared operator MCP servers and the
  coordination server excepted). It is keyed on declared tools, so it catches a hand-widened recipe on any
  agent.
- **`AR_GOOSE_MARKER_MISSING` (error, grant mode):** a recipe without exactly one marker. Bridge recipes
  are exempt.
- **`AR_GOOSE_READONLY_WRITE` (warning):** an unmarked recipe (legacy, bridge or hand-written) whose prose
  says read-only but which exposes `write`, `edit` or `shell`.
- **`AR_GOOSE_EXTENSIONS_FAIL_OPEN`:** a missing, bare or `null` `extensions`. It is an error in grant mode
  and a warning in legacy.

`--check` prints an advisory when the installed Goose differs from the version the tool map was verified
on (1.37.0). Re-run `scripts/goose-probe/` before relying on grant mode with another release.

Bridge recipes (`--bridge-from`) and CAI interop imports are outside grant scoping and keep the legacy
extensions.

### Delegated agents don't get their recipe's grant

The grant above applies when Goose runs a recipe directly (`goose run --recipe`). It does **not** apply
under `delegate` (biographies pilot 1, findings G1, G2 and G9; re-measured with
`scripts/goose-probe/run_delegate_case.sh`, `references/goose-tool-scoping-spike.md`):

- A delegate gets the **delegating session's** extensions. Goose ignores the sub-recipe's own
  `extensions:` and allowlists, and a `sub_recipes` entry has no field that could narrow them.
- `delegate`'s `extensions` argument narrows by extension name. Omitted, the delegate inherits everything,
  `developer` write, edit and shell included: a read-only auditor dispatched that way edited a deliverable.
  `[]` means no tools. A named extension keeps the delegator's allowlist, not the target's.
- `delegate` cannot add an extension the delegator lacks, so `agentteams_runner` and operator MCP servers
  never reach a delegate.
- A delegate never gets `delegate` itself, only `load`.

So in grant mode:

- The orchestrator recipe has a **Delegation narrowing (Goose)** table with the exact `extensions` argument
  for each `sub_recipes` target, and an instruction to pass it on every call. The argument names
  `agentteams_readfs` and `summon` whenever the target holds them, and `developer` only when the target holds
  all of it (write, edit, shell, tree). Any other part of the target's grant is listed as withheld, so that
  delegate only gets read-only work. An ad-hoc delegate, or a target the table doesn't list, gets
  `["agentteams_readfs"]`. If a target's declared tools can't be read at render time, it gets read-only.
- Work that needs a withheld grant (a partial `developer`, `agentteams_runner`, an MCP server) is not
  delegated. The orchestrator gives the operator `goose run --recipe .goose/recipes/<agent>.yaml` instead,
  and doesn't do the work with its own tools or start that run from its own shell. This is the conservative
  route: a direct run is the only Goose path where the recipe's own extensions apply, and the operator sees
  and starts it.
- Every recipe except the orchestrator's has `summon` limited to `load`. Those agents hand off with `load`,
  and a recipe run directly can no longer `delegate`.

The narrowing depends on the orchestrator passing the argument, and Goose doesn't enforce it: a delegate is
bounded by the orchestrator's own extensions, not by its recipe. The table also assumes those extensions are
the orchestrator recipe's. A plain `goose session` that adopts the orchestrator through `.goosehints` holds
your configured extensions instead: `agentteams_readfs` is usually missing (so read-only delegates get no
tools) and `developer` is usually unrestricted. Grant mode bounds nothing there, so start grant-mode teams
with `goose run --recipe .goose/recipes/orchestrator.yaml`. For a mechanical per-agent boundary, run the
agent's recipe directly. This is also why `write_policy: "orchestrator-only"` stays refused on Goose.

The stub-model runs started Goose in the project root, which the relative script path relies on.
Other entry points (Goose Desktop, `goose session` started elsewhere) have not been verified.

### Fully interactive (prompt before everything)

```yaml
GOOSE_MODE: approve
extensions:
  developer:
    enabled: true
    type: builtin
    name: developer
    timeout: 300
    bundled: true
```

### Fully autonomous (no prompts) — use with care

> **Caution — use with care.** `GOOSE_MODE: auto` runs edits, shell commands, and extension calls immediately with no confirmation. This profile is not a safety boundary; reserve it for throwaway/sandboxed workspaces. To constrain it, add `never_allow` on dangerous tools (e.g. `shell`) or trim `available_tools`.

```yaml
GOOSE_MODE: auto
extensions:
  developer:
    enabled: true
    type: builtin
    name: developer
    timeout: 300
    bundled: true
```

### Chat only (no tools)

```yaml
GOOSE_MODE: chat
```

### Shell gated (prompts before shell), file access permitted

Set `GOOSE_MODE: smart_approve` (which *prompts* before shell commands — it does not block them; the user can still approve any command). To actually *block* shell, use `available_tools` to exclude shell tools explicitly, or set `never_allow` on `shell`.

---

## OS-Enforced Confinement (macOS) — the real boundary

Everything above (`GOOSE_MODE`, per-tool permissions, `available_tools`) is an **in-process
cooperative control**: it decides *whether a tool runs*, not what the Goose process may
touch. Goose runs with your full user privileges and does not confine itself — its own
`SECURITY.md` recommends running it in a VM/container. Those controls are **not OS
boundaries**.

On **macOS**, agentteams can emit a real, kernel-enforced boundary using Apple Seatbelt.
When a team selects `privilege_profile: confined` (or `exclusive`) on the `goose`
framework, `generate` emits two inert artifacts into the project's `.goose/` directory:

- **`.goose/sandbox.sb`** — an Apple-Seatbelt profile that:
  - `deny file-write*` everywhere **except** the `workspace_write_roots` (default `["."]`,
    the project tree) — writes outside are kernel-denied — and re-`deny file-write*` the
    control-plane files (the agent may not edit its own gate/switch/profile);
  - **network:** the default `confined` profile leaves egress **open** (write-confinement
    only, matching Claude confined) so Goose can reach its LLM out of the box. Network
    isolation is an **`exclusive`**-only property: `exclusive` emits `deny network*` and
    re-allows one sanctioned **loopback** endpoint via `goose_egress_proxy` (`localhost:PORT`
    — Seatbelt `remote ip` accepts only `localhost`/`*`); absent a proxy, `exclusive` is
    fully network-isolated;
  - for `exclusive`, additionally `deny file-read*` of a curated secret set (`~/.ssh`,
    `~/.aws`, `~/.gnupg`, `~/.kube`, `~/.config/gcloud`, `~/.azure`) **plus** any
    `protected_read_paths` (e.g. sibling agent scratch roots).
- **`.goose/config.yaml.agentteams.example`** — an **inert example** carrying
  `GOOSE_SANDBOX: 1` and wiring notes. agentteams **never** writes your live
  `~/.config/goose/config.yaml` (clobbering operator config is a worse failure than an
  unwired boundary); you merge the snippet yourself.

**Two enforcement paths (macOS):**

1. **Ground truth (recommended, always enforces):** launch Goose under `sandbox-exec`,
   which applies the profile at the kernel regardless of Goose build:
   ```bash
   sandbox-exec -D WORKSPACE_ROOT="$PWD" -D HOME_DIR="$HOME" \
                -f .goose/sandbox.sb goose run --recipe .goose/recipes/orchestrator.yaml ...
   ```
2. **Goose-native (`GOOSE_SANDBOX`):** convenience, but its presence and semantics **vary
   by Goose build/version** (Desktop vs CLI differ; older builds may ignore it). agentteams
   does **not** claim this path enforces anything until you confirm it — prefer path 1 when
   in doubt (fail closed, do not assume Desktop behavior).

**Verify — never trust a config that only *looks* protective:**

```bash
agentteams generate ... --check-wiring   # checks GOOSE_SANDBOX live + profile + write roots
```

Then test by hand from inside the sandbox: a write **outside** `workspace_write_roots` MUST
be denied. For an **`exclusive`** team, a raw non-proxied network egress MUST also be denied;
under `confined`, egress is **open** by design (do not expect it to be denied). If the write
succeeds (or, for `exclusive`, egress succeeds), the boundary is not in effect on your build —
use path 1.

**Honest limits:**

- **macOS only.** Goose has **no native OS sandbox on Linux or Windows**. There a
  `confined`/`exclusive` goose team **fails closed** (or degrades to a visible advisory with
  `--allow-unenforced-confinement`) — agentteams never claims a boundary it cannot emit.
  Confine from **outside** the process instead: a container plus **seccomp-bpf + Landlock**
  (Linux) and **egress filtering**.
- **Apple deprecation.** `sandbox-exec`/Seatbelt is Apple-deprecated (App Sandbox
  preferred). It is the best OS boundary agentteams can emit for Goose today; the portable
  primary for untrusted code remains a **container / WASI** at the consumer layer.
- **Egress residual.** If you configure a sanctioned egress proxy (`goose_egress_proxy`),
  the profile re-allows that one endpoint. That endpoint (the LLM API/proxy) is a
  **bidirectional** channel — agentteams cannot close data exfiltration *through* it; bound
  it with the proxy's own content/rate controls. Do not read the allow as "exfiltration
  closed".
- **Denies files, not env vars.** A secret already exported into the agent's environment is
  not covered by a filesystem read-deny.

---

## Applying Changes

Changes to `config.yaml` take effect on the next session start. Running sessions are not affected. To apply immediately:

1. Save `~/.config/goose/config.yaml`
2. Start a new session: `goose session`

The VS Code extension reads the same config file and picks up changes on the next session.

---

## Verification

```bash
goose info --verbose   # shows GOOSE_MODE and extension status
goose doctor           # connectivity and config health check
```

Inside a session: the mode is shown at session start and can be confirmed with the first tool prompt (or absence of one).
