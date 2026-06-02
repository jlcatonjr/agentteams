# Framework Watch — WebAppBackend

<!--
SECTION MANIFEST — framework-watch.reference.template.md
| section_id      | designation | notes                                                |
|-----------------|-------------|------------------------------------------------------|
| framework_data  | FENCED      | Live upstream framework spec snapshot (Claude Code)  |
-->

This reference is refreshed during team initialization and update workflows.
It records the most recent observed upstream agent-framework specification
(currently Claude Code sub-agents) and the diff against this project's
framework adapter constants. Use it when reviewing `@framework-adapters-expert`,
`@docs-research-expert`, and `@agent-updater` proposals — it is the
canonical signal that the local adapter may need attention.

The detection is intentionally coarse. The machine-readable snapshot lives
at `tmp/daily-pipeline/framework-research/latest.json` and is the
authoritative source; the table below is a human-readable projection
re-rendered on every `--update --merge`.

<!-- AGENTTEAMS:BEGIN framework_data v=1 -->
{FRAMEWORK_RESEARCH_STALE_BANNER}

Generated on: `{FRAMEWORK_RESEARCH_GENERATED_ON}`
Source: {FRAMEWORK_RESEARCH_SOURCE_URL}
Fetch status: `{FRAMEWORK_RESEARCH_FETCH_STATUS}`
Diff summary: `{FRAMEWORK_RESEARCH_DIFF_SUMMARY}`

{FRAMEWORK_RESEARCH_TABLE}
<!-- AGENTTEAMS:END framework_data -->

## Operational Integration Process

1. Refresh this reference on every team initialization and update.
2. Route `new_upstream` keys to `@framework-adapters-expert` for triage.
3. Route `documented_locally_not_upstream` keys to `@docs-research-expert`
   for verification — they may indicate doc drift or a missed rename.
4. Escalate persistent unresolved drift to `@orchestrator` with a
   re-render request when adapter constants are out of date.
