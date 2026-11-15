"""A re-render never silently drops the roster the stub records (IM item 12, G-17-1).

``fleet._resolve_descriptor`` takes the root ``brief.json`` whole when it exists. In researchteam-derived repos the
roster (``selected_archetypes``) lives only in ``.github/agents/_build-description.json``, so a brief-only re-render
re-ran keyword selection and dropped agents, and ``--yes`` applied the shrunken roster. The roster is now taken from
the stub where the description is loaded, so the fleet and a hand-run ``--update`` both keep it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from agentteams import descriptor_union as du

REPO = Path(__file__).resolve().parents[1]
BRIEF = REPO / "examples" / "software-project" / "brief.json"
#: keyword selection for BRIEF, plus two archetypes it never picks: the roster the stub records.
SELECTED = ["primary-producer", "quality-auditor", "cohesion-repairer", "technical-validator", "format-converter",
            "output-compiler", "tool-doc-researcher"]
STUB_ROSTER = SELECTED + ["reference-manager", "visual-designer"]


def _project(tmp_path: Path, *, stub: dict | str | None = None, brief: dict | None = None) -> Path:
    ws = tmp_path / "ws"
    agents = ws / ".github" / "agents"
    agents.mkdir(parents=True)
    (agents / "a.agent.md").write_text("x\n", encoding="utf-8")
    doc = json.loads(BRIEF.read_text(encoding="utf-8")) if brief is None else brief
    (ws / "brief.json").write_text(json.dumps(doc), encoding="utf-8")
    if stub is not None:
        (agents / du.STUB_NAME).write_text(stub if isinstance(stub, str) else json.dumps(stub), encoding="utf-8")
    return ws


def _ns(ws: Path, **kw) -> argparse.Namespace:
    base = {"project": None, "output": str(ws / ".github" / "agents"), "description": str(ws / "brief.json"),
            "update": True, "check": False, "yes": False, "allow_roster_shrink": False}
    return argparse.Namespace(**{**base, **kw})


# --- finding the stub ------------------------------------------------------------------------------------------

def test_stub_for_finds_the_stub_from_project_output_or_the_description(tmp_path):
    ws = _project(tmp_path, stub={"selected_archetypes": STUB_ROSTER})
    stub = ws / du.STUB_REL
    assert du.stub_for(str(ws), None) == stub
    assert du.stub_for(None, str(ws / ".github" / "agents")) == stub          # --output is the agents dir
    assert du.stub_for(None, str(ws)) == stub                                 # --output is the project root
    assert du.stub_for(None, None, str(ws / "brief.json")) == stub            # beside the description's project
    assert du.stub_for(None, None, str(stub)) is None                         # the description is never its own stub


def test_with_project_only_that_project_s_stub_counts(tmp_path):
    # @adversarial: an example brief rendered into another project must not bring the example's stub roster along.
    example = _project(tmp_path / "example", stub={"selected_archetypes": STUB_ROSTER})
    other = tmp_path / "other"
    (other / ".github" / "agents").mkdir(parents=True)
    assert du.stub_for(str(other), str(other / ".github" / "agents"), str(example / "brief.json")) is None
    assert du.stub_for(str(example), None, str(example / du.STUB_REL)) is None  # never the description itself


def test_a_fresh_render_keeps_keyword_selection(tmp_path):
    ws = _project(tmp_path, stub={"selected_archetypes": STUB_ROSTER})
    desc = json.loads(BRIEF.read_text(encoding="utf-8"))
    du.apply_stub_roster(desc, ns := _ns(ws, update=False, check=False))
    assert "selected_archetypes" not in desc and getattr(ns, du._STATE_ATTR)[0] == "none"
    du.apply_stub_roster(desc, _ns(ws, update=False, check=True))         # --check compares like an update
    assert desc["selected_archetypes"] == STUB_ROSTER


@pytest.mark.parametrize("roster", [["primary-producer", "no-such-archetype"], ["primary-producer", 7],
                                    ["../../etc"]])
def test_a_stub_roster_with_an_unknown_archetype_is_not_applied(tmp_path, roster, capsys):
    ws = _project(tmp_path, stub={"selected_archetypes": roster})
    desc = json.loads(BRIEF.read_text(encoding="utf-8"))
    du.apply_stub_roster(desc, ns := _ns(ws, yes=True))
    assert "selected_archetypes" not in desc
    assert getattr(ns, du._STATE_ATTR)[0] == "unreadable"
    assert du.refuse_roster_shrink(ns, {"selected_archetypes": SELECTED}, ws) is True
    assert "not known archetypes" in capsys.readouterr().err


def test_a_symlinked_stub_is_never_read(tmp_path):
    ws = _project(tmp_path)
    real = tmp_path / "elsewhere.json"
    real.write_text(json.dumps({"selected_archetypes": STUB_ROSTER}), encoding="utf-8")
    (ws / du.STUB_REL).symlink_to(real)
    assert du.stub_for(str(ws), str(ws)) is None


# --- applying the roster ---------------------------------------------------------------------------------------

def test_a_description_without_a_roster_takes_the_stub_s(tmp_path, capsys):
    ws = _project(tmp_path, stub={"selected_archetypes": STUB_ROSTER})
    desc = json.loads(BRIEF.read_text(encoding="utf-8"))
    du.apply_stub_roster(desc, ns := _ns(ws))
    assert desc["selected_archetypes"] == STUB_ROSTER
    assert getattr(ns, du._STATE_ATTR)[0] == "injected"
    assert "selected_archetypes from" in capsys.readouterr().out


def test_a_description_s_own_roster_is_kept(tmp_path):
    ws = _project(tmp_path, stub={"selected_archetypes": STUB_ROSTER})
    desc = {"selected_archetypes": SELECTED}
    du.apply_stub_roster(desc, ns := _ns(ws))
    assert desc["selected_archetypes"] == SELECTED and getattr(ns, du._STATE_ATTR)[0] == "own"


def test_privileged_roster_keys_are_reported_never_applied(tmp_path, capsys):
    # A stale stub must not restore a write_policy or governance_agents an operator deleted from the brief.
    ws = _project(tmp_path, stub={"selected_archetypes": STUB_ROSTER, "write_policy": "open",
                                  "governance_agents": ["security"], "retrieval_integration": {"x": 1}})
    desc = json.loads(BRIEF.read_text(encoding="utf-8"))
    du.apply_stub_roster(desc, _ns(ws))
    for key in ("write_policy", "governance_agents", "retrieval_integration"):
        assert key not in desc, key
    err = capsys.readouterr().err
    assert "write_policy" in err and "not applied" in err


# --- the guard -------------------------------------------------------------------------------------------------

def test_an_unreadable_stub_under_yes_is_refused_and_the_flag_accepts_it(tmp_path, capsys):
    ws = _project(tmp_path, stub="{ not json")
    desc = json.loads(BRIEF.read_text(encoding="utf-8"))
    manifest = {"selected_archetypes": SELECTED}
    du.apply_stub_roster(desc, ns := _ns(ws, yes=True))
    assert getattr(ns, du._STATE_ATTR)[0] == "unreadable"
    assert du.refuse_roster_shrink(ns, manifest, ws) is True
    assert "Refused" in capsys.readouterr().err
    ns.allow_roster_shrink = True
    assert du.refuse_roster_shrink(ns, manifest, ws) is False
    ns.allow_roster_shrink, ns.yes = False, False
    assert du.refuse_roster_shrink(ns, manifest, ws) is False                 # without --yes: a warning
    assert "[WARN]" in capsys.readouterr().err


def test_a_thinner_own_roster_warns_but_is_never_refused(tmp_path, capsys):
    # A stale stub must not fail a deliberate roster every night (@adversarial).
    ws = _project(tmp_path, stub={"selected_archetypes": STUB_ROSTER + ["retrieval-integrator"]})
    du.apply_stub_roster({"selected_archetypes": SELECTED}, ns := _ns(ws, yes=True))
    assert du.refuse_roster_shrink(ns, {"selected_archetypes": SELECTED}, ws) is False
    err = capsys.readouterr().err
    assert "reference-manager, visual-designer" in err
    assert "retrieval-integrator" not in err                                  # force-appended: not a roster loss


def test_the_guard_ignores_runs_that_are_not_updates(tmp_path):
    ws = _project(tmp_path, stub="{ not json")
    du.apply_stub_roster(json.loads(BRIEF.read_text(encoding="utf-8")), ns := _ns(ws, yes=True, update=False))
    assert du.refuse_roster_shrink(ns, {"selected_archetypes": SELECTED}, ws) is False


# --- end to end ------------------------------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import build_team

    return build_team.main(argv)


@pytest.mark.parametrize("with_project", [True, False])
def test_a_brief_only_update_keeps_the_stub_roster(tmp_path, capsys, with_project):
    # --output is the agents dir, as the fleet's github target passes it.
    ws = _project(tmp_path, stub={"selected_archetypes": STUB_ROSTER})
    agents = ws / ".github" / "agents"
    rc = _main(["--description", str(ws / "brief.json"), "--output", str(agents), "--update", "--yes", "--no-scan",
                "--security-offline", "--dry-run", *(["--project", str(ws)] if with_project else [])])
    captured = capsys.readouterr()
    assert rc == 0, captured.err[-500:]
    archetypes = next(line for line in captured.out.splitlines() if line.strip().startswith("Archetypes:"))
    assert "reference-manager" in archetypes and "visual-designer" in archetypes
    assert "Plan counts" in captured.out                                      # the dry run planned the render


def test_a_brief_only_update_yes_with_an_unreadable_stub_is_refused_before_writing(tmp_path, capsys):
    # The refusal text is asserted, not just rc: a later gate (intel freshness) also exits 1.
    ws = _project(tmp_path, stub="{ not json")
    agents = ws / ".github" / "agents"
    before = {p.name: p.read_bytes() for p in agents.iterdir()}
    rc = _main(["--description", str(ws / "brief.json"), "--project", str(ws), "--output", str(agents), "--update",
                "--yes", "--no-scan", "--security-offline"])
    err = capsys.readouterr().err
    assert rc == 1 and "Refused: the description has no selected_archetypes" in err, err[-500:]
    assert {p.name: p.read_bytes() for p in agents.iterdir()} == before


def test_the_stub_as_descriptor_renders_without_refusal(tmp_path, capsys):
    doc = json.loads(BRIEF.read_text(encoding="utf-8"))
    ws = _project(tmp_path, stub={**doc, "selected_archetypes": STUB_ROSTER})
    agents = ws / ".github" / "agents"
    rc = _main(["--description", str(agents / du.STUB_NAME), "--project", str(ws), "--output", str(agents),
                "--update", "--yes", "--no-scan", "--security-offline", "--dry-run"])
    captured = capsys.readouterr()
    assert rc == 0 and "Refused" not in captured.err, captured.err[-500:]
    assert "visual-designer" in next(line for line in captured.out.splitlines()
                                     if line.strip().startswith("Archetypes:"))


# --- the dual-descriptor advisory ------------------------------------------------------------------------------

def test_the_dual_descriptor_advisory_reports_a_roster_divergence(tmp_path, capsys):
    import build_team

    doc = json.loads(BRIEF.read_text(encoding="utf-8"))
    ws = _project(tmp_path, stub={k: doc.get(k) for k in build_team._DUAL_DESCRIPTOR_FIELDS[:4]}
                  | {"selected_archetypes": STUB_ROSTER})
    build_team._check_dual_descriptor(argparse.Namespace(self_update=False, description=str(ws / "brief.json"),
                                                         output=str(ws)))
    err = capsys.readouterr().err
    assert "Dual descriptor detected" in err and "selected_archetypes" in err
