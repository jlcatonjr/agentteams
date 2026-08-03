"""test_self_brief_validates.py — the module's own brief must satisfy the module's own schema.

`.github/agents/_build-description.json` is what `--self` reads, so it is simultaneously this
project's team configuration and the most-exercised example of the input format. It did not
validate: it declares `doc_site_config_file`, a key `analyze.build_manifest` and
`manifest_format` both read, which the schema never listed while setting
`additionalProperties: false`. Every brief using that supported key failed validation, and
nothing noticed because nothing validated this one.

Also pins the two capabilities the deployed team depends on. `@cohesion-repairer` and
`@retrieval-integrator` are listed as team members in `.claude/CLAUDE.md` but were not emitted
by this brief, so both sat in `.claude/agents` as orphans that no advisory could see. They are
gated on `retrieval_integration.mode` and a documentation deliverable respectively; if either
declaration is dropped, the files silently become orphans again.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BRIEF = REPO_ROOT / ".github/agents/_build-description.json"
SCHEMA = REPO_ROOT / "schemas/project-description.schema.json"


def _brief() -> dict:
    return json.loads(BRIEF.read_text(encoding="utf-8"))


def test_the_self_brief_validates_against_the_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    if not BRIEF.exists():
        pytest.skip("self brief absent")
    jsonschema.validate(_brief(), json.loads(SCHEMA.read_text(encoding="utf-8")))


def test_every_brief_key_is_declared_in_the_schema() -> None:
    """The specific failure mode: a key the pipeline honours that the schema rejects.

    Stated as its own assertion rather than left to the validator, because the validator's
    message names one key at a time and this lists them all.
    """
    if not BRIEF.exists():
        pytest.skip("self brief absent")
    declared = set(json.loads(SCHEMA.read_text(encoding="utf-8"))["properties"])
    undeclared = sorted(set(_brief()) - declared)
    assert not undeclared, (
        f"brief keys absent from project-description.schema.json: {undeclared}. "
        "The schema sets additionalProperties:false, so these make it invalid."
    )


def test_the_capabilities_the_deployed_team_depends_on_stay_declared() -> None:
    """Dropping either declaration silently re-orphans a documented team member."""
    if not BRIEF.exists():
        pytest.skip("self brief absent")
    brief = _brief()

    mode = brief.get("retrieval_integration", {}).get("mode", "none")
    assert mode != "none", (
        "retrieval_integration.mode is 'none', so analyze.py will not emit "
        "@retrieval-integrator — but .claude/CLAUDE.md lists it as a team member"
    )

    deliverables = " ".join(brief.get("deliverables", [])).lower()
    assert any(k in deliverables for k in ("documentation", "doc", "book", "writing")), (
        "no documentation deliverable declared, so analyze.py will not emit "
        "@cohesion-repairer — but .claude/CLAUDE.md lists it as a team member"
    )


def test_the_retrieval_entrypoints_resolve() -> None:
    """No aspirational entries: every `module::symbol` named must actually exist.

    `@retrieval-integrator` validates against this contract, so a wrong entrypoint is worse
    than an absent one.
    """
    if not BRIEF.exists():
        pytest.skip("self brief absent")
    import importlib

    contract = _brief().get("retrieval_integration", {})
    missing = []
    for field in ("query_entrypoints", "maintenance_entrypoints"):
        for entry in contract.get(field, []):
            if "::" not in entry:
                continue  # a CLI invocation, checked below
            mod_path, symbol = entry.split("::", 1)
            mod_name = mod_path.removesuffix(".py").replace("/", ".")
            try:
                mod = importlib.import_module(mod_name)
            except ImportError:
                missing.append(f"{entry} (module {mod_name} not importable)")
                continue
            if not callable(getattr(mod, symbol, None)):
                missing.append(f"{entry} (no callable {symbol})")
    assert not missing, f"retrieval contract names things that do not exist: {missing}"


def test_the_declared_cli_flags_exist() -> None:
    """The other half of the contract: `build_team.py --refresh-index` must be a real flag."""
    if not BRIEF.exists():
        pytest.skip("self brief absent")
    from agentteams.cli import parser as _parser

    source = Path(_parser.__file__).read_text(encoding="utf-8")
    contract = _brief().get("retrieval_integration", {})
    missing = []
    for field in ("query_entrypoints", "maintenance_entrypoints"):
        for entry in contract.get(field, []):
            for token in entry.split():
                if token.startswith("--") and f'"{token}"' not in source:
                    missing.append(token)
    assert not missing, f"retrieval contract names CLI flags that do not exist: {missing}"
