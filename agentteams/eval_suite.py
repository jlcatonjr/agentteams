"""Framework-neutral eval-suite emitter (Cluster A Phase 2, increment 1).

Derives per-team behavioral expectations from the team manifest. The output is
*framework-neutral by contract*: it must contain no Inspect AI / OpenAI Evals
DSL terms (a Phase 0 hard requirement). Adapters translate this neutral suite
into a concrete eval framework in later increments.

Pure: no I/O here. ``build_eval_suite`` is a deterministic function of the
manifest so it is trivially testable and excluded from drift by construction
(the caller writes it as a generator-owned artifact, never into
output_files_map / template_hashes / file_hashes).
"""

from __future__ import annotations

from typing import Any

EVAL_SUITE_SCHEMA_VERSION = "1.0"

# The worker-governance triad every workstream expert must wire in, and the
# orchestrator-return edge — matches the Phase 0 canonical-scenario governance
# predicate (tmp/by-week/2026-W21/cluster-a-phase-0).
WORKER_GOVERNANCE_TRIAD = ["primary-producer", "adversarial", "reference-manager"]
RETURN_TO_ORCHESTRATOR = "Return to Orchestrator"

# Tokens that would couple the neutral suite to a specific eval framework.
# Used by tests to enforce the framework-neutral contract.
FRAMEWORK_COUPLED_TOKENS = (
    "inspect_ai",
    "inspect-ai",
    "@task",
    "openai.evals",
    "oaieval",
    "evals.elsuite",
    "Eval(",
    "Solver(",
)


def build_eval_suite(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return a framework-neutral eval-suite dict derived from *manifest*.

    Conforms to ``schemas/eval-suite.schema.json``. Deterministic.
    """
    experts: list[str] = list(manifest.get("workstream_expert_slugs", []))
    components: list[dict[str, Any]] = manifest.get("components", [])

    scenarios: list[dict[str, Any]] = []

    if experts:
        scenarios.append({
            "id": "routing-orchestrator-knows-all-experts",
            "category": "routing",
            "claim": (
                "The orchestrator's agents: list includes every workstream "
                "expert, so it can route to each declared component."
            ),
            "predicate": {
                "kind": "frontmatter-list-contains-all",
                "file": "orchestrator.agent.md",
                "field": "agents",
                "values": experts,
            },
        })
        scenarios.append({
            "id": "routing-expert-count-matches-components",
            "category": "routing",
            "claim": (
                f"Exactly {len(experts)} workstream-expert agent file(s) are "
                f"emitted for {len(experts)} declared component(s)."
            ),
            "predicate": {
                "kind": "agent-count",
                "suffix": "-expert.agent.md",
                "count": len(experts),
            },
        })

    for comp in components:
        slug = comp.get("slug")
        if not slug:
            continue
        expert = f"{slug}-expert"
        upstreams = [
            f"{ref}-expert"
            for ref in comp.get("cross_refs", [])
            if ref
        ]
        if upstreams:
            scenarios.append({
                "id": f"handoff-{slug}",
                "category": "handoff",
                "claim": (
                    f"{expert} receives upstream from "
                    f"{', '.join(upstreams)} and returns to the orchestrator "
                    "(coordination is orchestrator-mediated, not peer-to-peer)."
                ),
                "predicate": {
                    "kind": "handoff-chain",
                    "chain": upstreams + [expert],
                    "returns_to": "orchestrator",
                },
            })
        scenarios.append({
            "id": f"governance-{slug}-triad-and-return",
            "category": "governance",
            "claim": (
                f"{expert} wires the worker-governance triad "
                f"({', '.join(WORKER_GOVERNANCE_TRIAD)}) and includes a "
                f"'{RETURN_TO_ORCHESTRATOR}' edge."
            ),
            "predicate": {
                "kind": "frontmatter-and-body",
                "file": f"{expert}.agent.md",
                "agents_contains_all": list(WORKER_GOVERNANCE_TRIAD),
                "body_contains": RETURN_TO_ORCHESTRATOR,
            },
        })

    return {
        "artifact_type": "eval-suite",
        "eval_suite_schema_version": EVAL_SUITE_SCHEMA_VERSION,
        "project_name": manifest.get("project_name", ""),
        "framework": manifest.get("framework", ""),
        "generated_from": "manifest",
        "scenarios": scenarios,
    }


__all__ = [
    "EVAL_SUITE_SCHEMA_VERSION",
    "WORKER_GOVERNANCE_TRIAD",
    "RETURN_TO_ORCHESTRATOR",
    "FRAMEWORK_COUPLED_TOKENS",
    "build_eval_suite",
]
