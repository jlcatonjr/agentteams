"""branch_cleanup.py — guarded branch deletion (``--branch-cleanup`` and ``--branch-post-merge``).

The executor half of the branch lifecycle (:mod:`agentteams.branch_inventory` is the read-only
half). Two modes, two authorizations, one set of guards:

* ``--branch-cleanup PLAN.json`` executes a deletion plan written by ``--branch-inventory``. It
  is a bulk/remote destructive action, so it needs a recorded ``@security`` clearance (C-5),
  checked by the existing destructive-action gate under the action id
  ``branch-cleanup:<the plan's sha256>``. The clearance therefore covers exactly
  one plan, and the gate consumes it, so it cannot be replayed.
* ``--branch-post-merge BRANCH`` deletes the one branch a session just merged: its tip must be
  the second parent of the push remote's default-branch tip (a ``--no-ff`` merge), proven by
  ancestry (never by the PR test). It runs under an operator **Ed25519-signed, time-bounded
  ``branch-delete`` capability grant** (:mod:`agentteams.cli.grants`), never during an
  unretracted ``@security`` HALT (C-2), and each use is counted.

Guards, both modes: re-inventory just before acting and skip any ref whose tip or verdict
drifted; re-check ancestry (or re-query the PR and confirm ``refs/pull/<n>/head``) per ref; local
deletes use ``git branch -d`` (ancestry) or a compare-and-delete ``git update-ref -d <ref> <sha>``
(Merged-by-PR, where ``-d`` would refuse), never ``-D``; remote deletes are one at a time with
``--force-with-lease`` to the audited SHA; tags are created only if absent locally and on the
remote, never moved, and confirmed with ``ls-remote`` before their branch goes; the first failure
stops the run, never retried with force. Every deletion is recorded in the hash-chained ledger
``references/branch-deletions.log.csv`` — an ``attempt`` row is appended **before** the command
runs, then its outcome with the restore command.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentteams.branch_inventory import (
    GitHubAPI,
    InventoryConfig,
    InventoryError,
    build_inventory,
    git,
    git_ok,
    plan_digest,
    pr_ref_matches,
)

#: The deletion ledger, relative to the repository root.
LEDGER_REL = "references/branch-deletions.log.csv"

LEDGER_COLUMNS: tuple[str, ...] = (
    "timestamp", "run_id", "mode", "authorization", "ref_type", "branch", "old_sha", "via",
    "event", "detail", "restore", "prev_digest",
)

#: A ``@security`` HALT on this action id blocks both modes (HALTs match their exact id, so the
#: per-plan ``branch-cleanup:<sha256>`` id alone would not see a broad HALT).
HALT_FAMILY = "branch-delete"

#: Exit codes: 0 done (or dry run), 1 refused/failed, 3 no authorization (operator step needed).
EXIT_OK, EXIT_FAIL, EXIT_UNAUTHORIZED = 0, 1, 3


class CleanupError(RuntimeError):
    """Raised when a plan, authorization or ledger check fails (fail closed)."""


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


def _row_digest(row: dict[str, str]) -> str:
    joined = "\x1f".join(row.get(c, "") for c in LEDGER_COLUMNS)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def read_ledger(repo: Path) -> list[dict[str, str]]:
    """Read and chain-verify the deletion ledger.

    Args:
        repo: Repository root.

    Returns:
        The ledger rows (empty when the file is absent).

    Raises:
        CleanupError: The header is wrong or a ``prev_digest`` link is broken (a row was edited,
            removed or reordered).
    """
    path = repo / LEDGER_REL
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if tuple(reader.fieldnames or ()) != LEDGER_COLUMNS:
            raise CleanupError(f"{LEDGER_REL}: unexpected header {reader.fieldnames}")
        rows = [{k: (v or "") for k, v in r.items()} for r in reader]
    prev = ""
    for i, row in enumerate(rows, start=2):
        if row["prev_digest"] != prev:
            raise CleanupError(f"{LEDGER_REL}: hash chain broken at line {i} (fail closed)")
        prev = _row_digest(row)
    return rows


def append_ledger(repo: Path, row: dict[str, str]) -> None:
    """Append one chained row to the ledger, creating it with a header when absent.

    Args:
        repo: Repository root.
        row: Values for :data:`LEDGER_COLUMNS` (``prev_digest`` is filled in).

    Returns:
        None.

    Raises:
        CleanupError: The existing ledger fails its chain check (nothing is appended).
    """
    rows = read_ledger(repo)
    path = repo / LEDGER_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {c: str(row.get(c, "")) for c in LEDGER_COLUMNS}
    record["prev_digest"] = _row_digest(rows[-1]) if rows else ""
    new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(LEDGER_COLUMNS))
        if new:
            writer.writeheader()
        writer.writerow(record)


def grant_uses(repo: Path, grant_id: str) -> int:
    """Count the uses of ``grant_id``: distinct runs that attempted a deletion under it.

    One ``--branch-post-merge`` run (the local and the remote ref of one branch) is one use.

    Args:
        repo: Repository root.
        grant_id: The grant id.

    Returns:
        The number of distinct runs recorded against the grant.

    Raises:
        CleanupError: The ledger fails its chain check.
    """
    auth = f"grant:{grant_id}"
    return len({r["run_id"] for r in read_ledger(repo)
                if r["authorization"] == auth and r["event"] == "attempt"})


# ---------------------------------------------------------------------------
# Plan loading and re-verification
# ---------------------------------------------------------------------------


def load_plan(path: Path) -> dict[str, Any]:
    """Load a deletion plan and check that its sha256 still matches its contents.

    Args:
        path: ``branch-deletion-plan.json``.

    Returns:
        The plan.

    Raises:
        CleanupError: Unreadable, or edited after it was written.
    """
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CleanupError(f"cannot read plan {path}: {exc}") from exc
    if plan_digest(plan) != plan.get("sha256"):
        raise CleanupError(f"plan {path} was modified after it was written (sha256 mismatch)")
    return plan


def clearance_action(plan: dict[str, Any]) -> str:
    """The security-decision action id that clears ``plan``.

    The full digest, not a prefix: the id is the only thing binding a clearance to one plan.

    Args:
        plan: A deletion plan.

    Returns:
        ``branch-cleanup:<plan sha256>``.
    """
    return f"branch-cleanup:{plan['sha256']}"


_Key = tuple[str, str]


def _current_verdicts(
    cfg: InventoryConfig, api: GitHubAPI | None,
) -> tuple[dict[_Key, dict[str, Any]], dict[_Key, dict[str, Any]], dict[str, Any]]:
    inv = build_inventory(cfg, api=api)
    refs = {(r["ref_type"], r["branch"]): r for r in inv["refs"]}
    items = {(i["ref_type"], i["branch"]): i for i in inv["plan"]["items"]}
    return refs, items, inv["meta"]


def _drift(item: dict[str, Any], refs: dict[_Key, dict[str, Any]],
           items: dict[_Key, dict[str, Any]]) -> str:
    """Why ``item`` may not run now, or ''. The fresh inventory must rebuild it byte-for-byte —
    tip, state, via, tag, and the exact ``run``/``restore`` commands (security review C4)."""
    key = (item["ref_type"], item["branch"])
    rec = refs.get(key)
    if rec is None:
        return "ref no longer exists"
    if rec["tip"] != item["tip"]:
        return f"tip moved {item['tip'][:7]} -> {rec['tip'][:7]}"
    fresh = items.get(key)
    if fresh is None:
        return f"no longer deletable (state {rec['state']}, holds {rec['holds']})"
    if fresh != item:
        changed = sorted(k for k in set(fresh) | set(item) if fresh.get(k) != item.get(k))
        return f"plan item differs from the fresh inventory ({', '.join(changed)})"
    return ""


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _recheck(repo: Path, remote: str, default_branch: str, item: dict[str, Any],
             api: GitHubAPI | None) -> str:
    """Just-in-time per-ref re-check. Returns a refusal reason, or '' when it may proceed."""
    default_ref = f"refs/remotes/{remote}/{default_branch}"
    if item["via"] == "ancestor":
        ok = git_ok(repo, "merge-base", "--is-ancestor", item["tip"], default_ref)
        return "" if ok else "tip is no longer an ancestor of the default branch"
    number = item["via"][3:]
    if api is None:
        return "Merged-by-PR needs the GitHub API, which is unavailable"
    verdict, pr = api.get(f"pulls/{number}")
    if verdict != "ok" or not isinstance(pr, dict):
        return f"PR #{number} could not be re-queried"
    if (pr.get("head") or {}).get("sha") != item["tip"] or not pr.get("merged_at"):
        return f"PR #{number} head moved or is not merged"
    if not pr_ref_matches(repo, remote, number, item["tip"]):
        return f"refs/pull/{number}/head is missing or differs on {remote}: refusing"
    return ""


def _remote_tag(repo: Path, remote: str, tag: str) -> tuple[bool, str]:
    """``(exists, peeled commit)`` for ``refs/tags/<tag>`` on ``remote``."""
    exists, peeled = False, ""
    for line in git(repo, "ls-remote", "--tags", remote, check=False).splitlines():
        sha, _, ref = line.partition("\t")
        if ref == f"refs/tags/{tag}":
            exists = True
        elif ref == f"refs/tags/{tag}^{{}}":
            peeled = sha
    return exists, peeled


def _ensure_tag(repo: Path, remote: str, tag: str, sha: str) -> str:
    """Create and push an annotated tag on ``sha`` unless it exists. Returns an error or ''.

    A tag that already exists (locally or on the remote) on another commit is never moved.
    """
    try:
        return _ensure_tag_or_raise(repo, remote, tag, sha)
    except InventoryError as exc:  # a failed tag/push/ls-remote is a recorded failure, not a crash
        return str(exc)


def _ensure_tag_or_raise(repo: Path, remote: str, tag: str, sha: str) -> str:
    remote_has, remote_peeled = _remote_tag(repo, remote, tag)
    if remote_has and remote_peeled != sha:
        return f"tag {tag} already exists on {remote} at {remote_peeled[:7] or '?'}; never moved"
    if git_ok(repo, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}"):
        local = git(repo, "rev-parse", f"refs/tags/{tag}^{{commit}}", check=False)
        if local != sha:
            return f"tag {tag} already exists locally on {local[:7]}; tags are never moved"
    else:
        git(repo, "tag", "-a", tag, "-m", f"archive of branch tip {sha[:7]}", sha)
    if not remote_has:
        git(repo, "push", remote, f"refs/tags/{tag}")
    if _remote_tag(repo, remote, tag) != (True, sha):
        return f"tag {tag} not confirmed on {remote} at {sha[:7]}"
    return ""


def _delete(repo: Path, remote: str, item: dict[str, Any]) -> str:
    """Run the deletion for one item. Returns an error or ''."""
    b, sha = item["branch"], item["tip"]
    if item["ref_type"] == "remote":
        args = ["push", "--porcelain", f"--force-with-lease=refs/heads/{b}:{sha}",
                "--delete", remote, f"refs/heads/{b}"]
    elif item["via"] == "ancestor":
        args = ["branch", "-d", "--", b]
    else:
        args = ["update-ref", "-d", f"refs/heads/{b}", sha]
    try:
        git(repo, *args)
    except InventoryError as exc:
        return str(exc)
    return ""


def execute_items(repo: Path, plan: dict[str, Any], items: list[dict[str, Any]], *,
                  mode: str, authorization: str, api: GitHubAPI | None,
                  run_id: str) -> tuple[int, list[str]]:
    """Delete ``items`` (local refs first, then remote refs); stop at the first failure.

    Local first: ``git branch -d`` then checks against the branch's upstream while it still
    exists, instead of against a ``HEAD`` that may lag the remote default branch.

    Args:
        repo: Repository root.
        plan: The plan (for remote and default branch).
        items: Re-verified plan items.
        mode: ``cleanup`` or ``post-merge`` (ledger).
        authorization: ``clearance:<action id>`` or ``grant:<grant id>`` (ledger).
        api: GitHub client for Merged-by-PR re-checks.
        run_id: Ledger run id.

    Returns:
        ``(exit code, report lines)``.
    """
    remote, default = plan["push_remote"], plan["default_branch"]
    ordered = sorted(items, key=lambda i: (i["ref_type"] != "local", i["branch"]))
    report: list[str] = []

    def log(item: dict[str, Any], event: str, detail: str = "") -> None:
        append_ledger(repo, {
            "timestamp": _now(), "run_id": run_id, "mode": mode, "authorization": authorization,
            "ref_type": item["ref_type"], "branch": item["branch"], "old_sha": item["tip"],
            "via": item["via"], "event": event, "detail": detail,
            "restore": " && ".join(item["restore"]),
        })

    for item in ordered:
        label = f"{item['ref_type']} {item['branch']} ({item['tip'][:7]}, {item['via']})"
        reason = _recheck(repo, remote, default, item, api)
        if reason:
            log(item, "skipped", reason)
            report.append(f"  ✗  {label}: {reason} — stopping")
            return EXIT_FAIL, report
        if item.get("tag"):
            err = _ensure_tag(repo, remote, item["tag"], item["tip"])
            if err:
                log(item, "failed", err)
                report.append(f"  ✗  {label}: {err} — stopping")
                return EXIT_FAIL, report
            log(item, "tagged", item["tag"])
        log(item, "attempt")
        err = _delete(repo, remote, item)
        if err:
            log(item, "failed", err)
            report.append(f"  ✗  {label}: {err} — stopping (never retried with force)")
            return EXIT_FAIL, report
        log(item, "deleted")
        report.append(f"  ✓  deleted {label}; restore: {' && '.join(item['restore'])}")
    return EXIT_OK, report


def run_cleanup(plan_path: Path, cfg: InventoryConfig, *, team_dir: Path, apply: bool,
                api: GitHubAPI | None = None) -> tuple[int, list[str]]:
    """``--branch-cleanup``: re-verify a plan, then (with ``apply``) execute it under clearance.

    Args:
        plan_path: The plan written by ``--branch-inventory``.
        cfg: Inventory inputs for the re-inventory (same remote/default/thresholds).
        team_dir: The team agents dir whose ``references/security-decisions.log.csv`` holds the
            ``branch-cleanup:<plan sha256>`` clearance.
        apply: Execute; otherwise a dry run that changes nothing (and consumes nothing).
        api: GitHub client override (tests).

    Returns:
        ``(exit code, report lines)``.

    Raises:
        CleanupError: The plan is unreadable, edited, or for another repository.
        InventoryError: The re-inventory cannot run (not a repo, no remote/default branch).
    """
    from agentteams.cli import security_gate

    plan = load_plan(plan_path)
    if Path(plan["repo"]).resolve() != cfg.repo.resolve():
        raise CleanupError(f"plan is for {plan['repo']}, not {cfg.repo.resolve()}")
    cfg.push_remote, cfg.default_branch = plan["push_remote"], plan["default_branch"]
    action = clearance_action(plan)
    current, fresh_items, meta = _current_verdicts(cfg, api)
    report = [f"Plan {plan['sha256'][:12]}: {len(plan['items'])} item(s); clearance action "
              f"{action!r}; api {meta['api_status']}"]
    ready, drifted = [], []
    for item in plan["items"]:
        why = _drift(item, current, fresh_items)
        (drifted if why else ready).append((item, why))
    for item, why in drifted:
        report.append(f"  -  skip {item['ref_type']} {item['branch']}: {why}")
    if not apply:
        for item, _ in ready:
            report.append(f"  ·  would run: {' ; '.join(item['run'])}")
        report.append("Dry run: nothing changed. Re-run with --apply after @security records "
                      f"a PASS for action {action!r}.")
        return EXIT_OK, report
    try:  # C-2: one HALT on the family id stops every branch deletion, whatever the plan
        security_gate._assert_no_unretracted_halt(team_dir, action=HALT_FAMILY)
    except RuntimeError as exc:
        report.append(f"Refused: an unretracted @security HALT is in force (C-2): {exc}")
        return EXIT_FAIL, report
    ok, reason = security_gate.check_clearance(team_dir, action=action)
    if not ok:
        report.append(f"Refused: no usable clearance for {action!r}: {reason}")
        return EXIT_UNAUTHORIZED, report
    try:
        security_gate._assert_destructive_action_allowed(team_dir, action=action)  # consumes it
    except RuntimeError as exc:
        raise CleanupError(f"clearance for {action!r} could not be consumed: {exc}") from exc
    if api is None and meta.get("github_repo"):
        api = GitHubAPI(meta["github_repo"])
    code, lines = execute_items(cfg.repo.resolve(), plan, [i for i, _ in ready], mode="cleanup",
                                authorization=f"clearance:{action}", api=api,
                                run_id=f"cleanup-{plan['sha256'][:12]}-{_now()}")
    return code, report + lines


# ---------------------------------------------------------------------------
# Post-merge (grant)
# ---------------------------------------------------------------------------


def _team_id(team_dir: Path, explicit: str | None) -> str:
    """The grant holder id: ``--team-id``, else the build log's ``project_name`` slug.

    Mirrors ``analyze.build_manifest``'s derivation, minus the brief (this mode reads none).
    """
    if explicit:
        return explicit
    from agentteams._utils import _slugify

    try:
        log = json.loads((team_dir / "references" / "build-log.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CleanupError(f"cannot derive the team id from {team_dir}: {exc}; pass --team-id") \
            from exc
    team = _slugify(str(log.get("project_name", ""))).lstrip("-")
    if not team:
        raise CleanupError("build-log.json has no project_name; pass --team-id")
    return team


def find_branch_grant(repo: Path, *, team_dir: Path, team_id: str,
                      now: datetime | None = None) -> tuple[dict[str, str] | None, list[str]]:
    """Return the first valid, unexhausted ``branch-delete`` grant held by ``team_id`` for ``repo``.

    Validity is :func:`agentteams.cli.grants.validate_grant` (Ed25519 signature against the team
    dir's verify-key store, approver on its roster, unexpired) plus a use count from the deletion
    ledger below ``max_uses``.

    Args:
        repo: Repository root (holds the grant ledger and the deletion ledger).
        team_dir: Team agents dir (verify keys, roster).
        team_id: Grant holder id.
        now: Reference time for expiry.

    Returns:
        ``(grant or None, reasons each candidate was rejected)``.
    """
    from agentteams.cli import grants

    reasons: list[str] = []
    try:
        rows = grants._read_grant_rows(repo)
    except grants.GrantError as exc:
        return None, [str(exc)]
    for row in rows:
        if not grants.grant_covers(row, holder_team=team_id, target_path=str(repo),
                                   op=grants.BRANCH_DELETE_OP):
            continue
        try:
            grants.validate_grant(row, team_dir=team_dir, now=now)
        except grants.GrantError as exc:
            reasons.append(str(exc))
            continue
        used = grant_uses(repo, row["grant_id"])
        if used >= int(row["max_uses"]):
            reasons.append(f"grant {row['grant_id']!r} is used up ({used}/{row['max_uses']})")
            continue
        return row, reasons
    return None, reasons or ["no branch-delete grant covers this repository"]


def run_post_merge(branch: str, cfg: InventoryConfig, *, team_dir: Path, team_id: str | None,
                   apply: bool, api: GitHubAPI | None = None) -> tuple[int, list[str]]:
    """``--branch-post-merge``: delete the branch just merged with ``--no-ff``, under a grant.

    Args:
        branch: The merged feature branch.
        cfg: Inventory inputs.
        team_dir: Team agents dir (security log, verify-key store, roster, build log).
        team_id: Grant holder id (default: slug of the build log's ``project_name``).
        apply: Execute; otherwise report only.
        api: GitHub client override (tests).

    Returns:
        ``(exit code, report lines)``. Exit 3 means "no valid grant — use --branch-cleanup
        under a per-run clearance".

    Raises:
        CleanupError: The team id cannot be derived, or the deletion ledger is tampered.
        InventoryError: The inventory cannot run.
    """
    from agentteams.cli import security_gate

    repo = cfg.repo.resolve()
    inv = build_inventory(cfg, api=api)
    meta = inv["meta"]
    default_ref = f"refs/remotes/{meta['push_remote']}/{meta['default_branch']}"
    parents = git(repo, "rev-list", "--parents", "-n", "1", default_ref).split()
    if len(parents) != 3:
        return EXIT_FAIL, [f"Refused: {meta['default_branch']} tip {parents[0][:7]} is not a "
                           "two-parent merge; the grant covers only a --no-ff merge just pushed"]
    second = parents[2]
    recs = [r for r in inv["refs"] if r["branch"] == branch]
    if not recs:
        return EXIT_FAIL, [f"Refused: no local or {meta['push_remote']} branch named {branch!r}"]
    lines: list[str] = []
    for rec in recs:
        if rec["tip"] != second:
            return EXIT_FAIL, [f"Refused: {rec['ref_type']} {branch} tip {rec['tip'][:7]} is not "
                               f"the second parent ({second[:7]}) of the merge at "
                               f"{meta['default_branch']}"]
        if rec["state"] != "merged" or rec["action"] != "delete" or rec["via"] != "ancestor":
            return EXIT_FAIL, [f"Refused: {rec['ref_type']} {branch} is {rec['state']} with holds "
                               f"{rec['holds'] or 'none'}; resolve or use --branch-cleanup"]
    items = [i for i in inv["plan"]["items"] if i["branch"] == branch]
    try:
        security_gate._assert_no_unretracted_halt(team_dir, action=HALT_FAMILY)
    except RuntimeError as exc:
        return EXIT_FAIL, [f"Refused: an unretracted @security HALT is in force (C-2): {exc}"]
    grant, reasons = find_branch_grant(repo, team_dir=team_dir, team_id=_team_id(team_dir, team_id))
    for item in items:
        lines.append(f"  ·  {' ; '.join(item['run'])}")
    if grant is None:
        return EXIT_UNAUTHORIZED, ["No usable branch-delete grant: " + "; ".join(reasons),
                                   "Ask @security for a per-run clearance and use "
                                   "--branch-cleanup, or have the operator sign a grant "
                                   "(references/branch-lifecycle.reference.md).", *lines]
    head = (f"Grant {grant['grant_id']} (expires {grant['expires_at']}, used "
            f"{grant_uses(repo, grant['grant_id'])}/{grant['max_uses']})")
    if not apply:
        return EXIT_OK, [head, *lines, "Dry run: nothing changed; re-run with --apply."]
    code, out = execute_items(repo, inv["plan"], items, mode="post-merge",
                              authorization=f"grant:{grant['grant_id']}", api=api,
                              run_id=f"post-merge-{branch}-{_now()}")
    return code, [head, *out]


__all__ = [
    "EXIT_FAIL", "EXIT_OK", "EXIT_UNAUTHORIZED", "HALT_FAMILY", "LEDGER_COLUMNS", "LEDGER_REL", "CleanupError",
    "append_ledger", "clearance_action", "execute_items", "find_branch_grant", "grant_uses",
    "load_plan", "read_ledger", "run_cleanup", "run_post_merge",
]
