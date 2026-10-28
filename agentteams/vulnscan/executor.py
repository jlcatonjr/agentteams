"""The out-of-sandbox executor: performs remediation actions that a standing grant covers.

Agents never hold credentials. They write *requests* into the spool; this executor runs as
the operator, outside the sandbox, with the operator's git, gh and ssh credentials. For each
request it does the following, under one exclusive ledger lock:

1. Validates the request: strict schema, ``O_NOFOLLOW``, size cap, single-use id.
2. Builds every argument itself. Requested strings are never passed through as flags.
3. Finds a verified grant that covers the op and target and still has uses.
4. Checks the governance team's decision log for an unretracted HALT (C-2).
5. Writes a ``begun`` row, the recorded clearance and use reservation (C-5).
6. Executes the **deterministic** operation and records ``done`` or ``failed``.

Operations (v1):

- ``stage-fix REPO``: in an executor-owned worktree off ``origin/<default>``, applies
  :func:`agentteams.vulnscan.fix.fix_path` (non-major, OSV-clean). It then validates the
  diff: only requirements/lockfiles (regular files, modes unchanged, none deleted), only upward
  ``==`` version edits, no root-package change, and only allowlisted registries in lockfile URLs.
  A repository whose own git config defines a filter driver, or that git refuses to open, is refused
  before any checkout (:func:`agentteams.vulnscan.fix.git_config_problem`). Finally it commits locally. The agent cannot
  influence the content.
- ``open-pr REPO``: pushes the staged branch as a *new* ``vulnscan/*`` ref (never force; an existing
  ref is reused only for its open PR or when it is exactly the staged commit) and opens a PR, then
  removes the executor's worktree. It never merges.
- ``close-superseded REPO``: closes the repository's older executor PRs once its newest one is open,
  only those no person has reviewed, commented on or pushed to; never merges, deletes or pushes
  (:mod:`.executor_supersede`).
- The repo ops are refused, before any git runs, on a path outside every grant's ``repo_roots``; then on any
  worktree or clone of the agentteams checkout, ``agent.team_dir``, a grant's ``governance_dir`` or a publish ops
  repo (by git common dir and by origin; P7), and on a repository the registry enrols at a level other than
  ``remediate`` or whose brief says so (``delivery: plan`` refuses ``open-pr``; ``base_branch`` is the PR base)
  (:mod:`.executor_guard`). The PR body passes the publish scrubber (home paths, host name, private IPs).
- ``server-audit HOST`` / ``server-dryrun HOST``: run the *packaged* server kit's read-only audit,
  or ``run-host.sh`` without ``--apply``, on an exact host named in the grant and in ``hosts.conf``.

git always runs with system/global config ignored, hooks and commit/tag signing disabled, a fixed ssh
command and an explicit credential helper, so repository or user config cannot redirect what runs. Granted ops
run git, npm and the server kit in the hardened tools container (:mod:`.subproc_confine`), failing closed.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import stat
import subprocess
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from agentteams.vulnscan import engine, escalations, executor_guard, fix, grant, handoff, ledger, notify, paths
from agentteams.vulnscan import executor_supersede, report_store, repo_alias, retry_guard, subproc_confine
from agentteams.vulnscan import subproc_confine_ssh
from agentteams.vulnscan.manifests import REQ_NAME, check_lock_edit, check_pin_edit
from agentteams.vulnscan.ops import REPO_OPS   # repo-targeted ops: executor_guard applies

ACTION_ID = "vulnscan-remediation"           # decision-log action id a HALT would name
RELEASE_COOLDOWN_DAYS = 0                    # Rule S-10's cooldown is deprecated (2026-10-26); cooldown_days is ignored
ALLOWED_CHANGED = re.compile(r"(^|/)(requirements[\w.-]*\.txt|package-lock\.json|npm-shrinkwrap\.json)$", re.I)
REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")
#: origin URL forms gh can target: https://HOST/OWNER/REPO, git@HOST:OWNER/REPO, ssh://git@HOST/OWNER/REPO
_ORIGIN_RE = re.compile(r"^(?:https://([A-Za-z0-9.-]+)/|git@([A-Za-z0-9.-]+):|ssh://git@([A-Za-z0-9.-]+)(?::\d+)?/)"
                        r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
REQUEST_KEYS = {"id", "op", "target", "rationale", "created", "payload"}
MAX_REQUEST_BYTES = 64 * 1024
MAX_REPORT_REQUEST_BYTES = 256 * 1024        # a `report` request carries the agent's report (<= 200 KB markdown)
MAX_REQUESTS_PER_PASS = 200                  # more in the spool at once opens an escalation-flood (S-VM review)
INTERNAL_OPS = ("report", "escalate")        # grant-free: they only record (R6)
AGENT_ESCALATION_KINDS = escalations.AGENT_KINDS     # never "escalation-flood"
GIT_IDENTITY = ("agentteams-vulnscan", "vulnscan@agentteams.invalid")


class ExecError(RuntimeError):
    """An action was refused or failed; the message goes to the ledger and the review."""


# ---------------------------------------------------------------- locations

def spool_dir() -> Path:
    """Return the request spool (the only path agents may write).

    Returns:
        ``<state>/spool``.
    """
    return paths.state_dir() / "spool"


def kit_dir() -> Path:
    """Return the *packaged* server kit (never a user-writable copy).

    Returns:
        Path to ``agentteams/data/server_kit``.
    """
    return Path(str(resources.files("agentteams") / "data" / "server_kit"))


# ---------------------------------------------------------------- hardened git

_GIT_HARDENING = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.sshCommand=ssh",
                  "-c", "submodule.recurse=false", "-c", "fetch.recurseSubmodules=false", "-c", "credential.helper=",
                  "-c", "commit.gpgsign=false", "-c", "tag.gpgSign=false",   # never a repo-configured gpg.program
                  "-c", "core.askPass=",   # never a repo-configured askpass program (it would see the push token)
                  "-c", "core.pager=cat",   # never a repo-configured pager (output is captured; defence in depth)
                  # Transports: https, ssh and file only (no ext::, no remote helpers).
                  "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
                  "-c", "protocol.ssh.allow=always", "-c", "protocol.file.allow=always",
                  "-c", "protocol.ext.allow=never")
# A repository's remote.<name>.uploadpack / receivepack would run a program on fetch or push, and git keeps the
# FIRST value it reads (the repository's), so `-c` cannot override them; the explicit options can.
_PACK_PROGRAM = {"fetch": "--upload-pack=git-upload-pack", "ls-remote": "--upload-pack=git-upload-pack",
                 "push": "--receive-pack=git-receive-pack"}


def _pinned_pack(args: tuple[str, ...]) -> tuple[str, ...]:
    """Insert git's own upload-pack / receive-pack after a network subcommand."""
    opt = _PACK_PROGRAM.get(args[0]) if args else None
    return (args[0], opt, *args[1:]) if opt else args


