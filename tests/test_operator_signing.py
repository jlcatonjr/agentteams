"""Operator signing path (``--sign-decision`` / ``--sign-grant`` / ``--issue-grant``).

**T1 — characterization (pin-signing-cli-modules).** Every case below drives the real CLI
(``agentteams.cli.app.main``) and compares the exit code plus the *complete* stdout and stderr,
normalised only for the temp dir, the random grant id and the HOME path. The expectations were
recorded against the pre-extraction runners (main @ 4efabf3) and are the byte-identity proof
that moving the key handling, payload construction, display, refusals, signing and append into
the integrity-pinned ``agentteams/cli/operator_signing.py`` changed no operator-visible output.
Any later change to an expectation is a deliberate, reviewed output change — never a fixup.

The four error-ORDER cases (@adversarial, binding revisions) pin precedence, not just messages:
a bad framework with the env var unset (the env error wins), a bad framework with a non-integer
``max_uses`` (the framework error wins), ``max_uses: null``, and the ``--issue-grant`` write
refusal.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import pytest

pytest.importorskip("cryptography", reason="the 'signing' extra is not installed")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from agentteams.cli import grants  # noqa: E402
from agentteams.cli.app import main  # noqa: E402

_KEY_ENV = "AGENTTEAMS_DECISION_ED25519_KEYFILE"
_KEY_ID = "op-1"
_FAR = "2099-01-01T00:00:00Z"


def _pem_pair() -> tuple[bytes, bytes]:
    priv = Ed25519PrivateKey.generate()
    return (
        priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()),
        priv.public_key().public_bytes(serialization.Encoding.PEM,
                                       serialization.PublicFormat.SubjectPublicKeyInfo),
    )


_PRIV, _PUB = _pem_pair()
_OTHER_PRIV, _OTHER_PUB = _pem_pair()


def _keyfile(tmp: Path) -> Path:
    path = tmp / "op.key"
    path.write_bytes(_PRIV)
    path.chmod(0o600)
    return path


def _team(tmp: Path, *, pub: bytes = _PUB, roster: bool = True) -> Path:
    team = tmp / "team"
    store = team / "references" / "authorized-verify-keys"
    store.mkdir(parents=True, exist_ok=True)
    (team / "references" / "agent-privilege.json").write_text("{}\n", encoding="utf-8")
    (store / f"{_KEY_ID}.pub.pem").write_bytes(pub)
    if roster:
        (team / "references" / "security-approvers.txt").write_text("alice\n", encoding="utf-8")
    return team


def _write(tmp: Path, name: str, payload: object) -> Path:
    path = tmp / name
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


def _decision(**over: object) -> dict:
    spec = {
        "date": "2026-09-30", "action_reviewed": "grant-cross-repo-write", "verdict": "PASS",
        "effect_grants": "write:cross-repo", "derives_from": "approved-cleanup",
        "key_id": _KEY_ID, "author": "security",
    }
    spec.update(over)
    return {k: v for k, v in spec.items() if v is not None}


def _grant(**over: object) -> dict:
    spec = {
        "issuer_team": "team-b", "holder_team": "team-a", "target_path": "/abs/b/shared",
        "permitted_ops": "write", "expires_at": _FAR, "max_uses": 1, "approver": "alice",
        "ticket_id": "T-1", "reason_code": "collab", "key_id": _KEY_ID,
    }
    spec.update(over)
    return spec


# --------------------------------------------------------------------------------------------
# Case builders: each prepares tmp state + env and returns the argv.
# --------------------------------------------------------------------------------------------

def _sd(tmp: Path, mp: pytest.MonkeyPatch, *, spec: object = None, team: Path | None = None,
        key: bool = True) -> list[str]:
    if key:
        mp.setenv(_KEY_ENV, str(_keyfile(tmp)))
    else:
        mp.delenv(_KEY_ENV, raising=False)
    out = team if team is not None else _team(tmp)
    return ["--sign-decision", str(_write(tmp, "spec.json", _decision() if spec is None else spec)),
            "--output", str(out)]


def _sg(tmp: Path, mp: pytest.MonkeyPatch, *, spec: object = None, key: bool = True,
        framework: str = "claude", roster: bool = True) -> list[str]:
    if key:
        mp.setenv(_KEY_ENV, str(_keyfile(tmp)))
    else:
        mp.delenv(_KEY_ENV, raising=False)
    team = _team(tmp, roster=roster)
    return ["--sign-grant", str(_write(tmp, "grant.json", _grant() if spec is None else spec)),
            "--framework", framework, "--output", str(team)]


def _ig(tmp: Path, mp: pytest.MonkeyPatch, *, spec: object) -> list[str]:
    mp.setenv(grants.GRANT_KEY_ENV, "hmac-test-key")
    team = _team(tmp)
    return ["--issue-grant", str(_write(tmp, "grant.json", spec)),
            "--framework", "claude", "--output", str(team)]


def _missing_key(tmp: Path, mp: pytest.MonkeyPatch, flag: str) -> list[str]:
    argv = _sd(tmp, mp) if flag == "decision" else _sg(tmp, mp)
    mp.setenv(_KEY_ENV, str(tmp / "absent.key"))
    return argv


def _not_a_team(tmp: Path, mp: pytest.MonkeyPatch) -> list[str]:
    proj = tmp / "proj"
    proj.mkdir()
    return _sd(tmp, mp, team=proj)


def _mismatch(tmp: Path, mp: pytest.MonkeyPatch) -> list[str]:
    return _sd(tmp, mp, team=_team(tmp, pub=_OTHER_PUB))


CASES = {
    # --sign-decision
    "decision-success": lambda t, m: _sd(t, m),
    "decision-env-unset": lambda t, m: _sd(t, m, key=False),
    "decision-unreadable-key": lambda t, m: _missing_key(t, m, "decision"),
    "decision-not-a-team-dir": _not_a_team,
    "decision-malformed-spec": lambda t, m: _sd(t, m, spec="{not json"),
    "decision-no-action-reviewed": lambda t, m: _sd(t, m, spec={"verdict": "PASS"}),
    "decision-non-eligible": lambda t, m: _sd(t, m, spec=_decision(
        action_reviewed="grant-verify-key", effect_grants=None, derives_from=None,
        effect_targets="references/authorized-verify-keys/x.pub.pem")),
    "decision-classifier-error": lambda t, m: _sd(t, m, spec=_decision(
        effect_class="non-relaxing")),
    "decision-grant-purpose": lambda t, m: _sd(t, m, spec=_decision(
        date="agentteams-grant-v1")),
    "decision-verify-mismatch": _mismatch,
    # --sign-grant
    "grant-success": lambda t, m: _sg(t, m),
    "grant-env-unset": lambda t, m: _sg(t, m, key=False),
    "grant-missing-key-id": lambda t, m: _sg(t, m, spec={
        k: v for k, v in _grant().items() if k != "key_id"}),
    "grant-unreadable-key": lambda t, m: _missing_key(t, m, "grant"),
    "grant-bad-framework": lambda t, m: _sg(t, m, framework="canonical"),
    "grant-missing-roster": lambda t, m: _sg(t, m, roster=False),
    "grant-malformed-spec": lambda t, m: _sg(t, m, spec={"issuer_team": "x"}),
    # error ORDER (binding revisions, @adversarial)
    "grant-bad-framework-env-unset": lambda t, m: _sg(t, m, framework="canonical", key=False),
    "grant-bad-framework-bad-max-uses": lambda t, m: _sg(
        t, m, framework="canonical", spec=_grant(max_uses="lots")),
    # --issue-grant
    "issue-success": lambda t, m: _ig(t, m, spec=_grant(permitted_ops="read")),
    "issue-write-refusal": lambda t, m: _ig(t, m, spec=_grant()),
}

_HOME_RE = re.escape(os.path.expanduser("~"))


def _norm(text: str, tmp: Path) -> str:
    text = text.replace(str(tmp.resolve()), "<TMP>").replace(str(tmp), "<TMP>")
    text = re.sub(r"grant-[0-9a-f]{16}", "grant-<ID>", text)
    return re.sub(_HOME_RE, "<HOME>", text)


def _invoke(argv: list[str]) -> int:
    """Run ``argv`` through the CLI; ``canonical`` is driven at the runner (see below)."""
    if "canonical" not in argv:
        return main(argv)
    # The parser refuses --framework canonical/generic for every non-interop/bridge verb, so the
    # runner's own ValueError branch is only reachable below the parser (an edited parser, a
    # programmatic caller). It is still part of the precedence contract, so pin it there.
    from agentteams.cli.commands import _run_sign_grant

    flags = dict(zip(argv[::2], argv[1::2]))
    return _run_sign_grant(argparse.Namespace(
        sign_grant=flags["--sign-grant"], framework=flags["--framework"],
        output=flags["--output"], project=None))


def _run(name: str, tmp: Path, mp: pytest.MonkeyPatch, capsys) -> tuple[int, str, str]:
    argv = CASES[name](tmp, mp)
    capsys.readouterr()
    rc = _invoke(argv)
    cap = capsys.readouterr()
    return rc, _norm(cap.out, tmp), _norm(cap.err, tmp)


_KEY_WARN = ("Warning: <TMP>/op.key is outside ~/.config/agentteams/keys: no emitted sandbox "
             "read-denies it, so a sandboxed agent may be able to read it.\n")
_DISPLAY = (
    "About to sign a constraint-relaxing security decision:\n"
    "  action_reviewed : grant-cross-repo-write\n"
    "  verdict         : PASS\n"
    "  derived class   : relaxing\n"
    "  needs operator  : True\n"
    "  grants          : ['write:cross-repo']\n"
    "  write targets   : []\n"
    "  relaxes         : []\n"
    "  destructive/xrepo/bulk: False/False/False\n"
    "  derives_from    : approved-cleanup\n"
    "  key_id          : op-1\n"
)
_NOT_SET = ("Error: AGENTTEAMS_DECISION_ED25519_KEYFILE is not set — it must name the operator "
            "private key file (never an agent env). Refusing to sign (fail-closed).\n")
_ISSUED = ("Issued capability grant grant-<ID> ({scheme}): team-b → team-a may {ops} "
           "/abs/b/shared (expires 2099-01-01T00:00:00Z, max_uses 1)\n"
           "  appended to <TMP>/team/references/capability-grants.log.csv\n"
           "  verified against team dir <TMP>/team\n")

EXPECTED: dict[str, tuple[int, str, str]] = {
    "decision-success": (
        0,
        _DISPLAY + "\nSigned decision appended to <TMP>/team/references/security-decisions.log.csv\n",
        _KEY_WARN,
    ),
    "decision-env-unset": (1, "", _NOT_SET),
    "decision-unreadable-key": (
        1, "",
        "Error: cannot read operator private key file: [Errno 2] No such file or directory: "
        "'<TMP>/absent.key'\n",
    ),
    "decision-not-a-team-dir": (
        1, "",
        "Error: <TMP>/proj is not an agentteams team dir (no references/agent-privilege.json or "
        "references/build-log.json). The gate reads the decisions log and the verify-key store "
        "relative to the team dir; pass --output <project>/.claude/agents (or the team's agents "
        "dir). Refusing to sign (fail-closed).\n",
    ),
    "decision-malformed-spec": (
        1, "",
        "Error: cannot read --sign-decision spec: Expecting property name enclosed in double "
        "quotes: line 1 column 2 (char 1)\n",
    ),
    "decision-no-action-reviewed": (
        1, "", "Error: spec must be a JSON object with a non-empty action_reviewed\n",
    ),
    "decision-non-eligible": (
        1, "",
        _KEY_WARN + "Error: this decision is categorically NON-ELIGIBLE: it authorizes a write to governance/trust root "
        "'references/authorized-verify-keys/x.pub.pem'.\n",
    ),
    "decision-classifier-error": (
        1, "", _KEY_WARN + "Error: inconsistent effect declaration: row declares "
        "effect_class='non-relaxing' but its derived effect is 'relaxing': the relaxing class is "
        "derived from effect, never self-declared, and a disagreement is refused (fail-closed). "
        "Correct the declaration or the effect fields.\n",
    ),
    "decision-grant-purpose": (
        1, _DISPLAY,
        _KEY_WARN + "Error: refusing to sign — this decision's payload begins with the capability-"
        "grant purpose tag (use --sign-grant for grants).\n",
    ),
    "decision-verify-mismatch": (
        1, _DISPLAY,
        _KEY_WARN + "Error: the signature does not verify against "
        "references/authorized-verify-keys/op-1.pub.pem in <TMP>/team (a private/public key "
        "mismatch, or the public key was provisioned into another team dir). Nothing appended.\n",
    ),
    "grant-success": (0, _ISSUED.format(scheme="ed25519", ops="write"), _KEY_WARN),
    "grant-env-unset": (1, "", _NOT_SET),
    "grant-missing-key-id": (
        1, "", "Error: --sign-grant spec must name a key_id (the verify key's file stem)\n",
    ),
    "grant-unreadable-key": (
        1, "",
        "Error: cannot read operator private key file: [Errno 2] No such file or directory: "
        "'<TMP>/absent.key'\n",
    ),
    "grant-bad-framework": (
        1, "", _KEY_WARN + "Error: --framework 'canonical' has no team directory for grants\n",
    ),
    "grant-missing-roster": (1, "", _KEY_WARN + "Error: cross-workspace grant authorization requires an explicit approver "
        "roster; references/security-approvers.txt is absent or names no approver (refusing the "
        "built-in security/@security self-clear fallback — add at least one approver to the "
        "roster before issuing or honouring a grant)\n"),
    "grant-malformed-spec": (
        1, "",
        "Error: grant spec missing required field(s): holder_team, target_path, permitted_ops, "
        "expires_at, max_uses, approver, ticket_id, reason_code\n",
    ),
    "grant-bad-framework-env-unset": (1, "", _NOT_SET),
    "grant-bad-framework-bad-max-uses": (
        1, "", _KEY_WARN + "Error: --framework 'canonical' has no team directory for grants\n",
    ),
    "issue-success": (0, _ISSUED.format(scheme="hmac", ops="read"), ""),
    "issue-write-refusal": (1, "", "Error: grant 'grant-<ID>' permits 'write' but is hmac-signed: a grant that "
        "widens the sandbox allowWrite must be Ed25519-signed by the operator (the shared "
        "AGENTTEAMS_GRANT_SIGNING_KEY is inherited by sandboxed agents). REFUSED. Migrate: "
        "re-issue it with `agentteams --sign-grant SPEC.json --framework <fw> --output <holder "
        "team dir>` (operator key via AGENTTEAMS_DECISION_ED25519_KEYFILE; public key at <team "
        "dir>/references/authorized-verify-keys/<key_id>.pub.pem).\n"),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_characterization(name, tmp_path, monkeypatch, capsys):
    """Exit code + full stdout/stderr equal the recorded pre-extraction output."""
    assert set(CASES) == set(EXPECTED)
    got = _run(name, tmp_path, monkeypatch, capsys)
    assert got == EXPECTED[name]


# ``max_uses: null`` — RECORDED CURRENT BEHAVIOUR: ``int(None)`` raises an uncaught TypeError
# (a traceback, not a clean error). COMMIT-3-FLIP: the max_uses fix replaces this with a clean
# ``Error:`` line and exit 1; this test is flipped in that commit, not deleted.
@pytest.mark.parametrize("flag", ["--sign-grant", "--issue-grant"])
def test_max_uses_null_currently_raises_type_error(flag, tmp_path, monkeypatch):
    spec = _grant(max_uses=None, permitted_ops="read")
    argv = _sg(tmp_path, monkeypatch, spec=spec) if flag == "--sign-grant" else _ig(
        tmp_path, monkeypatch, spec=spec)
    with pytest.raises(TypeError):
        main(argv)


def test_parser_refuses_a_non_rendering_framework_first(tmp_path, monkeypatch):
    """At the CLI, --framework canonical never reaches the runner: argparse exits 2 first."""
    with pytest.raises(SystemExit) as exc:
        main(_sg(tmp_path, monkeypatch, framework="canonical", key=False))
    assert exc.value.code == 2
