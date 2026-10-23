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
  :func:`agentteams.vulnscan.fix.fix_path` (non-major, cooldown-clean). It then validates the
  diff: only requirements/lockfiles, only ``==`` version edits, no root-package change, and only
  allowlisted registries in lockfile URLs. Finally it commits locally. The agent cannot
  influence the content.
- ``open-pr REPO``: pushes the staged branch as a *new* ``vulnscan/*`` ref (never force, refused
  if the ref already exists) and opens a PR. It never merges.
- Both repo ops are refused on any worktree of the agentteams checkout, ``agent.team_dir``, a
  grant's ``governance_dir`` or a publish ops repo (compared by git common dir; P7).
- ``server-audit HOST`` / ``server-dryrun HOST``: run the *packaged* server kit's read-only audit,
  or ``run-host.sh`` without ``--apply``, on an exact host named in the grant and in ``hosts.conf``.

git always runs with system/global config ignored, hooks disabled, a fixed ssh command and an
explicit credential helper, so repository or user config cannot redirect what runs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

import agentteams
from agentteams.vulnscan import engine, escalations, fix, grant, ledger, notify, paths, report_store
from agentteams.vulnscan.manifests import REQ_NAME, REQ_PIN

ACTION_ID = "vulnscan-remediation"           # decision-log action id a HALT would name
MIN_COOLDOWN_DAYS = 14                       # Rule S-10 floor: config may raise it, never lower it
ALLOWED_CHANGED = re.compile(r"(^|/)(requirements[\w.-]*\.txt|package-lock\.json|npm-shrinkwrap\.json)$", re.I)
REGISTRY_HOSTS = frozenset({"registry.npmjs.org", "registry.yarnpkg.com"})
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

def _git_env() -> dict[str, str]:
    gcfg = paths.state_dir() / "executor.gitconfig"
    gcfg.parent.mkdir(parents=True, exist_ok=True)
    gcfg.write_text(f"[user]\n\tname = {GIT_IDENTITY[0]}\n\temail = {GIT_IDENTITY[1]}\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(gcfg), "GIT_TERMINAL_PROMPT": "0"})
    return env


def _git_proc(repo: Path, *args: str, push: bool = False, timeout: int = 600) -> subprocess.CompletedProcess:
    """Run git hardened (no hooks, fsmonitor, submodule recursion or stored credentials) and return the process."""
    pre = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.sshCommand=ssh",
           "-c", "submodule.recurse=false", "-c", "fetch.recurseSubmodules=false", "-c", "credential.helper="]
    if push:
        pre += ["-c", "credential.helper=!gh auth git-credential"]
    return subprocess.run(["git", *pre, "-C", str(repo), *args], capture_output=True, text=True,
                          env=_git_env(), timeout=timeout, check=False)


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
    proc = _git_proc(repo, *args, push=push)
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
            ops repository or a protected repository (:func:`_refuse_protected_repos`); no grant
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
    if op in ("stage-fix", "open-pr"):
        _refuse_ops_repo(target)
        _refuse_protected_repos(target, cfg)
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


def _refuse_ops_repo(target: str) -> None:
    """Refuse repo ops on any publish grant's ops repository, valid or not (reports go there by ``publish`` only)."""
    from agentteams.vulnscan import publish   # lazy: publish imports this module

    if {Path(target).resolve(), Path(canonical_target(target) or target).resolve()} & publish.all_ops_repos():
        raise ExecError(f"{target} is a publish grant's ops repository; remediation ops never touch it")


def repo_identity(path: Path) -> Path | None:
    """Return the git common dir of the repository containing ``path`` (shared by all its worktrees).

    Args:
        path: An existing absolute file or directory.

    Returns:
        The resolved common dir, or None when ``path`` is relative, missing or not in a repository.

    Raises:
        subprocess.TimeoutExpired: git did not answer within 30 s.
    """
    p = path.expanduser()
    if not p.is_absolute() or not p.exists():
        return None
    base = p if p.is_dir() else p.parent
    proc = _git_proc(base, "rev-parse", "--path-format=absolute", "--git-common-dir", timeout=30)
    out = proc.stdout.strip()
    return Path(out).resolve() if not proc.returncode and out else None


def protected_repo_paths(cfg: dict[str, Any]) -> list[Path]:
    """Return the paths whose repositories remediation never touches (P7).

    These are the checkout this ``agentteams`` package runs from, ``agent.team_dir``, the
    ``governance_dir`` of every remediation- and publish-grant *file* (valid or not), and every
    publish grant's ops repository. Unsigned or unverified sources can only add paths here, so they
    narrow what the executor does and never widen it.

    Args:
        cfg: Daily config.

    Returns:
        Candidate paths (each may or may not be inside a repository).
    """
    from agentteams.vulnscan import publish   # lazy: publish imports this module

    out = [Path(agentteams.__file__).resolve().parent]
    agent = cfg.get("agent")
    team = agent.get("team_dir") if isinstance(agent, dict) else None
    if isinstance(team, str) and team:
        out.append(Path(team).expanduser())
    for d in (grant.grants_dir(), publish.grants_dir()):
        out.extend(Path(gov).expanduser() for gov in grant.unverified_values(d, "governance_dir"))
    out.extend(sorted(publish.all_ops_repos()))
    return out


def _refuse_protected_repos(target: str, cfg: dict[str, Any]) -> None:
    """Refuse repo ops whose target shares a repository (any worktree) with a protected path."""
    try:
        mine = repo_identity(Path(target))
        protected = [(p, repo_identity(p)) for p in protected_repo_paths(cfg)] if mine is not None else []
    except subprocess.TimeoutExpired as exc:   # cannot tell: refuse rather than crash the serving loop (V2-7)
        raise ExecError(f"could not identify the repository of {target} or a protected path in time; refusing") from exc
    for p, ident in protected:
        if ident == mine:
            raise ExecError(f"{target} is the same repository as {p} (the agentteams checkout, agent.team_dir, "
                            "a grant's governance_dir or a publish ops repo); remediation ops never touch it")


# ---------------------------------------------------------------- repo operations

def _repo_root(target: str) -> Path:
    p = Path(target).expanduser()
    if not p.is_absolute():
        raise ExecError("repo targets must be absolute paths")
    p = p.resolve()
    top = _git_proc(p, "rev-parse", "--show-toplevel").stdout.strip()
    if not top or Path(top).resolve() != p:
        raise ExecError(f"{p} is not the top of a git repository")
    return p


def _state_slug(repo: Path) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", repo.name) + "-" + hashlib.sha256(str(repo).encode()).hexdigest()[:8]


def validate_diff(wt: Path, base: str) -> list[str]:
    """Check that a staged change touches only what fix.py may touch (B1).

    Args:
        wt: Worktree.
        base: Base commit.

    Returns:
        The changed paths.

    Raises:
        ExecError: Any disallowed change.
    """
    changed = [p for p in git(wt, "diff", "--name-only", base).splitlines() if p]
    changed += [p for p in git(wt, "ls-files", "--others", "--exclude-standard").splitlines() if p]
    for rel in changed:
        if not ALLOWED_CHANGED.search(rel):
            raise ExecError(f"refusing change outside dependency pins/lockfiles: {rel}")
        new = (wt / rel).read_text(encoding="utf-8", errors="replace")
        old = git(wt, "show", f"{base}:{rel}", check=False)
        if REQ_NAME.search(Path(rel).name):
            a, b = old.splitlines(), new.splitlines()
            if len(a) != len(b):
                raise ExecError(f"{rel}: line count changed")
            for x, y in zip(a, b):
                if x != y:
                    mx, my = REQ_PIN.match(x), REQ_PIN.match(y)
                    if not (mx and my and mx.group(1) == my.group(1)):
                        raise ExecError(f"{rel}: a non-pin line changed")
        else:
            try:
                before, after = json.loads(old or "{}"), json.loads(new)
            except json.JSONDecodeError as exc:
                raise ExecError(f"{rel}: not valid JSON after fix: {exc}") from exc
            root_before = (before.get("packages") or {}).get("", {})
            root_after = (after.get("packages") or {}).get("", {})
            for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies", "scripts"):
                if root_before.get(key) != root_after.get(key):
                    raise ExecError(f"{rel}: root package {key} changed")
            for name, info in (after.get("packages") or {}).items():
                url = str(info.get("resolved") or "")
                host = re.sub(r"^https://([^/]+)/.*$", r"\1", url) if url else ""
                if url and (not url.startswith("https://") or host not in REGISTRY_HOSTS):
                    raise ExecError(f"{rel}: {name or '(root)'} resolves outside the registry allowlist: {url[:120]}")
    return changed


def stage_fix(repo: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    """Create an executor-owned worktree, apply deterministic fixes, validate, commit locally.

    Args:
        repo: Repository root.
        cfg: Daily config (``cooldown_days``).

    Returns:
        Result with worktree, branch, base and the fix plan markdown.

    Raises:
        ExecError: Anything fails (the worktree is removed).
    """
    git(repo, "fetch", "-q", "origin", push=True)
    head = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False).strip() or "origin/main"
    base = git(repo, "rev-parse", head).strip()
    wt = paths.state_dir() / "worktrees" / f"{_state_slug(repo)}-{datetime.now(UTC):%Y%m%d%H%M%S}"
    wt.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-q", "--detach", str(wt), base)
    try:
        cooldown = max(MIN_COOLDOWN_DAYS, int(cfg.get("cooldown_days", MIN_COOLDOWN_DAYS)))
        res = fix.fix_path(wt, apply=True, allow_major=False, cooldown_days=cooldown)
        if res.feed_errors:
            raise ExecError("feeds unavailable: " + "; ".join(res.feed_errors))
        changed = validate_diff(wt, base)
        if not changed:
            raise ExecError("nothing fixable without a major jump or cooldown exception")
        digest = hashlib.sha256(git(wt, "diff", base).encode()).hexdigest()[:10]
        branch = f"vulnscan/{datetime.now(UTC):%Y%m%d}-{digest}"
        git(wt, "checkout", "-q", "-b", branch)
        git(wt, "add", "--", *changed)
        git(wt, "commit", "-q", "-m", f"security(deps): fix known-vulnerable dependency versions ({digest})",
            "-m", "Staged by the agentteams vulnscan executor (deterministic fix; lockfile/pin changes only).")
    except (ExecError, OSError) as exc:
        git(repo, "worktree", "remove", "--force", str(wt), check=False)
        raise ExecError(str(exc)) from exc
    result = {"repo": str(repo), "worktree": str(wt), "branch": branch, "base": base, "base_ref": head,
              "changed": changed, "plan": res.markdown()}
    _write_json(spool_dir() / "staged" / f"{_state_slug(repo)}.json", result)
    return result


def _sanitize(text: str, limit: int = 2000) -> str:
    text = text[:limit].replace("@", "@​").replace("```", "'''")
    return "\n".join("> " + ln for ln in text.splitlines()) or "> (none)"


def open_pr(repo: Path, rationale: str) -> dict[str, Any]:
    """Push the staged branch as a new ref and open a PR (never merge, never force).

    Args:
        repo: Repository root.
        rationale: The agent's rationale (untrusted; quoted and neutralised in the body).

    Returns:
        Result with the PR URL.

    Raises:
        ExecError: No staged change, the ref already exists, or push / PR creation failed.
    """
    staged_file = spool_dir() / "staged" / f"{_state_slug(repo)}.json"
    if not staged_file.is_file():
        raise ExecError("no staged fix for this repo — request stage-fix first")
    st = json.loads(staged_file.read_text(encoding="utf-8"))
    wt, branch = Path(st["worktree"]), st["branch"]
    if not branch.startswith("vulnscan/") or not wt.is_dir():
        raise ExecError("staged state is invalid or its worktree is gone")
    target_repo = _origin_repo(wt)   # before anything is pushed; never let gh pick a base repo
    if git(wt, "ls-remote", "--heads", "origin", branch, push=True).strip():
        raise ExecError(f"remote branch {branch} already exists — not overwriting (likely already proposed)")
    git(wt, "push", "origin", f"HEAD:refs/heads/{branch}", push=True)
    body = (f"Automated security fix staged by the agentteams **vulnscan executor** "
            f"(deterministic; dependency pins and lockfiles only).\n\n{st['plan']}\n\n"
            f"### Agent rationale (untrusted text, quoted)\n\n{_sanitize(rationale)}\n\n"
            "Tests run in CI. This PR is never merged automatically.\n")
    body_file = wt.parent / f"{wt.name}.pr.md"
    body_file.write_text(body, encoding="utf-8")
    base_branch = st["base_ref"].split("/", 1)[-1]
    proc = subprocess.run(["gh", "pr", "create", "--repo", target_repo, "--base", base_branch, "--head", branch,
                           "--title", f"security(deps): fix known-vulnerable dependencies ({branch.rsplit('-', 1)[-1]})",
                           "--body-file", str(body_file)], cwd=wt, capture_output=True, text=True,
                          env=_git_env() | {"GIT_CONFIG_GLOBAL": os.devnull}, timeout=300, check=False)
    if proc.returncode:
        raise ExecError(f"gh pr create failed: {(proc.stderr or proc.stdout).strip()[:400]}")
    url = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    return {"repo": str(repo), "branch": branch, "pr": url}


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
    proc = _git_proc(base, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir")
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
                             canonical=canonical_target)
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
    """Authorize and perform one granted op. Call inside ``ledger.locked()`` (see :func:`act`)."""
    try:
        if op not in grant.OPS_V1:
            raise ExecError(f"unknown or non-v1 operation {op!r}")
        canonical = str(_repo_root(target)) if op in ("stage-fix", "open-pr") else target
        g = authorize(op, canonical, request_id, cfg)
    except ExecError as exc:
        ledger.append("refused", op=op, target=target, request_id=request_id, detail=str(exc)[:1000])
        return {"status": "refused", "detail": str(exc)}
    try:
        if op == "stage-fix":
            result = stage_fix(Path(canonical), cfg)
        elif op == "open-pr":
            result = open_pr(Path(canonical), rationale)
        else:
            result = server_op(op, canonical)
    except (ExecError, OSError, subprocess.SubprocessError, ValueError) as exc:
        ledger.append("failed", grant_id=g["grant_id"], op=op, target=canonical, request_id=request_id,
                      detail=str(exc)[:1000])
        return {"status": "failed", "detail": str(exc)}
    slim = {k: v for k, v in result.items() if k != "plan"}
    ledger.append("done", grant_id=g["grant_id"], op=op, target=canonical, request_id=request_id,
                  detail=json.dumps(slim)[:2000], rationale=rationale[:2000])
    return {"status": "done", **slim}


def _read_request(path: Path) -> dict[str, Any]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if not os.fstat(fd).st_mode & 0o100000:
            raise ExecError("not a regular file")
        data = os.read(fd, MAX_REPORT_REQUEST_BYTES + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_REPORT_REQUEST_BYTES:
        raise ExecError("request too large")
    req = json.loads(data.decode("utf-8"))
    if not isinstance(req, dict) or set(req) - REQUEST_KEYS or not {"id", "op", "target"} <= set(req):
        raise ExecError("request does not match the schema")
    if len(data) > MAX_REQUEST_BYTES and req.get("op") != "report":
        raise ExecError("request too large")
    if not REQUEST_ID.match(str(req["id"])) or path.name != f"{req['id']}.json":
        raise ExecError("bad request id")
    if not all(isinstance(req.get(k, ""), str) for k in REQUEST_KEYS - {"payload"}):
        raise ExecError("request fields must be strings")
    _check_payload(req)
    return req


def _check_payload(req: dict[str, Any]) -> None:
    """Validate the per-op payload: required for and only allowed with internal ops."""
    op, payload = req["op"], req.get("payload")
    if op not in INTERNAL_OPS:
        if payload is not None:
            raise ExecError("payload is only allowed with internal ops")
        return
    if not isinstance(payload, dict):
        raise ExecError(f"op {op!r} requires a payload object")
    if op == "report":
        if set(payload) != {"markdown"} or not isinstance(payload["markdown"], str):
            raise ExecError("report payload must be {\"markdown\": str}")
    else:
        if set(payload) != {"kind", "evidence"} or not all(isinstance(v, str) for v in payload.values()):
            raise ExecError("escalate payload must be {\"kind\": str, \"evidence\": str}")
        if payload["kind"] not in AGENT_ESCALATION_KINDS:
            raise ExecError(f"escalation kind must be one of {AGENT_ESCALATION_KINDS}")


def quarantine_pending(reason: str) -> int:
    """Move every pending spool request aside unprocessed (``processed/stale-<id>.json``), with one ledger row.

    Called at the start of a run, under the run lock: requests a crashed or killed run left behind
    must not be served as the new run's (its report, its escalation count, its actions).

    Args:
        reason: Why (recorded on each ``refused`` row).

    Returns:
        How many requests were moved.
    """
    req_dir, done_dir = spool_dir() / "requests", spool_dir() / "processed"
    pending = sorted(req_dir.glob("*.json")) if req_dir.is_dir() else []
    if not pending:
        return 0
    done_dir.mkdir(parents=True, exist_ok=True)
    for path in pending:
        shutil.move(str(path), str(done_dir / f"stale-{path.name}"))
    with ledger.locked():   # one row, however many files: a planted pile cannot stall the ledger
        ledger.append("refused", op="(stale)", target=f"{len(pending)} request files", request_id="",
                      detail=reason[:300])
    return len(pending)


def _set_aside_overflow(cfg: dict[str, Any], overflow: list[Path], done_dir: Path, run_id: str | None) -> None:
    """Move requests over the per-pass cap aside unread and open (or keep) an escalation-flood."""
    for path in overflow:
        shutil.move(str(path), str(done_dir / f"overflow-{path.name}"))
    detail = f"{len(overflow)} spool requests over the per-pass cap of {MAX_REQUESTS_PER_PASS} were set aside unread"
    with ledger.locked():
        ledger.append("refused", op="(overflow)", target=f"{len(overflow)} request files", request_id="", detail=detail)
        esc, new = escalations.record_flood(detail, run_id=run_id or f"adhoc-{datetime.now(UTC).date().isoformat()}")
    if new:
        notify.send(cfg, "escalation", id=esc.id, kind=esc.kind)


def serve_requests(cfg: dict[str, Any], *, run_dir: Path | None = None, run_id: str | None = None,
                   deadline: float | None = None) -> list[dict[str, Any]]:
    """Process pending spool requests once — internal ops (escalations, reports) first.

    At most :data:`MAX_REQUESTS_PER_PASS` are read; any more are moved aside unread and open an
    ``escalation-flood`` (blocks everything until the operator resolves it). Processing stops at
    ``deadline``; what is left stays queued for the next pass.

    Args:
        cfg: Daily config.
        run_dir: The current run directory (passed to ``report``).
        run_id: The current run id (passed to ``escalate``).
        deadline: ``time.monotonic()`` value after which no further request is started.

    Returns:
        Results (also written to ``<spool>/results/<id>.json`` for the agents to read). A ledger
        integrity failure stops the batch with one refused result.
    """
    req_dir = spool_dir() / "requests"
    done_dir = spool_dir() / "processed"
    req_dir.mkdir(parents=True, exist_ok=True)
    done_dir.mkdir(parents=True, exist_ok=True)
    try:
        seen = ledger.consumed_request_ids()
    except ValueError as exc:
        return [{"status": "refused", "detail": f"ledger integrity failure — nothing processed: {exc}"}]
    files = sorted(req_dir.glob("*.json"))
    if len(files) > MAX_REQUESTS_PER_PASS:
        _set_aside_overflow(cfg, files[MAX_REQUESTS_PER_PASS:], done_dir, run_id)
        files = files[:MAX_REQUESTS_PER_PASS]
    valid: list[tuple[Path, dict[str, Any]]] = []
    for path in files:
        try:
            req = _read_request(path)
            if req["id"] in seen:
                raise ExecError("request id already used (replay)")
        except (ExecError, OSError, ValueError, UnicodeDecodeError) as exc:
            with ledger.locked():
                ledger.append("refused", op="(invalid)", target=path.name[:80], request_id="",
                              detail=f"malformed request: {exc}"[:500])
            shutil.move(str(path), str(done_dir / f"rejected-{path.name}"))
            continue
        valid.append((path, req))
    valid.sort(key=lambda pr: (pr[1]["op"] not in INTERNAL_OPS, pr[1]["op"] != "escalate"))
    out = []
    for path, req in valid:
        if deadline is not None and time.monotonic() >= deadline:
            break   # the rest stays queued; the run's cap is enforced on the container, not here
        res = {"id": req["id"], "op": req["op"], "target": req["target"],
               **act(req["op"], req["target"], cfg, rationale=req.get("rationale", ""), request_id=req["id"],
                     payload=req.get("payload"), run_dir=run_dir, run_id=run_id)}
        _write_json(spool_dir() / "results" / f"{req['id']}.json", res)
        shutil.move(str(path), str(done_dir / path.name))
        seen.add(req["id"])
        out.append(res)
    return out
