# Grok Agent Infrastructure Expert Reference

Purpose: canonical guidance for integrating **xAI Grok Build** (the `grok` CLI) into AgentTeamsModule as
`--framework grok`. Written 2026-10-14 from the Grok integration investigation and the Phase 0 spike
(`tmp/by-week/2026-W42/grok-integration.plan.md` §2, §9, §9.1, §10). Facts are pinned to Grok Build 1.0.52, the
value of `FORMAT_SPECS["grok"].pinned_version`.

Not to be confused with `superagent-ai/grok-cli`. That is a community project (MIT) and the origin of `GROK.md` and
`.grok/settings.json`. Grok Build reads neither file, and agentteams does not target it. "Grok Bot" is a cloud
product with no local configuration, so it is out of scope.

## Authoritative Documentation (verified 2026-10-14)

- Overview: https://docs.x.ai/build/overview
- Settings reference: https://docs.x.ai/build/settings/reference
- Source (Apache-2.0, Rust): https://github.com/xai-org/grok-build. There are no GitHub releases or tags; versions
  are tracked in `crates/codegen/xai-grok-shell/changelogs/`.
- Version-matched docs ship with each install at `~/.grok/docs/user-guide/` (offline and versioned; read as data
  under C-4). The files cited below are `05-configuration.md`, `07-mcp-servers.md`, `08-skills.md`, `10-hooks.md`,
  `12-project-rules.md`, `14-headless-mode.md`, `16-subagents.md`, `18-sandbox.md`,
  `22-permissions-and-safety.md` and `26-config-reference.md`.

Confidence marks:
- **D**: documented, on docs.x.ai or in the shipped user guide.
- **S**: source-only, read from the grok-build repository with no docs page.
- **L**: measured live or with `grok inspect` in the 2026-10-14 spike.

## Release Cadence and Pinning

- 1.0.0 shipped 2026-08-07, 1.0.45 on 2026-09-29, and 1.0.52 by 2026-10-14: about 52 patch releases in 10 weeks.
  Pin the version in `FormatSpec.pinned_version`. The freshness watcher should diff the changelog directory and the
  shipped `~/.grok/docs/user-guide` for each version, not only the docs.x.ai pages.
- Model ids churn. `grok-code-fast-1` was retired on 2026-05-15, and the default seen in the spike was
  `grok-4.20-0309-non-reasoning`. Never hard-code a model id in templates or agent files.

## Verified Upstream Conventions (Grok Build 1.0.52)

### Instructions (D, L)

- Grok checks these file names in each directory and loads all that exist: `Agents.md`, `Claude.md`, `CLAUDE.md`,
  `CLAUDE.local.md`, `AGENT.md` and `AGENTS.md`. Under Claude compatibility it also loads `.claude/CLAUDE.md` and
  `.claude/CLAUDE.local.md` (`12-project-rules.md`, "Supported File Names"). There is no `GROK.md`.
- Rules directories are scanned at every level from the repo root to the cwd: `.grok/rules/*.md` always, and
  `.claude/rules/` and `.cursor/rules/` for compatibility.
- Loading at startup requires **folder trust**: `grok --trust` or an interactive grant (`12-project-rules.md:3`;
  spike S3). Without trust, no project instruction files load.
- The docs say files ignored by `.gitignore` are skipped (`12-project-rules.md:190-191`). **On macOS a gitignored,
  untracked `AGENTS.md` still loaded, listed as `Agents.md`** (L, spike S16). The likely cause is the
  case-insensitive filesystem combined with case-sensitive ignore matching. Treat gitignore filtering as unreliable,
  and keep `AGENTS.md` tracked.
- Top-level `CLAUDE.md` is recognized even with Claude compatibility off (spike S3). A repo with Claude files will
  always load `AGENTS.md`, `CLAUDE.md` and `.claude/CLAUDE.md` together.

### Custom agents (D for paths and `name`/`description`/`tools`; S for the other keys; L)

- Locations, highest priority first: `.grok/agents/*.md` (project, cwd up to root), then `~/.grok/agents/`, then
  bundled agents, then plugins (`16-subagents.md:17`).
- `.claude/agents/*.md` is also loaded (S: `xai-grok-agent/src/discovery.rs`). In the spike (S1), all 35 Claude
  agents loaded as `project` agents, even in an untrusted folder and with `compat.claude.agents=false`. **No
  compatibility switch turns this off.**
