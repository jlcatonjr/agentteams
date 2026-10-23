"""agentteams.vulnscan.operator_cli — halts, resolve, say, publish-grant, publish (R6 I6). Network-free."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from agentteams.vulnscan import __main__ as cli
from agentteams.vulnscan import escalations, ledger, notes, operator_cli, publish


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_CONFIG_DIR", str(home / ".config" / "agentteams"))
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AGENTTEAMS_SERVER_KIT_DIR", str(tmp_path / "kit"))
    gov = tmp_path / "gov"
    (gov / "references").mkdir(parents=True)
    (gov / "references" / "security-decisions.log.csv").write_text(
        "date,plan_slug,step,decision,status,conditions,conditions_verified,evidence,owner\n")
    return {"tmp": tmp_path, "gov": gov, "cfg": {"governance_dir": str(gov)}}


def _escalate(kind: str, target: str = "web1", run_id: str = "r1") -> escalations.Escalation:
    with ledger.locked():
        return escalations.record(kind, target, "evidence text", request_id="q" + kind, run_id=run_id,
                                  canonical=lambda t: t)


def test_halts_lists_escalations_and_prints_a_retraction_spec_for_a_halt(env: dict, capsys: pytest.CaptureFixture,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    assert operator_cli.cmd_halts(env["cfg"]) == 0 and "- none" in capsys.readouterr().out
    esc = _escalate("other")
    assert operator_cli.cmd_halts(env["cfg"]) == 1
    out = capsys.readouterr().out
    assert esc.id in out and f"vulnscan resolve {esc.id}" in out and "--key-id" not in out
    from agentteams.cli import decision_log

    def halt(gov: Path, *, action: str) -> None:
        if action == operator_cli.ACTION_ID:
            raise RuntimeError("latest decision for action 'vulnscan-remediation:web1' is HALT (row 3, dated x)")
    monkeypatch.setattr(decision_log, "_assert_no_unretracted_halt", halt)
    operator_cli.cmd_halts(env["cfg"])
    out = capsys.readouterr().out
    assert "vulnscan-remediation blocked: latest decision" in out and "vulnscan-publish blocked" not in out
    assert '"action_reviewed": "vulnscan-remediation:web1"' in out          # the HALT's own id, not the family
    assert "--sign-decision" in out and '"verdict": "HALT-RETRACTED"' in out


def test_halts_says_none_for_the_halt_section_while_an_escalation_is_open(env: dict, capsys: pytest.CaptureFixture,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    _escalate("other")
    monkeypatch.setattr(operator_cli, "_governance_dirs", lambda cfg: [env["gov"]])
    assert operator_cli.cmd_halts(env["cfg"]) == 1
    halt_section = capsys.readouterr().out.split("## @security HALTs (decision log)")[1]
    assert halt_section.strip() == "- none"


def test_halts_reports_a_missing_decision_log_as_blocking(env: dict, capsys: pytest.CaptureFixture) -> None:
    (env["gov"] / "references" / "security-decisions.log.csv").unlink()
    assert operator_cli.cmd_halts(env["cfg"]) == 1
    out = capsys.readouterr().out
    assert "missing or a symlink" in out and "repair the log" in out and "--sign-decision" not in out


def test_serve_requests_refuses_while_a_run_holds_the_lock(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    import fcntl

    from agentteams.vulnscan import agent_run
    monkeypatch.setattr(cli.daily, "load_config", lambda: env["cfg"])
    agent_run.run_lock_path().parent.mkdir(parents=True, exist_ok=True)
    with open(agent_run.run_lock_path(), "a+") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert cli.main(["serve-requests"]) == 2
    assert cli.main(["serve-requests"]) == 0


def _resolve_args(esc_id: str, **kw: object) -> argparse.Namespace:
    return argparse.Namespace(esc_id=esc_id, evidence=kw.get("evidence", "checked"), key_id=kw.get("key_id", "op"),
                              confirm_review_sha256=None, allow_checkout_signing=False)


def test_resolve_other_needs_confirmation_and_serious_needs_a_signature(env: dict,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    other = _escalate("other")
    assert operator_cli.cmd_resolve(_resolve_args(other.id), ask=lambda q: "n") == 1
    assert operator_cli.cmd_resolve(_resolve_args(other.id), ask=lambda q: "y") == 0
    assert not escalations.open_escalations()
    ioc = _escalate("ioc", run_id="r2")
    monkeypatch.setattr(escalations, "sign_resolution", lambda rec, **kw: None)     # operator declined to sign
    assert operator_cli.cmd_resolve(_resolve_args(ioc.id), ask=lambda q: "y") == 1
    assert [e.id for e in escalations.open_escalations()] == [ioc.id]              # "y" alone never clears it
    assert operator_cli.cmd_resolve(_resolve_args("ve-0000000000000000")) == 2


def test_say_add_list_remove_through_main(env: dict, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["say", "pin tqdm below 5 in svc", "--days", "3"]) == 0
    note_id = capsys.readouterr().out.split()[1]
    assert cli.main(["say", "--list"]) == 0 and "pin tqdm" in capsys.readouterr().out
    assert cli.main(["say", "--remove", note_id]) == 0 and not notes.active()[0]
    assert cli.main(["say"]) == 2


def test_publish_grant_and_publish_without_a_grant(env: dict, capsys: pytest.CaptureFixture,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    assert cli.main(["publish-grant", "list"]) == 0
    assert cli.main(["publish-grant", "sign", "--ops-repo", str(env["tmp"]), "--node-id", "R_x", "--owner-id", "1",
                     "--governance-dir", str(env["gov"]), "--key-id", "op"]) == 2      # not a git repo: refused
    (env["tmp"] / "state" / "2026-10-22").mkdir(parents=True)
    (env["tmp"] / "state" / "2026-10-22" / "summary.json").write_text(json.dumps({"findings": []}))
    monkeypatch.setattr(cli.daily, "load_config", lambda: env["cfg"])
    monkeypatch.setattr(publish, "load_active", lambda **k: ([], []))
    assert cli.main(["publish"]) == 1
    assert cli.main(["publish", "--run", "2000-01-01"]) == 2
