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
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("cryptography", reason="the 'signing' extra is not installed")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from agentteams.cli import grants  # noqa: E402
from agentteams.cli.app import main  # noqa: E402

pytestmark = [*globals().get("pytestmark", []), pytest.mark.usefixtures("signing_preapproved")] if isinstance(globals().get("pytestmark", []), list) else [globals()["pytestmark"], pytest.mark.usefixtures("signing_preapproved")]

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

# --------------------------------------------------------------------------------------------
# The pre-sign self-check reads the running package's source root (manifest, git HEAD, latest
# tag). To keep the characterization deterministic regardless of the developer's checkout state,
# every test here points it at a HERMETIC source root: a temp git repo holding a copy of the
# pinned modules, a manifest written from those copies, one commit and one release tag.
# --------------------------------------------------------------------------------------------

REPO = Path(__file__).resolve().parents[1]
_GIT_ID = ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
           "-c", "tag.gpgsign=false", "-c", "core.hooksPath=/dev/null"]


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *_GIT_ID, "-C", str(root), *args], check=True, capture_output=True)


def _hermetic_src(tmp: Path, *, git: bool = True, manifest: bool = True) -> Path:
    from agentteams import integrity

    root = tmp / "src"
    for rel in integrity.ENFORCEMENT_MODULES:
        if rel in integrity.INSTALLED_COPIES or not (REPO / rel).is_file():
            continue
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, root / rel)
    if manifest:
        integrity.write_manifest(root)
    if git:
        _git(root, "init", "-q")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "release")
        _git(root, "tag", "v0.0.1")
    return root


@pytest.fixture(autouse=True)
def _pin_source_root(tmp_path, monkeypatch):
    from agentteams.cli import operator_signing

    src = _hermetic_src(tmp_path)
    monkeypatch.setattr(operator_signing, "_source_root", lambda: src)
    return src


_HOME_RE = re.escape(os.path.expanduser("~"))


