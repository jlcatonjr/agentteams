# `model_routing` — AgentTeamsModule

Generate framework-neutral model-routing contracts for agent cost/capability tiering.

Assigns each agent in a team to a tier role (`cheap`, `primary`, `fallback`) based on manifest governance classification. The contract is emitted only when the caller passes `--cost-routing`; this module is pure and never decides on its own.

> *Source: `agentteams/model_routing.py`*

---

## Design Principles

- **Framework-neutral** — Assigns tier *roles*, not concrete model strings
- **Off by default** — Only emitted when explicitly requested (`--cost-routing` in CLI)
- **Deterministic** — Pure functions; same manifest always yields same routing
- **Conservative** — Unknown agents default to `primary` (never downgraded)
- **Governance-driven** — Tier rule is derived purely from the manifest; no hardcoded archetype lists

---

## Constants

### `ROUTING_SCHEMA_VERSION`

> *Source: `agentteams/model_routing.py`*

Current schema version for routing contract artifacts. Used to detect compatibility between build and consumer versions.

**Type:** `str`  
**Current value:** `"1.0"`

---

### `MODEL_TIERS`

> *Source: `agentteams/model_routing.py`*

Tuple of recognized tier role names.

**Type:** `tuple[str, ...]`  
**Value:** `("primary", "cheap", "fallback")`

---

## Functions

### `agent_tier(slug, manifest)`

> *Source: `agentteams/model_routing.py`*

Determine the tier role for a single agent, pure.

**Args:**

- `slug` (`str`) — Agent slug (e.g., `"quality-auditor"`).
- `manifest` (`dict[str, Any]`) — Team manifest dict from [`analyze.build_manifest()`](analyze.md).

**Returns:** `str` — Tier role: one of `MODEL_TIERS` (`"primary"`, `"cheap"`, `"fallback"`).

**Rule:**

- If `slug` is in `manifest['governance_agents']` → `"cheap"` (read-only, structured, cost-optimizable)
- Otherwise → `"primary"` (conservative; unknown agents stay on primary tier)

**Notes:**

- No hardcoded archetype list; rule is data-driven from the manifest
- `"fallback"` is declared in `MODEL_TIERS` but currently not assigned (reserved for future use)

---

### `build_routing_contract(manifest)`

> *Source: `agentteams/model_routing.py`*

Build a framework-neutral model-routing contract dict from a team manifest.

**Args:**

- `manifest` (`dict[str, Any]`) — Team manifest dict from [`analyze.build_manifest()`](analyze.md).

**Returns:** `dict[str, Any]` — Routing contract with keys:
- `artifact_type`: `"model-routing"`
- `routing_schema_version`: Current schema version
- `project_name`: Copied from manifest
- `framework`: Copied from manifest
- `tiers`: List of all recognized tier roles (from `MODEL_TIERS`)
- `assignments`: List of dicts, each with `agent` (slug) and `tier` (role)

**Behavior:**

- Pure function; no network/I/O
- Processes all agents in `manifest['agent_slug_list']`
- Order matches the manifest's agent list (stable, deterministic)

---

## Typical Usage

```python
from agentteams import analyze
from agentteams.model_routing import build_routing_contract

# Build manifest
description = {"name": "my-project", ...}
manifest = analyze.build_manifest(description)

# Generate routing contract (optional; only if cost tiering needed)
routing = build_routing_contract(manifest)

# Inspect assignments
for assignment in routing['assignments']:
    agent, tier = assignment['agent'], assignment['tier']
    print(f"{agent:30} → {tier}")

# Output example:
# orchestrator                  → primary
# @navigator                    → cheap
# @code-hygiene                 → cheap
# @primary-producer             → primary
# @quality-auditor              → primary
```

---

## Integration with build_team.py

The CLI emits this contract only when `--cost-routing` is passed:

```bash
python build_team.py --description brief.json --cost-routing
# Writes: .github/agents/references/model-routing.json
```

---

## Downstream Consumption

The routing contract is typically consumed by:

1. **VS Code Copilot** — Map tier roles to concrete model strings in settings
2. **Claude API** — Route governance agents to a cheaper model (e.g. Claude Haiku 4.5, `claude-haiku-4-5`); domain agents to a stronger one (e.g. Claude Sonnet 4.6, `claude-sonnet-4-6`). *Illustrative only — `model_routing.py` emits tier roles, never concrete model strings; the adapter chooses the model.*
3. **Cost analysis tools** — Compute expected token costs per tier
4. **Policy enforcement** — Ensure sensitive agents (auditors, security) stay on premium tiers

The contract does **not** specify concrete models; that's the runtime/adapter's responsibility.

---

## Downstream Integration Examples

### Runtime model selection map

> The concrete model ids below are **illustrative only** and may be stale. `model_routing.py` emits tier *roles* and never any model string — the runtime/adapter owns the tier→model mapping. Substitute whatever models your runtime uses (e.g. for Claude: `claude-haiku-4-5` for `cheap`, `claude-sonnet-4-6` or `claude-opus-4-8` for `primary`).

```python
from agentteams.model_routing import build_routing_contract

routing = build_routing_contract(manifest)

# Illustrative mapping only — choose models for your own runtime.
tier_to_model = {
    "cheap": "gpt-4.1-mini",
    "primary": "gpt-5.3-codex",
    "fallback": "gpt-4.1-mini",
}

resolved = {
    item["agent"]: tier_to_model[item["tier"]]
    for item in routing["assignments"]
}
```

### Combined eval + routing export pattern

```python
from pathlib import Path
from agentteams.eval_suite import build_eval_suite
from agentteams.model_routing import build_routing_contract
from agentteams.eval_adapters.openai_evals import write_openai_evals_definition

suite = build_eval_suite(manifest)
routing = build_routing_contract(manifest)

write_openai_evals_definition(suite, Path("references/evals/openai_evals_definition.json"))
Path("references/model-routing.json").write_text(__import__("json").dumps(routing, indent=2) + "\n", encoding="utf-8")
```

This pattern keeps behavioral verification (`eval-suite`) and cost/capability policy (`model-routing`) decoupled but composable.

---

## Tier Semantics

> Model names in the guidance column are **illustrative only** and may be stale — `model_routing.py` assigns tier roles, not models. The runtime/adapter maps roles to whatever models it uses.

| Tier | Semantic | Typical Agents | Model Guidance (illustrative) |
|------|----------|---|---|
| `primary` | Default; full capability | orchestrator, primary-producer, domain experts | Latest/largest models (e.g. Claude Sonnet 4.6 / Claude Opus 4.8) |
| `cheap` | Read-only, structured | governance agents (auditors, validators, hygiene checkers) | Smaller/faster models (e.g. Claude Haiku 4.5) |
| `fallback` | Reserved | (none currently) | Minimal fallback models or cached responses |

---

## Schema Note

The routing contract schema is not yet released as a standalone `.schema.json`. Follow `routing_schema_version` for version tracking and compatibility checks.
