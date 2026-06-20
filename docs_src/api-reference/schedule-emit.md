# `schedule_emit`

Recurring routine spec emitter for Claude's `/schedule` skill. Writes `.claude/schedules.agentteams.json` — a list of `(cron, agent slug, description)` triples that the `/schedule` skill can enroll. agentteams itself does **not** enroll the routines.

Opt-in via [`--target-host-features bridge:copilot-vscode-to-claude:schedule`](host-features.md).

## Default Routine Set

| Slug | Cron (UTC) | Description |
|---|---|---|
| `work-summarizer` | `0 22 * * *` (daily 22:00) | Append the day's events to `workSummaries/daily/<date>.md`. |
| `drift` | `0 14 * * 1` (Monday 14:00) | Cross-team template→artifact drift scan. |
| `post-production-auditor` | `0 18 * * 5` (Friday 18:00) | Weekly post-production sampling audit. |
| `advisory` | `0 12 1 * *` (1st of month 12:00) | Monthly advisory PR roll-up. |

Routines are only emitted when the matching `<slug>.agent.md` exists in `source_dir`. Missing slugs are reported in `ScheduleEmissionResult.omitted_routines`.

## Public Surface

```python
@dataclass
class ScheduleEmissionResult:
    written: list[str]
    skipped: list[str]
    errors: list[str]
    omitted_routines: list[str]
    success: bool  # property: len(errors) == 0
```

```python
build_routines(source_dir: Path) -> tuple[list[dict[str, str]], list[str]]
```
Return `(active_routines, omitted_slugs)`. Each routine dict has keys `name`, `cron`, `agent`, `description`, `tier`, `bridge`. `tier` is the literal string `"cheap"` (hardcoded in `build_routines` for every routine — it is **not** looked up from `model_routing._ALWAYS_CHEAP_SLUGS`, which does not contain the routine slugs); `bridge` is always `"copilot-vscode-to-claude"`.

```python
emit_schedule_artifact(
    *,
    source_dir: Path,
    output_root: Path,
    dry_run: bool = False,
    overwrite: bool = True,
) -> ScheduleEmissionResult
```
Write the schedules JSON to `<output_root>/.claude/schedules.agentteams.json`. Payload is `{_agentteams_managed, schema_version: "1.0", routines: [...]}`. JSON is deterministic (sorted keys, 2-space indent).

## Relationship to `model_routing`

Routines run on the cheap tier regardless of governance-agents membership. This is set directly: `build_routines` writes the literal `tier: "cheap"` on every routine dict — the routine slugs are **not** members of `model_routing._ALWAYS_CHEAP_SLUGS`.

Separately, `model_routing.agent_tier()` consults its own `_ALWAYS_CHEAP_SLUGS` set, which contains only the bridge's per-action lookup roles (`critic`, `retrieval-policy`, `navigator`, `reference-manager`, `memory-index-query`) — so the Phase 3 PreToolUse critic and Phase 6 retrieval policy stay affordable. The two cheap-tier mechanisms are independent and the routine slugs above do not appear in `_ALWAYS_CHEAP_SLUGS`.
