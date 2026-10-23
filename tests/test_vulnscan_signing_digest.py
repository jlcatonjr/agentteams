"""The review digest covers what the operator chose, so ``--confirm-review-sha256`` works (P1).

Before: every run minted a fresh ``grant_id`` / ``issued`` and the digest hashed them (and the working
directory), so the digest a non-interactive run printed could never confirm the next run. Now the
digest covers the record minus the minted fields, plus the exact lifetime. The flow exercised here is
the real one: the first run (no terminal) prints the digest and refuses; a second run, with a freshly
built record, is confirmed by that digest.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from agentteams.cli import operator_signing as ops_  # noqa: E402
from agentteams.vulnscan import escalations, grant, ledger  # noqa: E402

_DIGEST = re.compile(r"review sha256\s*:\s*([0-9a-f]{64})")


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_CONFIG_DIR", str(home / ".config" / "agentteams"))
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(tmp_path / "state"))
    key = Ed25519PrivateKey.generate()
    vk = home / ".config" / "agentteams" / "verify-keys"
    vk.mkdir(parents=True)
    (vk / "op.pub.pem").write_bytes(key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    gov = tmp_path / "gov"
    (gov / "references").mkdir(parents=True)
    (gov / "references" / "security-decisions.log.csv").write_text(
        "date,plan_slug,step,decision,status,conditions,conditions_verified,evidence,owner\n")
    (tmp_path / "code").mkdir()
    # The preflight (env key file, integrity, install location) is covered by test_sign_confirm.py;
    # here it passes, and the key read returns the test key. _confirm is the real one, without a terminal.
    monkeypatch.setattr(ops_, "_signing_preflight", lambda *a: ("keyfile", False))
    monkeypatch.setattr(ops_, "_read_operator_private_key", lambda path: pem)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    return {"tmp": tmp_path, "gov": gov}


def _grant(env: dict, **over: object) -> dict:
    args = {"ops": ["stage-fix"], "repo_roots": [str(env["tmp"] / "code")], "hosts": [], "days": 1, "max_uses": 5,
            "key_id": "op", "governance_dir": str(env["gov"])}
    args.update(over)
    return grant.build(**args)  # type: ignore[arg-type]


def _printed_digest(capsys: pytest.CaptureFixture) -> str:
    out = capsys.readouterr().out
    m = _DIGEST.search(out)
    assert m, out
    return m.group(1)


def test_a_printed_digest_confirms_the_next_run(env: dict, capsys: pytest.CaptureFixture) -> None:
    assert grant.sign(_grant(env)) is None                       # no terminal: prints the digest, refuses
    digest = _printed_digest(capsys)
    fresh = _grant(env)                                          # a new grant_id and issued time
    signed = grant.sign(fresh, confirm_sha256=digest)
    assert signed is not None and signed["grant_id"] == fresh["grant_id"]
    grant.verify(signed)


def test_the_digest_still_binds_every_choice_and_the_lifetime(env: dict, capsys: pytest.CaptureFixture) -> None:
    grant.sign(_grant(env))
    digest = _printed_digest(capsys)
    for change in ({"max_uses": 6}, {"days": 2}, {"ops": ["stage-fix", "open-pr"]},
                   {"repo_roots": [str(env["tmp"])]}):
        assert grant.sign(_grant(env, **change), confirm_sha256=digest) is None, change
        assert "does not match" in capsys.readouterr().err


def test_the_digest_does_not_depend_on_the_working_directory(env: dict, capsys: pytest.CaptureFixture,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    grant.sign(_grant(env))
    digest = _printed_digest(capsys)
    monkeypatch.chdir(env["tmp"] / "code")
    assert grant.sign(_grant(env), confirm_sha256=digest) is not None


def test_minted_fields_are_labelled_in_the_display(env: dict, capsys: pytest.CaptureFixture) -> None:
    grant.sign(_grant(env))
    out = capsys.readouterr().out
    for name in ("grant_id", "issued", "expires"):
        line = next(ln for ln in out.splitlines() if ln.strip().startswith(name))
        assert "minted for this run" in line, line
    assert "minted" not in next(ln for ln in out.splitlines() if ln.strip().startswith("max_uses"))


def test_review_choices_keeps_the_exact_lifetime() -> None:
    now = datetime(2026, 10, 22, tzinfo=UTC)
    rec = {"grant_id": "x", "issued": now.isoformat(), "expires": (now + timedelta(days=3)).isoformat(), "max_uses": 5}
    assert ops_.review_choices(rec) == {"max_uses": 5, "lifetime_seconds": 3 * 86400}
    assert ops_.review_choices({"esc_id": "ve-1", "issued": now.isoformat(), "key_id": "op"}) == \
        {"esc_id": "ve-1", "key_id": "op"}


def test_a_serious_resolution_is_confirmable_by_a_printed_digest(env: dict, capsys: pytest.CaptureFixture) -> None:
    with ledger.locked():
        esc = escalations.record("ioc", "web1", "suspicious binary", request_id="r", run_id="run",
                                 canonical=lambda t: t)
    first = escalations.build_resolution(esc.id, "rebuilt the host", "op",
                                         now=datetime.now(UTC) - timedelta(seconds=30))
    assert escalations.sign_resolution(first) is None
    digest = _printed_digest(capsys)
    signed = escalations.sign_resolution(escalations.build_resolution(esc.id, "rebuilt the host", "op"),
                                         confirm_sha256=digest)
    assert signed is not None
    escalations.apply_signed_resolution(signed)
    assert not escalations.open_escalations()


def test_core_mcp_direct_grant_digest_is_reproducible(env: dict, capsys: pytest.CaptureFixture,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    from agentteams import mcp_direct_grants as G

    monkeypatch.setattr(G, "sign_record", lambda record, pem: {**record, "signature": "sig"})

    def record(offset: int) -> dict:   # built the way cli/mcp_grant_commands.py builds it, per run
        now = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=offset)
        return {"grant_id": os.urandom(8).hex(), "agent": "writer", "tools": ["write_file"], "issued": now.isoformat(),
                "expires": (now + timedelta(days=7)).isoformat(), "max_writes": 50, "key_id": "op"}

    assert ops_.sign_mcp_direct_grant(record(60)) is None
    digest = _printed_digest(capsys)
    signed = ops_.sign_mcp_direct_grant(record(0), confirm_sha256=digest)
    assert signed is not None and signed[0]["signature"] == "sig"
    longer = record(0)
    longer["expires"] = (datetime.fromisoformat(longer["issued"]) + timedelta(days=8)).isoformat()
    assert ops_.sign_mcp_direct_grant(longer, confirm_sha256=digest) is None    # the lifetime is bound
