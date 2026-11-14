"""toolchain_group.py — Rule 11(f): the operator-signed first-party toolchain group (Rule S-10's cooldown scope).

A governed workspace may carry ``.agentteams/toolchain-group.json``: one file, signed once by the operator's Ed25519
decision key, that names the repositories whose releases the operator cuts and signs (the *first-party toolchain
group*) and the workspaces the file binds. Releases of a group member are exempt from Rule S-10's 14-day cooldown,
and a pin move to one needs no per-release operator decision row (operator rulings R5 and T2, 2026-11-13). Nothing
else is lifted: the vulnerability search, Rule S-9's signed-tag check (:func:`verify_pins`, fail-closed) and the
latest-stable rule all still apply.

The constitution's "First-party toolchain group" section makes the file a bounded constraint-relaxing authorization:
operator-signed, a trust root, ended by a HALT, and exempt only from the aggregate cap and sunset (operator ruling,
2026-11-13: no sunset). Its signed ``derives_from`` (format version 2, v1.1.3, operator ruling) names the recorded decision(s) it derives
from: each must be an authentic authorizing row in the governance team's log with a recorded, acyclic lineage, and an
unretracted HALT on it or any ancestor ends the group (:func:`lineage_problem`). So:

* **Operator-signed:** the signature is Ed25519 over :func:`signed_values`, verified against the governance
  team's ``references/authorized-verify-keys/<key_id>.pub.pem``. A missing ``cryptography`` backend is reported as an
  environment error (``ENV``), distinct from a signature that does not verify.
* **HALT, C-2:** an unretracted HALT on the :data:`FAMILY` action, or on any ``s10-cooldown-exempt`` action, in any
  surface's decisions log (symlinked surfaces included) ends the group. Removing the key's ``.pub.pem`` from the store revokes it (the file then cannot verify).
* **Trust root:** the file is a governance target (``cli/governance_targets``); no decision row may target it.
* **Binding:** the file binds only the ``(repo:<git origin>, team:<team_id>)`` pairs it lists, so a copied file does
  not apply in an unlisted workspace. The binding is a guard against accidents, not a cryptographic one: the origin and
  the team id are local data, so a workspace that re-points both to a listed pair gets that pair's group (Rule 12). An unlisted workspace gets an empty group and an ``::error::`` line;
  its render still goes ahead (fail-closed for the relaxation, never for the team).
* **Domain separation and rollback:** the payload starts with :data:`PURPOSE` (never a date, as a decision row's does,
  nor the grant tag), and ``sequence`` is monotonic: a file older than the highest sequence recorded in the
  governance team's exception registry is refused. That mark is per workspace and unsigned (deleting the
  git-tracked registry resets it to 0, which shows in review), so it only stops a rollback in a workspace that already
  saw the newer file; ending the group reliably takes a HALT on ``s10-toolchain-group``
  or on any ``s10-cooldown-exempt`` action (the gate checks both; the matcher over-matches, so either drops every
  member) or removing the key; dropping one member takes a new signed file.

No repository is named here or in any template (Rule 11: ship the machinery, never a specific exception).

Stdlib only (``cryptography`` is the lazily imported ``signing`` extra).
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from agentteams.manifest_format import _REPO_ID_RE

#: Where the signed file lives, relative to the project root.
GROUP_FILE_REL = ".agentteams/toolchain-group.json"
#: The signing-domain tag: the first signed value of every group payload.
PURPOSE = "agentteams-toolchain-group-v1"
#: The only scope a version-1 file may carry.
SCOPE = "s10-cooldown"
#: The HALT family that ends the group (C-2), in any surface's decisions log.
FAMILY = "s10-toolchain-group"
#: The exception-registry key holding the highest sequence seen (rollback guard).
HIGH_WATER_KEY = "toolchain_group_sequence"

_TEAM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_ORIGIN_RE = re.compile(r"^(?:https://(?:[^@/\s]+@)?github\.com/|ssh://git@github\.com/|git@github\.com:)"
                        r"([A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*?)(?:\.git)?/?$")
_FIELDS = ("version", "scope", "group", "workspaces", "purpose", "sequence", "key_id", "derives_from", "signature")
#: The file format: version 2 adds the signed ``derives_from`` lineage (v1.1.3). No version-1 file was ever signed.
FORMAT_VERSION = 2
_ACTION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9:/._-]{0,199}$", re.ASCII)


class ToolchainGroupError(RuntimeError):
    """The group file is malformed or does not verify (``env`` is True when the cause is the environment)."""

    def __init__(self, message: str, *, env: bool = False) -> None:
        super().__init__(message)
        self.env = env


@dataclass
class GroupResult:
    """The outcome of :func:`load_group`.

    Attributes:
        present: Whether the file exists.
        repos: The verified group (``owner/name``, this workspace's own repository excluded); empty on any problem.
        problem: Why the group is empty although the file exists, else None.
        env: True when the problem is the environment (no Ed25519 backend), not the file.
        sequence: The verified file's sequence (0 when none verified).
    """

    present: bool = False
    repos: list[str] = field(default_factory=list)
    problem: str | None = None
    env: bool = False
    sequence: int = 0


def signed_values(doc: dict) -> list[str]:
    """Return the ordered values the signature covers (every field except ``signature``).

    Args:
        doc: A structurally valid group document (:func:`validate`).

    Returns:
        ``[purpose, version, scope, sequence, key_id, group, workspaces]``, lists joined with ``,`` and each
        workspace as ``repo:<owner/name>=team:<id>``.
    """
    group = ",".join(str(r).lower() for r in doc["group"])
    workspaces = ",".join(f"{w['repo'].lower()}={w['team']}" for w in doc["workspaces"])
    lineage = ",".join(_lineage(doc))
    return [str(doc["purpose"]), str(doc["version"]), str(doc["scope"]), str(doc["sequence"]),
            str(doc["key_id"]), group, workspaces, lineage]


def _lineage(doc: dict) -> list[str]:
    """The ``derives_from`` parent ids, normalized (lowercased, ``;``-separated in the file)."""
    return [p.strip().lower() for p in str(doc.get("derives_from") or "").split(";") if p.strip()]


def validate(doc: object, *, signed: bool = True) -> dict:
    """Check the document's shape; return it (raises :class:`ToolchainGroupError`).

    Args:
        doc: The parsed JSON.
        signed: Require a ``signature`` (False while signing).

    Returns:
        The document.

    Raises:
        ToolchainGroupError: An unknown or missing field, or a value of the wrong form.
    """
    if not isinstance(doc, dict):
        raise ToolchainGroupError("the file is not a JSON object")
    unknown = sorted(set(doc) - set(_FIELDS))
    if unknown:
        raise ToolchainGroupError(f"unknown field(s): {', '.join(unknown)}")
    if doc.get("version") != FORMAT_VERSION or doc.get("scope") != SCOPE or doc.get("purpose") != PURPOSE:
        raise ToolchainGroupError(f"version must be {FORMAT_VERSION}, scope {SCOPE!r} and purpose {PURPOSE!r}")
    parents = _lineage(doc) if isinstance(doc.get("derives_from"), str) else []
    if not parents or not all(_ACTION_ID_RE.fullmatch(p) for p in parents):
        raise ToolchainGroupError("derives_from must name one or more recorded decision action ids (';'-separated)")
    from agentteams.cli.governance_targets import target_is_governance_root

    if any(target_is_governance_root(p) for p in parents):
        raise ToolchainGroupError("derives_from names a trust-root id (for example one containing 'toolchain-group'); "
                                  "a group file cannot derive from a decision about a trust root")
    seq = doc.get("sequence")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
        raise ToolchainGroupError("sequence must be a positive integer")
    if not isinstance(doc.get("key_id"), str) or not _KEY_ID_RE.match(doc["key_id"]):
        raise ToolchainGroupError("key_id must be a bare key-file stem")
    group = doc.get("group")
    if not isinstance(group, list) or not group or not all(isinstance(r, str) and _REPO_ID_RE.fullmatch(r)
                                                           for r in group):
        raise ToolchainGroupError("group must be a non-empty list of owner/name repository ids")
    if len({r.lower() for r in group}) != len(group):
        raise ToolchainGroupError("group lists a repository twice")
    ws = doc.get("workspaces")
    if not isinstance(ws, list) or not ws:
        raise ToolchainGroupError("workspaces must be a non-empty list")
    seen: set[tuple[str, str]] = set()
    for w in ws:
        if not (isinstance(w, dict) and set(w) == {"repo", "team"} and isinstance(w["repo"], str)
                and isinstance(w["team"], str) and w["repo"].startswith("repo:") and w["team"].startswith("team:")
                and _REPO_ID_RE.fullmatch(w["repo"][5:]) and _TEAM_ID_RE.fullmatch(w["team"][5:])):
            raise ToolchainGroupError('each workspace must be {"repo": "repo:<owner/name>", "team": "team:<id>"}')
        pair = (w["repo"].lower(), w["team"])
        if pair in seen:
            raise ToolchainGroupError(f"workspace {w['repo']} {w['team']} is listed twice")
        seen.add(pair)
    if signed and not (isinstance(doc.get("signature"), str) and re.fullmatch(r"[0-9a-f]{128}", doc["signature"])):
        raise ToolchainGroupError("the file is not signed (no 128-hex Ed25519 signature)")
    return doc


def read_document(project_root: Path, *, signed: bool = True) -> dict | None:
    """Read and validate ``<project_root>/.agentteams/toolchain-group.json``; None when absent.

    Args:
        project_root: The project root.
        signed: Passed to :func:`validate`.

    Returns:
        The document, or None when the file does not exist.

    Raises:
        ToolchainGroupError: Unreadable, a symlink, not JSON, or malformed.
    """
    path = project_root / GROUP_FILE_REL
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file():
        raise ToolchainGroupError(f"{GROUP_FILE_REL} is not a regular file")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ToolchainGroupError(f"{GROUP_FILE_REL} cannot be read: {exc}") from exc
    return validate(doc, signed=signed)


def backend_available() -> bool:
    """Whether the Ed25519 backend (the ``signing`` extra, ``cryptography``) can be imported."""
    return importlib.util.find_spec("cryptography") is not None


def verify_signature(doc: dict, governance_dir: Path) -> None:
    """Verify the document's signature against the governance team's verify-key store (raises on failure).

    Args:
        doc: A validated, signed document.
        governance_dir: The team dir holding ``references/authorized-verify-keys``.

    Raises:
        ToolchainGroupError: No backend (``env=True``), no such key (revoked or never provisioned), or a signature
            that does not verify.
    """
    from agentteams.cli import decision_log as dl
    from agentteams.cli import signed_ledger as sl

    if not backend_available():
        raise ToolchainGroupError(
            "the Ed25519 backend (the 'signing' extra, cryptography) is not installed here, so the signed group "
            "cannot be verified; install the extra in this environment (CI included)", env=True)
    try:
        public_pem = dl._load_verify_key(governance_dir, doc["key_id"])
        ok = sl.ed25519_verify(public_pem, signed_values(doc), doc["signature"])
    except RuntimeError as exc:
        raise ToolchainGroupError(str(exc)) from exc
    if not ok:
        raise ToolchainGroupError(f"the signature does not verify against key {doc['key_id']!r}")


def workspace_repo(project_root: Path) -> str | None:
    """This workspace's ``owner/name`` from its git ``origin`` (GitHub URLs only), else None."""
    try:
        proc = subprocess.run(["git", "-C", str(project_root), "config", "--get", "remote.origin.url"],
                              capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    m = _ORIGIN_RE.match(proc.stdout.strip()) if proc.returncode == 0 else None
    return m.group(1) if m and _REPO_ID_RE.fullmatch(m.group(1)) else None


def _halted(halt_dirs: tuple[Path, ...]) -> str | None:
    from agentteams.cli import security_gate

    for d in halt_dirs:
        try:
            security_gate._assert_no_unretracted_halt(d, action="s10-toolchain-group")  # == FAMILY
        except RuntimeError as exc:
            return f"a HALT on {FAMILY} ends it ({exc})"
        except (OSError, UnicodeDecodeError, ValueError) as exc:  # an unreadable log fails closed, never fatally
            return f"the decisions log in {d} cannot be read ({exc})"
    return None


def lineage_problem(doc: dict, governance_dir: Path, halt_dirs: tuple[Path, ...]) -> str | None:
    """Why the file's ``derives_from`` lineage does not hold, or None (C-2 along the chain).

    Each parent must be recorded in *governance_dir*'s decisions log by an authorizing row (``PASS``, or ``CONDITIONAL
    PASS`` with verified conditions) that is authentic now (approved author, signature, lineage); the whole ancestor
    chain must be recorded and acyclic; and no unretracted HALT on a parent or any ancestor may stand in any of
    *halt_dirs*' logs.

    Args:
        doc: A verified group document.
        governance_dir: The governed team dir holding the authoritative decisions log.
        halt_dirs: Every team dir whose HALTs bind.

    Returns:
        A one-line reason, or None.
    """
    from agentteams.cli import decision_log as dl
    from agentteams.cli.decision_lineage import _build_lineage_map, _row_action_id, _transitive_ancestors

    try:
        read = dl._read_decision_rows(governance_dir)
        if read is None:
            return "derives_from cannot resolve: the governance team has no decisions log"
        rows, fieldnames = read
        dl._assert_decision_chain_intact(rows, fieldnames)
        lineage_map = _build_lineage_map(rows)
        chain: list[str] = []
        for lineage_action in _lineage(doc):
            authorizing = [r for r in rows if _row_action_id(r) == lineage_action and (
                (r.get("verdict") or "").strip().upper() == "PASS" or (
                    (r.get("verdict") or "").strip().upper() == "CONDITIONAL PASS"
                    and (r.get("conditions_verified") or "").strip().lower() == "verified"))]
            if not authorizing:
                return f"derives_from names {lineage_action!r}, which no authorizing decision records"
            if (authorizing[-1].get("sig_scheme") or "").strip().lower() != "ed25519":
                return f"derives_from names {lineage_action!r}, whose authorizing row is not Ed25519-signed by the operator"
            dl._assert_authorizing_row_is_authentic(authorizing[-1], output_dir=governance_dir,
                                                    signing_active=dl._decision_signing_active(fieldnames),
                                                    action=lineage_action)
            chain += [lineage_action, *sorted(_transitive_ancestors(lineage_action, lineage_map))]
        # C-2 along the whole chain, by exact id in every dir: a surface's own log rarely holds the parent row, so
        # its ancestor walk would find nothing; the governance log's chain is checked id by id instead.
        for lineage_action in dict.fromkeys(chain):
            for d in dict.fromkeys(halt_dirs):
                dl._assert_no_unretracted_halt(d, action=lineage_action)
    except RuntimeError as exc:
        return f"its derives_from lineage does not hold ({exc})"
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return f"its derives_from lineage cannot be checked ({exc})"
    return None


def _high_water(governance_dir: Path) -> int:
    from agentteams.cli.exception_registry import load_registry

    value = load_registry(governance_dir).get(HIGH_WATER_KEY, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _record_high_water(governance_dir: Path, sequence: int) -> None:
    from agentteams.cli.exception_registry import load_registry, write_registry

    registry = load_registry(governance_dir)
    registry[HIGH_WATER_KEY] = sequence
    write_registry(governance_dir, registry)


def load_group(project_root: Path, governance_dir: Path, *, team_id: str, halt_dirs: tuple[Path, ...] = (),
               consume: bool = False, repo: str | None = None) -> GroupResult:
    """Verify the group file for this workspace and return the group it authorizes.

    Args:
        project_root: The project root (where ``.agentteams/`` lives and whose git origin identifies it).
        governance_dir: The governed team dir (verify keys, exception registry, decisions log).
        team_id: The brief's team id (``manifest["team_id"]``).
        halt_dirs: Every team dir whose HALTs bind (*governance_dir* is always included).
        consume: True to record a newer ``sequence`` as the registry's high-water mark (never on a dry run).
        repo: This workspace's ``owner/name`` (default: read from the git origin).

    Returns:
        A :class:`GroupResult`; ``repos`` is empty whenever ``problem`` is set.
    """
    try:
        doc = read_document(project_root)
    except ToolchainGroupError as exc:
        return GroupResult(present=True, problem=str(exc))
    if doc is None:
        return GroupResult()
    try:
        verify_signature(doc, governance_dir)
    except ToolchainGroupError as exc:
        return GroupResult(present=True, problem=str(exc), env=exc.env)
    halted = _halted((governance_dir, *halt_dirs))
    if halted:
        return GroupResult(present=True, problem=halted)
    untraced = lineage_problem(doc, governance_dir, (governance_dir, *halt_dirs))
    if untraced:
        return GroupResult(present=True, problem=untraced)
    own = repo if repo is not None else workspace_repo(project_root)
    pair = (f"repo:{own}".lower() if own else "", f"team:{team_id}")
    if not own or pair not in {(w["repo"].lower(), w["team"]) for w in doc["workspaces"]}:
        return GroupResult(present=True, problem=(
            f"this workspace (repo:{own or '<no GitHub origin>'}, team:{team_id}) is not listed in the signed "
            "file's workspaces; adding it needs a new operator signature"))
    try:
        seen = _high_water(governance_dir)
        if doc["sequence"] < seen:
            return GroupResult(present=True, problem=(
                f"sequence {doc['sequence']} is older than sequence {seen} already recorded (rollback refused)"))
        if consume and doc["sequence"] > seen:
            _record_high_water(governance_dir, doc["sequence"])
    except (OSError, ValueError, RuntimeError) as exc:
        return GroupResult(present=True, problem=f"the exception registry cannot be read or written ({exc})")
    repos = [r.lower() for r in doc["group"] if r.lower() != own.lower()]   # the signed (lowercased) form
    return GroupResult(present=True, repos=repos, sequence=doc["sequence"])


def report(result: GroupResult) -> None:
    """Print a refusal: a notice, and an ``::error::`` line a CI log surfaces (agentteams#476)."""
    if result.problem is None:
        return
    kind = "environment" if result.env else "refused"
    print(f"  ✗  S-10 toolchain group ({GROUP_FILE_REL}) NOT applied: {result.problem}. The cooldown applies to "
          "every release until it verifies.", file=sys.stderr)
    print(f"::error title=S-10 toolchain group {kind}::{GROUP_FILE_REL} not applied: {result.problem}",
          file=sys.stderr)


# ---------------------------------------------------------------------------
# Operator signing (--sign-toolchain-group) and read-only verification verbs.
# ---------------------------------------------------------------------------

def sign_group_file(path: Path, team_dir: Path, *, confirm_sha256: str | None = None,
                    allow_checkout: bool = False) -> int:
    """Operator-only: sign an unsigned group file in place with the Ed25519 decision key.

    Shares ``--sign-decision``'s path: shape check, display, review digest and confirmation, preflight (key file,
    integrity, install location), sign, then verify against *team_dir*'s verify-key store before writing.

    Args:
        path: The group file (``signature`` absent or replaced).
        team_dir: A governed team dir whose ``references/authorized-verify-keys`` holds the key.
        confirm_sha256: ``--confirm-review-sha256`` (no terminal); None prompts on a terminal.
        allow_checkout: ``--allow-checkout-signing``.

    Returns:
        0 when signed and written, 1 otherwise (fail-closed).
    """
    from agentteams.cli import operator_signing as ops
    from agentteams.cli import signed_ledger as sl

    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(doc, dict):
            doc.pop("signature", None)
        validate(doc, signed=False)
    except (OSError, json.JSONDecodeError, ToolchainGroupError) as exc:
        print(f"Error: cannot sign {path}: {exc}", file=sys.stderr)
        return 1
    spoof = ops._spoofing_problem(doc)
    if spoof:
        print(f"Error: refusing to sign: {spoof}. The key was not read.", file=sys.stderr)
        return 1
    preflight = ops._signing_preflight(ops._project_roots(team_dir), allow_checkout, doc["key_id"])
    if preflight is None:
        return 1
    keyfile, _overridden = preflight
    print("About to sign the first-party toolchain group (Rule 11(f): no S-10 cooldown and no per-release pin "
          "row for releases of these repositories, in these workspaces, until a HALT or key revocation):")
    print(f"  group      : {', '.join(doc['group'])}")
    print(f"  workspaces : {len(doc['workspaces'])}")
    for w in doc["workspaces"]:
        print(f"               {w['repo']}  {w['team']}")
    print(f"  sequence   : {doc['sequence']}")
    print(f"  derives_from: {doc['derives_from']}")
    print(f"  key_id     : {doc['key_id']}")
    print(f"  values     : {json.dumps(signed_values(doc), ensure_ascii=True)}")
    if not ops._confirm(ops.review_digest("toolchain-group", doc, team_dir, doc["key_id"]), confirm_sha256):
        return 1
    private_pem = ops._read_operator_private_key(keyfile)
    if private_pem is None:
        return 1
    try:
        doc["signature"] = sl.ed25519_sign(private_pem, signed_values(doc))
        verify_signature(doc, team_dir)
    except (RuntimeError, ToolchainGroupError) as exc:
        print(f"Error: {exc}. Nothing written.", file=sys.stderr)
        return 1
    ordered = {k: doc[k] for k in _FIELDS}
    path.write_text(json.dumps(ordered, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"\nSigned {path}. Commit the same bytes in every listed workspace.")
    return 0


def verify_group_command(project_root: Path, governance_dir: Path, team_id: str,
                         halt_dirs: tuple[Path, ...] = (), *, member_filter=None) -> int:
    """``--verify-toolchain-group``: print the verified group for this workspace (exit 0) or why not (exit 1).

    *member_filter* (the gate's per-member HALT filter) is applied to a verified group, so the verb reports exactly
    what a render would.
    """
    result = load_group(project_root, governance_dir, team_id=team_id, halt_dirs=halt_dirs, consume=False)
    if member_filter is not None and result.present and not result.problem:
        result.repos = member_filter(result.repos)
    if not result.present:
        print(f"No {GROUP_FILE_REL} in {project_root}: no toolchain group (the cooldown applies to every release).")
        return 1
    if result.problem:
        report(result)
        return 1
    print(f"Toolchain group verified (sequence {result.sequence}): {', '.join(result.repos) or '(only this repo)'}")
    return 0


#: Lock lines: ``<name>=<owner/repo>@<40-hex>`` and ``<name>.tag=<tag>``.
_LOCK_PIN_RE = re.compile(r"^([A-Za-z0-9._-]+)=([A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*)@([0-9a-f]{40})$")
_LOCK_TAG_RE = re.compile(r"^([A-Za-z0-9._-]+)\.tag=(v[0-9][0-9A-Za-z.+-]*)$", re.ASCII)


def _git(*args: str, cwd: Path | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    env_free = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]
    try:
        return _run([*env_free, *args], cwd, timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(args, 1, "", f"git failed: {exc}")


def _run(argv: list[str], cwd: Path | None, timeout: int) -> subprocess.CompletedProcess[str]:
    # A private, empty HOME and GNUPGHOME per call: no shared keyring, ssh config or git config can vouch for a tag.
    with tempfile.TemporaryDirectory() as home:
        return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False,
                              env={"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                                   "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
                                   "HOME": home, "GNUPGHOME": home})


def _ssh_signed(body: str) -> bool:
    """True iff the tag object ends in exactly one SSH signature block and carries no PGP block."""
    lines = body.rstrip("\n").splitlines()
    if not lines or lines[-1] != "-----END SSH SIGNATURE-----" or "-----BEGIN PGP SIGNATURE-----" in lines:
        return False
    begins = [i for i, line in enumerate(lines) if line == "-----BEGIN SSH SIGNATURE-----"]
    return len(begins) == 1 and lines.count("-----END SSH SIGNATURE-----") == 1 and begins[0] < len(lines) - 1


def verify_tag_pin(remote: str, tag: str, sha: str, allowed_signers: Path) -> str | None:
    """Return None when *tag* at *remote* is release-key signed and peels to *sha*, else the problem.

    Args:
        remote: A git URL (or local path) for the repository.
        tag: The tag name.
        sha: The locked 40-hex commit.
        allowed_signers: An ssh ``allowed_signers`` file naming the release key.

    Returns:
        None, or a one-line reason.
    """
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "r"
        if _git("init", "-q", "--bare", str(repo)).returncode != 0:
            return "git init failed"
        fetched = _git("-C", str(repo), "fetch", "-q", "--no-tags", remote, f"refs/tags/{tag}:refs/tags/{tag}")
        if fetched.returncode != 0:
            return f"tag {tag} cannot be fetched from {remote}: {fetched.stderr.strip()[:200]}"
        if _git("-C", str(repo), "cat-file", "-t", f"refs/tags/{tag}").stdout.strip() != "tag":
            return f"{tag} is a lightweight tag (not signed)"
        body = _git("-C", str(repo), "cat-file", "tag", f"refs/tags/{tag}").stdout
        if f"tag {tag}" not in body.splitlines()[:6]:
            return f"the signed tag object fetched as {tag} names a different tag (a re-pushed tag is refused)"
        if not _ssh_signed(body):
            return f"{tag} is not SSH-signed (only the release key's SSH signature is accepted)"
        check = _git("-C", str(repo), "-c", "gpg.format=ssh", "-c", f"gpg.ssh.allowedSignersFile={allowed_signers}",
                     "verify-tag", f"refs/tags/{tag}")
        if check.returncode != 0:
            return f"{tag} does not verify against {allowed_signers}: {check.stderr.strip()[:200]}"
        peeled = _git("-C", str(repo), "rev-parse", f"refs/tags/{tag}^{{commit}}").stdout.strip()
        if peeled != sha:
            return f"{tag} peels to {peeled or '?'}, not the locked {sha}"
    return None


def verify_pins(lockfile: Path, group: list[str], allowed_signers: Path,
                remote_base: str = "https://github.com/", *, checked: list[str] | None = None) -> list[str]:
    """Check every lock pin to a group member: a tag line, a release-key signature, and the locked commit.

    Args:
        lockfile: ``name=owner/repo@sha`` / ``name.tag=tag`` lines (others ignored).
        group: The verified group (``owner/name``).
        allowed_signers: The release key's ``allowed_signers`` file.
        remote_base: URL prefix for ``owner/repo`` (tests pass a local directory).
        checked: When given, the name of every pin to a group member is appended (verified or not).

    Returns:
        Problems, one per failing pin (empty: all pins to group members verify).
    """
    members = {r.lower() for r in group}
    pins: dict[str, tuple[str, str]] = {}
    tags: dict[str, str] = {}
    for line in lockfile.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if (m := _LOCK_PIN_RE.match(line)):
            pins[m.group(1)] = (m.group(2), m.group(3))
        elif (m := _LOCK_TAG_RE.match(line)):
            tags[m.group(1)] = m.group(2)
    problems: list[str] = []
    for name, (repo, sha) in pins.items():
        if repo.lower() not in members:
            continue
        if checked is not None:
            checked.append(name)
        tag = tags.get(name)
        if not tag:
            problems.append(f"{name}: pins group member {repo} with no {name}.tag line (a signed tag is required)")
            continue
        if not re.fullmatch(r"v\d+\.\d+\.\d+", tag, re.ASCII):
            problems.append(f"{name}: {tag} is not a final release tag (the latest-stable rule: a pre-release needs a "
                            "recorded @security clearance, which this check does not read)")
            continue
        reason = verify_tag_pin(remote_base.rstrip("/") + "/" + repo, tag, sha, allowed_signers)
        if reason:
            problems.append(f"{name}: {reason}")
    return problems


__all__ = ["FAMILY", "GROUP_FILE_REL", "GroupResult", "HIGH_WATER_KEY", "PURPOSE", "SCOPE", "ToolchainGroupError",
           "backend_available", "load_group", "read_document", "report", "sign_group_file", "signed_values",
           "validate", "verify_group_command", "verify_pins", "verify_signature", "verify_tag_pin",
           "workspace_repo"]
