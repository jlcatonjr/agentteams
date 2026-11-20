"""``--overwrite`` and the routed agents it would orphan (issue #392 item 12: report and refusal).

The on-disk orchestrator may route to agents the build does not keep: a bespoke routing row, workflow step or
handoff naming ``@my-agent``. ``--overwrite`` replaces that orchestrator, so the routing would be lost. The report
names every such slug in the dry-run JSON and on stderr. A real ``--overwrite``, ``--update --overwrite`` or
``--migrate`` run refuses before the overwrite clearance is spent and before any backup or write, unless
``--accept-routing-loss`` names exactly the at-risk set (``@security`` design conditions 1-10, 2026-11-19).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import build_team
from agentteams import emit
from agentteams.cli import security_gate
from agentteams.cli.routing_at_risk import (RoutingLoss, assess_routing_loss, at_risk_routed_slugs,
                                            mentioned_slugs, refuse_routing_loss)
from tests.test_update_dry_run import _seed_gates, _seed_pass

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "data-pipeline" / "brief.json"
BESPOKE_ROWS = ("| Ledger reconciliation | `@ledger-keeper` | Monthly close |\n"
                "| Vendor feeds | `@feed-wrangler` | Upstream CSV drops |\n")
BOTH = "feed-wrangler,ledger-keeper"


@pytest.fixture
def team(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    # The overwrite clearance in _seed_pass is unsigned; these tests are about routing, not decision signing.
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


def _run(team: tuple[Path, Path], *extra: str, **kw: object) -> int:
    brief, out = team
    return build_team.main(["--description", str(brief), "--output", str(out), *extra,
                            "--yes", "--no-scan", "--security-offline"], **kw)


def _marked_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every write path print a marker on stderr, so a test can see whether (and when) one ran."""
    from agentteams.cli import generate as _generate

    def _mark(name, fn):
        def wrapper(*a, **k):
            sys.stderr.write(f"<<{name}>>\n")
            return fn(*a, **k)
        return wrapper

    monkeypatch.setattr(emit, "emit_all", _mark("emit_all", emit.emit_all))
    monkeypatch.setattr(emit, "backup_output_dir", _mark("backup", emit.backup_output_dir))
    monkeypatch.setattr(_generate, "_remove_stale_tool_agents",
                        _mark("stale_tool_agents", _generate._remove_stale_tool_agents))


# --- the report ----------------------------------------------------------------------------------------------------

def test_dry_run_json_lists_the_loss_and_says_a_real_run_refuses(team: tuple[Path, Path], capsys) -> None:
    capsys.readouterr()
    assert _run(team, "--update", "--overwrite", "--dry-run", "--json") == 0   # a dry run never refuses
    out = capsys.readouterr().out
    payload = json.loads(out[out.find("{"):])
    assert payload["at_risk_routed_agents"] == [
        {"slug": "feed-wrangler", "has_file": False},
        {"slug": "ledger-keeper", "has_file": True},
    ]
    loss = payload["routing_loss"]
    assert loss["would_refuse"] is True and loss["accepted"] is None
    assert f"--accept-routing-loss {BOTH} (drop this exact set on purpose)" in loss["clear_with"]


def test_dry_run_with_the_exact_acknowledgement_would_not_refuse(team: tuple[Path, Path], capsys) -> None:
    capsys.readouterr()
    assert _run(team, "--update", "--overwrite", "--accept-routing-loss", BOTH, "--dry-run", "--json") == 0
    out = capsys.readouterr().out
    loss = json.loads(out[out.find("{"):])["routing_loss"]
    assert loss["would_refuse"] is False and loss["accepted"] == ["feed-wrangler", "ledger-keeper"]


def test_merge_reports_nothing(team: tuple[Path, Path], capsys) -> None:
    capsys.readouterr()
    assert _run(team, "--update", "--dry-run", "--json") == 0
    captured = capsys.readouterr()
    assert "at_risk_routed_agents" not in captured.out and "routing_loss" not in captured.out
    assert "lose their routing" not in captured.err


