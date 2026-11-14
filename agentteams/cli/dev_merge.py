"""dev_merge.py — the operator-signed integration-branch merge authorization and ``agentteams --merge-pr``.

A governed workspace whose branch model is ``integration`` may carry ``.agentteams/dev-merge-authorization.json``:
one file, signed once by the operator's Ed25519 decision key, that names the integration branch and the workspaces
it binds. While it verifies, ``agentteams --merge-pr N`` merges pull request N into that branch without the
constitutional gate's merge prompt, and only when every condition in :func:`pr_problem` holds (operator rulings,
2026-11-14: a signed dev-merge exception; scope the first-party repositories; no expiry; conditions required checks
green, merge commit only, ``@security`` clearance).

The constitution's "Integration-branch merge authorization" section makes the file a bounded constraint-relaxing
authorization, built like the first-party toolchain group (:mod:`agentteams.cli.toolchain_group`, whose verification
helpers it shares):

* **Operator-signed:** Ed25519 over :func:`signed_values`, verified against the governance team's
  ``references/authorized-verify-keys/<key_id>.pub.pem``. The signing verb refuses inside an agent session.
* **Traceable:** the signed ``derives_from`` must resolve to authentic authorizing rows with an acyclic lineage; a HALT
  on any of them ends the authorization (C-2, :func:`agentteams.cli.toolchain_group.lineage_problem`).
* **HALT, C-2:** an unretracted HALT on :data:`FAMILY` or on any :data:`ACTION_FAMILY` (``dev-merge:``) action in any
  surface's decisions log ends it; removing the key or the file revokes it.
* **Trust root:** a governance target (stem ``dev-merge-authorization``); no decision row may target it.
* **No sunset and no cap slot** (operator ruling): with the toolchain group, the only two such authorizations.
* **Rollback guard:** ``sequence`` is monotonic against the exception registry's :data:`HIGH_WATER_KEY`.

**Residue (Rule 12).** The per-merge ``@security`` PASS is an ordinary decision row. Where decision signing is off it is
only as strong as the log's authorship check, so an agent with a shell could write one. The binding controls are the
signed file, the green checks, ``--match-head-commit``, the sensitive-path refusal and the integration-branch-only
target; a merge commit into the integration branch can be reverted.

No repository is named here or in any template (ship the machinery, never a specific exception).

Stdlib only (``cryptography`` is the lazily imported ``signing`` extra).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from agentteams.cli import toolchain_group as tg
from agentteams.manifest_format import _REPO_ID_RE

#: Where the signed file lives, relative to the project root.
AUTH_FILE_REL = ".agentteams/dev-merge-authorization.json"
#: The signing-domain tag: the first signed value of every authorization payload.
PURPOSE = "agentteams-dev-merge-authorization-v1"
#: The only scope an authorization may carry.
SCOPE = "integration-pr-merge"
#: The HALT family that ends the authorization (C-2).
FAMILY = "integration-merge"
#: The per-merge clearance family: ``dev-merge:<owner>/<name>:pr-<N>-<head sha>``. A HALT on any of them ends it too.
ACTION_FAMILY = "dev-merge"
#: The exception-registry key holding the highest sequence seen (rollback guard).
HIGH_WATER_KEY = "dev_merge_sequence"
#: The file format version.
FORMAT_VERSION = 1

_FIELDS = ("version", "scope", "branch", "workspaces", "purpose", "sequence", "key_id", "derives_from", "signature")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
#: Branches the authorization can never name, whatever the repository's settings.
_NEVER_BRANCHES = frozenset({"main", "master", "head"})
#: Path prefixes a pull request merged without the prompt may not touch (agent, harness, workflow and control-plane
#: files). Any governance target (``governance_targets.target_is_governance_root``) is refused too.
SENSITIVE_PREFIXES: tuple[str, ...] = (
    ".github/agents/", ".github/workflows/", ".github/actions/", ".github/hooks/", ".claude/", ".agentteams/",
    ".codex/", ".goose/", ".grok/", ".vscode/", ".githooks/",
)
#: Exact paths (case-insensitive) a pull request merged without the prompt may not touch: agent and CI surfaces.
SENSITIVE_FILES: frozenset[str] = frozenset({
    ".github/codeowners", "codeowners", "docs/codeowners", "agents.md", "claude.md", ".goosehints",
    ".pre-commit-config.yaml", ".github/dependabot.yml",
})
#: Merge flags the verb never passes (the release model and C-5: branch deletion is destructive).
FORBIDDEN_MERGE_FLAGS = ("--admin", "--auto", "--squash", "--rebase", "--delete-branch")


class DevMergeError(RuntimeError):
    """The authorization file is malformed or does not verify (``env`` is True when the cause is the environment)."""

    def __init__(self, message: str, *, env: bool = False) -> None:
        super().__init__(message)
        self.env = env


@dataclass
class AuthResult:
    """The outcome of :func:`load_authorization`.

    Attributes:
        present: Whether the file exists.
        repo: The signed ``owner/name`` this workspace's pair names (lowercased); "" on any problem.
        branch: The signed integration branch; "" on any problem.
        problem: Why the authorization does not apply although the file exists, else None.
        env: True when the problem is the environment (no Ed25519 backend), not the file.
        sequence: The verified file's sequence (0 when none verified).
    """

    present: bool = False
    repo: str = ""
    branch: str = ""
    problem: str | None = None
    env: bool = False
    sequence: int = 0


def signed_values(doc: dict) -> list[str]:
    """Return the ordered values the signature covers (every field except ``signature``).

    Args:
        doc: A structurally valid document (:func:`validate`).

    Returns:
        ``[purpose, version, scope, branch, sequence, key_id, workspaces, derives_from]``, each workspace as
        ``repo:<owner/name>=team:<id>`` (lowercased repository) and the lineage lowercased.
    """
    workspaces = ",".join(f"{w['repo'].lower()}={w['team']}" for w in doc["workspaces"])
    return [str(doc["purpose"]), str(doc["version"]), str(doc["scope"]), str(doc["branch"]), str(doc["sequence"]),
            str(doc["key_id"]), workspaces, ",".join(tg._lineage(doc))]


def branch_refusal(branch: object) -> str | None:
    """Why *branch* can never be an authorized integration branch, or None.

    Args:
        branch: A branch name.

    Returns:
        A reason for ``main``/``master``/``HEAD``, a ``release/*`` line, or a name ``branch_model`` rejects; else None.
    """
    from agentteams.branch_model import _valid_branch

    if not isinstance(branch, str) or not _valid_branch(branch):
        return "branch must be a valid branch name"
    if branch.lower() in _NEVER_BRANCHES or branch.lower().startswith("release/"):
        return f"branch {branch!r} is a default-branch or release name, which no authorization may name"
    return None


def validate(doc: object, *, signed: bool = True) -> dict:
    """Check the document's shape; return it.

    Args:
        doc: The parsed JSON.
        signed: Require a ``signature`` (False while signing).

    Returns:
        The document.

    Raises:
        DevMergeError: An unknown or missing field, or a value of the wrong form.
    """
    if not isinstance(doc, dict):
        raise DevMergeError("the file is not a JSON object")
    unknown = sorted(set(doc) - set(_FIELDS))
    if unknown:
        raise DevMergeError(f"unknown field(s): {', '.join(unknown)}")
    if doc.get("version") != FORMAT_VERSION or doc.get("scope") != SCOPE or doc.get("purpose") != PURPOSE:
        raise DevMergeError(f"version must be {FORMAT_VERSION}, scope {SCOPE!r} and purpose {PURPOSE!r}")
    refusal = branch_refusal(doc.get("branch"))
    if refusal:
        raise DevMergeError(refusal)
    parents = tg._lineage(doc) if isinstance(doc.get("derives_from"), str) else []
    if not parents or not all(tg._ACTION_ID_RE.fullmatch(p) for p in parents):
        raise DevMergeError("derives_from must name one or more recorded decision action ids (';'-separated)")
    from agentteams.cli.governance_targets import target_is_governance_root

    if any(target_is_governance_root(p) for p in parents):
        raise DevMergeError("derives_from names a trust-root id; the authorization cannot derive from a decision "
                            "about a trust root")
    seq = doc.get("sequence")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
        raise DevMergeError("sequence must be a positive integer")
    if not isinstance(doc.get("key_id"), str) or not tg._KEY_ID_RE.match(doc["key_id"]):
        raise DevMergeError("key_id must be a bare key-file stem")
    ws = doc.get("workspaces")
    if not isinstance(ws, list) or not ws:
        raise DevMergeError("workspaces must be a non-empty list")
    teams: set[str] = set()
    for w in ws:
        if not (isinstance(w, dict) and set(w) == {"repo", "team"} and isinstance(w["repo"], str)
                and isinstance(w["team"], str) and w["repo"].startswith("repo:") and w["team"].startswith("team:")
                and _REPO_ID_RE.fullmatch(w["repo"][5:]) and tg._TEAM_ID_RE.fullmatch(w["team"][5:])):
            raise DevMergeError('each workspace must be {"repo": "repo:<owner/name>", "team": "team:<id>"}')
        if w["team"] in teams:
            raise DevMergeError(f"{w['team']} is listed twice (one repository per team)")
        teams.add(w["team"])
    if signed and not (isinstance(doc.get("signature"), str) and re.fullmatch(r"[0-9a-f]{128}", doc["signature"])):
        raise DevMergeError("the file is not signed (no 128-hex Ed25519 signature)")
    return doc


def read_document(project_root: Path, *, signed: bool = True) -> dict | None:
    """Read and validate ``<project_root>/.agentteams/dev-merge-authorization.json``; None when absent.

    Args:
        project_root: The project root.
        signed: Passed to :func:`validate`.

    Returns:
        The document, or None when the file does not exist.

    Raises:
        DevMergeError: Unreadable, a symlink, not JSON, or malformed.
    """
    path = project_root / AUTH_FILE_REL
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file():
        raise DevMergeError(f"{AUTH_FILE_REL} is not a regular file")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DevMergeError(f"{AUTH_FILE_REL} cannot be read: {exc}") from exc
    return validate(doc, signed=signed)


def verify_signature(doc: dict, governance_dir: Path) -> None:
    """Verify the document's signature against the governance team's verify-key store.

    Args:
        doc: A validated, signed document.
        governance_dir: The team dir holding ``references/authorized-verify-keys``.

    Raises:
        DevMergeError: No backend (``env=True``), no such key (revoked or never provisioned), or a bad signature.
    """
    from agentteams.cli import decision_log as dl
    from agentteams.cli import signed_ledger as sl

    if not tg.backend_available():
        raise DevMergeError("the Ed25519 backend (the 'signing' extra, cryptography) is not installed here, so the "
                            "signed authorization cannot be verified", env=True)
    try:
        public_pem = dl._load_verify_key(governance_dir, doc["key_id"])
        ok = sl.ed25519_verify(public_pem, signed_values(doc), doc["signature"])
    except RuntimeError as exc:
        raise DevMergeError(str(exc)) from exc
    if not ok:
        raise DevMergeError(f"the signature does not verify against key {doc['key_id']!r}")


def _halted(halt_dirs: tuple[Path, ...]) -> str | None:
    from agentteams.cli import security_gate

    for d in dict.fromkeys(halt_dirs):
        for halt_family in (FAMILY, ACTION_FAMILY):
            try:
                security_gate._assert_no_unretracted_halt(d, action=halt_family)
            except RuntimeError as exc:
                return f"a HALT on {halt_family} ends it ({exc})"
            except (OSError, UnicodeDecodeError, ValueError) as exc:  # an unreadable log fails closed, never fatally
                return f"the decisions log in {d} cannot be read ({exc})"
    return None


def _high_water(governance_dir: Path) -> int:
    from agentteams.cli.exception_registry import load_registry

    value = load_registry(governance_dir).get(HIGH_WATER_KEY, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def load_authorization(project_root: Path, governance_dir: Path, *, team_id: str, halt_dirs: tuple[Path, ...] = (),
                       consume: bool = False, origin: str | None = None) -> AuthResult:
    """Verify the authorization for this workspace.

    The repository it binds comes from the **signed** pair for *team_id*; the git origin must agree with it, but is
    never the source of the repository a merge targets.

    Args:
        project_root: The project root (where ``.agentteams/`` lives).
        governance_dir: The governed team dir (verify keys, exception registry, decisions log).
        team_id: The brief's team id.
        halt_dirs: Every team dir whose HALTs bind (*governance_dir* is always included).
        consume: True to record a newer ``sequence`` as the registry's high-water mark.
        origin: This checkout's ``owner/name`` (default: read from the git origin).

    Returns:
        An :class:`AuthResult`; ``repo`` and ``branch`` are empty whenever ``problem`` is set.
    """
    try:
        doc = read_document(project_root)
    except DevMergeError as exc:
        return AuthResult(present=True, problem=str(exc))
    if doc is None:
        return AuthResult()
    try:
        verify_signature(doc, governance_dir)
    except DevMergeError as exc:
        return AuthResult(present=True, problem=str(exc), env=exc.env)
    dirs = (governance_dir, *halt_dirs)
    stopped = _halted(dirs) or tg.lineage_problem(doc, governance_dir, dirs)
    if stopped:
        return AuthResult(present=True, problem=stopped)
    mine = [w["repo"][5:].lower() for w in doc["workspaces"] if w["team"] == f"team:{team_id}"]
    if not mine:
        return AuthResult(present=True, problem=(f"team:{team_id} is not listed in the signed file's workspaces; "
                                                 "adding it needs a new operator signature"))
    here = (origin if origin is not None else tg.workspace_repo(project_root)) or ""
    if here.lower() != mine[0]:
        return AuthResult(present=True, problem=(f"this checkout's origin ({here or 'no GitHub origin'}) is not the "
                                                 f"signed repository for team:{team_id} ({mine[0]})"))
    try:
        seen = _high_water(governance_dir)
        if doc["sequence"] < seen:
            return AuthResult(present=True, problem=(f"sequence {doc['sequence']} is older than sequence {seen} "
                                                     "already recorded (rollback refused)"))
        if consume and doc["sequence"] > seen:
            from agentteams.cli.exception_registry import load_registry, write_registry

            registry = load_registry(governance_dir)
            registry[HIGH_WATER_KEY] = doc["sequence"]
            write_registry(governance_dir, registry)
    except (OSError, ValueError, RuntimeError) as exc:
        return AuthResult(present=True, problem=f"the exception registry cannot be read or written ({exc})")
    return AuthResult(present=True, repo=mine[0], branch=doc["branch"], sequence=doc["sequence"])


def merge_action_id(repo: str, number: int, head_sha: str) -> str:
    """Return the per-merge clearance action id ``dev-merge:<owner>/<name>:pr-<N>-<head sha>``.

    Args:
        repo: ``owner/name``.
        number: The pull-request number.
        head_sha: The 40-hex head commit.

    Returns:
        The lowercased action id a ``@security`` PASS row must carry.

    Raises:
        ValueError: *repo* is not ``owner/name``, *number* is not a positive integer, or *head_sha* is not 40 hex.
    """
    if not (isinstance(repo, str) and _REPO_ID_RE.fullmatch(repo)) or isinstance(number, bool) or not (
            isinstance(number, int) and number > 0) or not (isinstance(head_sha, str) and _SHA_RE.match(head_sha.lower())):
        raise ValueError("merge_action_id needs owner/name, a positive PR number and a 40-hex head commit")
    return f"{ACTION_FAMILY}:{repo.lower()}:pr-{number}-{head_sha.lower()}"


# ---------------------------------------------------------------------------
# The merge conditions (pure; the verb feeds it what `gh` reports).
# ---------------------------------------------------------------------------

def _sensitive(path: str) -> bool:
    from agentteams.cli.governance_targets import target_is_governance_root

    p = path.strip()
    while p.startswith("./"):
        p = p[2:]
    low = p.lower()
    return (low in SENSITIVE_FILES or any(low.startswith(prefix) for prefix in SENSITIVE_PREFIXES)
            or target_is_governance_root(p))


def _check_passed(item: dict) -> bool:
    if item.get("__typename") == "StatusContext" or ("context" in item and "conclusion" not in item):
        return str(item.get("state") or "").upper() == "SUCCESS"
    return (str(item.get("status") or "").upper() == "COMPLETED"
            and str(item.get("conclusion") or "").upper() == "SUCCESS")


def _check_name(item: dict) -> str:
    return str(item.get("name") or item.get("context") or "")


def pr_problem(*, repo: str, branch: str, default_branch: str, pull: dict, view: dict, required: list[dict],
               files: list[str]) -> str | None:
    """Why pull request *pull* may not be merged without the prompt, or None when every condition holds.

    Args:
        repo: The signed ``owner/name``.
        branch: The signed integration branch.
        default_branch: The repository's live default branch.
        pull: ``gh api repos/<repo>/pulls/<N>`` (REST).
        view: ``gh pr view <N> --json reviewDecision,mergeStateStatus,statusCheckRollup``.
        required: ``gh pr checks <N> --required --json name,bucket`` (empty when the branch requires none).
        files: Every path the pull request changes (renames: both names).

    Returns:
        A one-line reason, or None.
    """
    base = pull.get("base") or {}
    head = pull.get("head") or {}
    base_repo = str((base.get("repo") or {}).get("full_name") or "").lower()
    head_repo = str((head.get("repo") or {}).get("full_name") or "").lower()
    if base_repo != repo.lower():
        return f"its base repository is {base_repo or 'unknown'}, not the signed {repo}"
    if head_repo != repo.lower():
        return "it comes from a fork or another repository"
    if str(base.get("ref") or "") != branch:
        return f"its base branch is {base.get('ref')!r}, not the signed integration branch {branch!r}"
    if branch == default_branch or branch_refusal(branch):
        return f"{branch!r} is the default branch or a release line"
    if pull.get("state") != "open" or pull.get("merged"):
        return "it is not open"
    if pull.get("draft"):
        return "it is a draft"
    if not _SHA_RE.match(str(head.get("sha") or "")):
        return "its head commit is unknown"
    if str(view.get("mergeStateStatus") or "").upper() != "CLEAN":
        return f"its merge state is {view.get('mergeStateStatus') or 'unknown'}, not CLEAN"
    if str(view.get("reviewDecision") or "").upper() == "CHANGES_REQUESTED":
        return "a reviewer requested changes"
    checks = [c for c in (view.get("statusCheckRollup") or []) if isinstance(c, dict)]
    if not checks:
        return "it has no checks; at least one passed check is required"
    failing = sorted({_check_name(c) or "?" for c in checks if not _check_passed(c)})
    if failing:
        return f"check(s) not passed: {', '.join(failing[:5])}"
    reported = {_check_name(c) for c in checks}
    for req in required:
        name = str(req.get("name") or "")
        if str(req.get("bucket") or "").lower() != "pass" or name not in reported:
            return f"required check {name or '?'} has not passed"
    touched = sorted({p for p in files if _sensitive(p)})
    if touched:
        return f"it touches governance, agent or workflow path(s): {', '.join(touched[:5])}"
    return None


# ---------------------------------------------------------------------------
# `gh` access (callers pass a runner; tests pass a fake).
# ---------------------------------------------------------------------------

#: A ``gh`` runner: argv (without ``gh``) to a completed process.
GhRunner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]
#: The REST API lists at most this many files for one pull request; beyond it a sensitive path could go unseen.
MAX_PR_FILES = 3000


def run_gh(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run ``gh`` with the operator's own credentials (no token is ever printed).

    Args:
        argv: The ``gh`` arguments.

    Returns:
        The completed process (never raises on a non-zero exit).
    """
    return subprocess.run(["gh", *argv], capture_output=True, text=True, timeout=120, check=False)


class _GhError(RuntimeError):
    pass


def _gh_json(gh: GhRunner, argv: list[str]) -> object:
    proc = gh(argv)
    if proc.returncode != 0:
        raise _GhError(f"`gh {' '.join(argv[:3])} …` failed: {(proc.stderr or proc.stdout).strip()[:300]}")
    try:
        return json.loads(proc.stdout or "null")
    except json.JSONDecodeError as exc:
        raise _GhError(f"`gh {' '.join(argv[:3])} …` returned no JSON") from exc


def _gh_object(gh: GhRunner, argv: list[str]) -> dict:
    data = _gh_json(gh, argv)
    if not isinstance(data, dict):
        raise _GhError(f"`gh {' '.join(argv[:3])} …` returned no JSON object")
    return data


def _required_checks(gh: GhRunner, repo: str, number: int) -> list[dict]:
    proc = gh(["pr", "checks", str(number), "-R", repo, "--required", "--json", "name,bucket"])
    if proc.returncode != 0 and "no required checks" in (proc.stderr or "").lower():
        return []
    if proc.returncode not in (0, 8):  # 8: some checks pending; the bucket says so
        raise _GhError(f"`gh pr checks --required` failed: {(proc.stderr or proc.stdout).strip()[:300]}")
    try:
        data = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise _GhError("`gh pr checks --required` returned no JSON") from exc
    if not isinstance(data, list) or not all(isinstance(d, dict) for d in data):
        raise _GhError("`gh pr checks --required` returned an unexpected shape")
    return data


def _files(gh: GhRunner, repo: str, number: int, changed_files: object) -> list[str]:
    """Every path the pull request changes (renames: both names); refuses an incomplete or oversized list."""
    proc = gh(["api", "--paginate", f"repos/{repo}/pulls/{number}/files",
               "--jq", ".[] | .filename, (.previous_filename // empty)"])
    if proc.returncode != 0:
        raise _GhError(f"the pull request's file list could not be read: {(proc.stderr or '').strip()[:300]}")
    lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    count_ok = isinstance(changed_files, int) and not isinstance(changed_files, bool)
    if not count_ok or changed_files > MAX_PR_FILES:
        raise _GhError(f"the pull request changes {changed_files!r} files; at most {MAX_PR_FILES} can be checked")
    names = {line.strip() for line in lines}
    if len(names) < changed_files:   # renames add a second name, never fewer than one per file
        raise _GhError(f"the file list has {len(names)} paths for {changed_files} changed files (incomplete)")
    return sorted(names)


def _gather(gh: GhRunner, repo: str, number: int) -> tuple[str, dict, dict, list[dict], list[str]]:
    default_branch = str(_gh_object(gh, ["api", f"repos/{repo}"]).get("default_branch") or "")
    pull = _gh_object(gh, ["api", f"repos/{repo}/pulls/{number}"])
    view = _gh_object(gh, ["pr", "view", str(number), "-R", repo, "--json",
                           "reviewDecision,mergeStateStatus,statusCheckRollup"])
    if not default_branch:
        raise _GhError("the repository's default branch could not be read")
    files = _files(gh, repo, number, pull.get("changed_files"))
    return default_branch, pull, view, _required_checks(gh, repo, number), files


def merge_pr(project_root: Path, governance_dir: Path, *, team_id: str, number: int, integration_branch: str | None,
             halt_dirs: tuple[Path, ...] = (), origin: str | None = None, gh: GhRunner | None = None) -> int:
    """``agentteams --merge-pr N``: merge pull request *N* into the integration branch without the gate's prompt.

    Args:
        project_root: The project root.
        governance_dir: The governed team dir.
        team_id: The brief's team id.
        number: The pull-request number.
        integration_branch: The brief's integration branch (None for a trunk team).
        halt_dirs: Every team dir whose HALTs bind.
        origin: This checkout's ``owner/name`` (default: the git origin).
        gh: The ``gh`` runner (default :func:`run_gh`).

    Returns:
        0 merged; 2 a condition does not hold (ask the operator: the ordinary prompted path); 1 an error, or an alarm
        after a merge whose result does not check out.
    """
    from agentteams.cli import security_gate

    gh = gh or run_gh

    def refuse(why: str) -> int:
        print(f"Not merged: {why}. Ask the operator (a direct `gh pr merge` goes through the gate's prompt).",
              file=sys.stderr)
        return 2

    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        print("Error: --merge-pr needs a positive pull-request number.", file=sys.stderr)
        return 1
    auth = load_authorization(project_root, governance_dir, team_id=team_id, halt_dirs=halt_dirs, consume=True,
                              origin=origin)
    if not auth.present:
        return refuse(f"there is no {AUTH_FILE_REL} here")
    if auth.problem:
        return refuse(f"the integration-branch merge authorization does not apply ({auth.problem})")
    if not integration_branch:
        return refuse("this project's branch model is not `integration`")
    if integration_branch != auth.branch:
        return refuse(f"the brief's integration branch ({integration_branch!r}) is not the signed one ({auth.branch!r})")
    repo, branch = auth.repo, auth.branch
    try:
        default_branch, pull, view, required, files = _gather(gh, repo, number)
    except (_GhError, OSError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    problem = pr_problem(repo=repo, branch=branch, default_branch=default_branch, pull=pull, view=view,
                         required=required, files=files)
    if problem:
        return refuse(f"pull request #{number} does not qualify: {problem}")
    head_sha = str(pull["head"]["sha"]).lower()
    merge_action = merge_action_id(repo, number, head_sha)
    allowed, why = security_gate.check_clearance(governance_dir, action=merge_action)
    if not allowed:
        return refuse(f"no usable @security PASS for {merge_action} ({why})")
    try:
        # Spend the PASS before `gh` runs: a failure afterwards cannot replay it (one PASS, one merge).
        security_gate._assert_destructive_action_allowed(governance_dir, action=merge_action, consume=True)
    except RuntimeError as exc:
        return refuse(f"the @security PASS for {merge_action} could not be spent ({exc})")
    try:
        # Re-read everything at merge time (head, base, checks, files): a change since the first read refuses.
        _default, again, again_view, again_required, again_files = _gather(gh, repo, number)
    except (_GhError, OSError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc} (the PASS is spent; record a new one to retry).", file=sys.stderr)
        return 1
    if str((again.get("head") or {}).get("sha") or "").lower() != head_sha:
        return refuse("the pull request's head changed while it was being checked (the PASS is spent)")
    late = pr_problem(repo=repo, branch=branch, default_branch=_default, pull=again, view=again_view,
                      required=again_required, files=again_files)
    if late:
        return refuse(f"at merge time pull request #{number} no longer qualifies: {late} (the PASS is spent)")
    argv = ["pr", "merge", str(number), "-R", repo, "--merge", "--match-head-commit", head_sha]
    if set(argv) & set(FORBIDDEN_MERGE_FLAGS):  # never reached: the argv above is fixed; kept as a tripwire
        print("Error: a forbidden merge flag reached the merge command.", file=sys.stderr)
        return 1
    proc = gh(argv)
    if proc.returncode != 0:
        print(f"Error: `gh pr merge` failed: {(proc.stderr or proc.stdout).strip()[:300]}", file=sys.stderr)
        return 1
    return _verify_merged(gh, repo, number, branch, head_sha)


def _verify_merged(gh: GhRunner, repo: str, number: int, branch: str, head_sha: str) -> int:
    try:
        after = _gh_object(gh, ["api", f"repos/{repo}/pulls/{number}"])
        merge_sha = str(after.get("merge_commit_sha") or "")
        commit = _gh_object(gh, ["api", f"repos/{repo}/commits/{merge_sha}"]) if merge_sha else {}
        tip = _gh_object(gh, ["api", f"repos/{repo}/branches/{branch}"])
    except (_GhError, OSError, subprocess.SubprocessError) as exc:
        print(f"ALARM: #{number} was merged, but the result cannot be checked ({exc}). Tell the operator.",
              file=sys.stderr)
        return 1
    parents = [str(p.get("sha") or "").lower() for p in (commit.get("parents") or []) if isinstance(p, dict)]
    ok = (after.get("merged") is True and str((after.get("base") or {}).get("ref")) == branch
          and len(parents) == 2 and parents[1] == head_sha
          and (tip.get("commit") or {}).get("sha") == merge_sha)
    if not ok:
        print(f"ALARM: #{number} merged, but not as a merge commit of the checked head at the tip of {branch!r} "
              f"(merge {merge_sha[:12] or '?'}). Tell the operator.", file=sys.stderr)
        return 1
    print(f"Merged #{number} into {branch} as {merge_sha[:12]} (merge commit, PASS spent).")
    return 0


# ---------------------------------------------------------------------------
# Operator signing (--sign-dev-merge-authorization) and the read-only verify verb.
# ---------------------------------------------------------------------------

def agent_session() -> str | None:
    """Name the agent host this shell belongs to, from ``update_lock.INVOKER_ENV_MARKERS``.

    Advisory: an environment can be forged (``env -u CLAUDECODE``); the real control on signing is the operator key
    and the signing preflight.

    Returns:
        The host (``claude-code``, ``goose``, ``codex``), or None for a terminal.
    """
    from agentteams.update_lock import INVOKER_ENV_MARKERS

    return next((who for var, who in INVOKER_ENV_MARKERS if os.environ.get(var)), None)


def sign_file(path: Path, team_dir: Path, *, confirm_sha256: str | None = None, allow_checkout: bool = False) -> int:
    """Operator-only: sign an unsigned authorization file in place with the Ed25519 decision key.

    Refuses inside an agent session, then shares ``--sign-toolchain-group``'s path: shape check, display, review
    digest and confirmation, preflight (key file, integrity, install location), sign, verify, write.

    Args:
        path: The authorization file (``signature`` absent or replaced).
        team_dir: A governed team dir whose ``references/authorized-verify-keys`` holds the key.
        confirm_sha256: ``--confirm-review-sha256`` (no terminal); None prompts on a terminal.
        allow_checkout: ``--allow-checkout-signing``.

    Returns:
        0 when signed and written, 1 otherwise (fail-closed).
    """
    from agentteams.cli import operator_signing as ops
    from agentteams.cli import signed_ledger as sl

    host = agent_session()
    if host:
        print(f"Error: refusing to sign inside an agent session ({host}); run this from your own terminal.",
              file=sys.stderr)
        return 1
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(doc, dict):
            doc.pop("signature", None)
        validate(doc, signed=False)
    except (OSError, json.JSONDecodeError, DevMergeError) as exc:
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
    print("About to sign the integration-branch merge authorization (the constitution's Integration-branch merge "
          "authorization section: agents may merge a qualifying pull request into this branch without the merge "
          "prompt, in these workspaces, until a HALT or key revocation):")
    print(f"  branch      : {doc['branch']}")
    for w in doc["workspaces"]:
        print(f"  workspace   : {w['repo']}  {w['team']}")
    print(f"  sequence    : {doc['sequence']}")
    print(f"  derives_from: {doc['derives_from']}")
    print(f"  key_id      : {doc['key_id']}")
    print(f"  values      : {json.dumps(signed_values(doc), ensure_ascii=True)}")
    if not ops._confirm(ops.review_digest("dev-merge-authorization", doc, team_dir, doc["key_id"]), confirm_sha256):
        return 1
    private_pem = ops._read_operator_private_key(keyfile)
    if private_pem is None:
        return 1
    try:
        doc["signature"] = sl.ed25519_sign(private_pem, signed_values(doc))
        verify_signature(doc, team_dir)
    except (RuntimeError, DevMergeError) as exc:
        print(f"Error: {exc}. Nothing written.", file=sys.stderr)
        return 1
    path.write_text(json.dumps({k: doc[k] for k in _FIELDS}, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"\nSigned {path}. Commit it at {AUTH_FILE_REL} in each listed workspace.")
    return 0


def verify_command(project_root: Path, governance_dir: Path, team_id: str, halt_dirs: tuple[Path, ...] = (),
                   integration_branch: str | None = None) -> int:
    """``--verify-dev-merge-authorization``: report whether ``--merge-pr`` would be authorized here (read-only).

    Args:
        project_root: The project root.
        governance_dir: The governed team dir.
        team_id: The brief's team id.
        halt_dirs: Every team dir whose HALTs bind.
        integration_branch: The brief's integration branch (None for a trunk team).

    Returns:
        0 verified for this workspace and its integration branch; 1 otherwise.
    """
    result = load_authorization(project_root, governance_dir, team_id=team_id, halt_dirs=halt_dirs, consume=False)
    if not result.present:
        print(f"No {AUTH_FILE_REL} in {project_root}: every pull-request merge goes through the gate's prompt.")
        return 1
    if result.problem:
        print(f"::error title=Integration-branch merge authorization refused::{result.problem}", file=sys.stderr)
        return 1
    if integration_branch != result.branch:
        print(f"Error: the brief's integration branch ({integration_branch or 'none: trunk model'}) is not the signed "
              f"branch ({result.branch}), so --merge-pr refuses here.", file=sys.stderr)
        return 1
    print(f"Integration-branch merge authorization verified (sequence {result.sequence}): {result.repo}, "
          f"branch {result.branch}.")
    return 0


__all__ = [
    "ACTION_FAMILY", "AUTH_FILE_REL", "AuthResult", "DevMergeError", "FAMILY", "FORBIDDEN_MERGE_FLAGS",
    "FORMAT_VERSION", "GhRunner", "HIGH_WATER_KEY", "MAX_PR_FILES", "PURPOSE", "SCOPE", "SENSITIVE_FILES",
    "SENSITIVE_PREFIXES", "agent_session", "branch_refusal",
    "load_authorization", "merge_action_id", "merge_pr", "pr_problem", "read_document", "run_gh", "sign_file",
    "signed_values", "validate", "verify_command", "verify_signature",
]
