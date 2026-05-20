# `behavioral_drift` — AgentTeamsModule

Detect behavioral divergence in agent runs against the behavioral specification.

Answers the question: *"Did the agent team run the workflow it was specified to run?"* by comparing an actual execution trajectory (from Phase 1 replay substrate) against the behavioral spec in the eval-suite (Phase 2). Orthogonal to template drift (see [`drift`](drift.md)).

> *Source: `agentteams/behavioral_drift.py`*

---

## Concepts

### Trajectory vs. Template Drift

- **Template drift** ([`drift.py`](drift.md)) — "Did the *file* change after regeneration?"
- **Behavioral drift** — "Did the *team* run the workflow it was supposed to?"

Template changes are tracked by comparing generated files to their current on-disk state. Behavioral changes are detected by replaying recorded handoff chains against expected handoff scenarios from the eval-suite and checking for:

1. **Chain contiguity** — Edges form a single connected chain without gaps
2. **Chain conformance** — Actual chain matches one expected chain from the spec
3. **Mediation observability** — Expected return-to orchestration is recorded
4. **Typed-payload continuity** — Handoff payloads match their schema IDs (reuses Cluster C `audit_handoff_chain`)

---

## Constants

### Finding Code Constants

These string constants identify the class of behavioral divergence:

| Constant | Meaning |
|----------|---------|
| `BEHAVIOR_NO_TRAJECTORY` | Eval-suite expects handoff chains, but trajectory has no handoff_edges |
| `BEHAVIOR_BROKEN_CHAIN` | Edge sequence is not contiguous (gap or fork detected) |
| `BEHAVIOR_CHAIN_DIVERGENCE` | Actual agent chain doesn't match any expected chain |
| `BEHAVIOR_MISSING_RETURN` | Expected orchestrator mediation/return not observed |

---

## Functions

### `reconstruct_chain(trajectory)`

> *Source: `agentteams/behavioral_drift.py`*

Extract the ordered agent chain from a trajectory's handoff edges.

**Args:**

- `trajectory` (`dict[str, Any]`) — Trajectory dict with `handoff_edges` (list of edge dicts, each with `sequence`, `from_agent`, `to_agent`).

**Returns:** `list[str]` — Ordered agent chain (e.g., `['orchestrator', 'primary-producer', 'quality-auditor']`). Empty list if no edges.

**Details:**

- Edges are sorted by `sequence` before chain reconstruction
- Chain is built as: `[edges[0].from_agent, edges[0].to_agent, edges[1].to_agent, ...]`

---

### `detect_behavioral_drift(trajectory, eval_suite, *, today=None)`

> *Source: `agentteams/behavioral_drift.py`*

Compare a recorded execution trajectory against the behavioral specification in an eval-suite.

**Args:**

- `trajectory` (`dict[str, Any]`) — Execution trajectory dict with:
  - `session_slug` (optional): Session identifier
  - `root_agent` (optional): Starting agent
  - `handoff_edges` (required): List of edge dicts with `sequence`, `from_agent`, `to_agent`, `payload_schema_id`, `mediated_by`

- `eval_suite` (`dict[str, Any]`) — Eval-suite dict from `eval_suite.build_eval_suite()`. Expected to contain `scenarios` with `category == "handoff"` and `predicate.kind == "handoff-chain"` entries.

- `today` (`date | None`, keyword-only) — Override date for Finding records (default: uses current date).

**Returns:** `list[Finding]` — List of Finding records. Empty list means conforming run; one or more Finding means divergence detected.

**Finding Details:**

Each Finding in the return list has:
- `code`: One of the BEHAVIOR_* constants
- `severity`: `"HARD"` (always; behavioral divergence is critical)
- `message`: Human-readable explanation

**Behavior:**

- If no handoff edges but eval-suite expects chains → yields `BEHAVIOR_NO_TRAJECTORY`
- If edges don't form a single contiguous chain → yields `BEHAVIOR_BROKEN_CHAIN` per gap
- If actual chain doesn't match any expected chain → yields `BEHAVIOR_CHAIN_DIVERGENCE`
- If expected orchestrator return/mediation is missing → yields `BEHAVIOR_MISSING_RETURN`
- Reuses Cluster C `audit_handoff_chain()` to check typed-payload continuity

---

## Typical Usage

```python
from agentteams.behavioral_drift import detect_behavioral_drift
from agentteams.eval_suite import build_eval_suite

# Load trajectory from Phase 1 replay substrate
trajectory = {
    "session_slug": "my-session",
    "handoff_edges": [
        {"sequence": 1, "from_agent": "orchestrator", "to_agent": "primary-producer", "payload_schema_id": "plan"},
        {"sequence": 2, "from_agent": "primary-producer", "to_agent": "quality-auditor", "payload_schema_id": "draft"},
        {"sequence": 3, "from_agent": "quality-auditor", "to_agent": "orchestrator", "payload_schema_id": "review", "mediated_by": "orchestrator"},
    ]
}

# Load eval spec from Phase 2
eval_suite = build_eval_suite(manifest)

# Compare
findings = detect_behavioral_drift(trajectory, eval_suite)

if findings:
    for f in findings:
        print(f"{f.code}: {f.message}")
else:
    print("✓ Run conforms to behavioral spec")
```

---

## Integration with eval_suite

The `behavioral_drift` module consumes the `handoff-chain` scenarios from the eval-suite. Each scenario specifies:

- `chain`: Expected agent sequence (e.g., `["orchestrator", "primary-producer", ...]`)
- `returns_to` (optional): Orchestrator or mediator that coordinates returns

The detector verifies:
1. Actual edges form a contiguous chain
2. Actual chain equals one expected chain (mediator-agnostic)
3. If `returns_to` is set, at least one edge is mediated by or returns to that agent

---

## Finding Reuse (Cluster C Integration)

Payload-schema continuity findings reuse the `Finding` dataclass from [`handoff_payloads`](handoff_payloads.md) and the `audit_handoff_chain()` function from Cluster C. This provides a uniform finding shape across template drift, behavioral drift, and payload validation.
