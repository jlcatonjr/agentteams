# `rank_conformance`

AP-2 rank-conformance validator. Enforces the constitutional **C-3** capability surface
*against agent taxonomy rank*: an agent's declared `tools:` must not exceed what its rank
permits. This is the first enforcement on the **agent-position** privilege axis — the
per-agent `tools:` grant is already binding under C-3; this validator checks that grant
*against the agent's position* (orchestrator / governance / domain / workstream-expert).

Rank is **derived, not stored** — from the same signals `analyze` uses: the slug
`orchestrator`, membership in `analyze.GOVERNANCE_AGENTS`, an `-expert` suffix
(workstream-expert), else domain. Tool parsing is delegated to
[`capability_map.canonical_tools_for_claude`](capability-map.md), which returns the
canonical 7-token vocabulary (`read, edit, search, execute, todo, agent, retrieval`) or
`None` when an agent declares no capability key.

The CLI surface is [`--check-rank`](../cli-reference.md) (read-only; mirrors
`--check-budget`). Disposition is **warn-only** — it prints findings and always exits 0 —
because the policy data is unvalidated and the live team predates it; a real over-grant
routes to `@security` as a C-3 widening.

## Policy as data

| Rank | Default ceiling (`TIER_CEILINGS`) |
|---|---|
| `orchestrator` | all 7 tokens (no ceiling) |
| `governance` | `read, search, execute, agent` (read-only auditor baseline; **no `edit`**) |
| `domain` | `read, search, execute, retrieval` (**no `edit`**) |
| `workstream-expert` | `read, search, agent` (advisory; **no `edit`, no `execute`**) |

`PER_AGENT_OVERRIDES` records the tokens a specific agent legitimately holds beyond its
rank ceiling (e.g. `cleanup`/`agent-updater` hold `edit`, `reference-manager` holds
`edit, execute, retrieval`). Each override entry **is** the auditable record of a
deliberate C-3 widening; exceptions live here, never in a loosened tier ceiling.

## Public Surface

```python
rank_for(slug: str) -> str
```
Return the derived taxonomy rank for an agent slug — one of `orchestrator`,
`governance`, `workstream-expert`, `domain`. Raises `TypeError` if `slug` is not a string.

```python
check_rank_conformance(file_map: dict[str, str], agent_ext: str) -> list[AuditFinding]
```
Iterate the agent files in `file_map` (path → content), derive each agent's rank, parse
its canonical tool tokens, and emit an `AuditFinding`
(`category="RANK_CONFORMANCE"`, `code="AP2_RANK_CAPABILITY_EXCEEDED"`,
`severity="warning"`) for each agent whose declared tokens exceed `ceiling ∪ override`.
Agents declaring no capability key are skipped (nothing to check). Returns a path-sorted
list, empty when every agent is within its allowed surface.

## Limits

- **Claude-shape `tools:` only.** Parsing reuses `canonical_tools_for_claude`, which reads
  the bare comma-separated scalar Claude writes and rejects copilot-vscode's bracket-list
  `tools: [...]`. A copilot-vscode team is therefore skipped (reported vacuously clean);
  the authoritative check runs against a Claude team.
- **Policy is opinionated and unvalidated.** The ceilings and overrides are a first cut;
  warn-only disposition exists precisely so a wrong policy entry routes attention rather
  than breaking a build. Promote to a blocking severity only after a clean fleet pass.

## See also

- [`capability-map`](capability-map.md) — the `tools:`/`allowed-tools` → canonical-token
  parser this validator reuses.
- [`workspace-privilege-scoping`](workspace-privilege-scoping.md) — the *workspace* axis of
  the same privilege effort (this page is the *agent-position* axis).
