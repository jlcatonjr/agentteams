"""``--update --overwrite`` reports the routed agents it would orphan (issue #392 item 12, report half).

The on-disk orchestrator may route to agents the build does not emit: a bespoke routing row or workflow step naming
``@my-agent``. ``--overwrite`` replaces that orchestrator, so the routing is lost. The report names every such slug,
with or without a file, in the dry-run JSON and on stderr in a real run, before any file is written. The run still
proceeds; the refusal is a separate change.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import build_team
from agentteams import emit
from agentteams.cli.routing_at_risk import at_risk_routed_slugs
from tests.test_update_dry_run import _seed_gates, _seed_pass

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "data-pipeline" / "brief.json"
BESPOKE_ROWS = ("| Ledger reconciliation | `@ledger-keeper` | Monthly close |\n"
                "| Vendor feeds | `@feed-wrangler` | Upstream CSV drops |\n")


@pytest.fixture
def team(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    # The overwrite clearance in _seed_pass is unsigned; this test is about the report, not decision signing.
    brief = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    brief["enforce_decision_signing"] = False
    brief_path = tmp_path / "brief.json"
    brief_path.write_text(json.dumps(brief), encoding="utf-8")
    out = tmp_path / ".github" / "agents"
    _seed_gates(out, monkeypatch)
    assert build_team.main(["--description", str(brief_path), "--output", str(out),
                            "--yes", "--no-scan", "--security-offline"]) == 0
    _seed_pass(out)
    orch = out / "orchestrator.agent.md"
    orch.write_text(orch.read_text(encoding="utf-8") + "\n## Bespoke routing\n\n| Area | Agent | Notes |\n|---|---|---|\n"
                    + BESPOKE_ROWS, encoding="utf-8")
    (out / "ledger-keeper.agent.md").write_text("---\nname: ledger-keeper\n---\nBespoke.\n", encoding="utf-8")
    return brief_path, out


def _update(team: tuple[Path, Path], *extra: str) -> int:
    brief, out = team
    return build_team.main(["--description", str(brief), "--output", str(out), "--update", *extra,
                            "--yes", "--no-scan", "--security-offline"])


def test_dry_run_json_lists_every_routed_agent_the_build_drops(team: tuple[Path, Path], capsys) -> None:
    capsys.readouterr()
    assert _update(team, "--overwrite", "--dry-run", "--json") == 0
    out = capsys.readouterr().out
    payload = json.loads(out[out.find("{"):])
    assert payload["at_risk_routed_agents"] == [
        {"slug": "feed-wrangler", "has_file": False},
        {"slug": "ledger-keeper", "has_file": True},
    ]


def test_real_run_reports_on_stderr_before_any_write(team: tuple[Path, Path], capsys, monkeypatch) -> None:
    real_emit_all, real_backup = emit.emit_all, emit.backup_output_dir

    def _mark(name, fn):
        def wrapper(*a, **k):
            sys.stderr.write(f"<<{name}>>\n")
            return fn(*a, **k)
        return wrapper

    monkeypatch.setattr(emit, "emit_all", _mark("emit_all", real_emit_all))
    monkeypatch.setattr(emit, "backup_output_dir", _mark("backup", real_backup))
    capsys.readouterr()
    assert _update(team, "--overwrite") == 0
    err = capsys.readouterr().err
    report = err.find("agent(s) it routes to are not in this build")
    assert report != -1, err[-2000:]
    first_write = min(i for i in (err.find("<<backup>>"), err.find("<<emit_all>>")) if i != -1)
    assert report < first_write
    assert "@feed-wrangler  — no file" in err and "@ledger-keeper  — file kept on disk" in err
    assert "`@feed-wrangler`" not in (team[1] / "orchestrator.agent.md").read_text(encoding="utf-8")


def test_merge_reports_nothing(team: tuple[Path, Path], capsys) -> None:
    capsys.readouterr()
    assert _update(team, "--dry-run", "--json") == 0
    captured = capsys.readouterr()
    assert "at_risk_routed_agents" not in captured.out
    assert "lose their routing" not in captured.err


def test_slug_scan_ignores_front_matter_emitted_agents_and_chat_participants(tmp_path: Path) -> None:
    (tmp_path / "orchestrator.md").write_text(
        "---\ndescription: routes `@front-matter-only`\n---\n"
        "Use `@kept`, `@dropped`, `@workspace`, plain @prose-mention and `@types/node`.\n", encoding="utf-8")
    rendered = "---\n---\nRoutes `@still-routed` *(if in team)*.\n"
    (tmp_path / "orchestrator.md").write_text(
        (tmp_path / "orchestrator.md").read_text(encoding="utf-8") + "And `@still-routed`.\n", encoding="utf-8")
    assert at_risk_routed_slugs(tmp_path, ".md", {"kept"}, rendered) == [{"slug": "dropped", "has_file": False}]
    assert at_risk_routed_slugs(tmp_path / "missing", ".md", set(), rendered) == []
