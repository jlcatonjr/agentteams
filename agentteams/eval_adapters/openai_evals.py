"""OpenAI Evals adapter (Cluster A Phase 2, increment 3).

Translates the framework-neutral eval-suite into an OpenAI-Evals-shaped
definition (registry entry + id + metrics + samples), emitted as JSON text.
Like the Inspect AI adapter this module imports no eval framework: the
coupling lives only in the emitted definition's ``class`` reference, which
names a structural grader the downstream integration supplies (the OpenAI
Evals analogue of the Inspect adapter's embedded scorer — Evals cannot embed
code in its registry, so the grader is referenced, not inlined).

JSON (not YAML) is emitted deliberately: stdlib-only, deterministic, and
trivially round-trippable in tests without a pyyaml dependency. The structure
mirrors the classic openai/evals registry: ``<eval>`` → metadata, ``<id>`` →
``{class, args:{samples:[...]}}``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OPENAI_EVALS_DEFINITION_VERSION = "1.0"

# The structural grader the downstream OpenAI Evals integration must register.
# Named, not inlined — Evals registries reference a class path.
STRUCTURAL_GRADER_CLASS = "agentteams_evals.structural:PredicateMatch"


def build_openai_evals_definition(suite: dict[str, Any]) -> dict[str, Any]:
    """Return an OpenAI-Evals-shaped dict for a framework-neutral *suite*.

    Pure; *suite* is read-only and unmodified.
    """
    project = suite.get("project_name", "") or "team"
    eval_name = f"{project}-team-behavior".lower().replace(" ", "-")
    eval_id = f"{eval_name}.v1"

    samples = [
        {
            "input": sc["claim"],
            "ideal": "True",
            "scenario_id": sc["id"],
            "category": sc["category"],
            "predicate": sc["predicate"],
        }
        for sc in suite.get("scenarios", [])
    ]

    return {
        "definition_schema_version": OPENAI_EVALS_DEFINITION_VERSION,
        "adapter": "openai-evals",
        "eval": eval_name,
        eval_name: {"id": eval_id, "metrics": ["accuracy"]},
        eval_id: {
            "class": STRUCTURAL_GRADER_CLASS,
            "args": {
                # AGENTTEAMS_TEAM_DIR is read by the structural grader at run
                # time, mirroring the Inspect AI adapter contract.
                "team_dir_env": "AGENTTEAMS_TEAM_DIR",
                "samples": samples,
            },
        },
    }


def render_openai_evals_definition(suite: dict[str, Any]) -> str:
    """Return the definition as deterministic JSON text. Pure."""
    return json.dumps(
        build_openai_evals_definition(suite), indent=2, sort_keys=False
    ) + "\n"


def write_openai_evals_definition(suite: dict[str, Any], path: Path | str) -> Path:
    """Write the rendered definition to *path*. Thin wrapper."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_openai_evals_definition(suite), encoding="utf-8")
    return p


__all__ = [
    "OPENAI_EVALS_DEFINITION_VERSION",
    "STRUCTURAL_GRADER_CLASS",
    "build_openai_evals_definition",
    "render_openai_evals_definition",
    "write_openai_evals_definition",
]
