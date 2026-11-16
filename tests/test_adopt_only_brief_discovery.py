"""``--adopt-only`` finds the project's brief when none is named (issue #392 item 13, discovery half).

With no ``--description`` (and no ``--self``), the brief under ``--project`` (else the working directory) is used,
found as the retrieval modes find it, but only when the agents directory the run writes to lies inside that same
project. A named ``--description`` is never replaced.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

import build_team
from agentteams.cli.adopt_only import resolve_adopt_only_brief
from tests.test_update_dry_run import _seed_gates

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = json.loads((REPO / "examples" / "data-pipeline" / "brief.json").read_text(encoding="utf-8"))


def _project(tmp_path: Path, name: str = "proj") -> Path:
    root = tmp_path / name
    (root / ".agentteams").mkdir(parents=True)
    brief = {k: v for k, v in EXAMPLE.items() if k != "existing_project_path"}
    (root / ".agentteams" / "brief.json").write_text(json.dumps(brief), encoding="utf-8")
    return root


def _args(**kw) -> argparse.Namespace:
    base = {"adopt_only": "my-agent", "description": None, "self_update": False, "project": None,
            "output": None, "framework": "claude"}
    return argparse.Namespace(**{**base, **kw})


def test_the_brief_of_the_project_is_found(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path)
    args = _args(project=str(root), output=str(root / ".claude" / "agents"))
    assert resolve_adopt_only_brief(args) is None
    assert args.description == str((root / ".agentteams" / "brief.json").resolve())
    assert "using this project's brief" in capsys.readouterr().err


def test_an_output_in_another_project_refuses(tmp_path: Path) -> None:
    root = _project(tmp_path)
    other = tmp_path / "other" / ".claude" / "agents"
    args = _args(project=str(root), output=str(other))
    refusal = resolve_adopt_only_brief(args)
    assert refusal and "outside" in refusal and "--description" in refusal
    assert args.description is None


def test_the_working_directory_is_the_default_project(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)
    monkeypatch.chdir(root)
    args = _args()
    assert resolve_adopt_only_brief(args) is None
    assert args.description == str((root / ".agentteams" / "brief.json").resolve())


def test_a_project_flag_whose_default_output_lands_elsewhere_refuses(tmp_path: Path, monkeypatch) -> None:
    """Without --output the agents dir comes from the brief or the working directory, not --project."""
    root = _project(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    refusal = resolve_adopt_only_brief(_args(project=str(root)))
    assert refusal and "outside" in refusal


@pytest.mark.parametrize("kw", [
    {"description": "named-but-missing.json"},
    {"self_update": True},
    {"adopt_only": None},
    {"framework": "generic"},
])
def test_nothing_is_swapped_in(tmp_path: Path, kw: dict) -> None:
    root = _project(tmp_path)
    args = _args(project=str(root), output=str(root / ".claude" / "agents"), **kw)
    before = args.description
    assert resolve_adopt_only_brief(args) is None
    assert args.description == before


def test_no_brief_leaves_the_required_error(tmp_path: Path) -> None:
    args = _args(project=str(tmp_path), output=str(tmp_path / ".claude" / "agents"))
    assert resolve_adopt_only_brief(args) is None and args.description is None


def test_a_symlinked_brief_is_not_used(tmp_path: Path) -> None:
    root = _project(tmp_path)
    real = root / ".agentteams" / "brief.json"
    target = tmp_path / "outside.json"
    target.write_text(real.read_text(encoding="utf-8"), encoding="utf-8")
    real.unlink()
    real.symlink_to(target)
    args = _args(project=str(root), output=str(root / ".claude" / "agents"))
    assert resolve_adopt_only_brief(args) is None and args.description is None


def test_end_to_end_dry_run_adopts_without_a_description(tmp_path: Path, monkeypatch, capsys) -> None:
    root = _project(tmp_path)
    brief = root / ".agentteams" / "brief.json"
    out = root / ".claude" / "agents"
    _seed_gates(out, monkeypatch)
    assert build_team.main(["--description", str(brief), "--framework", "claude", "--output", str(out),
                            "--yes", "--no-scan", "--security-offline"]) == 0
    (out / "my-agent.md").write_text("---\nname: my-agent\ndescription: bespoke\ntools: Read, Grep\n---\nBody.\n",
                                     encoding="utf-8")
    capsys.readouterr()
    rc = build_team.main(["--project", str(root), "--framework", "claude", "--output", str(out),
                          "--adopt-only", "my-agent", "--dry-run", "--json", "--security-offline"])
    captured = capsys.readouterr()
    assert rc == 0, captured.err[-1500:]
    assert "using this project's brief" in captured.err
    payload = json.loads(captured.out[captured.out.find("{"):])
    assert "my-agent" in json.dumps(payload)