# A repository's config can name a remote helper (`ext::<command>`, `<helper>::...`) and allow it per protocol,
# which outranks `protocol.allow=never`; so origin's URLs are checked before any network command.
_SAFE_URL = re.compile(r"(?:https|ssh)://[^\s:]+[^\s]*|[\w.-]+@[\w.-]+:[^\s:][^\s]*|/[^\s:]*")


def _unsafe_origin(repo: Path, env: dict[str, str]) -> str:
    """Return why any of origin's fetch or push URLs is not an https, ssh, scp-like or local-path URL, else "".

    ``--all``: `git push origin` pushes to every pushurl, so every URL is checked, not just the first.
    """
    for extra in (("--all",), ("--push", "--all")):
        proc = subproc_confine.run_git(repo, ("remote", "get-url", *extra, "origin"), hardening=_GIT_HARDENING,
                                       env=env, timeout=30, readonly=True)
        urls = proc.stdout.split()
        for url in urls or [""]:
            if proc.returncode or "::" in url or not _SAFE_URL.fullmatch(url):
                return f"origin URL {url[:80]!r} is not an https, ssh or local path URL; refusing to contact it"
    return ""


def _clean_git_env(global_config: str) -> dict[str, str]:
    """Return the environment without ``GIT_*`` variables, system config off and ``global_config`` as global config."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    # GIT_LFS_SKIP_SMUDGE: git-lfs (fix.LFS_FILTER, plan C2) writes pointer files on checkout and downloads nothing
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": global_config, "GIT_TERMINAL_PROMPT": "0",
                "GIT_LFS_SKIP_SMUDGE": "1"})
    return env


def _git_env() -> dict[str, str]:
    gcfg = paths.state_dir() / "executor.gitconfig"
    gcfg.parent.mkdir(parents=True, exist_ok=True)
    gcfg.write_text(f"[user]\n\tname = {GIT_IDENTITY[0]}\n\temail = {GIT_IDENTITY[1]}\n", encoding="utf-8")
    env = _clean_git_env(str(gcfg))
    # repository config cannot rename the executor's commits (its branches are recognised by this identity)
    env.update({"GIT_AUTHOR_NAME": GIT_IDENTITY[0], "GIT_AUTHOR_EMAIL": GIT_IDENTITY[1],
                "GIT_COMMITTER_NAME": GIT_IDENTITY[0], "GIT_COMMITTER_EMAIL": GIT_IDENTITY[1]})
    return env


def git_proc(repo: Path, *args: str, push: bool = False, timeout: int = 600,
              text: bool = True) -> subprocess.CompletedProcess:
    """Run git hardened (no hooks, fsmonitor, submodule recursion or stored credentials) and return the process.

    ``text=False`` returns bytes: exact content, line endings included.
    """
    env = _git_env()
    if args and args[0] in _PACK_PROGRAM and (why := _unsafe_origin(repo, env)):
        return subprocess.CompletedProcess(["git", *args], 128, b"" if not text else "", why if text else why.encode())
    return subproc_confine.run_git(repo, _pinned_pack(args), hardening=_GIT_HARDENING, env=env, push=push,
                                   timeout=timeout, text=text)   # confined in a scope (push: gh helper / stdin token)


def query_git(repo: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a read-only git query hardened as :func:`git_proc` does, writing nothing (no executor state needed).

    Args:
        repo: Directory to run in (git may find its ``.git`` upwards: the tree can be untrusted).
        *args: git arguments.
        timeout: Seconds.

    Returns:
        The completed process (text).
    """
    return subproc_confine.run_git(repo, args, hardening=_GIT_HARDENING, env=_clean_git_env(os.devnull),
                                   timeout=timeout, readonly=True)


def git(repo: Path, *args: str, check: bool = True, push: bool = False) -> str:
    """Run git with repository/user config unable to redirect execution.

    Args:
        repo: Working directory.
        *args: git arguments (built by the executor only).
        check: Raise on non-zero exit.
        push: Add the explicit gh credential helper (only for network operations).

    Returns:
        stdout.

    Raises:
        ExecError: git failed and ``check`` is set.
    """
    proc = git_proc(repo, *args, push=push)
    if check and proc.returncode:
        raise ExecError(f"git {args[0]} failed: {(proc.stderr or proc.stdout).strip()[:400]}")
    return proc.stdout


# ---------------------------------------------------------------- authorization

