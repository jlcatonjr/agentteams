"""Framework-enum consistency between JSON schemas and the adapter registry.

Regression test for the recurring enum-staleness bug, found independently in
both team-manifest.schema.json and agent-cai.schema.json (durable-canonical-
agent-format plan, step A.3). The registry (agentteams/frameworks/registry.py)
is the single source of truth (CH-05); every schema framework enum must match
FRAMEWORK_IDS exactly, except agent-cai.schema.json's source_framework enum,
which additionally carries the 'canonical' pseudo-framework (plan section 5.6:
a deliberate, named exception — canonical is not a rendering adapter).
"""

from __future__ import annotations

import json
from pathlib import Path

from agentteams.frameworks.registry import FRAMEWORK_IDS

_SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"


def _load_schema(name: str) -> dict:
    return json.loads((_SCHEMAS_DIR / name).read_text(encoding="utf-8"))


def test_registry_ids_are_unique():
    assert len(set(FRAMEWORK_IDS)) == len(FRAMEWORK_IDS)


def test_team_manifest_framework_enum_matches_registry():
    schema = _load_schema("team-manifest.schema.json")
    enum = tuple(schema["properties"]["framework"]["enum"])
    assert enum == FRAMEWORK_IDS, (
        f"team-manifest.schema.json framework enum {enum!r} drifted from "
        f"frameworks/registry.py FRAMEWORK_IDS {FRAMEWORK_IDS!r}"
    )


def test_agent_cai_source_framework_enum_matches_registry_plus_canonical():
    schema = _load_schema("agent-cai.schema.json")
    enum = tuple(schema["properties"]["source_framework"]["enum"])
    expected = FRAMEWORK_IDS + ("canonical",)
    assert enum == expected, (
        f"agent-cai.schema.json source_framework enum {enum!r} drifted from "
        f"FRAMEWORK_IDS + 'canonical' {expected!r}"
    )
