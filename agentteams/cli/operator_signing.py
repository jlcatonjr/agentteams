"""operator_signing.py — the integrity-pinned operator signing path (pin-signing-cli-modules).

``--sign-decision`` and ``--sign-grant`` are the only code paths that read the operator's Ed25519
**private key** (the file named by ``AGENTTEAMS_DECISION_ED25519_KEYFILE``). Their orchestration —
env read, key-file read, payload construction, the operator display, the pre-sign refusals, the
signature, verify-before-append and the append — lives here, in a module listed in
``integrity.ENFORCEMENT_MODULES``, so an in-place edit to it is recorded by
``references/enforcement-integrity.json`` and flagged by ``--verify-integrity`` / red-team phase 6.
The CLI runners in ``commands.py`` and ``grant_commands.py`` are one-line delegators that only
choose the spec path and the output/team dir; they never see key material or a signed field.
The grant spec helpers (``load_grant_spec``, ``grant_spec_kwargs``, ``report_issued``) live here
too, so the HMAC ``--issue-grant`` payload construction is pinned by the same entry.

**What this does NOT defend against (stated so it is not over-trusted).** The pin closes in-place
edits to this orchestration and unnoticed drift. It does not defend against unpinned code running
in the same process: the dispatch chain (``app.py``, ``parser.py``, ``agentteams/__init__.py``,
``.pth`` files) can patch ``Path.read_text`` or this module's primitives, or call
``signed_ledger`` directly. The decision display is printed immediately before signing with no
confirmation: it is a RECORD, not a gate. The durable mitigation is to sign from a pinned install
outside every agent write root (a release-tag pin of the git source, for example
``pipx install "agentteams[signing] @ git+https://github.com/jlcatonjr/agentteams.git@v<tag>"``;
agentteams is not on PyPI).

Provenance: @security PR-E review ("pin them, or move the signing code into a pinned module");
remediation row ``pin-signing-cli-modules``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

#: The env var naming the operator's Ed25519 private key FILE (never the key itself).
KEYFILE_ENV = "AGENTTEAMS_DECISION_ED25519_KEYFILE"

#: The signing closure: the pinned modules the minters execute with the key in memory. Drift in
#: any of them (vs the integrity manifest) REFUSES the minter before the key is read; drift in any
#: other pinned module only warns. A constant in this pinned module, so shrinking it is recorded.
SIGNING_CLOSURE: frozenset[str] = frozenset({
    "agentteams/cli/operator_signing.py",
    "agentteams/cli/signed_ledger.py",
    "agentteams/cli/decision_log.py",
    "agentteams/cli/grants.py",
    "agentteams/cli/effect_classifier.py",
    "agentteams/cli/governance_targets.py",
    "agentteams/cli/management_directives.py",
    "agentteams/atomicio.py",
    "agentteams/frameworks/_sandbox_emit.py",
    "agentteams/integrity.py",
})

#: Finding reasons that refuse regardless of the module (nothing can be verified).
_REFUSE_REASONS = frozenset({"manifest-missing", "unreadable"})

_TRUSTED_INSTALL_HINT = (
    "sign from a pinned install outside every agent write root (a release-tag pin of the git "
    "source, e.g. "
    'pipx install "agentteams[signing] @ git+https://github.com/jlcatonjr/agentteams.git@v<tag>"'
    ") and run --verify-integrity first"
)
_TRUSTED_INSTALL_WARNING = (
    "the integrity check catches in-place edits to the signing code, not unpinned code running in "
    "this same process (the CLI dispatch chain, .pth files), and the pre-sign display is a record, "
    f"not a gate. To rule that out, {_TRUSTED_INSTALL_HINT}."
)


def _source_root() -> Path:
    """The running package's source root (where ``references/enforcement-integrity.json`` lives)."""
    import agentteams as _at

    return Path(_at.__file__).resolve().parent.parent


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """Run a read-only git command hardened against repo-planted config (fsmonitor, hooks).

    Same command-line overrides and environment as ``integrity._manifest_expected`` (@security
    PR-D C1): the repository being inspected is one an agent can write, so its own config must
    not be able to run code here. Returns None when git is unavailable or times out.
    """
    try:
        return subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
             "-c", "core.untrackedCache=false", "-C", str(cwd), *args],
            capture_output=True, text=True, timeout=10, check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _git_ok(cwd: Path, *args: str) -> str | None:
    """stdout of a successful hardened git command, else None."""
    res = _git(cwd, *args)
    if res is None or res.returncode != 0:
        return None
    return res.stdout


