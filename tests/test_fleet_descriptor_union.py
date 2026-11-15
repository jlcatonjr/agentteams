"""A re-render never silently drops the roster the stub records (IM item 12, G-17-1).

``fleet._resolve_descriptor`` takes the root ``brief.json`` whole when it exists. In researchteam-derived repos the
roster (``selected_archetypes`` and friends) lives only in ``.github/agents/_build-description.json``, so a brief-only
re-render re-ran keyword selection and dropped agents, and ``--yes`` applied the shrunken roster.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import pytest

from agentteams import descriptor_union, fleet

REPO = Path(__file__).resolve().parents[1]
BRIEF = REPO / "examples" / "software-project" / "brief.json"
#: keyword selection for BRIEF, plus two archetypes it never picks: the roster the stub records.
SELECTED = ["primary-producer", "quality-auditor", "cohesion-repairer", "technical-validator", "format-converter",
            "output-compiler", "tool-doc-researcher"]
STUB_ROSTER = SELECTED + ["reference-manager", "visual-designer"]


def _project(tmp_path: Path, *, stub: dict | None = None, brief: dict | None = None) -> Path:
    ws = tmp_path / "ws"
    agents = ws / ".github" / "agents"
    agents.mkdir(parents=True)
    (agents / "a.agent.md").write_text("x\n", encoding="utf-8")
    doc = json.loads(BRIEF.read_text(encoding="utf-8")) if brief is None else brief
    (ws / "brief.json").write_text(json.dumps(doc), encoding="utf-8")
    if stub is not None:
        (agents / "_build-description.json").write_text(json.dumps(stub), encoding="utf-8")
    return ws


# --- the union -------------------------------------------------------------------------------------------------

def test_union_takes_absent_roster_keys_from_the_stub_and_keeps_the_brief_s_own():
    brief = {"project_name": "P", "write_policy": "orchestrator-only"}
    stub = {"project_name": "stale", "selected_archetypes": ["a", "b"], "governance_agents": ["security"],
            "write_policy": "open", "retrieval_integration": {}}
    merged, injected = descriptor_union.union(brief, stub)
    assert injected == ["selected_archetypes", "governance_agents"]
    assert merged["selected_archetypes"] == ["a", "b"] and merged["governance_agents"] == ["security"]
    assert merged["project_name"] == "P"                 # content: the brief wins
    assert merged["write_policy"] == "orchestrator-only"  # a roster key the brief sets: the brief wins
    assert "retrieval_integration" not in merged          # an empty stub value adds nothing
    assert "selected_archetypes" not in brief             # the input is not modified


def test_the_fleet_renders_a_brief_only_workspace_with_the_stub_s_roster(tmp_path):
    ws = _project(tmp_path, stub={"selected_archetypes": STUB_ROSTER})
    before = sorted(p.relative_to(ws) for p in ws.rglob("*"))
    out = descriptor_union.for_fleet(ws, fleet._resolve_descriptor(ws))
    assert out is not None and out.name == "brief.json" and ws not in out.parents   # keeps --no-scan; not in ws
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["selected_archetypes"] == STUB_ROSTER
    assert doc["project_name"] == json.loads(BRIEF.read_text(encoding="utf-8"))["project_name"]
    assert sorted(p.relative_to(ws) for p in ws.rglob("*")) == before                # nothing written into ws


@pytest.mark.parametrize("case", ["brief has its own roster", "no stub", "stub adds nothing", "descriptor is the stub"])
def test_the_fleet_keeps_the_resolved_descriptor_when_there_is_nothing_to_add(tmp_path, case):
    doc = json.loads(BRIEF.read_text(encoding="utf-8"))
    if case == "brief has its own roster":
        ws = _project(tmp_path, stub={"selected_archetypes": STUB_ROSTER}, brief={**doc, "selected_archetypes": SELECTED})
    elif case == "no stub":
        ws = _project(tmp_path)
    elif case == "stub adds nothing":
        ws = _project(tmp_path, stub={"project_name": "x"})
    else:
        ws = _project(tmp_path, stub={"selected_archetypes": STUB_ROSTER})
        (ws / "brief.json").unlink()
    resolved = fleet._resolve_descriptor(ws)
    assert descriptor_union.for_fleet(ws, resolved) == resolved


def test_run_fleet_passes_the_union_to_the_render_and_records_the_real_source(tmp_path, monkeypatch):
    ws = _project(tmp_path / "parent", stub={"selected_archetypes": STUB_ROSTER})
    seen: list[dict] = []

    def fake_main(argv):
        seen.append(json.loads(Path(argv[argv.index("--description") + 1]).read_text(encoding="utf-8")))
        return 0, "ok"

    monkeypatch.setattr(fleet, "_run_main", fake_main)

    class _Args:
        fleet, fleet_frameworks, fleet_report = str(ws.parent), "github", str(tmp_path / "_report")
        yes, dry_run, shrink_policy, fleet_allow_no_verify = False, False, "preserve", False
        fleet_exclude = fleet_ignore_locks = None
        fleet_skip_parent = False

    fleet.run_fleet(_Args(), parser=None)
    assert seen and seen[0]["selected_archetypes"] == STUB_ROSTER
    report = json.loads(next((tmp_path / "_report").rglob("*.json")).read_text(encoding="utf-8"))
    assert str(ws / "brief.json") in json.dumps(report)          # the record names the real descriptor


# --- the dual-descriptor advisory ------------------------------------------------------------------------------

def test_the_dual_descriptor_advisory_reports_a_roster_divergence(tmp_path, capsys):
    import build_team

    doc = json.loads(BRIEF.read_text(encoding="utf-8"))
    ws = _project(tmp_path, stub={k: doc.get(k) for k in build_team._DUAL_DESCRIPTOR_FIELDS[:4]}
                  | {"selected_archetypes": STUB_ROSTER})
    args = argparse.Namespace(self_update=False, description=str(ws / "brief.json"), output=str(ws))
    build_team._check_dual_descriptor(args)
    err = capsys.readouterr().err
    assert "Dual descriptor detected" in err and "selected_archetypes" in err


# --- the roster-shrink guard -----------------------------------------------------------------------------------

def _args(**kw) -> argparse.Namespace:
    return argparse.Namespace(update=True, yes=False, allow_roster_shrink=False, **kw)


def test_dropped_archetypes_compares_the_manifest_with_the_stub(tmp_path):
    ws = _project(tmp_path, stub={"selected_archetypes": STUB_ROSTER})
    assert descriptor_union.dropped_archetypes({"selected_archetypes": SELECTED}, ws) == [
        "reference-manager", "visual-designer"]
    assert descriptor_union.dropped_archetypes({"selected_archetypes": STUB_ROSTER + ["x"]}, ws) == []
    assert descriptor_union.dropped_archetypes({"selected_archetypes": []}, _project(tmp_path / "n")) == []


def test_a_shrink_under_yes_is_refused_and_allowed_only_with_the_flag(tmp_path, capsys):
    ws = _project(tmp_path, stub={"selected_archetypes": STUB_ROSTER})
    manifest = {"selected_archetypes": SELECTED}
    assert descriptor_union.refuse_roster_shrink(_args(), manifest, ws) is False  # no --yes: warns, continues
    assert "[WARN]" in capsys.readouterr().err
    yes = _args()
    yes.yes = True
    assert descriptor_union.refuse_roster_shrink(yes, manifest, ws) is True
    assert "Refused" in capsys.readouterr().err
    yes.allow_roster_shrink = True
    assert descriptor_union.refuse_roster_shrink(yes, manifest, ws) is False
    not_update = _args()
    not_update.update, not_update.yes = False, True
    assert descriptor_union.refuse_roster_shrink(not_update, manifest, ws) is False


@pytest.mark.parametrize("with_project", [True, False])
def test_a_brief_only_update_yes_is_refused_before_writing(tmp_path, capsys, with_project):
    # --output is the agents dir here (as fleet's github target passes it), so the stub is found either through
    # --project or as a sibling of the output dir. The refusal text is asserted, not just rc: a later gate (intel
    # freshness) also exits 1, and must not be mistaken for this one.
    import build_team

    ws = _project(tmp_path, stub={"selected_archetypes": STUB_ROSTER})
    agents = ws / ".github" / "agents"
    before = {p.name: p.read_bytes() for p in agents.iterdir()}
    argv = ["--description", str(ws / "brief.json"), "--output", str(agents), "--update", "--yes", "--no-scan",
            "--security-offline", *(["--project", str(ws)] if with_project else [])]
    rc = build_team.main(argv)
    err = capsys.readouterr().err
    assert rc == 1 and "Refused: this update would drop 2 archetype(s)" in err, err[-500:]
    assert "reference-manager, visual-designer" in err
    assert {p.name: p.read_bytes() for p in agents.iterdir()} == before


def test_the_stub_as_descriptor_renders_without_refusal(tmp_path, capsys):
    import build_team

    doc = json.loads(BRIEF.read_text(encoding="utf-8"))
    ws = _project(tmp_path, stub={**doc, "selected_archetypes": STUB_ROSTER})
    agents = ws / ".github" / "agents"
    rc = build_team.main(["--description", str(agents / "_build-description.json"), "--project", str(ws),
                          "--output", str(agents), "--update", "--yes", "--no-scan", "--security-offline",
                          "--dry-run"])
    captured = capsys.readouterr()
    assert rc == 0, captured.err[-500:]
    assert "Refused" not in captured.err
    archetypes = next(line for line in captured.out.splitlines() if line.strip().startswith("Archetypes:"))
    assert "reference-manager" in archetypes and "visual-designer" in archetypes
    assert "Plan counts" in captured.out                       # the dry run planned the whole render
    shutil.rmtree(ws)
