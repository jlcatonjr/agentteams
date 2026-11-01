"""Which repositories the executor's repo operations (``stage-fix``/``stage-major``, ``open-pr``) may never touch, or not yet.

Every check here only *narrows* what a grant allows; none can widen it. They run inside
:func:`agentteams.vulnscan.executor.authorize`, before a grant is looked up.

- **Grants first (:func:`refuse_outside_grants`).** Before anything runs git in a requested path, it must be
  an existing directory inside an active grant's ``repo_roots``. Only the filesystem and the signed grants
  are read, so a path no grant covers (``/root``, another user's home, a guessed path) never reaches git.

- **Protected repositories (P7).** The agentteams checkout this package runs from, ``agent.team_dir``,
  the ``governance_dir`` of every remediation- and publish-grant file (valid or not) and every publish
  grant's ops repository. A target is refused when it shares a git common dir with one of them (any
  worktree) **or** when one of its origins (:func:`target_origins`: the config file's ``origin`` URL
  and the push URL git itself would use) is the origin of one of them: a separate clone of a protected
  repository is the same repository. An identity that cannot be read is refused.
- **The publish ops repository** is refused by path as well (:func:`refuse_ops_repo`).
- **Enrolment level.** A repository enrolled in the project registry at a level other than ``remediate``
  is scanned only (:func:`refuse_scan_only`); it is matched by identity and by registered checkout path,
  and a checkout whose origin changed since enrolment is refused. A repository the registry does not
  know (explicit ``repo_roots``) keeps the grant as its only scope. An unreadable registry refuses every
  repo op. The registry only narrows: it never authorizes anything (grants do).
- **The project's brief** (:func:`refuse_by_brief`): a level set below ``remediate`` refuses both ops,
  ``delivery: plan`` refuses ``open-pr``, and ``base_branch`` is the PR base (:func:`brief_base_ref`).
- **Plan levels for the agent.** :func:`write_plan_levels` records, in the run directory, which fix
  plans name a scan-only repository, so the MCP server (inside the container, without the registry)
  can mark them.

``grant sign --from-registry`` uses :func:`protected_identities` to leave protected origins out of
the roots it proposes.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import agentteams
from agentteams.vulnscan import grant, mcp, ops, registry, repo_alias

#: Identity kinds that are paths on this machine, not an origin (never matched across clones).
_PATH_KINDS = ("local", "file", "dir")


def _exec_error(message: str) -> Exception:
    from agentteams.vulnscan import executor   # lazy: executor imports this module

    return executor.ExecError(message)


def refuse_outside_grants(op: str, target: str) -> str:
    """Refuse a repo-op target that no active grant covers, before anything runs git in it.

    The target is the agent's string. Only the filesystem is consulted (absolute path, symlinks
    resolved, an existing directory) and the signed grants are read; no subprocess runs. A target
    outside every granted ``repo_roots`` (``/root``, a guessed or another user's path) is refused here,
    so it never reaches git. :func:`agentteams.vulnscan.executor.authorize` still checks coverage and
    uses for the canonical repository afterwards.

    Args:
        op: A repo op (:data:`agentteams.vulnscan.ops.REPO_OPS`).
        target: The requested target.

    Returns:
        The resolved path that was checked (symlinks resolved). Callers use it, never ``target`` again,
        so what runs is what was checked.

    Raises:
        ExecError: Not an absolute path, not an existing directory, or outside every active grant.
    """
    if "\0" in target or not Path(target).is_absolute():
        raise _exec_error("repo targets must be absolute paths")
    try:
        real = Path(target).resolve()
        is_dir = real.is_dir()
    except (OSError, RuntimeError) as exc:   # RuntimeError: a symlink loop on older Pythons
        raise _exec_error(f"{target}: cannot be read ({exc})") from exc
    grants, problems = grant.load_active()
    if not any(grant.covers(g, op, str(real)) for g in grants):
        why = "; ".join(problems) if problems else "outside every active grant's repo_roots"
        raise _exec_error(f"not authorized: {op} {target}: {why}")
    if not is_dir:
        raise _exec_error(f"{target} is not an existing directory")
    return str(real)


def canonical_if_granted(target: str) -> str | None:
    """Canonicalise an escalation target, running git only in a path an active grant covers.

    ``escalate`` needs no grant and its target is the agent's string. A ``hosts.conf`` name, or an
    absolute path inside an active grant's ``repo_roots`` (symlinks resolved), goes to
    :func:`agentteams.vulnscan.executor.canonical_target`; anything else returns None without a
    subprocess, which makes the escalation's scope every target (fail-safe).

    Args:
        target: The agent's escalation target.

    Returns:
        The canonical target, or None.
    """
    from agentteams.cli.signed_ledger import path_within
    from agentteams.vulnscan import executor   # lazy: executor imports this module

    if target in executor._kit_hosts():
        return target
    if "\0" in target or not Path(target).is_absolute():
        return None
    try:
        real = str(Path(target).resolve())
    except (OSError, RuntimeError):
        return None
    grants, _problems = grant.load_active()
    if not any(path_within(real, root) for g in grants for root in g.get("repo_roots", [])):
        return None
    return executor.canonical_target(real)


def refuse_ops_repo(target: str) -> None:
    """Refuse repo ops on any publish grant's ops repository, valid or not (reports go there by ``publish`` only).

    Args:
        target: Canonical repository path.

    Raises:
        ExecError: ``target`` is an ops repository.
    """
    from agentteams.vulnscan import executor, publish   # lazy: both import this module's callers

    if {Path(target).resolve(), Path(executor.canonical_target(target) or target).resolve()} & publish.all_ops_repos():
        raise _exec_error(f"{target} is a publish grant's ops repository; remediation ops never touch it")


def repo_identity(path: Path) -> Path | None:
    """Return the git common dir of the repository containing ``path`` (shared by all its worktrees).

    Args:
        path: An existing absolute file or directory.

    Returns:
        The resolved common dir, or None when ``path`` is relative, missing or not in a repository.

    Raises:
        subprocess.TimeoutExpired: git did not answer within 30 s.
    """
    from agentteams.vulnscan import executor   # lazy: the hardened git lives there

    p = path.expanduser()
    if not p.is_absolute() or not p.exists():
        return None
    base = p if p.is_dir() else p.parent
    proc = executor.git_proc(base, "rev-parse", "--path-format=absolute", "--git-common-dir", timeout=30)
    out = proc.stdout.strip()
    return Path(out).resolve() if not proc.returncode and out else None


def origin_of(path: Path) -> str | None:
    """Return the origin identity (``host/owner/name``) of the checkout containing ``path``, or None.

    The checkout is the nearest directory at or above ``path`` with a ``.git`` entry. Git is not run
    (:func:`registry.identity` reads the config file). Identities that are machine paths (``local:``,
    ``file:``, ``dir:``) are not origins and give None: two checkouts without a host origin are compared
    by common dir instead (:func:`repo_identity`).

    Args:
        path: Any path (it need not exist).

    Returns:
        The origin identity, or None.
    """
    p = Path(os.path.realpath(path.expanduser()))
    top = next((d for d in (p, *p.parents) if os.path.lexists(d / ".git")), None)
    if top is None:
        return None
    ident = registry.identity(top)
    return None if registry.owner_of(ident) in _PATH_KINDS else ident


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

    Raises:
        GrantError: A grant file exists but cannot be read.
    """
    from agentteams.vulnscan import publish   # lazy: publish imports the executor, which imports this module

    out = [Path(agentteams.__file__).resolve().parent]
    agent = cfg.get("agent")
    team = agent.get("team_dir") if isinstance(agent, dict) else None
    if isinstance(team, str) and team:
        out.append(Path(team).expanduser())
    for d in (grant.grants_dir(), publish.grants_dir()):
        out.extend(Path(gov).expanduser() for gov in grant.unverified_values(d, "governance_dir"))
    out.extend(sorted(publish.all_ops_repos()))
    return out


def protected_identities(cfg: dict[str, Any], extra: tuple[str | Path, ...] = ()) -> dict[str, Path]:
    """Return the origin identities of the protected repositories, each with the path it came from.

    Args:
        cfg: Daily config.
        extra: Further protected paths (``grant sign`` adds the governance dir it is about to sign).

    Returns:
        ``{origin identity: protected path}`` (paths without a host origin are left out).

    Raises:
        GrantError: A grant file exists but cannot be read.
    """
    out: dict[str, Path] = {}
    for p in [*protected_repo_paths(cfg), *(Path(x).expanduser() for x in extra if x)]:
        ident = origin_of(p)
        if ident is not None:
            out.setdefault(ident, p)
    return out


def refuse_protected_repos(target: str, cfg: dict[str, Any]) -> None:
    """Refuse repo ops whose target is a protected repository: same common dir (any worktree) or same origin.

    Args:
        target: Canonical repository path.
        cfg: Daily config.

    Raises:
        ExecError: ``target`` is protected, or git did not answer in time (refusing rather than crashing the
            serving loop, V2-7).
        GrantError: A grant file exists but cannot be read (the caller refuses).
    """
    candidates = protected_repo_paths(cfg)
    try:
        mine = repo_identity(Path(target))
        protected = [(p, repo_identity(p)) for p in candidates] if mine is not None else []
    except subprocess.TimeoutExpired as exc:
        raise _exec_error(f"could not identify the repository of {target} or a protected path in time; "
                          "refusing") from exc
    for p, ident in protected:
        if ident == mine:
            raise _exec_error(f"{target} is the same repository as {p} (the agentteams checkout, agent.team_dir, "
                              "a grant's governance_dir or a publish ops repo); remediation ops never touch it")
    origins = target_origins(target)
    for p in candidates:
        origin = origin_of(p)
        if origin is not None and origin in origins:
            raise _exec_error(f"{target} is a clone of {origin}, the same repository as {p} (the agentteams "
                              "checkout, agent.team_dir, a grant's governance_dir or a publish ops repo); "
                              "remediation ops never touch it")


def target_origins(target: str) -> set[str]:
    """Return every origin identity of ``target``: its config file's ``origin`` URL, and the push URL git uses.

    Git itself applies ``remote.origin.pushurl`` and ``url.*.pushInsteadOf``, which the text reading of
    :func:`registry.identity` does not; a push goes wherever git says. Git runs hardened (hooks and
    fsmonitor off, no global or system config: :func:`executor.query_git`).

    Args:
        target: Canonical repository path.

    Returns:
        The ``host/owner/name`` identities (empty when neither names a host origin).

    Raises:
        ExecError: The repository's ``.git`` file, ``commondir`` or ``config`` is not a small regular file
            (an unreadable identity is refused, never read as "no origin"), or git did not answer in time.
    """
    from agentteams.vulnscan import executor   # lazy: executor imports this module

    p = Path(os.path.realpath(Path(target).expanduser()))
    top = next((d for d in (p, *p.parents) if os.path.lexists(d / ".git")), None)
    out: set[str] = set()
    if top is not None:
        ident, problem = registry.checked_identity(top)
        if problem:
            raise _exec_error(f"cannot read the identity of {target} ({problem}); refusing")
        if registry.owner_of(ident) not in _PATH_KINDS:
            out.add(ident)
    try:
        proc = executor.query_git(p if p.is_dir() else p.parent, "remote", "get-url", "--push", "origin", timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _exec_error(f"could not ask git for the push URL of {target}; refusing") from exc
    if proc.returncode and out:   # it has an origin in its config, but git would not say where it pushes
        raise _exec_error(f"git could not resolve the push URL of {target}; refusing")
    pushed = registry.origin_identity(proc.stdout.strip()) if not proc.returncode else None
    return out | ({pushed} if pushed else set())


def _enrolled_checkout(entries: list[registry.Entry], real: Path) -> tuple[registry.Entry, Path] | None:
    """Return the entry and checkout path whose registered checkout is, or most closely contains, ``real``."""
    best: tuple[registry.Entry, Path] | None = None
    for e in entries:
        for c in {e.fix_checkout, *e.checkouts}:
            co = Path(os.path.realpath(Path(c).expanduser()))
            if (co == real or co in real.parents) and (best is None or len(co.parts) > len(best[1].parts)):
                best = (e, co)
    return best


def refuse_scan_only(target: str) -> None:
    """Refuse repo ops on a repository the registry enrols below ``remediate``, or whose identity moved.

    The entry is found by the target's identity and by its registered checkout paths (realpath containment).
    A registered checkout whose origin is no longer the enrolled one is refused ("re-run enroll"), as is a
    different repository nested inside a registered checkout that the registry does not know on its own.

    Args:
        target: Canonical repository path.

    Raises:
        ExecError: The registry enrols ``target`` at ``scan``, ``off`` or ``unanswered``; its identity differs
            from the enrolled one or cannot be read; or the registry cannot be read (fail closed).
    """
    try:
        entries = registry.load()
    except (OSError, ValueError) as exc:
        raise _exec_error(f"cannot read the project registry ({exc}); refusing repo operations") from exc
    real = Path(os.path.realpath(Path(target).expanduser()))
    ident, problem = registry.checked_identity(real)
    if problem:
        raise _exec_error(f"cannot read the identity of {target} ({problem}); refusing")
    entry = next((e for e in entries if e.identity == ident), None)
    by_path = _enrolled_checkout(entries, real)
    if by_path is not None and by_path[0].identity != ident:
        enrolled, co = by_path
        if co == real:
            raise _exec_error(f"{target}: origin changed since enrolment (enrolled as {enrolled.identity}, now "
                              f"{ident}); re-run `python -m agentteams.vulnscan enroll`")
        if entry is None:
            raise _exec_error(f"{target} is a different repository ({ident}) inside the enrolled checkout {co}; "
                              "enrol it on its own")
    entry = entry or (by_path[0] if by_path else None)
    if entry is not None and entry.level != "remediate":
        raise _exec_error(f"{target} is enrolled at {entry.level}: scanning only")


def refuse_plan_only(op: str, target: str, editable: re.Pattern[str]) -> None:
    """Refuse a ``stage-fix`` on a repository whose only fix plans are plan-only (``manifests.PLAN_ONLY_LOCKS``:
    ``Cargo.lock``, ``go.mod``, ``pnpm-lock.yaml``, ``bun.lock``, ``Gemfile.lock``, ``gradle.lockfile``,
    ``packages.lock.json``).

    Checked before a grant use is spent: ``stage-fix`` could only fail there, with a reason that does not apply
    ("nothing fixable without a major jump"), and the retry guard lets it try again whenever the plan changes.
    A repository with any manifest ``stage-fix`` may edit, or with no plan-only lockfile, is left to the usual checks.

    Args:
        op: Requested operation.
        target: Canonical repository path.
        editable: The executor's pattern of files a staged fix may change (``executor.ALLOWED_CHANGED``).

    Raises:
        ExecError: ``op`` is ``stage-fix`` and every manifest the scan reads there is a plan-only lockfile.
    """
    if op not in ops.STAGE_OPS:   # stage-major too: it edits the same files (and package.json)
        return
    from agentteams.vulnscan import manifests, project_policy   # lazy: only stage-fix walks the repository

    repo = Path(target)
    excl = project_policy.read_policy(repo).exclude_paths
    found = [m.relative_to(repo).as_posix() for m in manifests.discover(repo)]
    found = [rel for rel in found if not (excl and project_policy.is_excluded(rel, excl))]
    plan_only = [rel for rel in found if rel.rsplit("/", 1)[-1] in manifests.PLAN_ONLY_LOCKS]
    if plan_only and not any(editable.search(rel) for rel in found):
        raise _exec_error(f"{repo.name}: only plan-only fixes here ({len(plan_only)} plan-only lockfile(s) such as "
                          "Cargo.lock or go.mod); stage-fix never edits them: run the fix plan's commands by hand")


def refuse_repo_op(op: str, target: str, editable: re.Pattern[str]) -> None:
    """Apply the per-repository refusals, in order: :func:`refuse_scan_only`, :func:`refuse_by_brief`,
    :func:`refuse_plan_only`.

    Args:
        op: Requested operation.
        target: Canonical repository path.
        editable: The executor's pattern of files a staged fix may change.

    Raises:
        ExecError: Whichever refusal applies first.
    """
    refuse_scan_only(target)
    refuse_by_brief(op, target)
    refuse_plan_only(op, target, editable)


def refuse_by_brief(op: str, target: str) -> None:
    """Refuse what the project's own brief does not ask for (narrowing only: a brief never widens a grant).

    Args:
        op: ``stage-fix``, ``stage-major``, ``open-pr`` or ``close-superseded`` (a plan-only brief does not refuse
            closing).
        target: Canonical repository path.

    Raises:
        ExecError: The brief's ``vulnerability_scanning.level`` is set and is not ``remediate``, or ``op`` is
            ``open-pr`` and its ``delivery`` is ``plan``.
    """
    from agentteams.vulnscan import project_policy   # lazy: briefs are read only for repo ops

    pol = project_policy.read_policy(Path(target))
    if pol.level is not None and pol.level != "remediate":
        raise _exec_error(f"{target}: the project's brief sets vulnerability_scanning.level {pol.level}, not "
                          "remediate; it asks for no fixes")
    if op == "open-pr" and pol.delivery == "plan":
        raise _exec_error(f"{target}: project asks for plans only (vulnerability_scanning.delivery: plan); no PR")


def brief_base_ref(repo: Path) -> str | None:
    """Return ``origin/<base_branch>`` when the project's brief sets ``base_branch``, else None.

    Call after fetching origin: the branch must exist there (its remote-tracking ref), or the fix is refused
    rather than staged on another base.

    Args:
        repo: Repository root.

    Returns:
        The base ref, or None (the caller uses ``origin/HEAD``).

    Raises:
        ExecError: The brief names a branch origin does not have.
    """
    from agentteams.vulnscan import executor, project_policy

    branch = project_policy.read_policy(repo).base_branch
    if not branch:
        return None
    proc = executor.git_proc(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}^{{commit}}",
                             timeout=60)
    if proc.returncode or not proc.stdout.strip():
        raise _exec_error(f"the brief's base_branch {branch!r} does not exist on origin; refusing")
    return f"origin/{branch}"


def require_remote_branch(wt: Path, branch: str) -> None:
    """Refuse unless origin has ``branch`` now (``ls-remote``, before a PR is opened against it).

    Args:
        wt: The executor's worktree.
        branch: The PR base branch.

    Raises:
        ExecError: origin has no such branch, or ``ls-remote`` failed.
    """
    from agentteams.vulnscan import executor

    ref = f"refs/heads/{branch}"
    if not any(ln.endswith(f"\t{ref}") for ln in executor.git(wt, "ls-remote", "origin", ref, push=True).splitlines()):
        raise _exec_error(f"base branch {branch!r} does not exist on origin; refusing to open the PR")


_PLAN_NPM = re.compile(r"^## npm: `([^`\n]+)`$")


def stage_major_packages(plan: str, repo: Path) -> list[str]:
    """Return the ``major-held`` packages of a fix plan that ``stage-major`` could take, or [] when it has a ``fix`` row.

    The rules are :func:`agentteams.vulnscan.fix.fix_path`'s with ``allow_major``: a requirements pin (the plan's
    top table) is taken; an npm package is taken only when the ``package.json`` beside its lockfile lists it
    directly (:func:`.npm_major.direct_section`) and the lockfile is not in npm workspaces. A transitive-only or
    workspace row stays held, as does every row of a symlinked or unreadable ``package.json``. Any ``fix`` row
    returns [] (the non-major fix is staged first). The executor re-checks all of it when it stages.

    Args:
        plan: The fix plan's Markdown (:meth:`agentteams.vulnscan.fix.FixResult.markdown`).
        repo: The plan's repository (its ``stage-fix`` target).

    Returns:
        Package names, in plan order, without duplicates.
    """
    from agentteams.vulnscan import fix, npm_major   # lazy: only the host's plan marking reads manifests

    found: list[str] = []
    section: str | None = ""   # "": the requirements table; a lockfile path: an npm section; None: anything else
    root = repo.resolve()
    for line in plan.splitlines():
        if line.startswith("## "):
            m = _PLAN_NPM.fullmatch(line)
            section = m.group(1) if m else None
            continue
        cells = [c.strip() for c in line.split("|")[1:]] if line.startswith("|") and section is not None else []
        name_at, action_at = (1, 4) if section == "" else (0, 3)   # File | Package | ... vs Package | ...
        if len(cells) <= action_at:
            continue
        name, action = cells[name_at], cells[action_at]
        if action == "fix":
            return []
        if action != "major-held":
            continue
        if section:
            lock = (root / section).resolve()
            pkg = lock.parent / "package.json"
            if not lock.is_relative_to(root) or pkg.is_symlink() or fix._npm_workspaces(lock):
                continue
            try:
                manifest = json.loads(pkg.read_text(encoding="utf-8"))
                direct = npm_major.direct_section(manifest, name) if isinstance(manifest, dict) else ""
            except (OSError, ValueError):
                direct = ""
            if not direct:
                continue
        if name not in found:
            found.append(name)
    return found


def _major_next(plans: list[tuple[str, str]]) -> dict[str, dict[str, Any]]:
    """Return ``{target: {"since", "packages"}}`` for the plans whose next step is ``stage-major`` (see the caller)."""
    from agentteams.vulnscan import executor, ledger, retry_guard   # lazy: the executor imports this module

    try:
        rows = ledger.read()
    except (OSError, ValueError):   # an untrusted ledger marks nothing; the executor refuses on it anyway
        return {}
    staged = {r.rstrip("/") for rec in executor.staged_records() if isinstance(r := rec.get("repo"), str)}
    out: dict[str, dict[str, Any]] = {}
    for target, text in plans:
        since = "" if target.rstrip("/") in staged else retry_guard.major_next_since(target, rows)
        packages = stage_major_packages(text, Path(target)) if since else []
        if packages:
            out[target] = {"since": since, "packages": packages}
    return out


def write_plan_levels(run_dir: Path) -> Path:
    """Record which of the run's fix plans name a repository the executor will refuse as scan-only.

    The MCP server runs in the agent container without the registry; it reads this file to mark those
    plans (:func:`agentteams.vulnscan.mcp.Server`). The executor enforces the level either way.

    Args:
        run_dir: The run directory (host side, before the agent starts).

    It also records (``major_next``) each remediate-level plan whose next step is ``stage-major``, because the
    agent never sees in its own run the result of a ``stage-fix`` it requested (the executor serves requests
    after it): the repository's last ``stage-fix`` found nothing fixable without a major jump and is held at
    this plan (:func:`.retry_guard.major_next_since`), no ``stage-major`` followed, no fix is staged for it, and
    the plan has no ``fix`` row but has ``major-held`` rows ``stage-major`` can take (:func:`stage_major_packages`).

    Returns:
        The file written: ``{"scan_only": {target: level}, "registry_problem": str,
        "major_next": {target: {"since": date, "packages": [name, ...]}}}``.
    """
    try:
        entries = registry.load()
        problem = ""
    except (OSError, ValueError) as exc:
        entries, problem = [], str(exc)[:300]
    scan_only: dict[str, str] = {}
    remediate: list[tuple[str, str]] = []
    for plan in sorted([*run_dir.glob("fixplan-*.md"), *run_dir.glob("fix-*.md")]):
        text = plan.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        m = repo_alias.PLAN_TARGET.fullmatch(lines[2]) if len(lines) > 2 else None
        entry = registry.find(entries, Path(m.group(1))) if m else None
        if m and entry is not None and entry.level != "remediate":
            scan_only[m.group(1)] = entry.level
        elif m:
            remediate.append((m.group(1), text))
    major_next = {} if problem else _major_next(remediate)
    path = run_dir / mcp.PLAN_LEVELS_FILE
    path.write_text(json.dumps({"scan_only": scan_only, "registry_problem": problem, "major_next": major_next},
                               indent=2), encoding="utf-8")
    return path
