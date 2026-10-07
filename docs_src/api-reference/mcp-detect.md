# `mcp_detect`

MCP-suitability detection rubric (pure, dependency-free). Given an integration
hint, it decides whether a project should **build** an MCP server, use a **direct
API** call, or **defer** the decision to operator security review. It only
recommends — it never provisions anything.

Run during analyze (see [`analyze`](analyze.md)); recommendations land in the
team-manifest's `mcp_candidates[]`. Background: `references/mcp-auto-detection-report.md`.

## Decision rule

The decision is a **necessary-condition gate**, not a flat signal count:

```
if hard_gate:                            DEFER_TO_SECURITY_REVIEW
elif cross_host_reuse and statefulness:  BUILD_MCP
else:                                    USE_DIRECT_API
```

- **`cross_host_reuse`** — the integration is needed by `>1` target host
  (`target_host_count`) OR by `≥2` components (`used_by_components`).
- **`statefulness`** — auth/session lifecycle, connection pooling, or caching.
- **Hard gate** (overrides everything) — a third-party [`trust_tier`](capability-map.md), an
  unrecognized/missing `trust_tier`, a `destructive` `max_side_effect`, or an
  unrecognized `max_side_effect`.

A large/dynamic operation surface is only a positive tiebreaker when paired with
a lazy-disclosure commitment; otherwise it raises `efficiency_risk`.

## Fail-closed posture

This module may run on raw, un-schema-validated dicts. Security-critical fields
therefore **fail closed**: a missing/unrecognized `trust_tier` or `max_side_effect`
triggers the hard gate (DEFER) rather than the permissive case. Booleans are
coerced strictly (`value is True`), so a stringy `"false"` cannot flip a result.

## Public Surface

```python
BUILD_MCP = "BUILD_MCP"
USE_DIRECT_API = "USE_DIRECT_API"
DEFER_TO_SECURITY_REVIEW = "DEFER_TO_SECURITY_REVIEW"
# per-agent decisions under the switch, owned by agentteams.mcp_need (re-exported here):
USE_CLI, REFUSE, USE_RUNNER_PATH


@dataclass
class McpCandidate:
    candidate_id: str
    recommendation: str
    rationale: str
    signals: dict[str, bool]
    agents: list[str] | None = None            # under write_policy "orchestrator-only" only
    per_agent: list[dict] | None = None        # under write_policy "orchestrator-only" only
    def to_manifest_entry(self) -> dict: ...   # team-manifest mcp_candidates item (None fields omitted)
```

```python
evaluate_hint(hint: dict, *, target_host_count: int = 1, write_policy: bool = False) -> McpCandidate
```
Evaluate one integration hint (shape: the `mcp_hints` item in
`agentteams/schemas/project-description.schema.json`). `target_host_count > 1` is itself
evidence of cross-host reuse. With `write_policy` (the orchestrator-only switch), the candidate keeps
its `agents` and gets a `per_agent` decision for each.
- **Agents:** `used_by_components` names components, so each maps to its workstream expert (`<slug>-expert`).
  A value already on the roster, or the orchestrator, is kept as is. Anything else is listed in the rationale
  as ignored, never invented.
- **Which result governs:** the project-level `recommendation` (for example `BUILD_MCP`) stays advisory, for
  the operator. For non-orchestrator agents under the switch, `per_agent` governs, by the MCP-need protocol.

```python
classify_agent_need(agent: str, hint: dict) -> dict
```
One agent's decision under the agent MCP-need protocol ([`mcp_need`](mcp-need.md)) at generation time,
before anything is measured:
- `USE_CLI` for the orchestrator;
- `REFUSE` for a capability that writes (`max_side_effect` `write` or `destructive`);
- `DEFER_TO_SECURITY_REVIEW` when the hard gate fires (an unknown or third-party trust tier, or an
  unknown side effect);
- otherwise `USE_RUNNER_PATH`: wait for the ledger. A brief hint is never evidence, so generation never
  proposes a server.

```python
detect_mcp_candidates(description: dict, *, target_host_count: int = 1) -> list[McpCandidate]
```
Evaluate every `mcp_hints` entry in a description. Returns `[]` when none are
declared (the default — direct API). `analyze` passes the *effective* switch (`write_policy_frameworks`
can turn it off for a framework), the roster and the component slugs. Direct callers that pass no
`write_policy` get it from the description. Duplicate `candidate_id` slugs are
disambiguated with a numeric suffix so a punctuation/case collision never
silently drops a recommendation.

## Notes

- `mcp_hints` (detection input) is distinct from `mcp_servers` (emission input,
  see [`mcp_emit`](mcp-emit.md)): hints feed *whether to build*; servers are
  *specified definitions to emit*.
- The signal `target_host_count` is `1` at the live analyze call site (a build
  targets one framework); cross-host reuse is therefore driven by
  `used_by_components ≥ 2` in practice.