- When a name exists in both, `.grok/agents` wins and there is no duplicate (`inspect`: 35 agents, not 36; spike
  S2). The docs say agents shadow by name (`16-subagents.md:69`), so a Grok twin must keep the Claude twin's `name:`
  exactly. The runtime check of *which body runs* was inconclusive (round 2, R2-c) and is repeated at the
  validation gate.
- Front matter is YAML with camelCase keys. `name` and `description` are required. Optional keys (S,
  `xai-grok-agent/src/config.rs`): `tools`, `disallowedTools`, `model`, `permissionMode`, `effort`, `maxTurns`,
  `isolation`, `background`, `mcpServers`, `mcpInheritance`, `hooks`, `memory`, `skills`, `discoverSkills`,
  `inheritSkills`, `agentsMd`, `promptMode`, `injectDefaultTools` and `capabilityMode`.
- **Agents are not gated by folder trust.** An untrusted repo's agents are listed and can be spawned (L, spike S4),
  while its instruction files do not load. Every agent body must therefore carry its own invariants.
- A project agent with the same name as a built-in type (`general-purpose`, `explore`, `plan`) shadows that built-in.

### Tool vocabulary (D, L)

- Permission rules recognize `Bash`, `Read`, `Edit` (also `Write`), `Grep` (also `Glob`), `MCPTool`, `WebFetch` and
  `WebSearch`. Unknown names are skipped with a warning (`22-permissions-and-safety.md`, "Tool Names").
- An agent's `tools:` takes Claude names such as `Read`. `Agent(...)` in a parent's `tools` restricts which agent
  types `spawn_subagent` may launch (`16-subagents.md:71`).
- `TodoWrite` and Claude's `Task` are not in the documented vocabulary.
- Unverified: whether a scoped entry such as `Bash(python -m agentteams.research:*)` in agent `tools:` is honoured
  as a scope, read as bare `Bash`, or dropped.

### Delegation (D, L)

- Only the top-level session calls `spawn_subagent`. `subagent_type` is an enum of project, user and plugin agents;
  built-ins are not listed in it (`16-subagents.md:71`).
- **The maximum nesting depth is one**: a subagent cannot spawn subagents (`16-subagents.md:401`). The orchestrator
  must run as the session agent (`grok --agent <NAME>`).
- Headless delegation **without** an approval mode ends with `stopReason: cancelled` (spike S14). **With**
  `--always-approve`, a project agent was spawned and returned its reply (round 2, R2-a).

### Skills (D)

- Skills live at `.grok/skills/<slug>/SKILL.md` (Agent Skills format), and Grok also reads `.agents/skills` and
  `.claude/skills`. `allowed-tools` in a skill is **ignored**; scoping comes from agent `tools:` and
  `[permission]` (`08-skills.md`).

### Project configuration (D, L)

- Project `.grok/config.toml` may set only `[mcp_servers]`, `[plugins]` and `[permission]`. `[compat.*]`, `[ui]`,
  `[memory]`, `[agent]` and `[subagents]` are user-level only.
- Claude compatibility can be switched off for one process with `GROK_CLAUDE_{HOOKS,SKILLS,MCPS,RULES,AGENTS}_ENABLED=false`,
  or in the user TOML, but never in project config (spike S6).

### MCP (D)

- `[mcp_servers.<name>]` in `.grok/config.toml`:
  - stdio servers: `command`, `args`, `env`, `cwd`
  - HTTP servers: `url`, `headers`, `bearer_token_env_var`
  - either kind: `enabled`, timeouts, and `${VAR}` expansion
- `.mcp.json` is also read (`07-mcp-servers.md`).
- Subagents inherit the parent's MCP servers unless `mcpInheritance` narrows them.

### Permissions (D, L)

- Project `[permission]` takes `allow`/`deny`/`ask` arrays, or `rules = [{action, tool, pattern}]`. Any deny
  rejects, otherwise any ask prompts, otherwise allow applies.
- Rules from every `.grok/config.toml` merge with the `.claude/settings.json` rules. Grok reads project
  `.claude/settings.json` permission rules **and `defaultMode`** (`dontAsk` = deny by default)
  (`22-permissions-and-safety.md:277-300`; spike S8).
