# Framework Watch — {PROJECT_NAME}

<!--
SECTION MANIFEST — framework-watch.reference.template.md
| section_id      | designation | notes                                                |
|-----------------|-------------|------------------------------------------------------|
| framework_data  | FENCED      | Live upstream framework spec snapshot (Claude Code)  |
| operational_integration_process| FENCED      | Template-owned section                               |
| conformance_standard_check| FENCED | Opt-in ≤24h standard-check block; empty when the team has no framework adapters |
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

<!-- AGENTTEAMS:BEGIN operational_integration_process v=1 -->
## Operational Integration Process

1. Refresh this reference on every team initialization and update.
2. Route a `missing_upstream` key (a token this project documents that upstream
   no longer shows) or a relocated/stub doc (`fetch_status` `moved`/`empty`) to
   `@framework-adapters-expert` for Stage-2 triage — this is the live drift signal
   the ≤24h Standard Conformance Check below routes. (`new_upstream` is *not* a
   usable trigger: the scan only searches expected keys, so it is structurally
   always empty.)
3. Route the same `missing_upstream` set to `@docs-research-expert` *(if in team)*
   for verification — it may indicate doc drift or a missed rename rather than an
   adapter change.
4. Escalate persistent unresolved drift to `@orchestrator` with a
   re-render request when adapter constants are out of date.
<!-- AGENTTEAMS:END operational_integration_process -->

<!-- AGENTTEAMS:BEGIN conformance_standard_check v=1 -->
{FRAMEWORK_CONFORMANCE_STANDARD_CHECK}
<!-- AGENTTEAMS:END conformance_standard_check -->

