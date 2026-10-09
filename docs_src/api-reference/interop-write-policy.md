# `interop_write_policy` — AgentTeamsModule

Carries the target brief's `write_policy` and `mcp_grants` into an interop import (`--interop-from DIR --framework
claude|goose --description BRIEF`). Adopted bespoke agents reach Claude and Goose through interop, and without these
fields a granted agent got no `agentteams_runner` block or tools, while the orchestrator's queue refused it.

> Source: `agentteams/interop_write_policy.py`. Called by `interop.import_from_cai` (`write_policy_fields=`) and the
> CLI. The policy itself is resolved by `write_policy.resolve`, which native generation (`analyze.build_manifest`)
> also uses, so both scope and refuse the switch identically.

## What an import under the switch does

- **Same checks as native generation.**
  - `write_policy.resolve` applies `write_policy_frameworks`, refuses legacy Goose scoping, and requires an explicit
    `privilege_profile` of `confined` or `exclusive`.
  - `mcp_grants` go through the runner's own validator.
  - Only `claude` and `goose` imports are accepted. Codex holds the switch only through its launcher, which interop
    doesn't emit.
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

Without `--description`, or with a brief that doesn't turn the switch on, an import is unchanged.

## Functions

### `manifest_fields(description: dict, framework: str) -> dict`

The fields native generation would set: `write_policy`, `mcp_grants` when set, and `goose_tool_scoping: "grant"` for
goose. Returns `{}` when the switch is off for this framework. **Raises:** `ValueError` for any `write_policy.resolve`
refusal, a framework other than claude/goose under the switch, or malformed `mcp_grants`.

### `prepare(manifest: dict, fields: dict | None, framework: str, target_dir: Path, *, preserve_existing: bool) -> None`

Merges the brief's fields into the interop manifest (last, so nothing captured overrides them), and checks the server's
install location before any agent is written. **Raises:** `ValueError` for an install outside the project, or
`preserve_existing` under the switch.

### `left_in_place(dest: Path, manifest: dict) -> list[str]`

The error for an existing agent file an import didn't replace, under the switch.

### `check_rendered(slug: str, name: str, rendered: str, framework: str, manifest: dict) -> None`

Raises `ValueError` when a restricted agent's rendered file fails `AR_WRITE_POLICY` (see `audit_problems`).

### `restricted(slug: str, manifest: dict, framework: str) -> bool`

True when the switch is on and the agent isn't this framework's orchestrator. **Raises:** `ValueError` for a
`bridge-orchestrator` imported to a framework other than goose (it writes only as Goose's bridge entry recipe; on
Claude it would be a second writer the audit doesn't exempt).

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
