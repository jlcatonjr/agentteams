# `write_policy` — AgentTeamsModule

Orchestrator-only-writes pilot, phase P3. Shapes generated agents under `"write_policy": "orchestrator-only"`
so that only the orchestrator can write.

> Source: `agentteams/write_policy.py` (integrity-pinned). Called by `cli/render_pipeline.py` before every
> framework adapter. Behaviour and migration: [`proposals`](proposals.md#generated-teams-under-the-switch-p3).

---

## Public surface

| Name | Purpose |
|---|---|
| `READ_ONLY_TOKENS` | Tools a non-orchestrator agent may hold under the switch. The `AR_WRITE_POLICY` audit imports this, so the generator and the check share one list. |
| `NARROWED_TOKENS` | What generated agents are narrowed to: `read`, `search`, `todo`. These are canonical tokens, so every adapter can map them. |
| `ORCHESTRATOR_SLUGS` | Agents that keep their tools: `orchestrator` and Goose `bridge-orchestrator`. |
| `enabled(manifest) -> bool` | True only for `write_policy: "orchestrator-only"`. |
| `narrow_tools(content) -> str` | Narrows the one-line `tools: [...]` flow list. `read` is always kept, and a missing key becomes `['read', 'search']`. A one-line legacy Claude `allowed-tools:` grant is removed, since a host that reads it would ignore `tools:`. Raises `ValueError` for a `tools:` key of any other shape, or a multi-line `allowed-tools:`, rather than adding a second key. |
| `resolve(description, framework) -> str \| None` | The write policy in effect for one framework's render, after every check: `write_policy_frameworks` scoping, legacy Goose refusal, supported frameworks, explicit `confined`/`exclusive` profile. Shared by `analyze.build_manifest` and `--interop-from --description`. Raises `ValueError`. |
| `ensure_section(body, slug, manifest, *, writer=None) -> str` | For an imported agent body (`writer` overrides the orchestrator-slug test; interop passes it because `bridge-orchestrator` writes only on Goose): returned unchanged when its only write-policy section is the one the brief calls for, given that section when it has none. Raises `ValueError` for a different section or more than one. |
| `narrow_scopes(scopes) -> list[str]` | The `narrow_tools` rule on a list of canonical tool scopes (always `read`; `['read', 'search']` when empty). |
| `apply(content, slug, manifest) -> str` | Without the switch, returns the content unchanged. With it, the orchestrator gains the "Applying Proposals" section and every other agent is narrowed and gains the "Return Proposals" section. The section is fenced only when the body already is. |
| `reference_doc() -> str` | The `references/write-policy.reference.md` shipped with a team under the switch. |

Both sections also carry the capability-gap route: agents attach gap notes, and the orchestrator opens the
plans and keeps the need register. See [`mcp_need`](mcp-need.md).

A static narrowing of declared tools, not runtime enforcement. The runtime boundary is the proposal CLI
(`agentteams --apply-proposal` / `--run-request`), served by the out-of-session runner in an OS sandbox
([`proposal_runner`](proposal-runner.md), [`confinement`](confinement.md)).