def _git_blob_id(path: Path) -> str | None:
    """git's blob id for ``path``'s bytes, computed here so git never runs a content filter on it."""
    import hashlib

    try:
        data = path.read_bytes()
    except OSError:
        return None
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _git_drift_warnings(root: Path) -> list[str]:
    """Warn when the manifest or a signing-closure module differs from HEAD / the latest tag.

    Drift detection only: an agent that can edit the closure can also regenerate the manifest
    (which :func:`presign_integrity_check` alone cannot see), but that two-step edit still shows
    up here against a commit the agent did not make. Skipped when ``root`` is not the top level
    of a git work tree (a pip install, a bare copy).
    """
    from agentteams import integrity

    top = _git_ok(root, "rev-parse", "--show-toplevel")
    if top is None or Path(top.strip()).resolve() != root.resolve():
        return []
    paths = [integrity.MANIFEST_REL_PATH, *sorted(SIGNING_CLOSURE)]
    warnings: list[str] = []
    # Compare against HEAD WITHOUT running repo-defined content filters (@security K3): `git diff`
    # would run a planted `.gitattributes` clean filter as the operator, outside the sandbox, with
    # the keyfile env set. `ls-tree` only reads object ids; the working-tree side is hashed here.
    listed = _git_ok(root, "ls-tree", "HEAD", "--", *paths) or ""
    head_ids = {}
    for line in listed.splitlines():
        meta, _, name = line.partition("\t")
        parts = meta.split()
        if len(parts) == 3:
            head_ids[name] = parts[2]
    changed = [rel for rel in paths if head_ids.get(rel) != _git_blob_id(root / rel)]
    if changed:
        warnings.append(
            f"the signing code or its manifest differs from git HEAD ({', '.join(changed)}); "
            "review that diff before signing.")
    tag = (_git_ok(root, "describe", "--tags", "--abbrev=0") or "").strip()
    # `cat-file blob` never applies filters/textconv (unlike `show`).
    tagged_text = _git_ok(root, "cat-file", "blob", f"{tag}:{integrity.MANIFEST_REL_PATH}") if tag else None
    if tagged_text is None:
        return warnings
    try:
        tagged = json.loads(tagged_text).get("modules", {})
        current = json.loads((root / integrity.MANIFEST_REL_PATH).read_text(encoding="utf-8")
                             ).get("modules", {})
    except (OSError, ValueError, AttributeError):
        return warnings
    differ = sorted(rel for rel in SIGNING_CLOSURE if tagged.get(rel) != current.get(rel))
    if differ:
        warnings.append(
            f"the signing code differs from the nearest local tag {tag} (unverified: a local tag can "
            f"be created by anyone who can write the repository) ({', '.join(differ)}).")
    return warnings


def _checkout_warning(root: Path) -> str | None:
    """C5: warn when the signing package itself sits where an agent may be able to edit it."""
    pkg = (root / "agentteams").resolve()
    in_git = (_git_ok(pkg, "rev-parse", "--is-inside-work-tree") or "").strip() == "true"
    if not in_git and not pkg.is_relative_to(Path.cwd().resolve()):
        return None
    return (f"signing with the agentteams package at {pkg}, which is inside a git work tree or "
            "under the current directory: code an agent may be able to edit.")


def presign_integrity_check() -> bool:
    """Verify the signing closure against the integrity manifest BEFORE the key is read.

    Refuses (prints the reason, returns False) on any manifest finding in :data:`SIGNING_CLOSURE`,
    on a missing manifest the repository should carry, or on an unreadable manifest (fail-closed,
    as ``generate_helpers._verify_enforcement_integrity`` does). Other pinned-module drift, a
    closure that differs from git HEAD or the latest release tag, and a signing package inside a
    git work tree or the current directory print warnings only. There is no environment override.
    In a pip install there is no manifest, so the check is a natural no-op. This check lives
    inside the module it protects: an edit that deletes it is still recorded by the manifest, so
    it is a speed bump with a recorded trail, not a boundary.

    Returns:
        True when signing may proceed.
    """
    from agentteams import integrity

    root = _source_root()
    try:
        findings = integrity.verify(root)
        unreadable = ""
    except RuntimeError as exc:
        findings = [integrity.IntegrityFinding(rel_path=integrity.MANIFEST_REL_PATH, expected="",
                                               actual="", reason="unreadable")]
        unreadable = str(exc)
    blocking = [f for f in findings if f.rel_path in SIGNING_CLOSURE or f.reason in _REFUSE_REASONS]
    if blocking:
        print("Error: refusing to sign: the operator signing code does not match "
              f"{root / integrity.MANIFEST_REL_PATH}:", file=sys.stderr)
        for finding in blocking:
            detail = unreadable if finding.reason == "unreadable" else finding.describe()
            print(f"  {detail}", file=sys.stderr)
        print("  If the change is yours and intended, review it and run "
              f"`agentteams --write-integrity-manifest`; otherwise {_TRUSTED_INSTALL_HINT}. "
              "The key was not read (fail-closed).", file=sys.stderr)
        return False
    for finding in findings:
        print(f"Warning: enforcement-module drift outside the signing path: {finding.describe()}",
              file=sys.stderr)
    warnings = _git_drift_warnings(root)
    checkout = _checkout_warning(root)
    if checkout:
        warnings.append(checkout)
    for warning in warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    if warnings:
        print(f"Warning: {_TRUSTED_INSTALL_WARNING}", file=sys.stderr)
    return True

