"""#9 sign-confirm gate and #10 install-location refusal, exercised WITHOUT the pre-approval fixture."""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from build_team import main  # noqa: E402

from agentteams.cli import operator_signing, signer_location  # noqa: E402
from agentteams.cli.signer_location import LocationFinding  # noqa: E402
from tests.test_operator_signing import _decision, _grant, _sd, _sg  # noqa: E402

_LOG = Path("references") / "security-decisions.log.csv"


@pytest.fixture(autouse=True)
def _location_ok(monkeypatch):
    """The checkout this suite runs from would be refused (#10); the location check is tested below."""
    monkeypatch.setattr(signer_location, "verify_install_location", lambda *a, **k: [])


@pytest.fixture
def key_reads(monkeypatch):
    calls: list[str] = []
    real = operator_signing._read_operator_private_key
    monkeypatch.setattr(operator_signing, "_read_operator_private_key",
                        lambda keyfile: calls.append(keyfile) or real(keyfile))
    return calls


def _no_tty(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)


def _digest(err: str) -> str:
    return re.search(r"--confirm-review-sha256 ([0-9a-f]{64})", err).group(1)


def _rows(team: Path) -> list[dict]:
    path = team / _LOG
    return list(csv.DictReader(path.open(encoding="utf-8"))) if path.exists() else []


def test_no_terminal_without_digest_refuses_with_key_unread(tmp_path, monkeypatch, capsys, key_reads):
    _no_tty(monkeypatch)
    argv = _sd(tmp_path, monkeypatch)
    assert main(argv) == 1
    err = capsys.readouterr().err
    assert "no terminal to confirm on" in err and _digest(err)
    assert key_reads == [] and _rows(tmp_path / "team") == []


def test_matching_digest_signs_and_wrong_digest_refuses(tmp_path, monkeypatch, capsys, key_reads):
    _no_tty(monkeypatch)
    argv = _sd(tmp_path, monkeypatch)
    main(argv)
    digest = _digest(capsys.readouterr().err)
    assert main([*argv, "--confirm-review-sha256", "0" * 64]) == 1
    assert "does not match" in capsys.readouterr().err and key_reads == []
    assert main([*argv, "--confirm-review-sha256", digest]) == 0
    assert len(key_reads) == 1 and len(_rows(tmp_path / "team")) == 1


def test_digest_binds_the_spec(tmp_path, monkeypatch, capsys):
    _no_tty(monkeypatch)
    argv = _sd(tmp_path, monkeypatch)
    main(argv)
    digest = _digest(capsys.readouterr().err)
    (tmp_path / "spec.json").write_text(
        __import__("json").dumps(_decision(action_reviewed="grant-something-else")), encoding="utf-8")
    assert main([*argv, "--confirm-review-sha256", digest]) == 1


def test_yes_never_bypasses(tmp_path, monkeypatch, capsys, key_reads):
    _no_tty(monkeypatch)
    assert main([*_sd(tmp_path, monkeypatch), "--yes"]) == 1 and key_reads == []


@pytest.mark.parametrize("answer,rc", [("y", 0), ("n", 1), ("", 1), (EOFError, 1)])
def test_terminal_prompt(tmp_path, monkeypatch, capsys, answer, rc):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)

    def fake_input(prompt=""):
        if answer is EOFError:
            raise EOFError
        return answer

    monkeypatch.setattr("builtins.input", fake_input)
    assert main(_sd(tmp_path, monkeypatch)) == rc


@pytest.mark.parametrize("bad", ["\x1b[2K", "‮", "​", "\x85", "\r"])
def test_display_spoofing_characters_refuse(tmp_path, monkeypatch, capsys, key_reads, bad):
    argv = _sd(tmp_path, monkeypatch, spec=_decision(action_reviewed=f"grant-x{bad}y"))
    assert main(argv) == 1
    assert "display-spoofing" in capsys.readouterr().err and key_reads == []


def test_grant_display_shows_every_field_and_needs_confirm(tmp_path, monkeypatch, capsys, key_reads):
    _no_tty(monkeypatch)
    argv = _sg(tmp_path, monkeypatch)
    assert main(argv) == 1
    out, err = capsys.readouterr()
    for field in ("issuer_team", "holder_team", "target_path", "permitted_ops", "expires_at",
                  "max_uses", "approver", "ticket_id", "reason_code", "grant_id", "ledger root",
                  "team dir", "review sha256"):
        assert field in out, field
    assert key_reads == [] and "no terminal" in err


def test_location_refusal_and_recorded_override(tmp_path, monkeypatch, capsys, key_reads):
    monkeypatch.setattr(operator_signing, "_confirm", lambda d, c: True)
    refuse = [LocationFinding("a", True, "the agentteams package (/x) is inside a git work tree")]
    monkeypatch.setattr(signer_location, "verify_install_location", lambda *a, **k: refuse)
    argv = _sd(tmp_path, monkeypatch)
    assert main(argv) == 1
    assert "--allow-checkout-signing" in capsys.readouterr().err and key_reads == []
    assert main([*argv, "--allow-checkout-signing"]) == 0
    assert "WARNING: --allow-checkout-signing" in capsys.readouterr().err
    (row,) = _rows(tmp_path / "team")
    assert "signed-from-checkout (--allow-checkout-signing)" in row["conditions_verified"]


def test_grant_contract_dirs_error_refuses_before_confirm(tmp_path, monkeypatch, capsys):
    import json

    confirms: list[str] = []
    monkeypatch.setattr(operator_signing, "_confirm", lambda d, c: confirms.append(d) or True)
    monkeypatch.setattr(operator_signing, "_signing_preflight", lambda *a: ("keyfile", False))
    spec = tmp_path / "grant.json"
    spec.write_text(json.dumps(_grant()), encoding="utf-8")

    def resolve():
        raise ValueError("--framework canonical does not render a team")

    assert operator_signing.sign_grant(str(spec), resolve) == 1
    assert confirms == [] and "does not render" in capsys.readouterr().err


def test_install_location_criterion_a_on_this_checkout(tmp_path):
    """Unpatched: this test suite runs from a git checkout, which (a) must flag."""
    import importlib

    real = importlib.reload(signer_location).verify_install_location
    findings = real([tmp_path], cwd=tmp_path)
    assert any(f.criterion == "a" and f.refuse and "git work tree" in f.message for f in findings)


def test_signer_location_is_pinned_and_in_the_closure():
    from agentteams import integrity

    assert "agentteams/cli/signer_location.py" in integrity.ENFORCEMENT_MODULES
    assert "agentteams/cli/signer_location.py" in operator_signing.SIGNING_CLOSURE


def test_install_location_allow_write_root_and_pth(tmp_path, monkeypatch):
    import importlib
    import json

    sl = importlib.reload(signer_location)
    proj = tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True)
    (proj / ".claude" / "settings.json").write_text(json.dumps(
        {"sandbox": {"filesystem": {"allowWrite": [".", str(Path(sys.prefix).resolve())]}}}))
    monkeypatch.setattr(sl, "_git_work_tree", lambda p: False)
    findings = sl.verify_install_location([proj], cwd=tmp_path)
    assert any(f.criterion == "a" and "agent-writable root" in f.message for f in findings)
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "evil.pth").write_text("import os; os.system('x')\n")
    monkeypatch.setattr(sl.site, "getsitepackages", lambda: [str(site_dir)])
    monkeypatch.setattr(sl.site, "getusersitepackages", lambda: str(tmp_path / "none"))
    assert any(f.criterion == "d" for f in sl.verify_install_location([], cwd=tmp_path))