# --- the refusal (conditions 1, 2, 3, 7) -------------------------------------------------------------------------

@pytest.mark.parametrize("mode", [("--update", "--overwrite"), ("--overwrite",)], ids=["update-overwrite", "plain"])
def test_overwrite_refuses_before_the_clearance_or_any_write(team: tuple[Path, Path], capsys, monkeypatch,
                                                             mode: tuple[str, ...]) -> None:
    before = (team[1] / "orchestrator.agent.md").read_bytes()
    _marked_writes(monkeypatch)
    capsys.readouterr()
    assert _run(team, *mode) == 1   # --yes is passed, and acknowledges nothing (condition 3)
    err = capsys.readouterr().err
    assert "[ROUTING-LOSS] blocked" in err and "@feed-wrangler" in err and "@ledger-keeper" in err
    assert "<<" not in err, "a write path ran before the refusal"
    assert (team[1] / "orchestrator.agent.md").read_bytes() == before
    assert security_gate.check_clearance(team[1], action="overwrite") == (True, ""), "the clearance was spent"


def test_an_edited_orchestrator_with_no_template_drift_still_refuses(team: tuple[Path, Path], capsys,
                                                                     monkeypatch) -> None:
    # @security F1: a hand-edited orchestrator whose template did not drift is not in the drift report's update set,
    # yet the update path still rewrites it (its "sidecar" loop adds every file that differs from disk). The refusal
    # must judge exactly what the run writes.
    from agentteams import drift

    real = drift.compute_structural_diff

    def no_orchestrator_drift(*a, **k):
        report = real(*a, **k)
        for name in ("added_files", "drifted_files"):   # update_files is derived from these
            setattr(report, name, [f for f in getattr(report, name) if not f["path"].endswith("orchestrator.agent.md")])
        assert not any(f["path"].endswith("orchestrator.agent.md") for f in report.update_files)
        return report

    monkeypatch.setattr(drift, "compute_structural_diff", no_orchestrator_drift)
    before = (team[1] / "orchestrator.agent.md").read_bytes()
    capsys.readouterr()
    assert _run(team, "--update", "--overwrite") == 1
    assert "[ROUTING-LOSS] blocked" in capsys.readouterr().err
    assert (team[1] / "orchestrator.agent.md").read_bytes() == before
    assert security_gate.check_clearance(team[1], action="overwrite") == (True, ""), "the clearance was spent"


def test_the_migrate_reentry_refuses_too(team: tuple[Path, Path], capsys, monkeypatch) -> None:
    # --migrate re-enters main() with --overwrite --yes and migrate_exemption=True (build_team._run_migrate).
    _marked_writes(monkeypatch)
    capsys.readouterr()
    assert _run(team, "--overwrite", migrate_exemption=True) == 1
    err = capsys.readouterr().err
    assert "[ROUTING-LOSS] blocked" in err and "<<" not in err