#: Spec fields every grant minter requires.
GRANT_SPEC_REQUIRED: tuple[str, ...] = (
    "issuer_team", "holder_team", "target_path", "permitted_ops",
    "expires_at", "max_uses", "approver", "ticket_id", "reason_code",
)


def _read_operator_private_key() -> str | None:
    """Read the operator private key PEM named by :data:`KEYFILE_ENV`; print the error on failure.

    Prints the fail-closed "not set" error, the keyfile location warnings, or the read error to
    stderr exactly as both minters always have. Between the env check and the key read it runs
    :func:`presign_integrity_check`, so a drifted signing closure refuses with the key unread.

    Returns:
        The PEM text, or None after printing why it could not be read.
    """
    keyfile = os.getenv(KEYFILE_ENV, "")
    if not keyfile:
        print(
            "Error: AGENTTEAMS_DECISION_ED25519_KEYFILE is not set — it must name the operator "
            "private key file (never an agent env). Refusing to sign (fail-closed).",
            file=sys.stderr,
        )
        return None
    if not presign_integrity_check():
        return None
    from agentteams.frameworks._sandbox_emit import signing_keyfile_warnings

    for warning in signing_keyfile_warnings(keyfile):
        print(f"Warning: {warning}", file=sys.stderr)
    try:
        return Path(keyfile).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Error: cannot read operator private key file: {exc}", file=sys.stderr)
        return None


def sign_decision_team_refusal(output_dir: Path) -> str | None:
    """F-2: return why ``output_dir`` is not a team root ``--sign-decision`` may append to, or None.

    The gate reads the decisions log and the verify-key store relative to the TEAM dir, so a row
    minted anywhere else (``--project .``, a bare CWD) was a silent no-op the gate never read. A
    team is detected by ``references/agent-privilege.json`` OR ``references/build-log.json``.

    Args:
        output_dir: The resolved ``--output`` / ``--project`` / CWD directory.

    Returns:
        The refusal sentence, or None when ``output_dir`` is a team dir.
    """
    refs = output_dir / "references"
    if (refs / "agent-privilege.json").is_file() or (refs / "build-log.json").is_file():
        return None
    return (f"{output_dir} is not an agentteams team dir (no references/agent-privilege.json or "
            "references/build-log.json). The gate reads the decisions log and the verify-key store "
            "relative to the team dir; pass --output <project>/.claude/agents (or the team's agents "
            "dir). Refusing to sign (fail-closed).")


