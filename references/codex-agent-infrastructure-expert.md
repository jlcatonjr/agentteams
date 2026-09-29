# Codex Agent Infrastructure Expert Reference

Purpose: Canonical guidance for integrating the OpenAI **Codex CLI** into
AgentTeamsModule. Authored 2026-08-15 (agent-doc-optimal-structure plan; closes
the codex gap in the per-framework expert-reference set — parity report R6).

## Authoritative Documentation (verified live 2026-08-15)

Codex docs relocated: `developers.openai.com/codex` 308-redirects to
learn.chatgpt.com ("ChatGPT Learn"). Update emitted links accordingly.

- AGENTS.md configuration: https://learn.chatgpt.com/docs/agent-configuration/agents-md
- MCP (CLI surface): https://learn.chatgpt.com/docs/extend/mcp?surface=cli
- Advanced config: https://learn.chatgpt.com/docs/config-file/config-advanced
- Custom prompts (deprecated in favor of skills): https://learn.chatgpt.com/docs/custom-prompts
- Skills & plugins: https://learn.chatgpt.com/docs/skills-and-plugins
- Custom agents / subagents (`.md` twin, verified 2026-09-29): https://learn.chatgpt.com/docs/agent-configuration/subagents.md
- Build skills (`.md` twin, verified 2026-09-29): https://learn.chatgpt.com/docs/build-skills.md

The HTML pages are client-rendered (a text fetch yields navigation only); read the
`.md` twins. The freshness watcher (`FORMAT_SPECS["codex"]`) fetches the agents-md,
subagents and build-skills twins together.

## Verified Upstream Conventions (2026-08-15)

- **AGENTS.md discovery.** Global: `~/.codex/AGENTS.override.md` first, then
  `~/.codex/AGENTS.md` (first non-empty only). Project: from git root down to
  cwd, each level checked `AGENTS.override.md` → `AGENTS.md` →
  `project_doc_fallback_filenames` (e.g. CLAUDE.md). Files merge root-downward;
  closer files override by appearing later. Combined cap 32 KiB
  (`project_doc_max_bytes`) — docs advise raising it or splitting across nested
  dirs.
- **MCP.** `[mcp_servers.<name>]` in config.toml. STDIO: `command` (required),
  `args`, `env`, `cwd`, `experimental_environment`. Streamable HTTP: `url`,
  `bearer_token_env_var`, `auth` (oauth|chatgpt), `http_headers`/`env_http_headers`.
  Both: `enabled`, `startup_timeout_sec` (10), `tool_timeout_sec` (60),
  `enabled_tools`/`disabled_tools`. CLI: `codex mcp add/list/login`. Config
  shared by CLI, IDE extension, and ChatGPT desktop.
- **Project config.** `.codex/config.toml` still documented; layering: system
  defaults → `~/.codex/config.toml` → project `.codex/config.toml` (**loaded
  only when the project is trusted**) → CLI overrides.
- **Profiles.** `[profiles.<name>]` in config.toml deprecated as of v0.134.0;
  current form is `~/.codex/profile-name.config.toml` + `--profile`.
- **Skills** (directory with SKILL.md + YAML front matter `name`/`description`,
  optional scripts/references) are the current customization surface; custom
  prompts (`~/.codex/prompts/*.md`) exist but are deprecated in favor of skills.

## Verified Upstream Conventions — Custom Agents (2026-09-29)

- **Location and keys.** Project custom agents are standalone TOML files in
  `.codex/agents/` (personal: `~/.codex/agents/`). Required: `name`, `description`,
  `developer_instructions`. Any other `config.toml` key may be set (`model`,
  `model_reasoning_effort`, `sandbox_mode`, `mcp_servers`, `skills.config`); keys a
  file omits inherit from the parent session. The docs warn the format "may evolve".
- **`name` is authoritative** over the file name (docs). openai/codex
  `codex-rs/agent-roles/src/agent_role_config.rs` (main, 2026-09-29) requires only a
  non-empty trimmed `name` — no character set. The `[A-Za-z0-9 _-]` rule in that file
  applies to `nickname_candidates` only. We emit the hyphenated slug.
- **Strict schema.** The role-file struct is `#[serde(deny_unknown_fields)]` over
  `ConfigToml`: an unknown key makes the agent fail to load. Emit known keys only;
  carry translation data inside `developer_instructions` (which must not be blank).
- **Discovery.** `codex-rs/agent-roles/src/discovery.rs` walks the agents dir
  recursively and loads only `*.toml` files, so Markdown under
  `.codex/agents/references/` is ignored. No other `.toml` file may live there.
