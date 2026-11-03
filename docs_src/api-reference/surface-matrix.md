# `surface_matrix` — which agents exist on which provider

> *Source: `agentteams/surface_matrix.py`*

An instance's team runs on several providers, each with its own agent folder. Adopted, bespoke and per-surface agents
leave gaps: in baseAgent 15 of 56 agents were missing on at least one provider, and no provider's instructions said
where the missing ones were (P3, 2026-11-02). Every generate and `--update` writes
`references/agent-surface-matrix.md`, and every projected rules block points to it
([`rules_projection`](rules-projection.md)). The matrix is reported, never enforced.

The file holds:

- one row per agent and one column per present surface (`claude`, `copilot`, `goose`, `codex`, `grok`);
- for each agent with a gap, the tools each surface declares for it (front-matter `tools`, a Goose recipe's
  `extensions`, a Codex `sandbox_mode`) and a conservative capability label: read-only only when proven, otherwise
  assumed to write and run. Names that don't look like names are shown as `(invalid entry)`, since every provider
  reads this file;
- the rule that another provider's file is a role description, never a grant (`NEVER_A_GRANT`);
- the agents whose `## Project-Specific Notes` differ between surfaces (P10), ignoring projected blocks;
- the skills on each surface (`.claude/skills`, `.agents/skills`, `.github/skills`), and those on some only.

| Name | Purpose |
|---|---|
| `build_matrix(root)` | Read every present surface's agent files and skill folders. |
| `AgentEntry` / `Matrix` | One agent file on one surface (its declared tools and notes); the agents and skills of every present surface. |
| `Matrix.gaps()` / `Matrix.notes_differ()` | Agents missing on a present surface; agents whose notes differ. |
| `capability(tools)` | A conservative label: `read-only` only when every declared tool is in `READ_ONLY_TOOLS`; a Codex `read-only` sandbox blocks writes but may still run commands; nothing declared, unparsed, or any other tool is assumed to write and run. |
| `render_matrix(matrix)` / `write_matrix(root)` | The Markdown, and writing it when it changed: never through a symlink, outside the project, or over a file agentteams didn't generate. |