def sign_decision(output_dir: Path, spec_path: str) -> int:
    """Mint and append one operator Ed25519-signed constraint-relaxing decision row.

    Reads a JSON spec (the decision fields + effect_* + derives_from + key_id), loads the operator
    private key from the file named by :data:`KEYFILE_ENV`, refuses a categorically non-eligible
    row, prints the row's derived material effect (a record, not a gate), refuses a payload in the
    grant signing domain, signs the canonical payload with Ed25519, verifies it against the team's
    verify-key store, and appends the signed row.

    Args:
        output_dir: The resolved team dir the row is appended under.
        spec_path: Path to the JSON decision spec.

    Returns:
        0 on success, 1 on any error (fail-closed).
    """
    import json

    from agentteams.cli import decision_log as dl
    from agentteams.cli import effect_classifier as ec
    from agentteams.cli import signed_ledger as sl

    refusal = sign_decision_team_refusal(output_dir)
    if refusal:
        print(f"Error: {refusal}", file=sys.stderr)
        return 1
    try:
        spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Error: cannot read --sign-decision spec: {exc}", file=sys.stderr)
        return 1
    if not isinstance(spec, dict) or not (spec.get("action_reviewed") or "").strip():
        print("Error: spec must be a JSON object with a non-empty action_reviewed", file=sys.stderr)
        return 1

    private_pem = _read_operator_private_key()
    if private_pem is None:
        return 1

    row = {k: str(v) for k, v in spec.items()}
    row["sig_scheme"] = sl.SIG_SCHEME_ED25519
    row.setdefault("verdict", "PASS")

    # Classify the row up front. derive_effect_class / requires_operator_signature raise
    # EffectClassifierError on a dangerous self-declared effect_class divergence — catch it here so
    # a malformed spec fails closed with a clear message rather than an unhandled traceback.
    # (non_eligibility_reason does not raise; it is grouped here only so all classifier calls that
    # can fail share one guard.)
    try:
        reason = ec.non_eligibility_reason(row, kind="decision")
        derived_class = ec.derive_effect_class(row, kind="decision")
        needs_operator = ec.requires_operator_signature(row, kind="decision")
    except ec.EffectClassifierError as exc:
        print(f"Error: inconsistent effect declaration: {exc}", file=sys.stderr)
        return 1
    if reason is not None:
        print(f"Error: this decision is categorically NON-ELIGIBLE: it {reason}.", file=sys.stderr)
        return 1

    # MAJOR-4: show the full derived material effect, not just a class label, before writing.
    profile = ec.effect_profile_from_row(row)
    print("About to sign a constraint-relaxing security decision:")
    print(f"  action_reviewed : {row.get('action_reviewed')}")
    print(f"  verdict         : {row.get('verdict')}")
    print(f"  derived class   : {derived_class}")
    print(f"  needs operator  : {needs_operator}")
    print(f"  grants          : {list(profile.grants_capabilities)}")
    print(f"  write targets   : {list(profile.write_targets)}")
    print(f"  relaxes         : {list(profile.relaxes)}")
    print(f"  destructive/xrepo/bulk: {profile.destructive}/{profile.cross_repo}/{profile.bulk}")
    print(f"  derives_from    : {row.get('derives_from', '')}")
    print(f"  key_id          : {row.get('key_id', '')}")

    from agentteams.cli.grants import payload_claims_grant_purpose

    if payload_claims_grant_purpose(dl._decision_signature_values(row)):
        # PR-E domain separation: never sign a decision payload that is also a grant payload.
        print("Error: refusing to sign — this decision's payload begins with the capability-"
              "grant purpose tag (use --sign-grant for grants).", file=sys.stderr)
        return 1
    try:
        values = dl._decision_signature_values(row)
        row["signature"] = sl.ed25519_sign(private_pem, values)
        # F-2 verify-before-append: the key the GATE will load (this team's store, by key_id)
        # must verify the new signature, or the row is refused now rather than at gate time.
        public_pem = dl._load_verify_key(output_dir, row.get("key_id", ""))
        if not sl.ed25519_verify(public_pem, values, row["signature"]):
            raise RuntimeError(
                f"the signature does not verify against {dl._VERIFY_KEY_STORE_REL}/"
                f"{row.get('key_id', '')}.pub.pem in {output_dir} (a private/public key mismatch, "
                "or the public key was provisioned into another team dir). Nothing appended.")
        dl.append_signed_decision_row(output_dir, row)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"\nSigned decision appended to {output_dir / 'references' / 'security-decisions.log.csv'}")
    return 0


def load_grant_spec(path: str, flag: str) -> dict | None:
    """Read and shape-check a grant JSON spec; print the error and return None on failure.

    Args:
        path: Path to the JSON grant spec.
        flag: The CLI flag named in error messages (``--issue-grant`` / ``--sign-grant``).

    Returns:
        The spec dict, or None after printing why it was refused.
    """
    import json

    try:
        spec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Error: unable to read {flag} spec {path!r}: {exc}", file=sys.stderr)
        return None
    if not isinstance(spec, dict):
        print(f"Error: {flag} spec must be a JSON object", file=sys.stderr)
        return None
    missing = [k for k in GRANT_SPEC_REQUIRED if k not in spec]
    if missing:
        print(f"Error: grant spec missing required field(s): {', '.join(missing)}", file=sys.stderr)
        return None
    return spec


