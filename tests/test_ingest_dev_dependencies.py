"""`devDependencies` are a stack signal, not a reason to emit a reference document each.

`_parse_package_json` categorised every entry in **both** `dependencies` and `devDependencies` as
`library`. `library` is in `_REFERENCE_CATEGORIES`, so each one was routed to the reference tier
and given its own `references/ref-<tool>-reference.md`. A mid-sized JavaScript project carries
dozens of lint plugins, type stubs and formatter configs, and each was producing a document.

The fix categorises `devDependencies` as `other` — passive by default — while leaving them in
`tools[]`, so stack inference still sees them. It is a *default*, not a filter: the name-based
promotion in `classify_tool_importance` still fires, which is what keeps the tools that matter.

`other` rather than a new `dev-tool` value because `category` is enum-constrained in
`schemas/project-description.schema.json`, and the schema outranks the pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentteams.analyze import classify_tool_importance
from agentteams.ingest import _parse_package_json


def _tiers(pkg: dict) -> dict[str, str]:
    return {t["name"]: classify_tool_importance(t) for t in _parse_package_json(json.dumps(pkg))}


def test_runtime_dependencies_still_reach_the_reference_tier():
    tiers = _tiers({"dependencies": {"react": "^18.0.0", "some-runtime-lib": "^1.0.0"}})
    assert tiers["react"] == "reference"
    assert tiers["some-runtime-lib"] == "reference", "category=library must still route here"


def test_the_dev_dependency_long_tail_goes_passive():
    """The actual complaint: one reference document per lint plugin."""
    tiers = _tiers({"devDependencies": {
        "eslint-plugin-import": "^2", "@types/node": "^20", "prettier": "^3",
    }})
    assert set(tiers.values()) == {"passive"}


def test_dev_dependencies_are_still_ingested_as_tools():
    """Demotion, not deletion — dev tooling remains a real signal about the stack."""
    parsed = _parse_package_json(json.dumps({"devDependencies": {"prettier": "^3.0.0"}}))
    assert [t["name"] for t in parsed] == ["prettier"]
    assert parsed[0]["version"] == "3.0.0", "version parsing must be unaffected"


def test_named_tools_are_still_promoted_from_devdependencies():
    """Where the tools that matter actually live. A section is not a judgement of importance."""
    tiers = _tiers({"devDependencies": {
        "typescript": "^5.0.0",   # the compiler
        "vite": "^5.0.0",         # the bundler
        "jest": "^29.0.0",        # the test framework
    }})
    assert tiers == {"typescript": "reference", "vite": "reference", "jest": "reference"}


def test_promotion_is_to_reference_not_specialist():
    """Preserving the prior tier, not escalating it — a bundler gets no dedicated agent."""
    tiers = _tiers({"devDependencies": {"webpack": "^5", "rollup": "^4", "esbuild": "^0.20"}})
    assert set(tiers.values()) == {"reference"}


def _schema_category_enum() -> set[str]:
    """Pull the allowed `category` values out of the schema itself, not a copy of them."""
    schema = json.loads(
        (Path(__file__).resolve().parents[1]
         / "schemas" / "project-description.schema.json").read_text(encoding="utf-8")
    )
    found: list[list[str]] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            cat = node.get("category")
            if isinstance(cat, dict) and isinstance(cat.get("enum"), list):
                found.append(cat["enum"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    assert found, "schema no longer constrains `category` — this guard needs rewriting"
    return set(found[0])


def test_the_category_stays_inside_the_schema_enum():
    """`category` is enum-constrained by a higher authority than this module."""
    parsed = _parse_package_json(json.dumps(
        {"dependencies": {"a": "^1"}, "devDependencies": {"b": "^1"}}
    ))
    assert {t["category"] for t in parsed} <= _schema_category_enum()
