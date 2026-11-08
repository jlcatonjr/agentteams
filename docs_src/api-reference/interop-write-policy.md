# `interop_write_policy` — AgentTeamsModule

> **Goose is refused under `write_policy: "orchestrator-only"`** (biographies pilot 1, finding G1, 2026-10-12): Goose 1.37 delegates inherit the orchestrator's `write`/`edit`/`shell` extensions instead of their sub-recipe's narrowed `extensions:`, so the policy can't be enforced there. `write_policy.resolve` and `--check-wiring` refuse it; use the switch on Claude (or Codex through its launcher).

Carries the target brief's `write_policy` and `mcp_grants` into an interop import (`--interop-from DIR --framework
claude|goose|codex --description BRIEF`). Adopted bespoke agents reach Claude, Goose and Codex through interop, and
without these fields a granted agent got no `agentteams_runner` block or tools, while the orchestrator's queue refused
it.

> Source: `agentteams/interop_write_policy.py`. Called by `interop.import_from_cai` (`write_policy_fields=`) and the
> CLI. The policy itself is resolved by `write_policy.resolve`, which native generation (`analyze.build_manifest`)
> also uses, so both scope and refuse the switch identically.

## What an import under the switch does

- **Same checks as native generation.**
  - `write_policy.resolve` applies `write_policy_frameworks`, refuses legacy Goose scoping, and requires an explicit
    `privilege_profile` of `confined` or `exclusive`.
  - `mcp_grants` go through the runner's own validator.
  - `claude`, `goose` and `codex` imports are accepted (`SUPPORTED`). A `codex` import has extra conditions; see
    below.
- **Every non-orchestrator agent is narrowed.** Its declared tool scopes are cut to `read`/`search`/`todo` (always
  `read`), the rule `write_policy.narrow_tools` applies. Goose imports declare those tools too, because grant-mode
  recipes derive their extensions from them.
- **Forbidden capability keys are withheld.** The keys `AR_WRITE_POLICY` errors on (`mcpServers`, `hooks`,
  `skills`, `memory`, ..., and a `permissionMode` that relaxes checks) aren't restored from the source, and a notice
  says so. The adapter emits the one canonical runner block.
- **Every agent gets its write-policy section once** (`write_policy.ensure_section`).
  - A source already rendered with `write_policy.apply` keeps its section, with no second copy.
  - A source without one gets it appended.
  - A source with a *different* section, or with more than one, is refused: for example, the proposals section on an agent the brief now
    grants. Re-render the source with the current brief first.
- **Granted agents get the runner**, exactly as native generation renders it (see [`runner_mcp`](runner-mcp.md)).
- **Each restricted agent's rendered file must pass `AR_WRITE_POLICY`** (`audit_problems`), or the import refuses.
  This backstops anything a source could smuggle into the front matter.
- **Every agent is re-rendered.** An existing agent file left in place (no `--overwrite`) is an error, since it may
  still hold full tools, and `preserve_existing` (pinned sync) is refused under the switch.
- **The pinned server is installed** in `.agentteams/bin/`. The agents directory must be the project's
  `.claude/agents` or `.goose/recipes`; this is checked before any agent is written.

### Codex imports (since 2026-10-27)

Codex ignores a spawned agent's `sandbox_mode` and tool list. On Codex the switch holds only through agentteams'
launcher (`.codex/confined-run.example.sh`) and the role gate it runs as a PreToolUse hook. The gate denies every
spawned agent's writes by its runtime `agent_type`, whatever the agent file says. Interop emits neither file, so:

- **The import needs a native Codex team rendered under the switch** (`require_native_codex_gate`). All of these
  must hold, and every missing one is named before any agent is written:
  - the target is the project's `.codex/agents`;
  - its `references/build-log.json` records `write_policy: "orchestrator-only"`;
  - `.agentteams/bin/codex-role-gate.py` matches `CODEX_ROLE_GATE_SHA256`;
  - `.codex/hooks.json` is agentteams' role-gate wiring;
  - the launcher exists.
- **`mcp_grants` are kept, as native Codex generation keeps them, but not honoured.** A source rendered with them
  keeps its matching write-policy section. On Codex the role gate allows spawned agents only the read-only
  `agentteams_readfs` tools, so a granted agent is read-only there; a notice names it. Grants hold on Claude.
- **`privilege_profile` is carried**, so the imported agents get the role-gate reading instructions native agents
  get.
- **The import never replaces the native team.**
  - An imported slug that matches a native agent is an error (`native_agent`). Native agents are those in the
    build-log's `agent_slug_list` or its generated `.toml` files, plus `orchestrator` always, compared
    case-insensitively. A build-log with no `agent_slug_list` is refused.
  - `AGENTS.md` is never written, even when it is missing (`keeps_native_instructions`).
  - A notice says to route to the imported agents in `AGENTS.md`'s editable section, and that a later native
    `--update` lists them as orphans; keep them with `--adopt-orphans` (`codex_import_notices`).

Without `--description`, or with a brief that doesn't turn the switch on, an import is unchanged.

## Functions

### `manifest_fields(description: dict, framework: str) -> dict`

The fields native generation would set: `write_policy`, `mcp_grants` when set, `goose_tool_scoping: "grant"` for
goose, and `privilege_profile` for codex. Returns `{}` when the switch is off for this framework. **Raises:**
`ValueError` for any `write_policy.resolve` refusal, a framework outside `SUPPORTED` under the switch, or malformed
`mcp_grants`.

### `prepare(manifest: dict, fields: dict | None, framework: str, target_dir: Path, *, preserve_existing: bool) -> None`

Merges the brief's fields into the interop manifest (last, so nothing captured overrides them), and checks the server's
install location before any agent is written. For codex under the switch it also runs `require_native_codex_gate`.
**Raises:** `ValueError` for an install outside the project, `preserve_existing` under the switch, or a codex target
without its native gate.

### `require_native_codex_gate(target_dir: Path) -> list[str]`

Checks the preconditions above and returns the native agents' slugs (roster, generated `.toml` stems and
`orchestrator`). **Raises:** `ValueError` naming every missing precondition, or for a build-log with no
`agent_slug_list`.

### `native_agent(slug: str, dest: Path, manifest: dict) -> list[str]`

The error for an imported agent that would replace a native Codex agent, under the switch.

### `keeps_native_instructions(framework: str, manifest: dict, inst_dest: Path) -> bool`

True for a codex import under the switch: the import never writes `AGENTS.md`, which is the native team's.

### `codex_import_notices(manifest: dict, imported: list[str]) -> list[str]`

The notices a codex import under the switch leaves for the operator: imported agents render read-only and need routing
in `AGENTS.md`, a later native `--update` flags them as orphans, and any `mcp_grants` for them aren't honoured on
Codex.

### `left_in_place(dest: Path, manifest: dict) -> list[str]`

The error for an existing agent file an import didn't replace, under the switch.

### `check_rendered(slug: str, name: str, rendered: str, framework: str, manifest: dict) -> None`

Raises `ValueError` when a restricted agent's rendered file fails `AR_WRITE_POLICY` (see `audit_problems`).

### `restricted(slug: str, manifest: dict, framework: str) -> bool`

True when the switch is on and the agent isn't this framework's writer: `orchestrator`, plus `bridge-orchestrator`
on Goose, where it is the bridge entry recipe. Elsewhere `bridge-orchestrator` is narrowed and audited like any
agent, for example the read-only one a Copilot bridge emits.

### `withheld(key: str, value: Any) -> bool`

Whether a restricted agent's captured front-matter key must not be restored.

### `audit_problems(name: str, rendered: str, framework: str, manifest: dict) -> list[str]`

`AR_WRITE_POLICY` error descriptions for one rendered restricted agent. Empty when it passes.

### `install_server(manifest: dict, framework: str, target_dir: Path, *, dry_run: bool) -> list[str]`

Install the pinned `agentteams_runner` server when an agent is granted, and return its path. **Raises:**
`ValueError` when `target_dir` isn't the canonical agents directory, or when the packaged server fails its pin.

## Example

```bash
agentteams --interop-from .github/agents --interop-source-framework copilot-vscode \
  --framework claude --output .claude/agents --description brief.json --overwrite
```

## Convert and bridge refusal

`--convert-from` and bridge subagent stubs apply none of the above, so they refuse a team under the switch.

### `target_under_switch(agents_dir: Path) -> bool`

True when the team's `references/build-log.json` records `write_policy: "orchestrator-only"`. Native generation
writes that field only under the switch. A missing build-log reads as off. A build-log that exists but can't be
read, or isn't a JSON object, reads as **on**: it fails closed.

### `refuse_outside_interop(mode: str, framework: str, agents_dir: Path, description: dict | None = None) -> None`

**Raises:** `ValueError` when the target's
build-log records the switch, or when `description` turns it on for `framework` (including `write_policy.resolve`'s
own refusals). For a framework in `SUPPORTED`, the message names the `--interop-from … --description … --overwrite`
command; for codex it adds that the import goes into a native Codex team rendered under the switch. For any other
framework it says to regenerate natively with `--description`.

### `refuse_bridge_stubs(source_framework: str, framework: str, output_root: Path, host_features: list[str], description: dict | None = None) -> None`

Calls `refuse_outside_interop` when the bridge's subagent-stub feature for a Claude or Goose target is requested.

