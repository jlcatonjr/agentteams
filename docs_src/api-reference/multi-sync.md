# `multi_sync` — AgentTeamsModule

Multi-framework **pinned sync** orchestrator (multi-framework-pinned-sync plan).

> Source: `agentteams/multi_sync.py`

Keeps every agentic interface in sync through the canonical hub under one locked model:
frameworks are peers, a clean one-sided change in any framework projects to all others, and a
genuine conflict is **always** decided in favor of the bootstrap pin (and logged for review).

Builds entirely on shipped primitives — `interop.export_to_cai` / `import_from_cai`,
`canonical.materialize_canonical` / `load_canonical`, `sync_classifier.classify_sync`, and
`sync_baseline` — plus the pin contract in `sync_pin`.

---

## Reconciliation policy

- **Pin first, authoritatively.** The pin framework's native-moved fields — including conflicts
  and capability fields — are absorbed into canonical (the pin is the trusted source of truth).
- **Peers next.** Clean, non-capability `native-moved` fields absorb and fan out. A
  `both-moved-conflict` keeps canonical (the pin's authority) and is logged. A peer *capability*
  change is **never** silently fanned out — it is withheld and logged (the shipped
  capability-safety invariant, preserved deliberately).
- **Project + rebaseline.** Canonical is projected to every framework and every baseline is
  rewritten from the on-disk result, so the next run never re-absorbs this run's own output.
- **Change detection is commit-to-commit** (operator decision): a framework is "changed" when a
  path under its agents directory moved between the last synced commit and HEAD. A framework's
  own projected-but-uncommitted output therefore never re-triggers a sync.

---

## Public constants

- `CONFLICT_LOG_SUBPATH` (`".agentteams/sync-conflicts.log.csv"`): the durable, append-only,
  human-reviewable conflict log.

## Public types

- `ConflictRecord`: one documented conflict or withheld peer capability change
  (`framework`, `agent`, `field_name`, `classification`, `resolution`, `note`).
- `SyncResult`: outcome of a run (`changed_frameworks`, `projected_frameworks`, `conflicts`,
  `applied_fields`, `dry_run`, `note`; property `did_work`).

## Public functions

### `sync_init`

Bootstrap the pinned sync: export the pin framework to canonical, materialize the hub, project
to every framework in the set, and record all baselines plus the change-detection anchor.
Writes `.agentteams/pin.json`. Raises `ValueError` for an unregistered pin or a pin outside the
sync set.

### `run_sync`

Run one pass: detect changed frameworks from commit diffs, reconcile each (pin first) into
canonical per the policy above, project canonical to all frameworks, rewrite baselines, append
the conflict log, and advance the anchor. Returns early with no writes when nothing changed.
Raises `ValueError` if the project is not pinned.

### `framework_agents_dir`

Return a registered framework's agents directory under a project root.
