# `project_notes` — AgentTeamsModule

The USER-EDITABLE "Project-Specific Notes" section that every generated agent persona carries.
It was carved out of [`emit`](emit.md) on 2026-10-04 (CH-07), and `emit` re-exports both helpers.

> *Source: `agentteams/project_notes.py`*

| Name | Purpose |
|---|---|
| `_is_agent_doc(rel_path, content) -> bool` | True for a generated agent persona: Markdown with front matter, not a reference, skill, instructions file or `SETUP-REQUIRED.md`. |
| `_ensure_project_notes_section(rel_path, content) -> str` | Append the section when it is absent. Pure append and idempotent; applied to fresh renders and merged output alike. |

The section text is `fences.PROJECT_NOTES_SECTION`. A natively rendered Codex TOML gets the same
section, inside its `developer_instructions`.
