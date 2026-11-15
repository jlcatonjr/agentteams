"""The project-description schema matches the code and is checked when a brief is loaded (IM item 13a, G-17-2).

``ingest.validate`` checked three fields and the schema was never run on a brief. It had also drifted: its archetype
enum lacked seven archetypes agentteams renders (tool-doc-researcher among them, which keyword selection picks), and its
tool categories rejected "infrastructure" and "build". 98 of 159 descriptors in the workspace failed it. Warn mode only
this release; refusal comes once the census is clean.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams import brief_schema, ingest

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = sorted((REPO / "examples").glob("*/brief.json"))


@pytest.fixture(autouse=True)
def _fresh_warnings():
    brief_schema._warned.clear()
    yield
    brief_schema._warned.clear()


def test_the_schema_s_known_archetypes_are_exactly_the_domain_agent_templates():
    # IM: derive or pin, so the list can never drift again.
    schema = json.loads(brief_schema.SCHEMA_PATH.read_text(encoding="utf-8"))
    documented = schema["properties"]["selected_archetypes"]["x-agentteams-known-archetypes"]
    assert documented == sorted(brief_schema.known_archetypes())
    assert "enum" not in schema["properties"]["selected_archetypes"]["items"]


def test_every_archetype_the_analyzer_can_select_is_known():
    from agentteams import analyze

    selectable = {slug for _, slug in analyze._ARCHETYPE_TRIGGERS} | set(analyze._MODULE_DOC_ARCHETYPES)
    selectable |= {"retrieval-integrator", "research-analyst", "agentteams-updater", "vuln-remediation"}
    assert selectable <= brief_schema.known_archetypes()


@pytest.mark.parametrize("brief", EXAMPLES, ids=lambda p: p.parent.name)
def test_every_example_brief_conforms(brief):
    assert brief_schema.schema_errors(json.loads(brief.read_text(encoding="utf-8"))) == []


@pytest.mark.parametrize("patch", [{"selected_archetypes": ["primary-producer", "tool-doc-researcher",
                                                            "agentteams-updater", "work-summarizer"]},
                                   {"tools": [{"name": "Terraform", "category": "infrastructure"},
                                              {"name": "make", "category": "build"}]}])
def test_what_the_code_supports_now_validates(patch):
    doc = {**json.loads(EXAMPLES[0].read_text(encoding="utf-8")), **patch}
    assert brief_schema.schema_errors(doc) == []


def _write(tmp_path: Path, doc: dict) -> Path:
    p = tmp_path / "brief.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def test_an_invalid_brief_warns_on_load_and_still_loads(tmp_path, capsys):
    doc = {**json.loads(EXAMPLES[0].read_text(encoding="utf-8")), "project_name": "Has Spaces",
           "tools": [{"name": "x", "category": "pipeline"}]}
    loaded = ingest.load(_write(tmp_path, doc), scan_project=False)
    assert loaded["project_name"] == "Has Spaces"              # warn mode: nothing refused
    err = capsys.readouterr().err
    assert "does not match the project-description schema (2 issue(s); not enforced yet)" in err
    assert "project_name" in err and "tools/0/category" in err


def test_a_conforming_brief_loads_silently(tmp_path, capsys):
    ingest.load(_write(tmp_path, json.loads(EXAMPLES[0].read_text(encoding="utf-8"))), scan_project=False)
    assert capsys.readouterr().err == ""


def test_an_archetype_agentteams_does_not_render_is_named_not_refused(tmp_path, capsys):
    doc = {**json.loads(EXAMPLES[0].read_text(encoding="utf-8")),
           "selected_archetypes": ["primary-producer", "interpretation-advisor"]}
    ingest.load(_write(tmp_path, doc), scan_project=False)
    err = capsys.readouterr().err
    assert "[INFO]" in err and "interpretation-advisor" in err and "another layer may provide" in err
    assert "does not match" not in err                         # a lowercase slug is schema-valid


def test_the_warnings_print_once_per_path(tmp_path, capsys):
    path = _write(tmp_path, {**json.loads(EXAMPLES[0].read_text(encoding="utf-8")), "project_name": "Has Spaces"})
    ingest.load(path, scan_project=False)
    ingest.load(path, scan_project=False)
    assert capsys.readouterr().err.count("[WARN]") == 1


def test_check_returns_zero_for_a_brief_with_only_schema_warnings(tmp_path, capsys):
    # IM: the informational WARN must never make --check non-zero.
    import build_team

    doc = {**json.loads(EXAMPLES[0].read_text(encoding="utf-8")), "selected_archetypes": ["primary-producer",
                                                                                       "interpretation-advisor"]}
    brief = _write(tmp_path, doc)
    out = tmp_path / ".github" / "agents"
    assert build_team.main(["--description", str(brief), "--project", str(tmp_path), "--output", str(out),
                            "--no-scan", "--yes", "--security-offline", "--dry-run"]) == 0
    assert "interpretation-advisor" in capsys.readouterr().err


# --- audit follow-ups ------------------------------------------------------------------------------------------

def test_the_fleet_report_carries_the_schema_warning(tmp_path, capsys):
    # @adversarial: the fleet captures each render's output, so a printed WARN never reached the operator.
    path = _write(tmp_path, {**json.loads(EXAMPLES[0].read_text(encoding="utf-8")), "project_name": "Has Spaces"})
    ingest.load(path, scan_project=False)
    lines = brief_schema.schema_warning_lines(capsys.readouterr().err)
    assert len(lines) == 1 and "project_name" in lines[0] and not lines[0].startswith("[WARN]")
    from agentteams.fleet import TargetResult

    assert TargetResult(workspace="w", target="github", status="OK").schema_warnings == []


def test_a_long_issue_is_clipped_so_brief_values_are_not_echoed_whole():
    doc = {**json.loads(EXAMPLES[0].read_text(encoding="utf-8")), "project_name": "x " + "/abs/path/" * 50}
    issue = next(e for e in brief_schema.schema_errors(doc) if e.startswith("project_name"))
    assert len(issue) <= brief_schema._MAX_ISSUE and issue.endswith("…")


def test_a_check_error_is_a_warning_not_a_crash(tmp_path, capsys, monkeypatch):
    # @adversarial: warn_on_load must never raise, or a warn-only check could fail a load.
    def broken(_description):
        raise TypeError("unexpected value")

    monkeypatch.setattr(brief_schema, "schema_errors", broken)
    ingest.load(_write(tmp_path, json.loads(EXAMPLES[0].read_text(encoding="utf-8"))), scan_project=False)
    assert "schema check was skipped (TypeError)" in capsys.readouterr().err


def test_the_capability_the_code_reads_validates():
    doc = {**json.loads(EXAMPLES[0].read_text(encoding="utf-8")), "capabilities": ["instance_maintenance"]}
    assert brief_schema.schema_errors(doc) == []


def test_the_manifest_schema_accepts_what_the_analyzer_builds():
    # @conflict-auditor: team-manifest.schema.json kept the stale archetype enum and tool categories.
    import jsonschema

    schema = json.loads((brief_schema.SCHEMA_PATH.parent / "team-manifest.schema.json").read_text(encoding="utf-8"))
    archetypes = schema["properties"]["selected_archetypes"]
    assert "enum" not in archetypes["items"]
    jsonschema.validate(["agentteams-updater", "tool-doc-researcher", "interpretation-advisor"], archetypes)
    text = json.dumps(schema)
    assert text.count('"infrastructure"') >= 2 and text.count('"build",') >= 2
