"""agentteams.vulnscan.operator_cli — halts, resolve, say, publish-grant, publish (R6 I6). Network-free."""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

import pytest

from agentteams.vulnscan import __main__ as cli
from agentteams.vulnscan import escalations, ledger, notes, operator_cli, publish
from tests._vulnscan_helpers import operator_env


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    world = operator_env(tmp_path, monkeypatch)
    return {**world, "cfg": {"governance_dir": str(world["gov"])}}


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


# ---------------------------------------------------------------- V23: resolve through main()

def test_resolve_through_main_without_a_terminal_changes_nothing(env: dict, monkeypatch: pytest.MonkeyPatch,
                                                                capsys: pytest.CaptureFixture) -> None:
    other = _escalate("other")
    monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))          # the real `input` would read this "y"
    before = ledger.ledger_path().read_bytes()
    assert cli.main(["resolve", other.id, "--evidence", "checked"]) == 2
    assert "interactive confirmation" in capsys.readouterr().err
    assert ledger.ledger_path().read_bytes() == before
    assert [e.id for e in escalations.open_escalations()] == [other.id]


def test_resolve_through_main_refuses_a_tampered_signed_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                               capsys: pytest.CaptureFixture) -> None:
    pytest.importorskip("cryptography")
    from agentteams.cli.signed_ledger import ed25519_sign
    from tests._vulnscan_helpers import operator_home

    world = operator_home(tmp_path, monkeypatch)
    ioc = _escalate("ioc")

    def sign(rec: dict, **kw: object) -> dict:
        return {**rec, "signature": ed25519_sign(world["pem"], escalations.resolution_signed_values(rec))}

    monkeypatch.setattr(escalations, "sign_resolution",
                        lambda rec, **kw: {**sign(rec), "evidence": rec["evidence"] + " (edited after signing)"})
    before = ledger.ledger_path().read_bytes()
    argv = ["resolve", ioc.id, "--evidence", "rebuilt host", "--key-id", "op"]
    assert cli.main(argv) == 2
    assert "signature does not verify" in capsys.readouterr().err
    assert ledger.ledger_path().read_bytes() == before
    assert [e.id for e in escalations.open_escalations()] == [ioc.id]
    monkeypatch.setattr(escalations, "sign_resolution", sign)                  # control: the untampered record
    assert cli.main(argv) == 0 and not escalations.open_escalations()


# ---------------------------------------------------------------- V29: halts with a real HALT row

def test_halts_through_main_with_a_real_halt_row(env: dict, capsys: pytest.CaptureFixture) -> None:
    cfg = env["tmp"] / "home" / ".config" / "agentteams" / "vulnscan.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"governance_dir": str(env["gov"])}))
    assert cli.main(["halts"]) == 0
    with (env["gov"] / "references" / "security-decisions.log.csv").open("a") as fh:
        fh.write("2026-10-22,vulnscan,s1,vulnscan-remediation:web1,HALT,,,suspected compromise,security\n")
    capsys.readouterr()
    assert cli.main(["halts"]) == 1
    out = capsys.readouterr().out
    assert "vulnscan-remediation blocked: " in out and "vulnscan-publish blocked" not in out
    spec = json.loads(out[out.index("{"):out.index("}") + 1])
    assert spec["action_reviewed"] == "vulnscan-remediation:web1" and spec["verdict"] == "HALT-RETRACTED"
    assert f"--output {env['gov'].resolve()}" in out


# ---------------------------------------------------------------- item 19: empty ids from failed gh lookups

@pytest.mark.parametrize(("flag", "value", "want"), [
    ("--owner-id", "", "--owner-id is empty — run the gh lookup first; it must be your numeric GitHub user id"),
    ("--owner-id", "alice", "--owner-id must be your numeric GitHub user id (got 'alice')"),
    ("--node-id", "", "--node-id is empty — run the gh lookup first; it must be the ops repository's node id"),
])
def test_publish_grant_sign_with_an_empty_id_says_why(env: dict, capsys: pytest.CaptureFixture, flag: str,
                                                      value: str, want: str) -> None:
    other = {"--owner-id": ["--node-id", "R_kgDOAAAAAA"], "--node-id": ["--owner-id", "12345"]}[flag]
    argv = ["publish-grant", "sign", "--ops-repo", str(env["tmp"]), flag, value, *other, "--key-id", "op"]
    assert cli.main(argv) == 2
    err = capsys.readouterr().err
    assert want in err and "invalid int value" not in err
