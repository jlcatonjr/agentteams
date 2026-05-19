"""Cost / model-routing contract (Cluster B / catalog #6).

OFF by default. Only emitted when the caller passes ``--cost-routing``; this
module is pure and never decides on its own. The contract is **framework-
neutral**: it assigns each agent a tier *role* (``cheap`` / ``primary``), not a
concrete model string — model resolution is the runtime/adapter's job, exactly
like the eval-suite neutrality contract. AgentTeams is a generator: it emits
the routing contract; it does not route.

Tier rule (derived purely from the manifest, no hardcoded archetype list):
read-only/structured governance agents (``manifest['governance_agents']``)
run on the ``cheap`` tier; everything else — orchestrator, workstream
experts, primary-producer, domain/support agents — stays ``primary``
(conservative: an unknown agent is never downgraded).
"""

from __future__ import annotations

from typing import Any

ROUTING_SCHEMA_VERSION = "1.0"
MODEL_TIERS = ("primary", "cheap", "fallback")


def agent_tier(slug: str, manifest: dict[str, Any]) -> str:
    """Return the tier role for *slug*. Governance ⇒ cheap; else primary."""
    governance = set(manifest.get("governance_agents", []))
    return "cheap" if slug in governance else "primary"


def build_routing_contract(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return the framework-neutral model-routing contract dict. Pure."""
    slugs = list(manifest.get("agent_slug_list", []))
    assignments = [
        {"agent": s, "tier": agent_tier(s, manifest)}
        for s in slugs
    ]
    return {
        "artifact_type": "model-routing",
        "routing_schema_version": ROUTING_SCHEMA_VERSION,
        "project_name": manifest.get("project_name", ""),
        "framework": manifest.get("framework", ""),
        "tiers": list(MODEL_TIERS),
        "assignments": assignments,
    }


__all__ = [
    "ROUTING_SCHEMA_VERSION",
    "MODEL_TIERS",
    "agent_tier",
    "build_routing_contract",
]