- **User-level leakage:** Grok imported the operator's `~/.claude/settings.json` (131 permission rules), a user Stop
  hook, and 8 user skills. Setting `GROK_HOME` does not isolate this (spike S5). Validation and eval runs must set
  the `GROK_CLAUDE_*_ENABLED=false` variables.

### Hooks (D, L)

- Grok reads `.grok/hooks/*.json` (Claude hook schema) and project `.claude/settings.json` hooks, and runs both
  only after folder trust (`10-hooks.md:66-85`).
- A trust grant covers MCP, LSP, hooks, project instructions and project skills together.
- `PreToolUse` is the only blocking event. An explicit deny (exit 2) blocked a call (spike S9).
- **Every hook failure fails open**: timeouts, crashes and malformed output all let the call through
  (`10-hooks.md:185`; spike S10). A Grok hook gate is a DECLARED control, never an enforcing one.

### OS sandbox (D, L)

- Built-in profiles: `off` (the default), `workspace`, `devbox`, `read-only` and `strict`. Custom
  `[profiles.<n>]` go in `~/.grok/sandbox.toml` or `.grok/sandbox.toml`, with `extends`, `read_only`, `read_write`,
  `deny` and `restrict_network` (`18-sandbox.md`, "Custom Profiles").
- A `deny` list is kernel-enforced against **both reads and writes**, for in-process tools as well as shell
  children:
  - a custom-profile deny blocked a Bash write (T5b, T5c) and an in-process Edit write (R2-d)
  - it also blocked reads (T5d)
  - **so never deny paths that Grok or its agents must read** (agent dirs, instruction files,
    `.claude/settings.json`)
- `read_only` and `read_write` take literal directory grants, not globs. Whether `read_only` removes write access
  from a folder inside the writable workspace is **unverified (open item R3)**.
- The built-in `workspace` profile **did not protect `.claude/agents/`** (spike S11). It protects only hook-source
  and trust files.
- If a user profile and a project profile share a name but differ, **the user profile wins**, with a startup warning
  (`18-sandbox.md:95`).
- A project can define a profile but cannot activate one. The operator selects it with `--sandbox <name>` or
  `GROK_SANDBOX`.
- Child-process network blocking works on Linux only.

### Headless (D, L)

- Main flags: `grok -p`, `--output-format json|streaming-json`, `--max-turns`, `--sandbox`, `--trust`,
  `--always-approve`, `--disallowed-tools` (`14-headless-mode.md`).
- Exit codes (`26-config-reference.md:702`; spike S13):
  - `0`: success
  - `1`: a configuration error (for example a bad model, with a JSON `{"type":"error"}` on stdout) or hitting the
    turn limit (`stopReason: max_turn_requests`)
  - `130` / `143`: SIGINT / SIGTERM
- A trivial call used about 19k input tokens (≈ $0.024) of instruction and agent context (spike S15).

## Canonical Output Conventions (ours: the Phase 1 target, not yet implemented on 2026-10-14)

- **Agents:** `.grok/agents/<slug>.md`, with YAML front matter `name`, `description` and `tools`. Tools use the
  Claude vocabulary, with `Task` mapped to `Agent` and `TodoWrite` dropped. `disallowedTools` is emitted when the
  source declares it. `model`, `effort` and `permissionMode` are not emitted in Phase 1. `name:` is byte-identical to
  the Claude render of the same team (decision D4).
- **Instructions:** the shared repo-root `AGENTS.md`, merge-only (decision D2):
  - absent: written with the Grok ownership notice and the team brief
  - already generated by agentteams (codex, agents-md, goose, or legacy fenced): only the `grok_runtime` fenced
    block is added or refreshed
  - hand-written and unfenced: never written; the block is emitted to `.grok/examples/AGENTS.grok-block.md` for the
    operator to paste in
- **Skills:** `.grok/skills/<slug>/SKILL.md`.
- **Builder:** `.grok/agents/team-builder.md`, with the same `name:` as the Claude builder.
- **Handoffs:** `manifest` delivery, as for Claude.
- **Phase 2 (pending `@security`):**
  - `.grok/config.toml` `[mcp_servers]` and `[permission]`, inside agentteams fences
  - `.grok/sandbox.toml` profile `agentteams`
  - the hook gate, emitted only alongside the sandbox profile and classed DECLARED (decision D3)