def authorize(op: str, target: str, request_id: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """Find the covering grant, check C-2, and reserve one use. Call inside ``ledger.locked()``.

    Args:
        op: Operation.
        target: Canonical target (absolute repo path or host name).
        request_id: Request id (or an operator-run id).
        cfg: Daily config. Only ``agent.team_dir`` is read, and only to *narrow* (P7): the
            governance team is signed into the grant.

    Returns:
        The grant used.

    Raises:
        ExecError: An open escalation blocks the target; a repo op targets the publish grant's
            ops repository, a protected repository (by common dir or origin) or a repository the
            registry enrols below ``remediate`` (:mod:`.executor_guard`); no grant
            covers the action, uses are exhausted, the decision log is missing,
            or a HALT is open on the ``vulnscan-remediation`` action. That includes target-tagged
            HALTs such as ``vulnscan-remediation:<repo-or-host>``: the action matcher treats a suffix as
            decoration, never as a narrowing, so any such HALT stops every grant use (fail-safe) until
            it is retracted.
    """
    rows = ledger.read()
    esc = escalations.blocking(canonical_target(target) or target, rows)
    if esc is not None:
        raise ExecError(f"blocked by open escalation {esc.id} ({esc.kind}, scope {esc.scope}); "
                        f"see: vulnscan halts")
    if op in REPO_OPS:
        try:
            executor_guard.refuse_ops_repo(target)
            executor_guard.refuse_protected_repos(target, cfg)
        except grant.GrantError as exc:   # an unreadable grant file could name a protected repo: refuse
            raise ExecError(f"cannot tell which repositories are protected ({exc}); refusing") from exc
        executor_guard.refuse_scan_only(target)
        executor_guard.refuse_by_brief(op, target)
        if why := retry_guard.futile_reason(op, target, rows):   # nothing fixable, plan unchanged since
            raise ExecError(why)
    if op == "open-pr" and handoff.required(cfg, Path(target)):   # H3: the repository's orchestrator approves first
        branch = handoff.staged_branch(Path(target))
        problem = handoff.approval_problem(Path(target), branch) if branch else ""   # no staged fix: open_pr says so
        if problem:
            raise ExecError(problem)
    grants, problems = grant.load_active()
    usable = [g for g in grants if grant.covers(g, op, target) and ledger.uses(g["grant_id"], rows) < int(g["max_uses"])]
    if not usable:
        why = "; ".join(problems) if problems else "no valid grant covers it, or its uses are exhausted"
        raise ExecError(f"not authorized: {op} {target}: {why}")
    g = min(usable, key=lambda g: g["expires"])
    gov = Path(str(g["governance_dir"]))
    log = gov / "references" / "security-decisions.log.csv"
    if not log.is_file() or log.is_symlink():
        raise ExecError(f"decision log {log} is missing or a symlink, so the C-2 HALT check cannot run — refusing")
    from agentteams.cli import decision_log

    try:
        decision_log._assert_no_unretracted_halt(gov, action=ACTION_ID)
    except RuntimeError as exc:
        raise ExecError(f"HALT in force: {exc}") from exc
    ledger.append("begun", grant_id=g["grant_id"], op=op, target=target, request_id=request_id)
    return g


# ---------------------------------------------------------------- repo operations

def _repo_root(target: str) -> Path:
    p = Path(target).expanduser()
    if not p.is_absolute():
        raise ExecError("repo targets must be absolute paths")
    p = p.resolve()
    proc = git_proc(p, "rev-parse", "--show-toplevel")
    if proc.returncode:   # e.g. safe.directory: say why git refuses rather than "not a repository top"
        problem = fix.git_config_problem(p)
        if problem:
            raise ExecError(f"{p}: {problem}")
    top = proc.stdout.strip()
    if not top or Path(top).resolve() != p:
        raise ExecError(f"{p} is not the top of a git repository")
    return p


def state_slug(repo: Path) -> str:
    """Return the slug naming a repository's spool record and worktrees (:func:`.repo_alias.alias`).

    Args:
        repo: Canonical repository path.

    Returns:
        ``<name>-<8 hex>``.
    """
    return repo_alias.alias(str(repo), empty="")   # "": state-file names stay byte-identical (no "root")


def staged_file(repo: Path) -> Path:
    """Return the path of the executor's record of the fix staged for ``repo`` (it may not exist)."""
    return spool_dir() / "staged" / f"{state_slug(repo)}.json"


def staged_records() -> list[dict[str, Any]]:
    """Return every readable staged-fix record (``repo``, ``branch``, ``worktree`` ...), in file-name order.

    Returns:
        The records that parse as JSON objects; unreadable files are skipped.
    """
    out = []
    for f in sorted((spool_dir() / "staged").glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):   # an unreadable record lists nothing
            data = None
        if isinstance(data, dict):
            out.append(data)
    return out


def _worktrees_dir() -> Path:
    """Return ``<state>/worktrees``: the only place the executor creates (and so may remove) worktrees."""
    return paths.state_dir() / "worktrees"


def _release_own_branch(repo: Path, branch: str) -> None:
    """Free a local ``branch`` an earlier staging of the same change left behind (D4).

    The branch name is the date plus the diff digest, so an existing one holds the same change.
    It is released only when its tip was committed by the executor and every worktree holding it is
    under :func:`_worktrees_dir`: those worktrees are removed and the *local* branch deleted (a
    pushed copy stays on the remote; nothing is forced there).

    Args:
        repo: Repository root.
        branch: ``vulnscan/...`` branch about to be created.

    Raises:
        ExecError: The branch is checked out elsewhere or was not created by the executor.
    """
    ref = f"refs/heads/{branch}"
    if git_proc(repo, "rev-parse", "--verify", "-q", ref).returncode:
        return
    holders = _branch_holders(repo, ref)
    if holders is None:
        raise ExecError(f"{branch} is checked out outside the executor's worktrees; not touching it")
    if git(repo, "log", "-1", "--format=%ce", ref).strip() != GIT_IDENTITY[1]:
        raise ExecError(f"local branch {branch} exists and was not created by the executor; not touching it")
    for p in holders:
        git(repo, "worktree", "remove", "--force", str(p))
    git(repo, "branch", "-D", branch)


def _branch_holders(repo: Path, ref: str) -> list[Path] | None:
    """Return the worktrees that have ``ref`` checked out, or None when any is outside :func:`_worktrees_dir`."""
    holders: list[Path] = []
    current: Path | None = None
    for ln in git(repo, "worktree", "list", "--porcelain").splitlines():
        if ln.startswith("worktree "):
            current = Path(ln[len("worktree "):])
        elif ln == f"branch {ref}" and current is not None:
            holders.append(current)
    owned = _worktrees_dir().resolve()
    return None if any(not p.resolve().is_relative_to(owned) for p in holders) else holders


def _drop_failed_branch(repo: Path, branch: str, base: str) -> None:
    """Delete the local ``branch`` a failed :func:`stage_fix` attempt itself created, so a retry starts clean.

    Only while it still points at ``base`` (compare-and-swap delete) and is checked out nowhere outside
    the executor's worktrees. A tip the executor committed is already reusable by :func:`_release_own_branch`.
    """
    ref = f"refs/heads/{branch}"
    if _branch_holders(repo, ref) is not None:
        git(repo, "update-ref", "-d", ref, base, check=False)   # no-op unless the ref is exactly ``base``


def _stage_changed(wt: Path, base: str, changed: list[str]) -> None:
    """Stage exactly the validated ``changed`` paths (``:(literal)`` pathspecs) and check nothing else is staged.

    Tracked files use ``add -u`` (index entries only, so a tracked pin under a gitignored directory
    stages without ``-f``); new, never-ignored files (``--exclude-standard``) a plain ``add``.
    """
    new = set(git(wt, "ls-files", "-z", "--others", "--exclude-standard").split("\0")) & set(changed)
    for flags, group in ((["-u"], [p for p in changed if p not in new]), ([], sorted(new))):
        if group:
            git(wt, "add", *flags, "--", *(f":(literal){p}" for p in group))
    staged = {p for p in git(wt, "diff", "--cached", "--name-only", "-z", "--no-renames", base).split("\0") if p}
    if staged != set(changed):
        raise ExecError(f"staged paths differ from the validated change: {sorted(staged ^ set(changed))}")


def _refuse_filtered(wt: Path, changed: list[str]) -> None:
    """Refuse a change to any path with a ``filter`` attribute (git-lfs's included).

    ``git add`` would commit the filter's output (for git-lfs, a pointer) instead of the bytes :func:`validate_diff`
    judged. ``check-attr`` only reads ``.gitattributes``; it runs no filter.

    Args:
        wt: Worktree.
        changed: The changed paths (:func:`validate_diff`).

    Raises:
        ExecError: A changed path has a filter.
    """
    parts = git(wt, "check-attr", "-z", "filter", "--", *changed).split("\0")
    for rel, _attr, value in zip(parts[0::3], parts[1::3], parts[2::3]):
        if value not in ("unspecified", "unset"):
            raise ExecError(f"refusing change: {rel} has filter={value} (git-lfs or another filter); fix it by hand")


def validate_diff(wt: Path, base: str) -> list[str]:
    """Check that a staged change touches only what fix.py may touch (B1, X1, M1).

    Tracked changes are listed with ``--no-renames`` (a rename is a deletion plus an addition, and a
    deletion is refused). Every changed or new path must be a requirements file or npm lockfile that
    is still a regular, non-executable file (never read through a symlink) with an unchanged mode.
    Content is judged byte for byte by :func:`manifests.check_pin_edit` and :func:`manifests.check_lock_edit`.

    Args:
        wt: Worktree.
        base: Base commit.

    Returns:
        The changed paths.

    Raises:
        ExecError: Any disallowed change.
    """
    raw = git(wt, "diff", "--raw", "-z", "--no-renames", "--no-abbrev", base).split("\0")
    changed: list[str] = []
    for meta, rel in zip(raw[0::2], raw[1::2]):
        fields = meta.lstrip(":").split()
        old_mode, new_mode, status = (fields[0], fields[1], fields[-1]) if len(fields) >= 5 else ("?", "?", "?")
        if status == "D":
            raise ExecError(f"refusing change: {rel} was deleted (or renamed)")
        if old_mode not in ("100644", "000000") or new_mode != "100644":
            raise ExecError(f"refusing mode change on {rel} ({old_mode} -> {new_mode}; symlink or exec bit)")
        changed.append(rel)
    changed += [p for p in git(wt, "ls-files", "-z", "--others", "--exclude-standard").split("\0") if p]
    for rel in changed:
        if not ALLOWED_CHANGED.search(rel):
            raise ExecError(f"refusing change outside dependency pins/lockfiles: {rel}")
        st = os.lstat(wt / rel)
        if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o111:
            raise ExecError(f"refusing {rel}: not a regular file, or its mode changed (symlink or exec bit)")
        new = (wt / rel).read_bytes()
        shown = git_proc(wt, "show", "--no-textconv", f"{base}:{rel}", text=False)   # never a diff.<driver>.textconv
        old = b"" if shown.returncode else shown.stdout
        try:
            if REQ_NAME.search(Path(rel).name):
                check_pin_edit(rel, old, new)
            else:
                check_lock_edit(rel, old.decode("utf-8", "replace") or "{}", new.decode("utf-8", "replace"))
        except ValueError as exc:
            raise ExecError(str(exc)) from exc
    return changed


def stage_fix(repo: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    """Create an executor-owned worktree, apply deterministic fixes, validate, commit locally.

    Args:
        repo: Repository root.
        cfg: Daily config.

    Returns:
        Result with worktree, branch, base and the fix plan markdown.

    Raises:
        ExecError: The repository's own git config defines a filter driver (or git refuses to open it),
            or anything else fails (the worktree, and a branch this attempt created, are removed, D4).
        subprocess.SubprocessError: A git or npm step timed out (the worktree is removed too).
    """
    unsafe = fix.git_config_problem(repo)   # before any git on the repo: checkout would run smudge filters
    if unsafe:
        raise ExecError(f"{repo}: {unsafe}")
    git(repo, "fetch", "-q", "origin", push=True)
    head = executor_guard.brief_base_ref(repo) or \
        git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False).strip() or "origin/main"
    base = git(repo, "rev-parse", head).strip()
    wt = _worktrees_dir() / f"{state_slug(repo)}-{datetime.now(UTC):%Y%m%d%H%M%S}-{os.urandom(3).hex()}"
    wt.parent.mkdir(parents=True, exist_ok=True)
    staged = False
    branch = ""   # set once this attempt (and only it) may have created the branch
    try:
        git(repo, "worktree", "add", "-q", "--detach", str(wt), base)
        cooldown = RELEASE_COOLDOWN_DAYS   # the config's cooldown_days is deprecated and ignored
        res = fix.fix_path(wt, apply=True, allow_major=False, cooldown_days=cooldown)
        if res.feed_errors:
            raise ExecError("feeds unavailable: " + "; ".join(res.feed_errors))
        changed = validate_diff(wt, base)
        if not changed:   # an npm that failed to run must not read as "nothing fixable"
            npm_err = next((ln for _, out in res.npm for ln in out.splitlines() if ln.startswith("npm error")), "")
            raise ExecError(f"npm failed: {npm_err[:200]}" if npm_err else
                            "nothing fixable without a major jump")
        _refuse_filtered(wt, changed)   # C2: a filtered file's commit would not hold what validate_diff checked
        digest = hashlib.sha256(git(wt, "diff", "--no-ext-diff", "--no-textconv", base).encode()).hexdigest()[:10]
        name = f"vulnscan/{datetime.now(UTC):%Y%m%d}-{digest}"
        _release_own_branch(repo, name)   # the branch is absent from here on, or this raised
        git(wt, "checkout", "-q", "-b", name)
        branch = name   # only now is the branch this attempt's own (cleanup may delete it)
        _stage_changed(wt, base, changed)
        git(wt, "commit", "-q", "-m", f"security(deps): fix known-vulnerable dependency versions ({digest})",
            "-m", "Staged by the agentteams vulnscan executor (deterministic fix; lockfile/pin changes only).")
        staged = True
    except OSError as exc:
        raise ExecError(str(exc)) from exc
    finally:
        if not staged:   # any exception, a timeout included (D4): undo this attempt's worktree and branch
            git(repo, "worktree", "remove", "--force", str(wt), check=False)
            if branch:
                with contextlib.suppress(ExecError, OSError, subprocess.SubprocessError):
                    _drop_failed_branch(repo, branch, base)
    result = {"repo": str(repo), "worktree": str(wt), "branch": branch, "base": base, "base_ref": head,
              "changed": changed, "plan": res.markdown()}
    _write_json(staged_file(repo), result)
    return result


def _sanitize(text: str, limit: int = 2000) -> str:
    text = text[:limit].replace("@", "@​").replace("```", "'''")
    return "\n".join("> " + ln for ln in text.splitlines()) or "> (none)"


def open_pr(repo: Path, rationale: str) -> dict[str, Any]:
    """Push the staged branch as a new ref and open a PR (never merge, never force).

    Idempotent (D4): when the remote branch already exists, an open PR for it is returned as is;
    without one, the PR is created from the remote branch only if it is exactly the staged commit
    (a push that succeeded before ``gh pr create`` failed). After the PR exists the executor's
    worktree and staged state are removed; the local branch is kept.

    Args:
        repo: Repository root.
        rationale: The agent's rationale (untrusted; quoted and neutralised in the body).

    Returns:
        Result with the PR URL (``existing`` is True when an open PR was found).

    Raises:
        ExecError: No staged change, the remote ref holds another commit with no open PR, or
            push / PR listing / PR creation failed.
    """
    staged = staged_file(repo)
    if not staged.is_file():
        raise ExecError("no staged fix for this repo — request stage-fix first")
    st = json.loads(staged.read_text(encoding="utf-8"))
    wt, branch = Path(st["worktree"]), st["branch"]
    if not branch.startswith("vulnscan/") or not wt.is_dir() or not wt.resolve().is_relative_to(
            _worktrees_dir().resolve()):
        raise ExecError("staged state is invalid or its worktree is gone")
    target_repo = _origin_repo(wt)   # before anything is pushed; never let gh pick a base repo
    base_branch = st["base_ref"].split("/", 1)[-1]   # the brief's base_branch, else origin's default branch
    executor_guard.require_remote_branch(wt, base_branch)
    ref = f"refs/heads/{branch}"
    remote_sha = next((ln.split("\t", 1)[0] for ln in git(wt, "ls-remote", "origin", ref, push=True).splitlines()
                       if ln.endswith(f"\t{ref}")), "")
    if remote_sha:
        url = _open_pr_url(wt, target_repo, branch)
        if url:
            # The PR is ours only if its branch holds exactly the staged content (a re-staged fix is a new
            # commit with the same tree); anything else on the branch is someone else's change (C13).
            git(wt, "fetch", "--no-tags", "origin", ref, push=True)
            if git(wt, "rev-parse", f"{remote_sha}^{{tree}}").strip() != git(wt, "rev-parse", "HEAD^{tree}").strip():
                raise ExecError(f"open PR {url} on {branch} does not hold the staged change — not claiming it")
            _finish_open_pr(repo, wt, staged)
            return {"repo": str(repo), "branch": branch, "pr": url, "existing": True}
        if remote_sha != git(wt, "rev-parse", "HEAD").strip():
            raise ExecError(f"remote branch {branch} already exists at another commit and has no open PR "
                            "— not overwriting")
    else:
        git(wt, "push", "origin", f"HEAD:{ref}", push=True)
    body = (f"Automated security fix staged by the agentteams **vulnscan executor** "
            f"(deterministic; dependency pins and lockfiles only).\n\n{st['plan']}\n\n"
            f"### Agent rationale (untrusted text, quoted)\n\n{_sanitize(rationale)}\n\n"
            "Tests run in CI. This PR is never merged automatically.\n")
    from agentteams.vulnscan import publish   # lazy: publish imports this module

    body = publish.rewrite_paths(body)   # the same scrubber as published reports (home, host name, private IPs)
    body_file = wt.parent / f"{wt.name}.pr.md"
    body_file.write_text(body, encoding="utf-8")
    proc = subprocess.run(["gh", "pr", "create", "--repo", target_repo, "--base", base_branch, "--head", branch,
                           "--title", f"security(deps): fix known-vulnerable dependencies ({branch.rsplit('-', 1)[-1]})",
                           "--body-file", str(body_file)], cwd=gh_cwd(), capture_output=True, text=True,
                          env=gh_push_env(), timeout=300, check=False)
    if proc.returncode:
        raise ExecError(f"gh pr create failed: {(proc.stderr or proc.stdout).strip()[:400]}")
    url = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    _finish_open_pr(repo, wt, staged)
    return {"repo": str(repo), "branch": branch, "pr": url}


def gh_env() -> dict[str, str]:
    """Return the environment for a read-only ``gh`` call made outside a repository operation.

    Inherited ``GIT_*`` variables are dropped, system and global git config are off, and ``gh`` never prompts.
    Shared by publish and notes (read-only API calls). The executor's own ``gh pr`` calls use
    :func:`gh_push_env`, which adds the hardened git environment.

    Returns:
        The environment.
    """
    return _clean_git_env(os.devnull) | {"GH_PROMPT_DISABLED": "1", "NO_COLOR": "1"}


def gh_push_env() -> dict[str, str]:
    """Return the hardened git environment for gh, with no global git config and no repository above its cwd.

    Public for ``executor_supersede``, which runs ``gh pr view`` / ``gh pr close`` under a grant."""
    return _git_env() | {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CEILING_DIRECTORIES": str(paths.state_dir())}


def gh_cwd() -> Path:
    """Return gh's working directory: empty, so gh (given ``--repo``) never reads or runs a checkout's config (W1).

    Public for the other modules that run ``gh`` (effectiveness, executor_supersede)."""
    (d := paths.state_dir() / "gh-cwd").mkdir(parents=True, exist_ok=True)
    return d


def _open_pr_url(wt: Path, target_repo: str, branch: str) -> str:
    """Return the URL of an open PR whose head is ``branch`` in ``target_repo`` itself, or "".

    ``--head`` matches a branch name in any fork, so a PR from a fork with the same branch name is
    ignored (``isCrossRepository``): the executor always pushes to ``target_repo``.

    Raises:
        ExecError: gh failed or did not answer with a JSON list.
    """
    proc = subprocess.run(["gh", "pr", "list", "--repo", target_repo, "--head", branch, "--state", "open",
                           "--json", "url,isCrossRepository"], cwd=gh_cwd(), capture_output=True, text=True, env=gh_push_env(), timeout=120,
                          check=False)
    if proc.returncode:
        raise ExecError(f"gh pr list failed: {(proc.stderr or proc.stdout).strip()[:400]}")
    try:
        prs = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise ExecError(f"gh pr list did not return JSON: {exc}") from exc
    urls = [p.get("url") for p in prs if isinstance(p, dict) and p.get("isCrossRepository") is False] \
        if isinstance(prs, list) else []
    return next((u for u in urls if isinstance(u, str) and u.startswith("https://")), "")


def _finish_open_pr(repo: Path, wt: Path, staged: Path) -> None:
    """Remove the executor's worktree and staged state once the PR exists (the branch is kept, D4)."""
    git(repo, "worktree", "remove", "--force", str(wt))
    staged.unlink(missing_ok=True)


def _origin_repo(wt: Path) -> str:
    """Return ``HOST/OWNER/REPO`` of ``origin``, the repository the branch was pushed to.

    Raises:
        ExecError: ``origin`` is not a URL gh can target.
    """
    url = git(wt, "config", "--get", "remote.origin.url").strip()
    m = _ORIGIN_RE.match(url)
    if not m:
        raise ExecError("origin is not a GitHub repository URL (https://HOST/OWNER/REPO or git@HOST:OWNER/REPO); "
                        "open-pr will not let gh choose a repository")
    host = m.group(1) or m.group(2) or m.group(3)
    return f"{host}/{m.group(4)}/{m.group(5)}"


# ---------------------------------------------------------------- server operations

def _kit_hosts() -> set[str]:
    conf = paths.server_kit_dir() / "hosts.conf"
    if not conf.is_file():
        return set()
    names = set()
    for ln in conf.read_text(encoding="utf-8").splitlines():
        if ln.strip() and not ln.lstrip().startswith("#"):
            names.add(ln.split("|", 1)[0].strip())
    return names


def server_op(op: str, host: str) -> dict[str, Any]:
    """Run the packaged kit's audit or full dry-run against one exact host.

    Args:
        op: ``server-audit`` or ``server-dryrun``.
        host: Exact hosts.conf name (never ``all``).

    Returns:
        Result with the log path (and the inventory report for audits).

    Raises:
        ExecError: Unknown host or the run failed to complete.
    """
    if host == "all" or host not in _kit_hosts():
        raise ExecError(f"host {host!r} is not a hosts.conf entry")
    logs = paths.state_dir() / "server-logs"
    logs.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENTTEAMS_SERVER_KIT")}
    env.update({"AGENTTEAMS_SERVER_KIT_CONFIG": str(paths.server_kit_dir()), "AGENTTEAMS_SERVER_KIT_LOGS": str(logs),
                "AGENTTEAMS_VULNSCAN_NO_REPORT": "1"})
    script = "remote.sh" if op == "server-audit" else "run-host.sh"
    argv = ["bash", str(kit_dir() / script), host] + (["L1-audit.sh"] if op == "server-audit" else [])
    if subproc_confine.active():   # ssh in the tools container: generated config, known_hosts lines, one egress host
        proc = subproc_confine_ssh.run_kit(kit_dir() / script, host, argv[3:], conf_dir=paths.server_kit_dir(),
                                           logs=logs, timeout=3600)
    else:
        proc = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=3600, check=False)
    out = logs / f"{host}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{op}.log"
    out.write_text(proc.stdout + proc.stderr, encoding="utf-8")
    if not re.search(r"^RESULT (clean|action-needed|COMPROMISE)", proc.stdout, re.M):
        raise ExecError(f"{op} on {host} did not complete (exit {proc.returncode}); log {out}")
    result: dict[str, Any] = {"host": host, "log": str(out), "exit": proc.returncode}
    if op == "server-audit":
        rep = engine.scan_inventory(proc.stdout, host)
        md = out.with_suffix(".report.md")
        md.write_text(rep.markdown(), encoding="utf-8")
        result.update({"report": str(md), "findings": len(rep.findings), "exit_code": rep.exit_code()})
    return result


# ---------------------------------------------------------------- dispatch

def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def new_run_id(run_dir: Path) -> str:
    """Return a run id: the run directory's date plus a random suffix (several runs per day are possible).

    Args:
        run_dir: The run directory (named by date).

    Returns:
        ``"<run_dir.name>-<6 hex>"``.
    """
    return f"{run_dir.name}-{os.urandom(3).hex()}"


def canonical_target(target: str) -> str | None:
    """Return the canonical form of a target, or None when it is neither a repository nor a known host.

    An absolute path inside a git repository canonicalises to the **main** repository's top-level
    path (subdirectories allowed; a linked worktree maps to the repository it belongs to, so an
    escalation on either blocks both); an exact ``hosts.conf`` name stays itself.

    Args:
        target: Path or host name.

    Returns:
        Canonical target, or None.
    """
    if target in _kit_hosts():
        return target
    p = Path(target).expanduser()
    if not p.is_absolute() or not p.exists():
        return None
    base = p if p.is_dir() else p.parent
    proc = git_proc(base, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir")
    lines = proc.stdout.split("\n")
    if proc.returncode or len(lines) < 2 or not lines[0].strip():
        return None
    top, common = Path(lines[0].strip()).resolve(), Path(lines[1].strip()).resolve()
    return str(common.parent if common.name == ".git" else top)


def _internal(op: str, target: str, *, payload: dict[str, Any], request_id: str,
              run_dir: Path | None, run_id: str | None) -> dict[str, Any]:
    """Perform an internal, grant-free op (``report``, ``escalate``). Call inside ``ledger.locked()``.

    ``report`` stores the agent's report for the run. ``escalate`` records an escalation whose scope
    the executor (not the agent) decides; a duplicate of an open escalation records nothing new. Both
    append a ``done`` row carrying the request id, so a replayed request is refused.

    Args:
        op: ``report`` or ``escalate``.
        target: The agent's target (``"run"`` for ``report``).
        payload: Validated payload.
        request_id: Request id.
        run_dir: The current run directory (``report`` requires it).
        run_id: The current run id (escalations are counted per run).

    Returns:
        Result fields. ``_notify`` (escalation id and kind) is set for a newly opened escalation.

    Raises:
        ExecError: ``report`` without a run in progress.
        ValueError: The store or the ledger refused the record.
    """
    if op == "report":
        if run_dir is None:
            raise ExecError("report needs a run in progress (no run directory)")
        path = report_store.save(run_dir, payload["markdown"], request_id=request_id)
        ledger.append("done", op=op, target="run", request_id=request_id, detail=f"stored {path.name}")
        return {"stored": str(path)}
    rid = run_id or f"adhoc-{datetime.now(UTC).date().isoformat()}"
    esc = escalations.record(payload["kind"], target, payload["evidence"], request_id=request_id, run_id=rid,
                             canonical=executor_guard.canonical_if_granted)
    new = esc.request_id == request_id
    ledger.append("done", op=op, target=esc.scope, request_id=request_id,
                  detail=f"{'opened' if new else 'duplicate of'} {esc.id}")
    out: dict[str, Any] = {"escalation": esc.id, "kind": esc.kind, "scope": esc.scope, "new": new}
    if new:
        out["_notify"] = {"id": esc.id, "kind": esc.kind}
    return out


def act(op: str, target: str, cfg: dict[str, Any], *, rationale: str = "", request_id: str = "",
        payload: dict[str, Any] | None = None, run_dir: Path | None = None,
        run_id: str | None = None) -> dict[str, Any]:
    """Authorize and perform one action, ledgering it (the single entry point for every op).

    A ledger that fails verification (tampering, truncation) refuses everything without appending:
    nothing can be ledgered into an untrusted chain.

    Args:
        op: Operation (grant.OPS_V1, or an internal op in INTERNAL_OPS).
        target: Repo path or host name (``"run"`` for ``report``).
        cfg: Daily config.
        rationale: Agent or operator rationale (untrusted text).
        request_id: Spool request id, or "" for an operator-run action.
        payload: Internal-op payload (``report``: markdown; ``escalate``: kind + evidence).
        run_dir: The current run directory (needed by ``report``).
        run_id: The current run id (needed by ``escalate``).

    Returns:
        ``{"status": "done"|"failed"|"refused", ...}``.
    """
    request_id = request_id or "op-" + os.urandom(8).hex()
    with ledger.locked():
        try:
            rows = ledger.read()
        except ValueError as exc:
            return {"status": "refused", "detail": f"ledger integrity failure — refusing everything: {exc}"}
        if request_id in ledger.consumed_request_ids(rows):   # a concurrent server took it first
            return {"status": "refused", "detail": "request id already used (replay)"}
        if op not in INTERNAL_OPS:
            return _granted(op, target, cfg, rationale=rationale, request_id=request_id)
        try:
            result = _internal(op, target, payload=payload or {}, request_id=request_id,
                               run_dir=run_dir, run_id=run_id)
        except (ExecError, OSError, ValueError) as exc:
            ledger.append("refused", op=op, target=target[:200], request_id=request_id, detail=str(exc)[:1000])
            return {"status": "refused", "detail": str(exc)}
    alert = result.pop("_notify", None)
    if alert:   # after the lock: a slow notifier must not hold the ledger
        notify.send(cfg, "escalation", **alert)   # fixed text: id + kind only, never the evidence
    return {"status": "done", **result}


def _granted(op: str, target: str, cfg: dict[str, Any], *, rationale: str, request_id: str) -> dict[str, Any]:
    """Authorize and perform one granted op, its subprocesses confined (fail closed). Call inside ``ledger.locked()``."""
    try:
        confined = subproc_confine.scope(cfg, scratch=[_worktrees_dir()])
    except subproc_confine.Unavailable as exc:   # nothing runs on the host instead
        ledger.append("refused", op=op, target=target, request_id=request_id, detail=str(exc)[:1000])
        return {"status": "refused", "detail": str(exc)}
    with confined:
        return _granted_op(op, target, cfg, rationale=rationale, request_id=request_id)


def _granted_op(op: str, target: str, cfg: dict[str, Any], *, rationale: str, request_id: str) -> dict[str, Any]:
    try:
        if op not in grant.OPS_V1:
            raise ExecError(f"unknown or non-v1 operation {op!r}")
        canonical = str(_repo_root(executor_guard.refuse_outside_grants(op, target))) if op in REPO_OPS else target
        g = authorize(op, canonical, request_id, cfg)
    except (ExecError, subprocess.SubprocessError) as exc:   # incl. subproc_confine.Unavailable and git timeouts
        ledger.append("refused", op=op, target=target, request_id=request_id, detail=str(exc)[:1000])
        return {"status": "refused", "detail": str(exc)}
    try:
        if op == "stage-fix":
            result = stage_fix(Path(canonical), cfg)
        elif op == "open-pr":
            result = open_pr(Path(canonical), rationale)
        elif op == "close-superseded":
            result = executor_supersede.close_superseded(Path(canonical))
        else:
            result = server_op(op, canonical)
    except (ExecError, OSError, subprocess.SubprocessError, ValueError) as exc:
        detail: str | None = str(exc)
    except (TypeError, AttributeError, KeyError) as exc:   # malformed input reached shape-trusting code (D3)
        detail = f"{type(exc).__name__}: {exc}"
    else:
        detail = None
    if detail is not None:
        ledger.append("failed", grant_id=g["grant_id"], op=op, target=canonical, request_id=request_id,
                      detail=detail[:1000])
        return {"status": "failed", "detail": detail}
    slim = {k: v for k, v in result.items() if k != "plan"}
    ledger.append("done", grant_id=g["grant_id"], op=op, target=canonical, request_id=request_id,
                  detail=json.dumps(slim)[:2000], rationale=rationale[:2000])
    return {"status": "done", **slim}


# The spool server (request reading, the batch preflight, serving) lives in .executor_serve (CH-07). These
# wrappers import it lazily, inside the call: executor_serve imports this module at load time (it looks up
# act, spool_dir and the request limits here, so they stay the one source), and a load-time import back
# would be an import cycle.
def serve_requests(cfg: dict[str, Any], *, run_dir: Path | None = None, run_id: str | None = None,
                   deadline: float | None = None) -> list[dict[str, Any]]:
    """See :func:`agentteams.vulnscan.executor_serve.serve_requests`."""
    from agentteams.vulnscan import executor_serve

    return executor_serve.serve_requests(cfg, run_dir=run_dir, run_id=run_id, deadline=deadline)


def quarantine_pending(reason: str) -> int:
    """See :func:`agentteams.vulnscan.executor_serve.quarantine_pending`."""
    from agentteams.vulnscan import executor_serve

    return executor_serve.quarantine_pending(reason)


def batch_block(cfg: dict[str, Any], op: str) -> str:
    """See :func:`agentteams.vulnscan.executor_serve.batch_block`."""
    from agentteams.vulnscan import executor_serve

    return executor_serve.batch_block(cfg, op)