def grant_spec_kwargs(spec: dict) -> dict:
    """Map a grant spec to the minter keyword arguments (fresh id + timestamp).

    Args:
        spec: A spec accepted by :func:`load_grant_spec`.

    Returns:
        The keyword arguments for ``grants.issue_grant`` / ``grants.sign_ed25519_grant``.

    Raises:
        ValueError: ``max_uses`` is not an integer.
    """
    import secrets
    from datetime import datetime, timezone

    try:
        max_uses = int(spec["max_uses"])
    except TypeError:
        # `null` (or a list/object) used to escape as an uncaught TypeError traceback. A
        # non-integer string keeps raising ValueError from int() unchanged.
        raise ValueError(
            f"grant spec max_uses must be an integer (got {spec['max_uses']!r})") from None
    return dict(
        issuer_team=str(spec["issuer_team"]), holder_team=str(spec["holder_team"]),
        target_path=str(spec["target_path"]), permitted_ops=str(spec["permitted_ops"]),
        expires_at=str(spec["expires_at"]), max_uses=max_uses,
        approver=str(spec["approver"]), ticket_id=str(spec["ticket_id"]),
        reason_code=str(spec["reason_code"]),
        grant_id=f"grant-{secrets.token_hex(8)}",
        timestamp=datetime.now(timezone.utc).isoformat(),
        # G-6 (optional): when the spec declares the issuer's tree, it is signed into the
        # grant and an absolute target_path outside it is rejected (P2-4 containment).
        issuer_root=str(spec.get("issuer_root", "")),
    )


def report_issued(record: dict[str, str], ledger_root: Path, team_dir: Path) -> None:
    """Print the one-line summary of a freshly minted grant.

    Args:
        record: The appended grant row.
        ledger_root: The holder workspace root whose ledger received the row.
        team_dir: The holder TEAM dir the row was verified against.
    """
    from agentteams.cli import grants

    print(
        f"Issued capability grant {record['grant_id']} ({record.get('sig_scheme') or 'hmac'}): "
        f"{record['issuer_team']} → {record['holder_team']} may {record['permitted_ops']} "
        f"{record['target_path']} (expires {record['expires_at']}, max_uses {record['max_uses']})"
    )
    print(f"  appended to {ledger_root / grants.GRANT_LOG_REL}")
    print(f"  verified against team dir {team_dir}")


def sign_grant(spec_path: str, resolve_dirs: Callable[[], tuple[Path, Path]]) -> int:
    """Mint and append one operator Ed25519-signed capability grant.

    Error precedence is part of the contract: spec → ``key_id`` → env → key read → *then*
    ``resolve_dirs`` (whose ``ValueError`` names a non-rendering ``--framework``), which is why the
    directory lookup is injected as a zero-argument callable rather than resolved by the caller.
    It is CALLED before the key read (it runs unpinned adapter code, which must not run with the
    key in memory) and its error is re-raised after, so the reported precedence is unchanged.

    Args:
        spec_path: Path to the JSON grant spec (the grant fields plus ``key_id``).
        resolve_dirs: Returns ``(ledger_root, team_dir)``; may raise ``ValueError``.

    Returns:
        0 on success, 1 on any error (fail-closed).
    """
    from agentteams.cli import grants

    spec = load_grant_spec(spec_path, "--sign-grant")
    if spec is None:
        return 1
    key_id = str(spec.get("key_id") or "").strip()
    if not key_id:
        print("Error: --sign-grant spec must name a key_id (the verify key's file stem)",
              file=sys.stderr)
        return 1
    # Resolve the directories BEFORE the key read (@security K1): resolve_dirs runs unpinned
    # adapter code, which must never execute while the key is in memory. Its error is held and
    # re-raised after the env/key checks, so error precedence is unchanged.
    dirs_error: Exception | None = None
    try:
        ledger_root, team_dir = resolve_dirs()
    except (grants.GrantError, ValueError) as exc:
        dirs_error = exc
    private_pem = _read_operator_private_key()
    if private_pem is None:
        return 1
    try:
        if dirs_error is not None:
            raise dirs_error
        record = grants.sign_ed25519_grant(
            ledger_root, team_dir=team_dir, private_pem=private_pem, key_id=key_id,
            **grant_spec_kwargs(spec),
        )
    except (grants.GrantError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    report_issued(record, ledger_root, team_dir)
    return 0


__all__ = [
    "GRANT_SPEC_REQUIRED",
    "KEYFILE_ENV",
    "SIGNING_CLOSURE",
    "grant_spec_kwargs",
    "load_grant_spec",
    "presign_integrity_check",
    "report_issued",
    "sign_decision",
    "sign_decision_team_refusal",
    "sign_grant",
]