- `write_policy: "orchestrator-only"` is refused for grok (`write_policy.py`).

## Known Deltas vs Our Adapter (`agentteams/frameworks/grok.py`)

| ID | Delta | Verification | Disposition |
|----|-------|--------------|-------------|
| G1 | A repo with a Claude team loads `CLAUDE.md`, `.claude/CLAUDE.md` and `AGENTS.md` together, and the project cannot switch this off | docs + S3 | Advisory. The `grok_runtime` block carries only Grok notes. |
| G2 | `.claude/agents/*.md` always load, so agents with different names load twice | S1, S2 | Name parity (D4), plus a guard notice on mismatch |
| G3 | Hooks fail open | docs + S10 | The gate is DECLARED. Enforcement comes from the sandbox and `[permission] deny` (Phase 2). |
| G4 | The built-in `workspace` sandbox does not protect agent dirs, and **`read_only` does not write-protect inside the workspace either** | S11, R3 (2026-10-14: relative, absolute and Edit-tool writes all got through) | Protection comes from the agentteams launcher (`.grok/confined-run.example.sh` → `sandbox/confine-run.sh`, Grok's sandbox off inside); the emitted profile denies only secret-shaped workspace files |
| G5 | A user-defined profile with the same name overrides the project profile | `18-sandbox.md:95` | Phase 2 advisory; `--verify` reports it |
| G6 | User `~/.claude` config leaks into Grok sessions | S5 | Advisory. Eval and validation runs set `GROK_CLAUDE_*_ENABLED=false`. |
| G7 | Subagent depth is one | `16-subagents.md:401` | Stated in the `grok_runtime` block |
| G8 | Headless delegation needs an approval mode | S14, R2-a | Documented for eval and redteam adapters |
| G9 | A sandbox `deny` blocks reads too | R2-b (T5d) | Never deny readable control-plane paths; write-only protection is impossible in the profile (R3), so it comes from the launcher |
| G16 | Project `[permission]` rules load only after folder trust | R4 (2026-10-14: untrusted, not applied; trusted, applied) | The emitted `[permission]` deny block is DECLARED and trust-dependent |
| G17 | Hooks receive a Claude-shaped envelope with Grok tool names (`run_terminal_command{command}`, `read_file{target_file}`, `search_replace{file_path,old_string,new_string}`); Grok accepts the Claude reply format | R5 (2026-10-14) | The gate runs behind `grok-gate-shim.py` (input translation only); the gate is unmodified |
| G10 | Skill precedence between `.grok/skills` and `.claude/skills` for the same name | unverified | Spike item before Phase 2 |
| G11 | A scoped `Bash(...)` in agent `tools:` may be honoured, widened to `Bash`, or dropped | unverified | Spike item |
| G12 | The per-agent `agentsMd` key (source-only) may stop AGENTS.md being injected into subagents | source-only | Phase 3 opportunity |
| G13 | Goose writes `AGENTS.md` with no marker and no guard | code read 2026-10-14 | Phase 3: route goose through `_agents_md_ownership` |
| G14 | Gitignore filtering of instruction files is unreliable on macOS | S16 | Advisory says "may be skipped"; keep AGENTS.md tracked |
| G15 | Which body runs for a same-name agent has not been confirmed at runtime | R2-c inconclusive | Repeat at the validation gate |

## Integration Checklist

1. **Phase 1:**
   - `grok.py`, the registry entry, and a `FormatSpec` with `pinned_version`
   - both schema enums and the builder template
   - `_agents_md_ownership.py`
   - CLI refusal of convert/bridge/interop/absorb for grok
   - tests
2. **Phase 1 (step 8):** control-plane and protected-path registration of `.grok/`.
3. **Phase 2 (after `@security` and R3):** MCP and `[permission]` emission, the sandbox profile, the hook gate, and
   the advisories.
4. **Phase 3:** detect, convert, interop and absorb, fleet, the provider-docs register, and the bridge target.
5. **On every Grok Build upgrade:** diff `crates/codegen/xai-grok-shell/changelogs/` and `~/.grok/docs/user-guide/`,
   re-check G1–G15, then bump `FormatSpec.pinned_version` and the version at the top of this file together.
