# `framework_conformance` — AgentTeamsModule

The standard agent-infrastructure framework-conformance check (≤24h, no-fetch).

Split out of [`framework_research`](framework-research.md) (CH-07 module-size
ceiling). This module holds the agent-side complement to the daily
`.github/workflows/framework-auto-update.yml` cron: where the cron *detects and
records* upstream drift into an observation-stanza PR, this decides — at most
once per window — whether observed drift warrants routing
`@framework-adapters-expert` to the Stage-2 adapter triage in
[`provider-adapter-refresh.procedure.md`](../../references/provider-adapter-refresh.procedure.md)
§7. It never fetches and never edits adapter code (Constitutional C-4); its only
write is a small best-effort local ledger that degrades safely on a read-only
tree. It imports the snapshot helpers from
[`framework_research`](framework-research.md) one direction only —
`framework_research` never imports this module at load time.

> *Source: `agentteams/framework_conformance.py`*

---

## Constants

### `AGENT_CHECK_LEDGER_REL`

> *Source: `agentteams/framework_conformance.py`*

Repo-relative path to the gitignored, per-checkout agent-check ledger
(`tmp/daily-pipeline/framework-research/agent-check-ledger.json`). Its
`last_checked` timestamp is a **distinct axis** from the snapshot's
`generated_at` (which the cron restamps daily): gating on snapshot age alone
could never signal "the agent has not acted."

### `AGENT_CHECK_WINDOW_HOURS`

> *Source: `agentteams/framework_conformance.py`*

Default ≤24h cadence. A run within this window of the ledger's `last_checked`
skips (`fresh-skip`).

### `AGENT_CHECK_ATTEMPT_THROTTLE_HOURS`

> *Source: `agentteams/framework_conformance.py`*

Short retry throttle (≈1h) applied ONLY to inconclusive (`stale-snapshot`)
outcomes, so an offline run does not advance the 24h clock — which would mask
real drift for a full day — yet also does not re-attempt on every session.

---

## Functions

### `run_agent_conformance_check(repo_root, *, window_hours=24.0, now=None) -> dict`

> *Source: `agentteams/framework_conformance.py`*

Reads the already-produced snapshot and decides whether observed drift or a
relocated doc warrants routing `@framework-adapters-expert`. Read-only against
the snapshot and adapters (records only the local ledger); never fetches.

Classification:

- **drift** — a framework whose `keys_diff.missing_upstream` is non-empty (a
  token this project relies on vanished from the upstream doc).
- **fetch-issue** — a framework whose `fetch_status` is `moved`/`empty`
  (relocated or stub doc). Transient `failed`/`skipped` never route.
- `new_upstream` is deliberately excluded — the scan only searches
  `expected_keys`, so it is structurally always empty.

Routing is idempotent per distinct drift: a `drift_signature` over the
actionable `missing_upstream` tokens plus the fetch-issue set is stored in the
ledger, so the same drift is routed once, not every window.

Returns a JSON-safe dict with `status` (`fresh-skip` | `attempt-throttled` |
`stale-snapshot` | `no-drift` | `drift`), `ran`, `needs_agent_action`, `route`,
`already_routed`, `drift_frameworks`, `fetch_issue_frameworks`,
`drift_signature`, `snapshot_generated_at`, `hours_since_last_check`, and a
human `message`. The payload carries only status codes, framework ids and
configured source URLs — never fetched page text (C-4). `window_hours=0` bypasses
the ledger gate (used by `--force` and the daily cron step) but a snapshot older
than the default window still degrades to `stale-snapshot`.

### `render_agent_check_report(result) -> str`

> *Source: `agentteams/framework_conformance.py`*

Renders the human + machine-readable lines for a check result — shared by
`scripts/research_claude_code_docs.py --agent-check` (the repo daily-pipeline
wrapper) and the portable `agentteams --agent-check` CLI so both surfaces print
identical `STATUS=` / `NEEDS_AGENT_ACTION=` / `ROUTE=` lines. On a route it also
emits `ROUTE_TARGET=@framework-adapters-expert`, the affected
`DRIFT_FRAMEWORKS=` / `FETCH_ISSUE_FRAMEWORKS=`, and the procedure anchor. Output
carries only status codes, framework ids and the anchor — never fetched page
text (C-4).

---

## Operator workflow

Run the check (no fetch, no adapter edits; records only a local ledger):

```bash
agentteams --agent-check                    # honor the 24h ledger gate
agentteams --agent-check --force            # re-check now (bypass the gate)
python scripts/research_claude_code_docs.py --agent-check   # repo daily-pipeline wrapper
```

On `ROUTE=true`, route the named frameworks to `@framework-adapters-expert` to
work the [`provider-adapter-refresh.procedure.md`](../../references/provider-adapter-refresh.procedure.md)
§5 Stage-2 edit-site table as a human-reviewed PR. The check itself never
authorizes an unattended adapter edit (C-4).