def _norm(text: str, tmp: Path) -> str:
    text = text.replace(str(tmp.resolve()), "<TMP>").replace(str(tmp), "<TMP>")
    text = re.sub(r"grant-[0-9a-f]{16}", "grant-<ID>", text)
    text = re.sub(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?\+00:00", "<TS>", text)
    text = re.sub(r"\b[0-9a-f]{64}\b", "<DIGEST>", text)
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

# Commit 2 (pre-sign self-check) — the ONLY output change: every case that gets past the env
# check now prints the C5 checkout warning plus the trusted-install line (the hermetic source root
# is a clean git repo matching its manifest, HEAD and its tag, so no refusal or drift warning).
_PRESIGN = (
    "Warning: signing with the agentteams package at <TMP>/src/agentteams, which is inside a git "
    "work tree or under the current directory: code an agent may be able to edit.\n"
    "Warning: the integrity check catches in-place edits to the signing code, not unpinned code "
    "running in this same process (the CLI dispatch chain, .pth files); the confirm gate binds "
    "the payload, not the code showing it. To rule that out, sign from a pinned install outside every agent "
    "write root (a release-tag pin of the git source, e.g. pipx install \"agentteams[signing] @ "
    "git+https://github.com/jlcatonjr/agentteams.git@v<tag>\") and run --verify-integrity "
    "first.\n"
)

EXPECTED: dict[str, tuple[int, str, str]] = {  # regenerated for #9/#10 (2026-09-30)
    'decision-classifier-error': (1, '',
        "Error: inconsistent effect declaration: row declares effect_class='non-relaxing' but its derived effect is 'relaxing': the relaxing class is derived from effect, never self-declared, and a disagreement is refused (fail-closed). Correct the declaration or the effect fields.\n"),
    'decision-env-unset': (1, '',
        'Error: AGENTTEAMS_DECISION_ED25519_KEYFILE is not set — it must name the operator private key file (never an agent env). Refusing to sign (fail-closed).\n'),
    'decision-grant-purpose': (1, '',
        "Error: refusing to sign — this decision's payload begins with the capability-grant purpose tag (use --sign-grant for grants).\n"),
    'decision-malformed-spec': (1, '',
        'Error: cannot read --sign-decision spec: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)\n'),
    'decision-no-action-reviewed': (1, '',
        'Error: spec must be a JSON object with a non-empty action_reviewed\n'),
    'decision-non-eligible': (1, '',
        "Error: this decision is categorically NON-ELIGIBLE: it authorizes a write to governance/trust root 'references/authorized-verify-keys/x.pub.pem'.\n"),
    'decision-not-a-team-dir': (1, '',
        "Error: <TMP>/proj is not an agentteams team dir (no references/agent-privilege.json or references/build-log.json). The gate reads the decisions log and the verify-key store relative to the team dir; pass --output <project>/.claude/agents (or the team's agents dir). Refusing to sign (fail-closed).\n"),
    'decision-success': (0, 'About to sign a constraint-relaxing security decision:\n  action_reviewed : grant-cross-repo-write\n  verdict         : PASS\n  derived class   : relaxing\n  needs operator  : True\n  grants          : [\'write:cross-repo\']\n  write targets   : []\n  relaxes         : []\n  destructive/xrepo/bulk: False/False/False\n  derives_from    : approved-cleanup\n  key_id          : op-1\n  team dir        : <TMP>/team\n  signature values: ["2026-09-30", "grant-cross-repo-write", "PASS", "", "security", "", "derives_from=approved-cleanup", "sig_scheme=ed25519", "key_id=op-1"]\n\nSigned decision appended to <TMP>/team/references/security-decisions.log.csv\n',
        'Warning: signing with the agentteams package at <TMP>/src/agentteams, which is inside a git work tree or under the current directory: code an agent may be able to edit.\nWarning: the integrity check catches in-place edits to the signing code, not unpinned code running in this same process (the CLI dispatch chain, .pth files); the confirm gate binds the payload, not the code showing it. To rule that out, sign from a pinned install outside every agent write root (a release-tag pin of the git source, e.g. pipx install "agentteams[signing] @ git+https://github.com/jlcatonjr/agentteams.git@v<tag>") and run --verify-integrity first.\nWarning: <TMP>/op.key is outside ~/.config/agentteams/keys: no emitted sandbox read-denies it, so a sandboxed agent may be able to read it.\n'),
    'decision-unreadable-key': (1, '',
        'Warning: signing with the agentteams package at <TMP>/src/agentteams, which is inside a git work tree or under the current directory: code an agent may be able to edit.\nWarning: the integrity check catches in-place edits to the signing code, not unpinned code running in this same process (the CLI dispatch chain, .pth files); the confirm gate binds the payload, not the code showing it. To rule that out, sign from a pinned install outside every agent write root (a release-tag pin of the git source, e.g. pipx install "agentteams[signing] @ git+https://github.com/jlcatonjr/agentteams.git@v<tag>") and run --verify-integrity first.\nError: cannot read operator private key file: \'<TMP>/absent.key\' does not exist or is not a file. Refusing to sign (fail-closed).\n'),
    'decision-verify-mismatch': (1, 'About to sign a constraint-relaxing security decision:\n  action_reviewed : grant-cross-repo-write\n  verdict         : PASS\n  derived class   : relaxing\n  needs operator  : True\n  grants          : [\'write:cross-repo\']\n  write targets   : []\n  relaxes         : []\n  destructive/xrepo/bulk: False/False/False\n  derives_from    : approved-cleanup\n  key_id          : op-1\n  team dir        : <TMP>/team\n  signature values: ["2026-09-30", "grant-cross-repo-write", "PASS", "", "security", "", "derives_from=approved-cleanup", "sig_scheme=ed25519", "key_id=op-1"]\n',
        'Warning: signing with the agentteams package at <TMP>/src/agentteams, which is inside a git work tree or under the current directory: code an agent may be able to edit.\nWarning: the integrity check catches in-place edits to the signing code, not unpinned code running in this same process (the CLI dispatch chain, .pth files); the confirm gate binds the payload, not the code showing it. To rule that out, sign from a pinned install outside every agent write root (a release-tag pin of the git source, e.g. pipx install "agentteams[signing] @ git+https://github.com/jlcatonjr/agentteams.git@v<tag>") and run --verify-integrity first.\nWarning: <TMP>/op.key is outside ~/.config/agentteams/keys: no emitted sandbox read-denies it, so a sandboxed agent may be able to read it.\nError: the signature does not verify against references/authorized-verify-keys/op-1.pub.pem in <TMP>/team (a private/public key mismatch, or the public key was provisioned into another team dir). Nothing appended.\n'),
    'grant-bad-framework': (1, '',
        'Warning: signing with the agentteams package at <TMP>/src/agentteams, which is inside a git work tree or under the current directory: code an agent may be able to edit.\nWarning: the integrity check catches in-place edits to the signing code, not unpinned code running in this same process (the CLI dispatch chain, .pth files); the confirm gate binds the payload, not the code showing it. To rule that out, sign from a pinned install outside every agent write root (a release-tag pin of the git source, e.g. pipx install "agentteams[signing] @ git+https://github.com/jlcatonjr/agentteams.git@v<tag>") and run --verify-integrity first.\nWarning: <TMP>/op.key is outside ~/.config/agentteams/keys: no emitted sandbox read-denies it, so a sandboxed agent may be able to read it.\nError: --framework \'canonical\' has no team directory for grants\n'),
    'grant-bad-framework-bad-max-uses': (1, '',
        'Warning: signing with the agentteams package at <TMP>/src/agentteams, which is inside a git work tree or under the current directory: code an agent may be able to edit.\nWarning: the integrity check catches in-place edits to the signing code, not unpinned code running in this same process (the CLI dispatch chain, .pth files); the confirm gate binds the payload, not the code showing it. To rule that out, sign from a pinned install outside every agent write root (a release-tag pin of the git source, e.g. pipx install "agentteams[signing] @ git+https://github.com/jlcatonjr/agentteams.git@v<tag>") and run --verify-integrity first.\nWarning: <TMP>/op.key is outside ~/.config/agentteams/keys: no emitted sandbox read-denies it, so a sandboxed agent may be able to read it.\nError: --framework \'canonical\' has no team directory for grants\n'),
    'grant-bad-framework-env-unset': (1, '',
        'Error: AGENTTEAMS_DECISION_ED25519_KEYFILE is not set — it must name the operator private key file (never an agent env). Refusing to sign (fail-closed).\n'),
    'grant-env-unset': (1, '',
        'Error: AGENTTEAMS_DECISION_ED25519_KEYFILE is not set — it must name the operator private key file (never an agent env). Refusing to sign (fail-closed).\n'),
    'grant-malformed-spec': (1, '',
        'Error: grant spec missing required field(s): holder_team, target_path, permitted_ops, expires_at, max_uses, approver, ticket_id, reason_code\n'),
    'grant-missing-key-id': (1, '',
        "Error: --sign-grant spec must name a key_id (the verify key's file stem)\n"),
    'grant-missing-roster': (1, "About to sign a capability grant (widens the holder's sandbox allowWrite):\n  issuer_team     : 'team-b'\n  holder_team     : 'team-a'\n  target_path     : '/abs/b/shared'\n  permitted_ops   : 'write'\n  expires_at      : '2099-01-01T00:00:00Z'\n  max_uses        : 1\n  approver        : 'alice'\n  ticket_id       : 'T-1'\n  reason_code     : 'collab'\n  issuer_root     : ''\n  grant_id        : 'grant-<ID>'\n  timestamp       : '<TS>'\n  key_id          : 'op-1'\n  ledger root     : <TMP>/team\n  team dir        : <TMP>/team\n",
        'Warning: signing with the agentteams package at <TMP>/src/agentteams, which is inside a git work tree or under the current directory: code an agent may be able to edit.\nWarning: the integrity check catches in-place edits to the signing code, not unpinned code running in this same process (the CLI dispatch chain, .pth files); the confirm gate binds the payload, not the code showing it. To rule that out, sign from a pinned install outside every agent write root (a release-tag pin of the git source, e.g. pipx install "agentteams[signing] @ git+https://github.com/jlcatonjr/agentteams.git@v<tag>") and run --verify-integrity first.\nWarning: <TMP>/op.key is outside ~/.config/agentteams/keys: no emitted sandbox read-denies it, so a sandboxed agent may be able to read it.\nError: cross-workspace grant authorization requires an explicit approver roster; references/security-approvers.txt is absent or names no approver (refusing the built-in security/@security self-clear fallback — add at least one approver to the roster before issuing or honouring a grant)\n'),
    'grant-success': (0, "About to sign a capability grant (widens the holder's sandbox allowWrite):\n  issuer_team     : 'team-b'\n  holder_team     : 'team-a'\n  target_path     : '/abs/b/shared'\n  permitted_ops   : 'write'\n  expires_at      : '2099-01-01T00:00:00Z'\n  max_uses        : 1\n  approver        : 'alice'\n  ticket_id       : 'T-1'\n  reason_code     : 'collab'\n  issuer_root     : ''\n  grant_id        : 'grant-<ID>'\n  timestamp       : '<TS>'\n  key_id          : 'op-1'\n  ledger root     : <TMP>/team\n  team dir        : <TMP>/team\nIssued capability grant grant-<ID> (ed25519): team-b → team-a may write /abs/b/shared (expires 2099-01-01T00:00:00Z, max_uses 1)\n  appended to <TMP>/team/references/capability-grants.log.csv\n  verified against team dir <TMP>/team\n",
        'Warning: signing with the agentteams package at <TMP>/src/agentteams, which is inside a git work tree or under the current directory: code an agent may be able to edit.\nWarning: the integrity check catches in-place edits to the signing code, not unpinned code running in this same process (the CLI dispatch chain, .pth files); the confirm gate binds the payload, not the code showing it. To rule that out, sign from a pinned install outside every agent write root (a release-tag pin of the git source, e.g. pipx install "agentteams[signing] @ git+https://github.com/jlcatonjr/agentteams.git@v<tag>") and run --verify-integrity first.\nWarning: <TMP>/op.key is outside ~/.config/agentteams/keys: no emitted sandbox read-denies it, so a sandboxed agent may be able to read it.\n'),
    'grant-unreadable-key': (1, '',
        'Warning: signing with the agentteams package at <TMP>/src/agentteams, which is inside a git work tree or under the current directory: code an agent may be able to edit.\nWarning: the integrity check catches in-place edits to the signing code, not unpinned code running in this same process (the CLI dispatch chain, .pth files); the confirm gate binds the payload, not the code showing it. To rule that out, sign from a pinned install outside every agent write root (a release-tag pin of the git source, e.g. pipx install "agentteams[signing] @ git+https://github.com/jlcatonjr/agentteams.git@v<tag>") and run --verify-integrity first.\nError: cannot read operator private key file: \'<TMP>/absent.key\' does not exist or is not a file. Refusing to sign (fail-closed).\n'),
    'issue-success': (0, 'Issued capability grant grant-<ID> (hmac): team-b → team-a may read /abs/b/shared (expires 2099-01-01T00:00:00Z, max_uses 1)\n  appended to <TMP>/team/references/capability-grants.log.csv\n  verified against team dir <TMP>/team\n',
        ''),
    'issue-write-refusal': (1, '',
        "Error: grant 'grant-<ID>' permits 'write' but is hmac-signed: a grant that widens the sandbox allowWrite must be Ed25519-signed by the operator (the shared AGENTTEAMS_GRANT_SIGNING_KEY is inherited by sandboxed agents). REFUSED. Migrate: re-issue it with `agentteams --sign-grant SPEC.json --framework <fw> --output <holder team dir>` (operator key via AGENTTEAMS_DECISION_ED25519_KEYFILE; public key at <team dir>/references/authorized-verify-keys/<key_id>.pub.pem).\n"),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_characterization(name, tmp_path, monkeypatch, capsys):
    """Exit code + full stdout/stderr equal the recorded pre-extraction output."""
    assert set(CASES) == set(EXPECTED)
    got = _run(name, tmp_path, monkeypatch, capsys)
    assert got == EXPECTED[name]


# ``max_uses: null`` used to raise an uncaught TypeError traceback; it is now a clean exit-1 error.
@pytest.mark.parametrize("flag", ["--sign-grant", "--issue-grant"])
def test_max_uses_null_is_a_clean_error(flag, tmp_path, monkeypatch, capsys):
    spec = _grant(max_uses=None, permitted_ops="read")
    argv = _sg(tmp_path, monkeypatch, spec=spec) if flag == "--sign-grant" else _ig(
        tmp_path, monkeypatch, spec=spec)
    assert main(argv) == 1
    assert "max_uses must be an integer (got None)" in capsys.readouterr().err


def test_parser_refuses_a_non_rendering_framework_first(tmp_path, monkeypatch):
    """At the CLI, --framework canonical never reaches the runner: argparse exits 2 first."""
    with pytest.raises(SystemExit) as exc:
        main(_sg(tmp_path, monkeypatch, framework="canonical", key=False))
    assert exc.value.code == 2


# --------------------------------------------------------------------------------------------
# T5 — the pre-sign integrity self-check (binding revisions D1, C1-C5). It runs after the env
# check and BEFORE the key file is opened: the refusal cases name a key file that does not
# exist, so "cannot read operator private key" in the output would mean the key read happened.
# --------------------------------------------------------------------------------------------

_REFUSAL = "Error: refusing to sign: the operator signing code does not match"


def _append(path: Path, text: str = "\n# tampered\n") -> None:
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def _sign_with(argv: list[str], mp: pytest.MonkeyPatch, capsys, key: Path | None = None):
    if key is not None:
        mp.setenv(_KEY_ENV, str(key))
    capsys.readouterr()
    rc = main(argv)
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def _assert_refused_unread(rc: int, out: str, err: str, *needles: str) -> None:
    assert rc == 1 and out == ""
    assert _REFUSAL in err and "The key was not read (fail-closed)." in err
    assert "cannot read operator private key" not in err  # the key file was never opened
    for needle in needles:
        assert needle in err, (needle, err)


@pytest.mark.parametrize("rel", ["agentteams/cli/operator_signing.py",
                                 "agentteams/cli/signed_ledger.py",
                                 "agentteams/frameworks/_sandbox_emit.py"])
def test_closure_drift_refuses_decision_before_key_read(rel, tmp_path, monkeypatch, capsys,
                                                        _pin_source_root):
    _append(_pin_source_root / rel)
    argv = _sd(tmp_path, monkeypatch)
    rc, out, err = _sign_with(argv, monkeypatch, capsys, key=tmp_path / "absent.key")
    _assert_refused_unread(rc, out, err, f"{rel}: modified", "--write-integrity-manifest")
    assert not (tmp_path / "team" / "references" / "security-decisions.log.csv").exists()


def test_closure_drift_refuses_grant_before_key_read(tmp_path, monkeypatch, capsys,
                                                     _pin_source_root):
    _append(_pin_source_root / "agentteams/cli/grants.py")
    argv = _sg(tmp_path, monkeypatch)
    rc, out, err = _sign_with(argv, monkeypatch, capsys, key=tmp_path / "absent.key")
    _assert_refused_unread(rc, out, err, "agentteams/cli/grants.py: modified")
    assert not (tmp_path / "team" / grants.GRANT_LOG_REL).exists()


def test_missing_tracked_manifest_refuses(tmp_path, monkeypatch, capsys, _pin_source_root):
    (_pin_source_root / "references" / "enforcement-integrity.json").unlink()
    argv = _sd(tmp_path, monkeypatch)
    rc, out, err = _sign_with(argv, monkeypatch, capsys, key=tmp_path / "absent.key")
    _assert_refused_unread(rc, out, err, "the integrity manifest is missing")


def test_unreadable_manifest_refuses(tmp_path, monkeypatch, capsys, _pin_source_root):
    (_pin_source_root / "references" / "enforcement-integrity.json").write_text("{not json")
    argv = _sg(tmp_path, monkeypatch)
    rc, out, err = _sign_with(argv, monkeypatch, capsys, key=tmp_path / "absent.key")
    _assert_refused_unread(rc, out, err, "unable to read enforcement integrity manifest")


def test_non_closure_drift_warns_and_still_signs(tmp_path, monkeypatch, capsys, _pin_source_root):
    _append(_pin_source_root / "agentteams/scan.py")
    rc, _out, err = _sign_with(_sd(tmp_path, monkeypatch), monkeypatch, capsys)
    assert rc == 0, err
    assert ("Warning: enforcement-module drift outside the signing path: agentteams/scan.py: "
            "modified") in err
    assert _REFUSAL not in err


def _regenerate(root: Path) -> None:
    from agentteams import integrity

    integrity.write_manifest(root)


def test_head_drift_warns_but_signs(tmp_path, monkeypatch, capsys, _pin_source_root):
    """A closure edit WITH a regenerated manifest passes verify; git HEAD still sees it."""
    _append(_pin_source_root / "agentteams/cli/grants.py")
    _regenerate(_pin_source_root)
    rc, _out, err = _sign_with(_sd(tmp_path, monkeypatch), monkeypatch, capsys)
    assert rc == 0, err
    assert ("differs from git HEAD (references/enforcement-integrity.json, "
            "agentteams/cli/grants.py)") in err
    assert "differs from the nearest local tag v0.0.1 (unverified" in err
    assert "(agentteams/cli/grants.py)" in err


def test_tag_drift_warns_when_head_is_clean(tmp_path, monkeypatch, capsys, _pin_source_root):
    _append(_pin_source_root / "agentteams/cli/decision_log.py")
    _regenerate(_pin_source_root)
    _git(_pin_source_root, "commit", "-q", "-am", "unreleased signing change")
    rc, _out, err = _sign_with(_sg(tmp_path, monkeypatch), monkeypatch, capsys)
    assert rc == 0, err
    assert "differs from git HEAD" not in err
    assert "(agentteams/cli/decision_log.py)" in err


def test_pip_layout_without_git_or_manifest_changes_no_output(tmp_path, monkeypatch, capsys):
    """No manifest, no git, outside cwd: the self-check is a silent no-op (commit-1 output)."""
    from agentteams.cli import operator_signing

    src = _hermetic_src(tmp_path / "pip", git=False, manifest=False)
    monkeypatch.setattr(operator_signing, "_source_root", lambda: src)
    got = _run("decision-success", tmp_path, monkeypatch, capsys)
    rc, out, err = EXPECTED["decision-success"]
    assert got == (rc, out, err.replace(_PRESIGN, ""))


def test_planted_fsmonitor_in_the_source_repo_never_runs(tmp_path, monkeypatch, capsys,
                                                         _pin_source_root):
    """The git comparisons use the same -c overrides as integrity._manifest_expected (PR-D C1)."""
    marker = tmp_path / "fsmonitor-ran"
    hook = tmp_path / "fsmonitor.sh"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    _git(_pin_source_root, "config", "core.fsmonitor", str(hook))
    _append(_pin_source_root / "agentteams/cli/grants.py")  # make `git diff HEAD` do real work
    _regenerate(_pin_source_root)
    # Anti-vacuous: an UNhardened git command in this repo does execute the planted hook.
    subprocess.run(["git", "-C", str(_pin_source_root), "status", "--porcelain"],
                   capture_output=True, check=False)
    assert marker.exists(), "planted fsmonitor did not run unhardened — the test would be vacuous"
    marker.unlink()
    rc, _out, err = _sign_with(_sd(tmp_path, monkeypatch), monkeypatch, capsys)
    assert rc == 0, err
    assert "differs from git HEAD" in err  # the hardened comparison did run
    assert not marker.exists(), "the signing self-check executed a repo-planted core.fsmonitor"


def test_self_check_has_no_environment_override() -> None:
    """D1: no env var can switch the self-check off (only the key path comes from the env)."""
    import inspect

    from agentteams.cli import operator_signing

    for fn in (operator_signing.presign_integrity_check, operator_signing._git_drift_warnings,
               operator_signing._checkout_warning):
        source = inspect.getsource(fn)
        assert "os.getenv" not in source and "os.environ" not in source, fn.__name__


def test_planted_clean_filter_in_the_source_repo_never_runs(tmp_path, monkeypatch, capsys,
                                                            _pin_source_root):
    """@security K3: `git diff`/`git show` run repo-defined content filters (a planted
    .gitattributes + filter.*.clean) as the operator, outside the sandbox, with the keyfile env
    set. The self-check compares via ls-tree object ids and Python-computed blob ids instead."""
    marker = tmp_path / "filter-ran"
    (_pin_source_root / ".gitattributes").write_text("* filter=evil\n", encoding="utf-8")
    _git(_pin_source_root, "config", "filter.evil.clean", f"touch '{marker}'; cat")
    _git(_pin_source_root, "config", "filter.evil.smudge", f"touch '{marker}'; cat")
    _append(_pin_source_root / "agentteams/cli/grants.py")
    _regenerate(_pin_source_root)
    # Anti-vacuous: an ordinary `git diff HEAD` here DOES run the planted clean filter.
    subprocess.run(["git", "-C", str(_pin_source_root), "diff", "--quiet", "HEAD", "--",
                    "agentteams/cli/grants.py"], capture_output=True, check=False)
    assert marker.exists(), "the planted clean filter did not run under git diff: test would be vacuous"
    marker.unlink()
    rc, _out, err = _sign_with(_sd(tmp_path, monkeypatch), monkeypatch, capsys)
    assert rc == 0, err
    assert "differs from git HEAD" in err  # drift is still detected without filters
    assert not marker.exists(), "the signing self-check executed a repo-planted content filter"


def test_sign_grant_resolves_dirs_before_the_key_is_read(tmp_path, monkeypatch):
    """@security K1: resolve_dirs runs unpinned adapter code; it must not run with the key in memory."""
    from agentteams.cli import operator_signing

    order: list[str] = []
    monkeypatch.setattr(operator_signing, "_signing_preflight", lambda *a: ("keyfile", False))
    monkeypatch.setattr(operator_signing, "_read_operator_private_key",
                        lambda keyfile: order.append("key") or None)
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({**_grant(), "key_id": "op"}), encoding="utf-8")

    def resolve():
        order.append("dirs")
        return tmp_path, tmp_path

    assert operator_signing.sign_grant(str(spec), resolve) == 1  # key unavailable → refuse
    assert order == ["dirs", "key"]


def test_signing_closure_covers_every_module_run_with_the_key(tmp_path):
    """@security K2: modules the minters execute between the key read and the append."""
    from agentteams import integrity
    from agentteams.cli import operator_signing

    for rel in ("agentteams/cli/governance_targets.py", "agentteams/cli/management_directives.py",
                "agentteams/atomicio.py"):
        assert rel in operator_signing.SIGNING_CLOSURE
        assert rel in integrity.ENFORCEMENT_MODULES



@pytest.fixture(autouse=True)
def _no_cryptography_floor_warning(monkeypatch):
    """The #20 one-shot floor warning depends on the host's cryptography; keep goldens host-neutral."""
    from agentteams.cli import signed_ledger

    monkeypatch.setattr(signed_ledger, "_CRYPTO_WARNED", [True])
