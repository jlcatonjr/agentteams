# `bridge_refresh` — AgentTeamsModule

What `--bridge-refresh` writes for a target file whose bridge render is only `AGENTTEAMS-BRIDGE` fences. It was
added on 2026-11-02 (baseAgent P12) and carved out of [`bridge`](bridge.md) for the CH-07 length ceiling.

> *Source: `agentteams/bridge_refresh.py`*

`--bridge-refresh` replaces each target entry file whole. `.goosehints` is rendered as nothing but the
`goose-bridge-hints` fence, so an instance that added its own text around the fence lost it on every refresh (only
the backup kept it). For `.goosehints` the refresh now replaces the fence in place, or appends it when the file has
none, and keeps the project text. A notice gives the number of lines kept.

| Name | Purpose |
|---|---|
| `PROJECT_EXTENDED_FILES` | The target files this applies to: `.goosehints`. The other fence-only targets (`.claude/agent-team.md`, `.claude/quickstart-snippet.md`) are bridge-owned and still reset. |
| `refresh_keeping_project_text(name, existing, rendered) -> (content, notice)` | The content to write, and a notice when project text was kept or could not be. A file whose bridge markers don't pair up is written whole, as before, because an in-place replacement could span project text. |

See `references/bridge-refresh-safety.md` for choosing a bridge mode.
