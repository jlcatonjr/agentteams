"""A merge that refuses a file to keep its user region is a FAIL row in the fleet report (#533 follow-up).

`user_regions.merge_carry` keeps a file as it is, and reports an error, when its Project-Specific Notes or Rules
can't be carried into the fresh render. That file then receives no template updates (tool or permission tightening
included) until the region is fixed by hand, so the fleet report must say so in the row, not only on stderr: an
apply run and a dry run both mark the target FAIL, and the detail names the file and the refusal.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentteams import _fleet_report, fleet
from tests.test_fleet import _Args, _init_repo

_REFUSAL = ("work-summarizer.yaml: not merged: '## Project-Specific Notes' is not indented inside the instructions "
            "block; the file is kept as it is")


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    agents = ws / ".github" / "agents"
    agents.mkdir(parents=True)
    (agents / "_build-description.json").write_text("{}", encoding="utf-8")
    (agents / "a.agent.md").write_text("x\n", encoding="utf-8")
    _init_repo(ws)
    return ws


def _rows(tmp_path: Path) -> list[dict]:
    report = next((tmp_path / ".agentteams-fleet").iterdir())
    data = json.loads((report / "report.json").read_text())
    return [t for w in data["workspaces"] for t in w["targets"]]


def test_an_apply_run_that_refused_a_region_is_a_fail_row_naming_the_file(tmp_path, monkeypatch):
    _workspace(tmp_path)
    out = f"Merged: 3 file(s)\n  Errors:   1\n\nErrors:\n  {_REFUSAL}\n"
    monkeypatch.setattr(fleet, "_run_main", lambda argv: (1, out))
    rc = fleet.run_fleet(_Args(fleet=str(tmp_path), yes=True), parser=None)
    [row] = _rows(tmp_path)
    assert rc == 1
    assert row["status"] == "FAIL"
    assert row["detail"].startswith(_fleet_report.REGION_REFUSAL_LABEL)
    assert "work-summarizer.yaml" in row["detail"]


def test_a_dry_run_that_would_refuse_is_a_fail_row_not_would_update(tmp_path, monkeypatch):
    _workspace(tmp_path)
    out = ("[DRY RUN PLAN] 'p' (goose) — no files written\n  UNCHANGED   work-summarizer.yaml\n\n  Notices (1):\n"
           f"    Notice: {_REFUSAL.replace(': not merged: ', ': would not merge: ')}\n")
    monkeypatch.setattr(fleet, "_run_main", lambda argv: (0, out))
    fleet.run_fleet(_Args(fleet=str(tmp_path), yes=False), parser=None)
    [row] = _rows(tmp_path)
    assert row["status"] == "FAIL"
    assert row["detail"].startswith(_fleet_report.REGION_REFUSAL_LABEL + "work-summarizer.yaml: would not merge")


def test_a_bare_errors_header_gives_way_to_the_line_under_it():
    assert _fleet_report.first_error("ok\nErrors:\n  Failed to write x.md: denied\n") == "Failed to write x.md: denied"
    assert _fleet_report.first_error("Error: bad brief\n") == "Error: bad brief"
    assert _fleet_report.first_error("nothing useful\n") == "non-zero exit (see captured output)"
    assert _fleet_report.region_refusal("Merged: 3 file(s)\n") is None


def test_the_markers_match_what_merge_carry_actually_writes():
    from agentteams.user_regions import merge_carry
    disk = 'title: x\ninstructions: |\n  body\n## Project-Specific Notes\n- note\n'
    fresh = 'title: x\ninstructions: |\n  body\n\n  ## Project-Specific Notes\n'
    for dry_run in (False, True):
        messages: list[str] = []
        assert merge_carry("r.yaml", disk, fresh, messages, dry_run=dry_run) == disk
        assert messages and all(_fleet_report.region_refusal(m) for m in messages)
