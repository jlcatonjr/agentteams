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
- **Hard gate** (overrides everything) — a third-party `trust_tier`, an
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


@dataclass
class McpCandidate:
    candidate_id: str
    recommendation: str
    rationale: str
    signals: dict[str, bool]
    def to_manifest_entry(self) -> dict: ...   # team-manifest mcp_candidates item
```

```python
evaluate_hint(hint: dict, *, target_host_count: int = 1) -> McpCandidate
```
Evaluate one integration hint (shape: the `mcp_hints` item in
`schemas/project-description.schema.json`). `target_host_count > 1` is itself
evidence of cross-host reuse.

```python
detect_mcp_candidates(description: dict, *, target_host_count: int = 1) -> list[McpCandidate]
```
Evaluate every `mcp_hints` entry in a description. Returns `[]` when none are
declared (the default — direct API). Duplicate `candidate_id` slugs are
disambiguated with a numeric suffix so a punctuation/case collision never
silently drops a recommendation.

## Notes

- `mcp_hints` (detection input) is distinct from `mcp_servers` (emission input,
  see [`mcp_emit`](mcp-emit.md)): hints feed *whether to build*; servers are
  *specified definitions to emit*.
- The signal `target_host_count` is `1` at the live analyze call site (a build
  targets one framework); cross-host reuse is therefore driven by
  `used_by_components ≥ 2` in practice.
