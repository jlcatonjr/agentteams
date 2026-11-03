# `copilot_body_limit` — AgentTeamsModule

`--check`'s guard for operator text past GitHub's 30,000-character custom-agent body limit (baseAgent P4,
2026-11-02).

> *Source: `agentteams/copilot_body_limit.py`*

GitHub may truncate a custom agent's body (the text after the YAML front matter) past 30,000 characters. The
generated orchestrator and security bodies exceed that from the templates alone, so an overrun by itself is only
reported. `--check` fails on what the limit costs an operator: a `## Project-Specific Notes` region that holds their
own text and starts past the limit, where a truncation removes it first. A fresh Copilot render places the notes
before the body's first fence when it is a named section ([`project_notes`](project-notes.md)). A file rendered earlier keeps its notes at the
end until the operator moves them.

The body is measured on disk, so the operator's text counts.

| Name | Purpose |
|---|---|
| `MAX_BODY_CHARS` | `30_000`, from [`frameworks.copilot_vscode`](frameworks.md). |
| `COPILOT_FRAMEWORKS` | `copilot-vscode`, `copilot-cli`. |
| `body_limit_findings(output_dir) -> (failures, advisories)` | One failure per agent whose operator notes start past the limit; one advisory per other agent over the limit, or unreadable. |
| `report_body_limit(output_dir, framework) -> bool` | Print the findings for a Copilot team; True when `--check` must exit 1. |
