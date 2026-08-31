# Claude Agent Infrastructure Expert Reference

Purpose: Canonical guidance for integrating Anthropic Claude Code sub-agent infrastructure into AgentTeamsModule.

## Authoritative Documentation

- Claude Code sub-agents:
  - https://docs.anthropic.com/en/docs/claude-code/sub-agents

## Canonical Output Conventions

- Claude sub-agent files:
  - Location: .claude/agents/
  - Extension: .md
  - Structure: Claude-compatible YAML front matter + Markdown body
  - Front matter keys used here: name, description, allowed-tools
- Claude project instructions:
  - Filename: CLAUDE.md
  - Location: repository root

## Function-Level Conformance Requirements

- Adapter layer must convert VS Code-oriented templates into Claude-compatible files:
  - Strip VS Code front matter keys and handoff sections
  - Inject Claude front matter with allowed-tools
- Pipeline must finalize output file naming for Claude architecture:
  - .agent.md -> .md for agent and builder outputs
  - ../copilot-instructions.md -> ../CLAUDE.md
- Emission and tests must assert Claude-native naming and structure.

## Integration Checklist

1. Extend adapter API to finalize paths by file type.
2. Ensure build pipeline calls path finalization for every rendered output.
3. Keep reference and artifact files at existing relative locations unless framework semantics require rename.
4. Add/maintain tests for CLAUDE.md naming and content transformation.

## Observed Upstream Tokens — `claude` (Daily Pipeline)

Recorded by the daily pipeline on `2026-07-02` from `https://docs.anthropic.com/en/docs/claude-code/sub-agents`.

- Upstream tokens observed: model, name, tools
- Upstream locations observed: .claude/agents, CLAUDE.md
- Fetch status: `ok`
- Matched against local required keys: name
- Documented locally but not seen upstream: description
- Seen upstream but not in local required set (advisory only — may be optional keys): model, tools

## Verified Upstream Conventions (2026-08-15, agent-doc-optimal-structure plan)

Verified live on code.claude.com (the current doc home; docs.anthropic.com
redirects there for Claude Code content):

- Sub-agents: https://code.claude.com/docs/en/sub-agents.md
- Memory / CLAUDE.md: https://code.claude.com/docs/en/memory.md
- Skills: https://code.claude.com/docs/en/skills.md

Front-matter contract: required = `name`, `description` (matches our
`_CLAUDE_REQUIRED_KEYS`). Optional = `tools`, `disallowedTools`, `model`
(family alias, full ID, or `inherit`), `permissionMode`, `maxTurns`, `skills`,
`mcpServers`, `hooks`, `memory` (user|project|local), `background`, `effort`,
`isolation` (worktree), `color`, `initialPrompt`. **Omitting `tools` means the
sub-agent inherits every tool available to sub-agents** ("Inherits every tool
available to subagents if omitted" — re-verified by direct fetch 2026-08-15).

Location precedence (highest→lowest): managed settings `.claude/agents/` →
`--agents` CLI flag → project `.claude/agents/` (what we emit) → user
`~/.claude/agents/` → plugin `agents/`.

Instructions: `./CLAUDE.md` and `./.claude/CLAUDE.md` are equal-scope
alternatives (C8 — locally confirmed: this repo's own `.claude/CLAUDE.md` is
honored as project instructions). `./.claude/CLAUDE.local.md` is the
gitignored personal-override form. `@path/to/file` import syntax supported.

## Known Deltas vs Our Adapter (`agentteams/frameworks/claude.py`)

| ID | Delta | Verification | Disposition |
|----|-------|--------------|-------------|
| C1 | Our `_CLAUDE_DEFAULT_ALLOWED_TOOLS = "Bash, Read, Write, Edit"` is applied on fallback paths (no front matter / no tools block / empty mapping) where upstream's omission semantics would grant full inheritance — our fallback silently narrows capability relative to emitting nothing. Scoped to those fallback paths only (`_map_allowed_tools`), not every emission. | **re-verified** (upstream quote + local code path check 2026-08-15) | Tranche 2 — decide narrow-by-default (safer) vs inherit (conformant) explicitly; current behavior is a defensible security posture but is undocumented as a deliberate divergence |
| C2 | Twelve+ documented optional keys we neither emit nor document (list above). Optional features, not conformance gaps. | researcher-claimed (list re-verified via direct fetch) | Recorded; pass-through is Tranche 2 |
| C3 | Upstream guidance: <200 lines per memory file, facts not procedures, distribute to skills/`.claude/rules/`. Our generated CLAUDE.md is a deliberate monolith (roster + constitutional rules + conventions). **Accepted, reasoned divergence:** the constitutional-governance content must be non-bypassable at session start, which on-demand skills do not guarantee; do not re-surface as an open gap. Revisit only if upstream adds an always-loaded rules mechanism matching our needs. | **re-verified** (guidance quoted) | Accepted divergence — recorded |
| C4/C5 | `.claude/rules/` path-scoped rules and SKILL.md procedure packaging exist; we emit neither. Design opportunity (move *procedural* template content, keep governance in CLAUDE.md), not a conformance failure. | researcher-claimed | Tranche 2 design item |
| C6/C7 | Sub-agent memory modes and location-precedence not documented in our references before this update. | researcher-claimed | Closed by this section |
| C8 | `./.claude/CLAUDE.md` is an equal-scope alternative to root `./CLAUDE.md` (this project uses both — root as a bridge entry point, `.claude/CLAUDE.md` as the full team doc). | **re-verified** (this repo's own `.claude/CLAUDE.md` is honored as project instructions) | Closed by this section |
