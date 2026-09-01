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

## Canonical Output Conventions (ours, current)

- Instructions: repo-root `AGENTS.md` (rendering delegated to the agents-md
  adapter; shared namespace with goose).
- Detail files: `.agents/<slug>.md` (not parsed by Codex).
- MCP: spliced into `.codex/config.toml` via `agentteams/codex_mcp_emit.py`,
  opt-in through the `codex:mcp` host-feature token.

## Known Deltas vs Our Adapter (`agentteams/frameworks/codex.py`, `agentteams/codex_mcp_emit.py`)

| ID | Delta | Verification | Disposition |
|----|-------|--------------|-------------|
| X1 | Our documented discovery order lags: missing `AGENTS.override.md` (global + per-dir), `project_doc_fallback_filenames`, root→cwd merge order, 32 KiB combined cap (relevant if brief + roster grows). | researcher-claimed | Tranche 2 — update adapter docstring + emitted docs |
| X2 | `.codex/config.toml` splice remains valid but loads only in trusted projects (we don't document the trust gate). `mcp_servers` shape matches; newer optional keys available (`enabled`, timeouts, `enabled_tools`, HTTP `url` servers, `auth`); verify our `env` emission shape against current examples. | researcher-claimed | Tranche 2 |
| X3 | New surfaces: skills (SKILL.md — the recommended packaging; custom prompts deprecated), file-based profiles (`[profiles.*]` deprecated v0.134.0). Deprecation half is a conformance issue only if we emit deprecated forms — we emit neither custom prompts nor profiles, so no current non-conformance; the skills mapping (`.agents/<slug>.md` → Codex skills) is an opportunity, not a gap. | researcher-claimed | Recorded; skills mapping noted for design discussion |
| X4 | Emitted/documented links should point at learn.chatgpt.com (`codex_mcp_emit.py` cites developers.openai.com — still redirects, low priority). | **re-verified** locally (one citing file found) | Tranche 2 (docstring-level; batched with X1) |

## Integration Checklist

1. Keep AGENTS.md-at-root emission (correct per discovery rules).
2. Tranche 2: document override/fallback/merge semantics and the 32 KiB cap;
   document the config-trust gate; refresh MCP key coverage.
3. Consider the `.agents/<slug>.md` → skills mapping when Codex-native
   per-specialist packaging is wanted.

## Observed Upstream Tokens — `codex` (Daily Pipeline)

Recorded by the daily pipeline on `2026-09-01` from `https://learn.chatgpt.com/docs/agent-configuration/agents-md`.

- Upstream tokens observed: —
- Upstream locations observed: .codex, AGENTS.md
- Fetch status: `ok`
