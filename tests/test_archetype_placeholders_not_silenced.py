"""No silent N/A for archetypes a brief names (issue #392 item 1).

When a brief names ``style-guardian`` (or ``reference-manager``) in ``selected_archetypes`` but sets no
``style_reference`` (or ``reference_db_path``), the placeholder stays ``{MANUAL:*}``, is listed in
SETUP-REQUIRED.md and is warned about, in every mode, including ``--enrich``, whose fill writes an em-dash
"N/A — ..." variant. A keyword-selected archetype keeps today's default plus a one-line warning.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import build_team
from agentteams.cli import security_gate
from agentteams._archetype_placeholders import explicitly_selected_unfilled
from agentteams.enrich._fills import _fill_reference_db_path, _fill_style_reference_path

REPO = Path(__file__).resolve().parents[1]
BASE = json.loads((REPO / "examples" / "research-project" / "brief.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _fresh_intel(monkeypatch: pytest.MonkeyPatch) -> None:
    # This is about placeholder policy, not the intel-freshness gate.
    monkeypatch.setattr(security_gate, "_assert_security_intelligence_fresh", lambda *_a, **_k: None)


def _brief(tmp_path: Path, **overrides) -> Path:
    brief = {k: v for k, v in BASE.items() if k not in {"style_reference", "reference_db_path", "reference_db"}}
    brief.update(overrides)
    path = tmp_path / "brief.json"
    path.write_text(json.dumps(brief), encoding="utf-8")
    return path


def _generate(tmp_path: Path, brief: Path, *extra: str) -> Path:
    out = tmp_path / "out"
    assert build_team.main(["--description", str(brief), "--output", str(out), "--framework", "copilot-vscode",
                            "--yes", "--no-scan", "--security-offline", *extra]) == 0
    return out


def _all_text(out: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in out.rglob("*.md"))


NAMED = ["style-guardian", "reference-manager", "technical-validator"]


def test_named_archetypes_keep_their_manual_placeholders(tmp_path: Path, capsys) -> None:
    # --enrich is covered by test_enrich_fills_leave_named_archetypes_unfilled: its rule fills are the only
    # source of the em-dash variant, and an --enrich run fetches over the network.
    out = _generate(tmp_path, _brief(tmp_path, selected_archetypes=NAMED))
    err = capsys.readouterr().err
    setup = (out / "SETUP-REQUIRED.md").read_text(encoding="utf-8")
    for key, slug in (("STYLE_REFERENCE_PATH", "style-guardian"), ("REFERENCE_DB_PATH", "reference-manager")):
        assert key in setup, f"{key} missing from SETUP-REQUIRED.md"
        assert f"{slug} is named in selected_archetypes" in err
    text = _all_text(out)
    assert "{MANUAL:STYLE_REFERENCE_PATH}" in (out / "style-guardian.agent.md").read_text(encoding="utf-8")
    # The exact fills (render_pipeline's hyphen, enrich's em-dash); content-enricher's own guidance quotes a
    # shorter "N/A — no formal style guide" as an instruction, which is not a fill.
    for silent in ("N/A - no formal style guide defined for this project",
                   "N/A — no formal style guide defined for this project",
                   "N/A - no citation database configured for this project",
                   "N/A — no citation database configured for this project"):
        assert silent not in text, f"rendered team still says {silent!r}"


def test_a_supplied_value_is_used_and_not_warned_about(tmp_path: Path, capsys) -> None:
    out = _generate(tmp_path, _brief(tmp_path, selected_archetypes=NAMED, style_reference="docs/style.md",
                                     reference_db_path="refs/library.bib"))
    err = capsys.readouterr().err
    assert "selected_archetypes but the brief sets no" not in err
    setup = (out / "SETUP-REQUIRED.md").read_text(encoding="utf-8") if (out / "SETUP-REQUIRED.md").exists() else ""
    assert "STYLE_REFERENCE_PATH" not in setup and "REFERENCE_DB_PATH" not in setup
    assert "docs/style.md" in (out / "style-guardian.agent.md").read_text(encoding="utf-8")


def test_keyword_selection_keeps_the_default_and_warns(capsys) -> None:
    manifest = {
        "selected_archetypes": ["style-guardian", "reference-manager"],
        "auto_resolved_placeholders": {"REFERENCE_DB_PATH": "{MANUAL:REFERENCE_DB_PATH}",
                                       "STYLE_REFERENCE_PATH": "{MANUAL:STYLE_REFERENCE_PATH}"},
        "manual_required_placeholders": [{"placeholder": "REFERENCE_DB_PATH"},
                                         {"placeholder": "STYLE_REFERENCE_PATH"}],
    }
    build_team._apply_placeholder_policy(manifest, strict_manual_placeholders=False, description={})
    err = capsys.readouterr().err
    assert manifest["auto_resolved_placeholders"]["STYLE_REFERENCE_PATH"].startswith("N/A")
    assert manifest["auto_resolved_placeholders"]["REFERENCE_DB_PATH"].startswith("N/A")
    assert manifest["manual_required_placeholders"] == []
    assert "style-guardian selected without a style_reference" in err
    assert "reference-manager selected without a reference_db_path" in err


@pytest.mark.parametrize("desc, expected", [
    ({}, set()),
    ({"selected_archetypes": ["style-guardian"]}, {"STYLE_REFERENCE_PATH"}),
    ({"selected_archetypes": ["reference-manager"], "reference_db": "x.bib"}, set()),
    ({"selected_archetypes": "style-guardian"}, set()),
])
def test_explicitly_selected_unfilled(desc: dict, expected: set) -> None:
    assert explicitly_selected_unfilled(desc) == expected


def test_enrich_fills_leave_named_archetypes_unfilled() -> None:
    manifest = {"description": {"selected_archetypes": ["style-guardian", "reference-manager"]}}
    assert _fill_style_reference_path(manifest) == ""
    assert _fill_reference_db_path(manifest) == ""
    assert _fill_style_reference_path({"description": {}}).startswith("N/A")
