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

    from agentteams.cli import generate as _generate

    monkeypatch.setattr(emit, "emit_all", _mark("emit_all", real_emit_all))
    monkeypatch.setattr(emit, "backup_output_dir", _mark("backup", real_backup))
    # The legacy tool-agent migration can back up and delete files; it must also come after the report.
    monkeypatch.setattr(_generate, "_remove_stale_tool_agents",
                        _mark("stale_tool_agents", _generate._remove_stale_tool_agents))
    capsys.readouterr()
    assert _update(team, "--overwrite") == 0
    err = capsys.readouterr().err
    report = err.find("agent(s) it routes to are not in this build")
    assert report != -1, err[-2000:]
    first_write = min(i for i in (err.find(f"<<{m}>>") for m in ("stale_tool_agents", "backup", "emit_all")) if i != -1)
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


def test_an_unreadable_orchestrator_is_warned_about_not_treated_as_safe(tmp_path: Path, capsys) -> None:
    (tmp_path / "orchestrator.md").write_bytes(b"\xff\xfe `@dropped`")
    assert at_risk_routed_slugs(tmp_path, ".md", set(), "") == []
    assert "could not read orchestrator.md" in capsys.readouterr().err


class _Adapter:
    def get_file_extension(self, kind: str) -> str:
        return ".md"


class _Args:
    def __init__(self, overwrite: bool = True, discard_user_regions: bool = False) -> None:
        self.overwrite, self.discard_user_regions = overwrite, discard_user_regions


def _report(tmp_path: Path, on_disk: str, rendered: str, manifest: dict | None = None, **args) -> list:
    from agentteams.cli.routing_at_risk import report_at_risk_routing

    (tmp_path / "orchestrator.md").write_text(on_disk, encoding="utf-8")
    manifest = manifest or {"agent_slug_list": ["orchestrator"]}
    return report_at_risk_routing(tmp_path, manifest, _Adapter(), [("orchestrator.md", rendered)], _Args(**args))


def test_adopted_agents_are_not_at_risk(tmp_path: Path) -> None:
    manifest = {"agent_slug_list": ["orchestrator"], "adopted_agents": ["adopted"]}
    assert _report(tmp_path, "Use `@adopted` and `@gone`.\n", "Fresh.\n", manifest) == [
        {"slug": "gone", "has_file": False}]
    assert _report(tmp_path, "Use `@gone`.\n", "Fresh.\n", overwrite=False) == []


NOTES = "# Orchestrator\n\nBody.\n\n## Project-Specific Notes\n\nRoute ledgers to `@ledger-keeper`.\n"


def test_routing_in_a_user_region_survives_overwrite_and_is_not_reported(tmp_path: Path) -> None:
    fresh = "# Orchestrator\n\nNew body.\n\n## Project-Specific Notes\n\n"
    assert _report(tmp_path, NOTES, fresh) == []
    assert _report(tmp_path, NOTES, fresh, discard_user_regions=True) == [
        {"slug": "ledger-keeper", "has_file": False}]


def test_an_orchestrator_this_run_does_not_rewrite_reports_nothing(tmp_path: Path) -> None:
    from agentteams.cli.routing_at_risk import report_at_risk_routing

    (tmp_path / "orchestrator.md").write_text("Use `@gone`.\n", encoding="utf-8")
    assert report_at_risk_routing(tmp_path, {"agent_slug_list": []}, _Adapter(),
                                  [("navigator.md", "x")], _Args()) == []