def test_the_exact_acknowledgement_proceeds_and_is_audited(team: tuple[Path, Path], capsys) -> None:
    import hashlib

    sha = hashlib.sha256((team[1] / "orchestrator.agent.md").read_bytes()).hexdigest()
    capsys.readouterr()
    assert _run(team, "--update", "--overwrite", "--accept-routing-loss", BOTH) == 0
    err = capsys.readouterr().err
    assert f"routing loss accepted: {BOTH.replace(',', ', ')} (orchestrator sha256 {sha})" in err
    assert "`@feed-wrangler`" not in (team[1] / "orchestrator.agent.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("mode", [("--update", "--overwrite"), ("--overwrite",)], ids=["update-overwrite", "plain"])
def test_an_accepted_loss_is_in_the_durable_update_report(team: tuple[Path, Path], mode: tuple[str, ...]) -> None:
    assert _run(team, *mode, "--accept-routing-loss", BOTH) == 0
    report = (team[1] / "update.report.md").read_text(encoding="utf-8")
    assert "## Routing loss accepted" in report and "orchestrator sha256 before the run" in report
    assert "- dropped: `@feed-wrangler`" in report and "- dropped: `@ledger-keeper`" in report


def test_plain_overwrite_dry_run_reports_the_loss(team: tuple[Path, Path], capsys) -> None:
    capsys.readouterr()
    assert _run(team, "--overwrite", "--dry-run", "--json") == 0
    out = capsys.readouterr().out
    payload = json.loads(out[out.find("{"):])
    assert payload["routing_loss"]["would_refuse"] is True
    assert [i["slug"] for i in payload["at_risk_routed_agents"]] == ["feed-wrangler", "ledger-keeper"]


def test_an_acknowledgement_with_nothing_at_risk_refuses() -> None:
    assert RoutingLoss(accepted=frozenset({"x"})).would_refuse        # exact means exact: {x} != {}
    assert not RoutingLoss().would_refuse and not RoutingLoss(accepted=None).would_refuse


@pytest.mark.parametrize("accepted", ["feed-wrangler", f"{BOTH},extra-agent"], ids=["subset", "superset"])
def test_an_inexact_acknowledgement_refuses(team: tuple[Path, Path], capsys, accepted: str) -> None:
    capsys.readouterr()
    assert _run(team, "--update", "--overwrite", "--accept-routing-loss", accepted) == 1
    err = capsys.readouterr().err
    assert "[ROUTING-LOSS] blocked" in err and "exactly feed-wrangler, ledger-keeper" in err


def test_adoption_clears_only_the_agents_it_adopts(team: tuple[Path, Path], capsys) -> None:
    capsys.readouterr()
    assert _run(team, "--update", "--overwrite", "--adopt-orphans") == 1   # condition 6: after adoption
    err = capsys.readouterr().err
    blocked = err[err.find("[ROUTING-LOSS]"):]
    assert "@feed-wrangler" in blocked and "@ledger-keeper" not in blocked


@pytest.mark.parametrize("argv, message", [
    (["--update", "--accept-routing-loss", "x"], "needs --overwrite or --migrate"),
    (["--overwrite", "--accept-routing-loss", "*"], "no wildcard"),
    (["--overwrite", "--accept-routing-loss", ""], "no wildcard or empty item"),
    (["--overwrite", "--accept-routing-loss", "a,"], "no wildcard or empty item"),
    (["--overwrite", "--accept-routing-loss", "a,,b"], "no wildcard or empty item"),
    (["--overwrite", "--accept-routing-loss", "Ledger-Keeper"], "no wildcard or empty item"),
], ids=["no-overwrite", "wildcard", "empty", "trailing-comma", "double-comma", "uppercase"])
def test_the_flag_is_validated(team: tuple[Path, Path], capsys, argv: list[str], message: str) -> None:
    with pytest.raises(SystemExit):
        _run(team, *argv)
    assert message in capsys.readouterr().err


# --- what counts as routing (condition 5) and failing closed (condition 4) ---------------------------------------

def test_front_matter_handoffs_bare_mentions_and_participant_collisions(tmp_path: Path) -> None:
    (tmp_path / "bare-one.md").write_text("x", encoding="utf-8")
    (tmp_path / "github.md").write_text("x", encoding="utf-8")
    on_disk = ("---\nname: orchestrator\nagents:\n  - orchestrator\n  - listed-only\nhandoffs:\n"
               "  - label: Go\n    agent: handoff-only\n    prompt: \"`@not-a-target`\"\n"
               "description: routes `@front-matter-prose`\n---\n"
               "Use `@kept`, `@github`, `@workspace`, @bare-one, @bare-none and `@types/node`.\n")
    (tmp_path / "orchestrator.md").write_text(on_disk, encoding="utf-8")
    got = {i["slug"] for i in at_risk_routed_slugs(tmp_path, ".md", {"kept", "orchestrator"}, "---\n---\n")}
    assert got == {"listed-only", "handoff-only", "github", "bare-one"}


@pytest.mark.parametrize("front, expect", [
    ("agents: [orchestrator, flow-one, 'flow-two']\n", {"orchestrator", "flow-one", "flow-two"}),
    ("agents:\n  - orchestrator   # the hub\n# a column-0 comment\n  - commented  # bespoke\n",
     {"orchestrator", "commented"}),
    ("handoffs:\n  - label: Go\n    agent: handed  # comment\n    send: false\n", {"handed"}),
    ("handoffs:\n- label: Go\n  agent: unindented\n  custom-key: anything\n", {"unindented"}),
    ("handoffs:\n  - label: Go\n    agent: Plan\n", {"plan"}),
    ("agents: ['*']\n", set()),
    ("\"agents\":\n  - quoted-key\n", {"quoted-key"}),
], ids=["flow-list", "comments", "handoff-comment", "column-0-sequence", "display-name", "star", "quoted-key"])
def test_front_matter_forms_are_parsed(front: str, expect: set[str]) -> None:
    assert mentioned_slugs(f"\ufeff---\n{front}---\nBody.\n") == expect   # a BOM before --- too


@pytest.mark.parametrize("front", ["agents: {a: 1}\n", "agents:\n  - Not A Slug!\n",
                                   "handoffs:\n  - label: Go\n    agent: [weird]\n", "handoffs: inline\n",
                                   "handoffs:\n  - {label: Go, agent: foo}\n"],
                         ids=["mapping", "bad-item", "bad-handoff-agent", "inline-handoffs", "flow-mapping-handoff"])
def test_unparseable_routing_front_matter_fails_closed(tmp_path: Path, front: str) -> None:
    with pytest.raises(ValueError):
        mentioned_slugs(f"---\n{front}---\nBody.\n")
    loss = _assess(tmp_path, f"---\n{front}---\nUse `@gone`.\n", "Fresh.\n")
    assert loss.unreadable and loss.would_refuse


def test_without_files_a_bare_mention_or_a_participant_is_not_an_agent() -> None:
    assert mentioned_slugs("Use `@github`, `@workspace`, @prose and `@real`.\n") == {"real"}


def test_a_slug_the_new_orchestrator_still_names_keeps_its_routing(tmp_path: Path) -> None:
    (tmp_path / "orchestrator.md").write_text("Use `@still-routed` and `@dropped`.\n", encoding="utf-8")
    rendered = "Routes `@still-routed` *(if in team)*.\n"
    assert at_risk_routed_slugs(tmp_path, ".md", set(), rendered) == [{"slug": "dropped", "has_file": False}]
    assert at_risk_routed_slugs(tmp_path / "missing", ".md", set(), rendered) == []


class _Adapter:
    def get_file_extension(self, kind: str) -> str:
        return ".md"


class _Args:
    def __init__(self, overwrite: bool = True, discard_user_regions: bool = False, accept: str | None = None,
                 dry_run: bool = False) -> None:
        self.overwrite, self.discard_user_regions, self.dry_run = overwrite, discard_user_regions, dry_run
        self.accept_routing_loss = accept


def _assess(tmp_path: Path, on_disk: str | bytes, rendered: str, manifest: dict | None = None,
            **args: object) -> RoutingLoss:
    path = tmp_path / "orchestrator.md"
    path.write_bytes(on_disk) if isinstance(on_disk, bytes) else path.write_text(on_disk, encoding="utf-8")
    manifest = manifest or {"agent_slug_list": ["orchestrator"]}
    return assess_routing_loss(tmp_path, manifest, _Adapter(), [("orchestrator.md", rendered)], _Args(**args))


def test_an_unreadable_orchestrator_refuses(tmp_path: Path, capsys) -> None:
    loss = _assess(tmp_path, b"\xff\xfe `@dropped`", "")
    assert loss.unreadable and loss.would_refuse and loss.at_risk == ()
    args = _Args()
    assert refuse_routing_loss(tmp_path, {"agent_slug_list": []}, _Adapter(), [("orchestrator.md", "")], args) == 1
    assert "can't be read" in capsys.readouterr().err
    assert at_risk_routed_slugs(tmp_path, ".md", set(), "") == []   # the report half still only warns
    assert "could not read orchestrator.md" in capsys.readouterr().err


def test_adopted_agents_are_not_at_risk(tmp_path: Path) -> None:
    manifest = {"agent_slug_list": ["orchestrator"], "adopted_agents": ["adopted"]}
    assert list(_assess(tmp_path, "Use `@adopted` and `@gone`.\n", "Fresh.\n", manifest).at_risk) == [
        {"slug": "gone", "has_file": False}]
    assert _assess(tmp_path, "Use `@gone`.\n", "Fresh.\n", overwrite=False).at_risk == ()


NOTES = "# Orchestrator\n\nBody.\n\n## Project-Specific Notes\n\nRoute ledgers to `@ledger-keeper`.\n"


def test_routing_in_a_user_region_survives_overwrite_and_is_not_at_risk(tmp_path: Path) -> None:
    fresh = "# Orchestrator\n\nNew body.\n\n## Project-Specific Notes\n\n"
    assert _assess(tmp_path, NOTES, fresh).at_risk == ()
    assert list(_assess(tmp_path, NOTES, fresh, discard_user_regions=True).at_risk) == [
        {"slug": "ledger-keeper", "has_file": False}]


def test_an_orchestrator_this_run_does_not_rewrite_drops_nothing(tmp_path: Path) -> None:
    (tmp_path / "orchestrator.md").write_text("Use `@gone`.\n", encoding="utf-8")
    loss = assess_routing_loss(tmp_path, {"agent_slug_list": []}, _Adapter(), [("navigator.md", "x")], _Args())
    assert loss.at_risk == () and not loss.would_refuse


def test_a_dry_run_never_refuses(tmp_path: Path) -> None:
    (tmp_path / "orchestrator.md").write_text("Use `@gone`.\n", encoding="utf-8")
    assert refuse_routing_loss(tmp_path, {"agent_slug_list": []}, _Adapter(), [("orchestrator.md", "x")],
                               _Args(dry_run=True)) is None


@pytest.mark.parametrize("mode", [("--update", "--overwrite"), ("--overwrite",)], ids=["update-overwrite", "plain"])
def test_carried_region_problems_leave_the_orchestrator_unwritten(team: tuple[Path, Path], capsys,
                                                                    mode: tuple[str, ...]) -> None:
    # Condition 9: when the user region can't be carried, the assessment reports no loss because emit refuses to
    # write the orchestrator. Prove that emit really leaves it as it was.
    from agentteams import user_regions

    orch = team[1] / "orchestrator.agent.md"
    text = orch.read_text(encoding="utf-8")
    span = user_regions.find_region(text, "## Project-Specific Notes")
    if span is None:
        pytest.skip("this framework's orchestrator has no Project-Specific Notes region")
    following = user_regions._next_heading(text, span[0] + 1, span[2] + "## ")
    assert following is not None
    # The user's region repeats the next template heading twice: carry_regions can't split it safely.
    orch.write_text(text.replace("## Project-Specific Notes\n",
                                 f"## Project-Specific Notes\n\nmine\n{following}\n\nagain\n{following}\n", 1),
                    encoding="utf-8")
    assert user_regions.carry_regions(orch.read_text(encoding="utf-8"), text, toml=False)[2], "not a carry problem"
    before = orch.read_bytes()
    capsys.readouterr()
    rc = _run(team, *mode)   # no acknowledgement: the assessment reports no loss in this case
    err = capsys.readouterr().err
    assert "[ROUTING-LOSS]" not in err, "a carry problem must not be reported as a routing loss"
    assert orch.read_bytes() == before, "emit wrote an orchestrator whose user region it couldn't carry"
    # emit refuses this one file and says why, and that error is the run's exit 1; nothing was a routing refusal.
    assert rc == 1 and "orchestrator.agent.md: not overwritten:" in err
