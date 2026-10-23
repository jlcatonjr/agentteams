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
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from agentteams.cli import operator_signing as ops_
from agentteams.vulnscan import escalations, grant, ledger, publish
from tests._vulnscan_helpers import operator_home

_DIGEST = re.compile(r"review sha256\s*:\s*([0-9a-f]{64})")


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """The shared operator home (0700 verify-keys dir, 0600 key), a code root, and a counting key reader.

    Returns:
        ``{"tmp", "gov", "reads"}``; ``reads`` lists every private-key read (one entry per read).
    """
    world = operator_home(tmp_path, monkeypatch)
    (tmp_path / "code").mkdir()
    reads: list[str] = []

    def read_key(path: str) -> str:
        reads.append(path)
        return world["pem"]

    # The preflight (env key file, integrity, install location) is covered by test_sign_confirm.py;
    # here it passes, and the key read returns the test key. _confirm is the real one, without a terminal.
    monkeypatch.setattr(ops_, "_signing_preflight", lambda *a: ("keyfile", False))
    monkeypatch.setattr(ops_, "_read_operator_private_key", read_key)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    return {"tmp": tmp_path, "gov": world["gov"], "reads": reads}


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
    gov2 = env["tmp"] / "gov2"
    (gov2 / "references").mkdir(parents=True)
    (gov2 / "references" / "security-decisions.log.csv").write_text("date\n")
    grant.sign(_grant(env))
    digest = _printed_digest(capsys)
    for change in ({"max_uses": 6}, {"days": 2}, {"ops": ["stage-fix", "open-pr"]},
                   {"repo_roots": [str(env["tmp"])]}, {"hosts": ["web1"]}, {"governance_dir": str(gov2)},
                   {"key_id": "op2"}):
        assert grant.sign(_grant(env, **change), confirm_sha256=digest) is None, change
        assert "does not match" in capsys.readouterr().err
    assert env["reads"] == []                                    # a mismatch never reads the key
    assert grant.sign(_grant(env), confirm_sha256=digest) is not None    # control: the unchanged choice signs
    assert len(env["reads"]) == 1


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


def _ops_repo(env: dict) -> Path:
    repo = env["tmp"] / "ops"
    repo.mkdir(exist_ok=True)
    if not (repo / ".git").exists():
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://github.com/acme/ops.git"],
                       check=True)
    return repo


def _publish_grant(env: dict, **over: object) -> dict:
    args = {"ops_repo": str(_ops_repo(env)), "remote_url": "https://github.com/acme/ops.git", "node_id": "R_kgDOabc",
            "owner_id": 42, "governance_dir": str(env["gov"]), "days": 7, "max_uses": 30, "key_id": "op"}
    args.update(over)
    return publish.build(**args)  # type: ignore[arg-type]


def test_a_printed_digest_confirms_the_next_publish_grant_run(env: dict, capsys: pytest.CaptureFixture) -> None:
    assert publish.sign(_publish_grant(env)) is None             # no terminal: prints the digest, refuses
    digest = _printed_digest(capsys)
    fresh = _publish_grant(env)
    signed = publish.sign(fresh, confirm_sha256=digest)
    assert signed is not None and signed["grant_id"] == fresh["grant_id"]
    publish.verify(signed)
    publish.install(signed)
    assert [g["grant_id"] for g in publish.load_active()[0]] == [fresh["grant_id"]]


def test_the_publish_grant_digest_binds_every_choice(env: dict, capsys: pytest.CaptureFixture) -> None:
    publish.sign(_publish_grant(env))
    digest = _printed_digest(capsys)
    for change in ({"max_uses": 31}, {"days": 8}, {"node_id": "R_other"}, {"owner_id": 43},
                   {"remote_url": "https://github.com/acme/other.git"}, {"governance_dir": str(env["tmp"] / "gov2")}):
        if "governance_dir" in change:
            (env["tmp"] / "gov2" / "references").mkdir(parents=True, exist_ok=True)
            (env["tmp"] / "gov2" / "references" / "security-decisions.log.csv").write_text("date\n")
        assert publish.sign(_publish_grant(env, **change), confirm_sha256=digest) is None, change
        assert "does not match" in capsys.readouterr().err


def test_a_remediation_grant_digest_does_not_confirm_a_publish_grant(env: dict, capsys: pytest.CaptureFixture) -> None:
    grant.sign(_grant(env))
    digest = _printed_digest(capsys)
    assert publish.sign(_publish_grant(env), confirm_sha256=digest) is None
    assert "does not match" in capsys.readouterr().err and env["reads"] == []


def test_a_publish_grant_digest_does_not_confirm_a_remediation_grant(env: dict, capsys: pytest.CaptureFixture) -> None:
    """V7, the reverse direction: the digest kind is bound, so neither record type confirms the other."""
    publish.sign(_publish_grant(env))
    digest = _printed_digest(capsys)
    assert grant.sign(_grant(env), confirm_sha256=digest) is None
    assert "does not match" in capsys.readouterr().err and env["reads"] == []
    assert publish.sign(_publish_grant(env), confirm_sha256=digest) is not None   # control: its own kind signs
    assert len(env["reads"]) == 1


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
