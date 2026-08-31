# `eval_suite` — AgentTeamsModule

Generate framework-neutral behavioral evaluation specifications for agent teams.

Builds a suite of expected behaviors that agent teams should exhibit during execution, derived from the team manifest. Used by [`behavioral_drift`](behavioral-drift.md) to verify that a recorded execution trajectory matches the specification. The output is deliberately framework-agnostic (no Inspect AI, OpenAI Evals, or framework-specific DSL terms).

> *Source: `agentteams/eval_suite.py`*

---

## Constants

### `EVAL_SUITE_SCHEMA_VERSION`

> *Source: `agentteams/eval_suite.py`*

Current schema version for eval-suite artifacts.

**Type:** `str`

---

### `WORKER_GOVERNANCE_TRIAD`

The standard governance agents every workstream expert must coordinate with.

**Type:** `list[str]`  
**Value:** `["primary-producer", "adversarial", "reference-manager"]`

---

### `RETURN_TO_ORCHESTRATOR`

> *Source: `agentteams/eval_suite.py`*

The literal edge label every governance scenario checks for in the expert's body — matches the Phase 0 canonical-scenario governance predicate documented in `orchestrator.agent.md` (Workflow 1, steps 6–8).

**Type:** `str`  
**Value:** `"Return to Orchestrator"`

---

## Functions

### `build_eval_suite(manifest)`

> *Source: `agentteams/eval_suite.py`*

Build a framework-neutral evaluation suite from a team manifest.

**Args:**

- `manifest` (`dict[str, Any]`) — Team manifest from [`analyze.build_manifest()`](analyze.md).

**Returns:** `dict[str, Any]` — Eval-suite dict conforming to `schemas/eval-suite.schema.json`. All keys below are required by the schema:
- `artifact_type`: `"eval-suite"`
- `eval_suite_schema_version`: Current schema version
- `project_name`: The manifest's `project_name` (empty string if absent)
- `framework`: The manifest's `framework` (empty string if absent)
- `generated_from`: Always `"manifest"`
- `scenarios`: List of behavioral scenario dicts (routing, handoff, governance expectations)

**Behavior Notes:**

- Pure function; no I/O or external calls
- Generated deterministically from the manifest (no randomness)
- Scenarios include:
  - **Routing scenarios** — Verify that orchestrator knows all experts; agent count matches components
  - **Handoff scenarios** — Expected agent chains for each component; return-to orchestrator mediation
  - **Governance scenarios** — Workstream experts coordinate with the governance triad

---

## Typical Usage

```python
from agentteams import analyze
from agentteams.eval_suite import build_eval_suite
from agentteams.behavioral_drift import detect_behavioral_drift

# Build manifest
description = {"name": "my-project", ...}
manifest = analyze.build_manifest(description)

# Generate eval spec
eval_suite = build_eval_suite(manifest)

# Later: verify a recorded execution against the spec
trajectory = {...}  # from Phase 1 replay substrate
findings = detect_behavioral_drift(trajectory, eval_suite)

if findings:
    print(f"❌ {len(findings)} behavioral divergences found")
else:
    print("✓ Execution conforms to spec")
```

---

## Framework Neutrality

The eval-suite is designed to be **framework-agnostic**: it contains no terms from Inspect AI, OpenAI Evals, or any other concrete eval framework. This allows:

1. Adapters to translate the neutral spec into framework-specific formats
2. Deterministic testing of the spec itself
3. Reusability across eval frameworks without re-derivation

Framework-specific adapters are provided by [`agentteams.eval_adapters`](eval-adapters.md); this module remains neutral and adapter-free.

The contract is enforced by the `FRAMEWORK_COUPLED_TOKENS` constant — a tuple of the terms an eval-suite must never contain (`inspect_ai`, `inspect-ai`, `@task`, `openai.evals`, `oaieval`, `evals.elsuite`, `Eval(`, `Solver(`). Tests scan every generated suite for these tokens to catch a framework-specific term leaking into the neutral spec.

---

## Adapter Integration

To convert the neutral suite into a runnable framework artifact, pass it to an
adapter in `agentteams.eval_adapters` — that reference documents the
`write_inspect_ai_module` / `write_openai_evals_definition` entry points and their
code examples, so they are not duplicated here.

The end-to-end pattern:

1. Build neutral suite with `build_eval_suite(manifest)`.
2. Convert through target adapter in `agentteams.eval_adapters`.
3. Execute with your selected eval runtime/tooling.

---

## See also

- [`model_routing`](model-routing.md) — the sibling cost/capability contract built from the same manifest.
