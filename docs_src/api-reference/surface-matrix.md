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
  `extensions`, a Codex `sandbox_mode`) and whether they can write or run;
- the rule that another provider's file is a role description, never a grant (`NEVER_A_GRANT`);
- the agents whose `## Project-Specific Notes` differ between surfaces (P10), ignoring projected blocks;
- the skills on each surface (`.claude/skills`, `.agents/skills`, `.github/skills`), and those on some only.

| Name | Purpose |
|---|---|
| `build_matrix(root)` | Read every present surface's agent files and skill folders. |
| `Matrix.gaps()` / `Matrix.notes_differ()` | Agents missing on a present surface; agents whose notes differ. |
| `can_write_run(tools)` | Whether a declared tool set can write and run (a heuristic over tool names). |
| `render_matrix(matrix)` / `write_matrix(root)` | The Markdown, and writing it when it changed. |