- **POLA.** Subagents inherit the parent's sandbox policy, and CLI permission
  overrides are re-applied to spawned children. A custom agent's `sandbox_mode` is
  therefore a default, **not a ceiling**. Codex does not enforce Copilot/Claude tool
  grants, so declared tools can only be stated as a self-imposed limit.
- **Reported, not re-verified here** (baseAgent research, 2026-09-29, citing
  `codex-rs/core/src/agents_md.rs`): untrusted projects skip project instruction
  files, and `project_root_markers` sets the walk root.

## Canonical Output Conventions (ours, current)

- Custom agents: `.codex/agents/<slug>.toml` — `name` (slug), `description`,
  `developer_instructions` (the canonical body with one title H1, followed by a fenced
  `codex_translation` block that states the declared tools as a self-imposed limit and
  lists "Hand off to" targets), plus `sandbox_mode = "read-only"` only when every
  declared tool is `read`/`search`. No `model`, provider or approval keys. Emitted by
  native generation and by `--interop-from <team> --framework codex`; parsed back by
  `CodexAdapter.parse_agent_source` (codex is an interop source too).
- Instructions: repo-root `AGENTS.md`, written only when absent or already
  Codex-generated (ownership notice). An existing goose/agents-md/hand-written
  `AGENTS.md` is skipped with a notice, even with `--overwrite`. `AGENTS.override.md`
  is never emitted.
- Flat `.agents/<slug>.md` specialist files are no longer emitted (Codex never loaded them).
- MCP: spliced into `.codex/config.toml` via `agentteams/codex_mcp_emit.py`,
  opt-in through the `codex:mcp` host-feature token.

## Known Deltas vs Our Adapter (`agentteams/frameworks/codex.py`, `agentteams/codex_mcp_emit.py`)

| ID | Delta | Verification | Disposition |
|----|-------|--------------|-------------|
| X1 | Our documented discovery order lags: missing `AGENTS.override.md` (global + per-dir), `project_doc_fallback_filenames`, root→cwd merge order, 32 KiB combined cap (relevant if brief + roster grows). | researcher-claimed | Tranche 2 — update adapter docstring + emitted docs |
| X2 | `.codex/config.toml` splice remains valid but loads only in trusted projects (we don't document the trust gate). `mcp_servers` shape matches; newer optional keys available (`enabled`, timeouts, `enabled_tools`, HTTP `url` servers, `auth`); verify our `env` emission shape against current examples. | researcher-claimed | Tranche 2 |
| X3 | New surfaces: skills (SKILL.md — the recommended packaging; custom prompts deprecated), file-based profiles (`[profiles.*]` deprecated v0.134.0). Deprecation half is a conformance issue only if we emit deprecated forms — we emit neither custom prompts nor profiles, so no current non-conformance; a skills mapping (selected procedures → `.agents/skills/<name>/SKILL.md`) is an opportunity, not a gap — see X7. | researcher-claimed | Recorded; skills mapping noted for design discussion |
| X5 | Custom agents (`.codex/agents/*.toml`) were not emitted; the adapter wrote flat `.agents/<slug>.md` files Codex does not load, with a duplicate H1 under interop. | **re-verified** 2026-09-29 (docs twin + codex-rs source) | **Closed** 2026-09-29 — TOML custom-agent emission (baseAgent cross-orchestrator request) |
| X6 | The watch covered only the agents-md HTML page (client-rendered), and the token scan matched YAML `key:` only, so no Codex TOML token could ever be observed. | **re-verified** 2026-09-29 | **Closed** 2026-09-29 — `.md` twins of agents-md + subagents + build-skills; scan accepts `key =` and `` `key` `` |
| X7 | Skills (`.agents/skills/<name>/SKILL.md`) not emitted (`recall`, `citation-audit` are first candidates). | docs twin 2026-09-29 | Open — optional follow-up |
| X4 | Emitted/documented links should point at learn.chatgpt.com (`codex_mcp_emit.py` cites developers.openai.com — still redirects, low priority). | **re-verified** locally (one citing file found) | Tranche 2 (docstring-level; batched with X1) |

## Integration Checklist

1. Keep AGENTS.md-at-root emission (correct per discovery rules).
2. Tranche 2: document override/fallback/merge semantics and the 32 KiB cap;
   document the config-trust gate; refresh MCP key coverage.
3. Custom agents ship as `.codex/agents/<slug>.toml` (X5). Skills
   (`.agents/skills/<name>/SKILL.md`) remain an optional follow-up (X7).

## Observed Upstream Tokens — `codex` (Daily Pipeline)

Recorded by the daily pipeline on `2026-09-11` from `https://learn.chatgpt.com/docs/agent-configuration/agents-md`.

- Upstream tokens observed: —
- Upstream locations observed: .codex, AGENTS.md
- Fetch status: `ok`
