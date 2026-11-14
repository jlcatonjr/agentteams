"""#453: the opt-in AR-01..AR-10 violation-code section of the agent-refactor template.

* Off (the default): the rendered agent-refactor file is byte-identical to the committed goldens.
* On: the file carries one "Violation Codes" section, inside the emitter's whole-body ``content``
  fence, with the ten codes, AR-05 at the
  brief's ``line_budget`` (default 200), and the code / file / evidence findings format.
* The brief and manifest schemas accept the key and reject bad values.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema
import pytest

from agentteams import agent_refactor_codes as arc
from agentteams.analyze import build_manifest
from agentteams.render import render_all

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "agentteams" / "templates"
TEMPLATE = TEMPLATES / "universal" / "agent-refactor.template.md"
GOLDEN_EXAMPLES = ["software-project", "research-project", "data-pipeline", "project-repositories"]


def _render_agent_refactor(description: dict) -> str:
    manifest = build_manifest(description)
    rendered = dict(render_all(manifest, templates_dir=TEMPLATES))
    [path] = [p for p in rendered if p.endswith("agent-refactor.agent.md")]
    return rendered[path]


def _emit_agent_refactor(example: str, tmp_path: Path) -> str:
    from tests.test_integration import _run_pipeline

    _run_pipeline(ROOT / "examples" / example / "brief.json", tmp_path)
    [path] = list(tmp_path.rglob("agent-refactor.agent.md"))
    return path.read_text(encoding="utf-8")


def test_template_places_the_placeholder_at_the_end_of_part_2() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert text.endswith("access level.{AGENT_REFACTOR_VIOLATION_CODES}\n")
    assert text.count("{AGENT_REFACTOR_VIOLATION_CODES}") == 1


@pytest.mark.parametrize("example", GOLDEN_EXAMPLES)
def test_default_render_is_byte_identical_to_the_golden(example: str, tmp_path: Path) -> None:
    golden = ROOT / "examples" / example / "expected" / "agent-refactor.agent.md"
    assert _emit_agent_refactor(example, tmp_path) == golden.read_text(encoding="utf-8")


@pytest.mark.parametrize("settings", [None, {}, {"violation_codes": False}, {"line_budget": 150}])
def test_codes_stay_off_unless_explicitly_true(settings) -> None:
    description = {"project_goal": "x"}
    if settings is not None:
        description["agent_refactor"] = settings
    assert arc.section(description) == ""
    assert "agent_refactor" not in build_manifest(description)
    assert "AR-01" not in _render_agent_refactor(description)


def test_opt_in_renders_the_ten_codes_once() -> None:
    text = _render_agent_refactor({"project_goal": "x", "agent_refactor": {"violation_codes": True}})
    assert text.count("### Violation Codes") == 1
    assert "AGENTTEAMS:" not in text  # no fence of its own: the emitter's content wrap covers it
    codes = re.findall(r"^\| (AR-\d\d) \|", text, flags=re.M)
    assert codes[:10] == [f"AR-{n:02d}" for n in range(1, 11)]
    assert "| Code | File | Evidence |" in text
    assert "Counts: AR-05 x1" in text
    assert "{AGENT_REFACTOR_VIOLATION_CODES}" not in text
    assert "{line_budget}" not in text
    assert "exceeds 200 lines" in text
    # The section follows Part 2's closing sentence, separated by one blank line.
    assert "access level.\n\n### Violation Codes\n" in text
    assert text.endswith("`Counts: AR-05 x1`\n")


def test_opt_in_section_sits_inside_the_emitted_content_fence() -> None:
    from agentteams.emit import _normalize_generated_content

    text = _render_agent_refactor({"project_goal": "x", "agent_refactor": {"violation_codes": True}})
    wrapped = _normalize_generated_content("agent-refactor.agent.md", text)
    body = wrapped.split("<!-- AGENTTEAMS:BEGIN content v=1 -->\n", 1)[1]
    body = body.split("<!-- AGENTTEAMS:END content -->", 1)[0]
    assert "### Violation Codes" in body and "| AR-10 |" in body


@pytest.mark.parametrize("turn_on", [True, False])
def test_update_merge_adds_and_removes_the_section(turn_on: bool) -> None:
    from agentteams.emit import _normalize_generated_content
    from agentteams.fences import _merge_fenced_content

    def emitted(on: bool) -> str:
        description = {"project_goal": "x", **({"agent_refactor": {"violation_codes": True}} if on else {})}
        return _normalize_generated_content("agent-refactor.agent.md", _render_agent_refactor(description))

    old, new = emitted(not turn_on), emitted(turn_on)
    result = _merge_fenced_content(new, old, rel_path="agent-refactor.agent.md")
    assert result.sections_replaced == ["content"]
    assert ("### Violation Codes" in result.merged_content) is turn_on
    assert bool(result.shrink_notices) is (not turn_on)


def test_line_budget_parameter_reaches_ar_05() -> None:
    description = {"project_goal": "x", "agent_refactor": {"violation_codes": True, "line_budget": 350}}
    text = _render_agent_refactor(description)
    assert "| AR-05 | Agent file exceeds 350 lines without justified extraction |" in text
    assert "(budget 350)" in text
    assert "200" not in arc.section(description)
    assert build_manifest(description)["agent_refactor"] == {"violation_codes": True, "line_budget": 350}


def test_manifest_records_the_default_budget() -> None:
    manifest = build_manifest({"project_goal": "x", "agent_refactor": {"violation_codes": True}})
    assert manifest["agent_refactor"] == {"violation_codes": True, "line_budget": arc.DEFAULT_LINE_BUDGET}


def _schema(name: str) -> dict:
    return json.loads((ROOT / "agentteams" / "schemas" / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["project-description.schema.json", "team-manifest.schema.json"])
def test_schemas_define_agent_refactor(name: str) -> None:
    prop = _schema(name)["properties"]["agent_refactor"]
    validator = jsonschema.Draft202012Validator({"type": "object", "properties": {"agent_refactor": prop}})
    assert validator.is_valid({"agent_refactor": {"violation_codes": True, "line_budget": 200}})
    for bad in ({"violation_codes": "yes"}, {"line_budget": 0}, {"line_budget": True}, {"typo": True}):
        assert not validator.is_valid({"agent_refactor": bad}), bad
