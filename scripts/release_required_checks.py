#!/usr/bin/env python3
"""Switch main's required status checks when a release PR renames CI jobs (git-procedures §I).

A release PR (``dev`` → ``main``) that renames a job main requires, such as a runner bump (``test (ubuntu-24.04, 3.11)``
→ ``test (ubuntu-26.04, 3.11)``) or a matrix change, cannot merge: GitHub waits for a check its workflows no longer
produce. The required set has to change at the moment the PR's new checks are green, and no earlier, or every other PR
into main waits on checks its own ``ci.yml`` never produces.

**Operator-only.** Changing branch protection needs an admin token and changes what guards ``main``; agents prepare
the command and never run ``--apply``.

Usage::

    python3 scripts/release_required_checks.py --pr 512                  # dry run: shows the change and a fingerprint
    python3 scripts/release_required_checks.py --pr 512 --apply 3fa9c1d2e4b5
    python3 scripts/release_required_checks.py --restore ~/.local/state/agentteams/branch-protection/<file>.json
    python3 scripts/release_required_checks.py --restore <that file> --apply <fingerprint it printed>

The proposal keeps every required check the PR still produces. A required check it no longer produces is replaced
by the PR's checks with the same name once version numbers are masked. Any other change needs ``--add NAME`` (a check
the PR produces) or ``--drop NAME`` (a required check to stop requiring). ``--apply`` takes the fingerprint the dry
run printed, which covers the PR head commit, the current and proposed sets and ``strict``, so it applies exactly what
was shown, or refuses.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

GREEN = {"success"}
BACKUP_DIR = Path.home() / ".local" / "state" / "agentteams" / "branch-protection"
_VERSION = re.compile(r"\d+(?:\.\d+)*")


class Refused(Exception):
    """A guard failed; nothing was changed."""


@dataclass
class Proposal:
    """The required-check change a release PR needs.

    Attributes:
        old: Current required checks, ``context -> app_id``.
        new: Proposed required checks, ``context -> app_id``.
        replaced: ``old context -> [new contexts]`` for each renamed check.
        not_green: Proposed checks that are not green on the PR head.
    """

    old: dict[str, int]
    new: dict[str, int]
    replaced: dict[str, list[str]] = field(default_factory=dict)
    not_green: list[str] = field(default_factory=list)


def mask_versions(name: str) -> str:
    """Return a check name with every version number replaced by ``#``.

    Args:
        name: A check context such as ``test (ubuntu-26.04, 3.11)``.

    Returns:
        The masked name, e.g. ``test (ubuntu-#, #)``.
    """
    return _VERSION.sub("#", name)


def propose(
    required: dict[str, int],
    pr_checks: dict[str, tuple[str, int]],
    add: list[str] | None = None,
    drop: list[str] | None = None,
) -> Proposal:
    """Work out main's new required set from the release PR's checks.

    Args:
        required: Main's current required checks, ``context -> app_id``.
        pr_checks: The PR head's checks, ``name -> (conclusion, app_id)``; conclusion is lower-case, ``""`` while
            running.
        add: Extra checks to require; each must be produced by the PR.
        drop: Required checks to stop requiring; each must be currently required.

    Returns:
        The proposal. ``not_green`` lists proposed checks that are not green on the PR.

    Raises:
        Refused: A required check is neither produced by the PR, nor renamed, nor dropped; or ``--add``/``--drop``
            names a check that cannot apply; a renamed check would lose its app pin; or nothing would be required.
    """
    add, drop = list(add or []), list(drop or [])
    for name in drop:
        if name not in required:
            raise Refused(f"--drop {name!r}: main does not require it")
    for name in add:
        if name not in pr_checks:
            raise Refused(f"--add {name!r}: the PR produces no check by that name")
    new: dict[str, int] = {}
    replaced: dict[str, list[str]] = {}
    unresolved: list[str] = []
    for ctx, app in sorted(required.items()):
        if ctx in drop:
            continue
        if ctx in pr_checks:
            new[ctx] = app
            continue
        twins = sorted(n for n in pr_checks if n not in required and mask_versions(n) == mask_versions(ctx))
        if not twins:
            unresolved.append(ctx)
            continue
        replaced[ctx] = twins
        for twin in twins:
            producer = pr_checks[twin][1]
            if app != -1 and producer != app:
                raise Refused(f"{twin!r} would replace {ctx!r}, which only app {app} may report, but the PR's "
                              f"{twin!r} comes from {'a commit status (any source)' if producer == -1 else f'app {producer}'}")
            new[twin] = app
    if unresolved:
        raise Refused(
            "the PR produces no check for required " + ", ".join(repr(c) for c in unresolved)
            + " and none renamed to it; pass --drop for a check main should stop requiring, or wait for the PR's checks"
        )
    for name in add:
        if pr_checks[name][1] == -1:
            raise Refused(f"--add {name!r}: the PR reports it as a commit status, which any token can post; "
                          "only a check run from a GitHub App can be pinned")
        new[name] = pr_checks[name][1]
    if not new:
        raise Refused("the proposal requires no checks at all; main must keep at least one required check")
    not_green = sorted(n for n in new if pr_checks.get(n, ("", 0))[0] not in GREEN)
    return Proposal(old=dict(required), new=new, replaced=replaced, not_green=not_green)


def fingerprint(proposal: Proposal, *, strict: bool, pr: int, head_sha: str) -> str:
    """Return a 12-character fingerprint of exactly what ``--apply`` would do.

    Args:
        proposal: The proposed change.
        strict: Main's current ``strict`` setting (kept unchanged).
        pr: The release PR number.
        head_sha: The PR head commit the checks were read from.

    Returns:
        A hex prefix of SHA-256 over the canonical JSON of the inputs.
    """
    doc = {"pr": pr, "head": head_sha, "strict": strict,
           "from": sorted(proposal.old.items()), "to": sorted(proposal.new.items())}
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()[:12]


# --------------------------------------------------------------------------- GitHub (gh) access

def _gh(*args: str, stdin: str | None = None) -> str:
    gh = os.environ.get("GH", "gh")
    proc = subprocess.run([gh, *args], input=stdin, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise Refused(f"gh {' '.join(args[:3])} failed: {proc.stderr.strip()}")
    return proc.stdout


def _protection(repo: str) -> tuple[bool, dict[str, int]]:
    doc = json.loads(_gh("api", f"repos/{repo}/branches/main/protection/required_status_checks"))
    checks = {c["context"]: c.get("app_id") if c.get("app_id") is not None else -1 for c in doc.get("checks", [])}
    for ctx in doc.get("contexts", []):
        checks.setdefault(ctx, -1)
    return bool(doc.get("strict")), checks


def _pr(repo: str, pr: int) -> tuple[str, dict[str, tuple[str, int]]]:
    meta = json.loads(_gh("pr", "view", str(pr), "-R", repo, "--json",
                          "state,baseRefName,headRefName,headRefOid,isCrossRepository"))
    if meta["state"] != "OPEN":
        raise Refused(f"PR {pr} is {meta['state']}, not open")
    if meta["baseRefName"] != "main":
        raise Refused(f"PR {pr} targets {meta['baseRefName']}, not main")
    if meta["isCrossRepository"]:
        raise Refused(f"PR {pr} comes from a fork; only a branch of {repo} itself may switch main's checks")
    head = meta["headRefName"]
    if head != "dev" and not head.startswith("hotfix/"):
        raise Refused(f"PR {pr} comes from {head}; only a release PR (dev) or a hotfix/* PR may switch main's checks")
    sha = meta["headRefOid"]
    lines = _gh("api", f"repos/{repo}/commits/{sha}/check-runs?per_page=100", "--paginate",
                "--jq", ".check_runs[] | {id, name, conclusion, app: .app.id}")
    runs = [json.loads(line) for line in lines.splitlines() if line.strip()]
    checks: dict[str, tuple[str, int]] = {}
    for run in sorted(runs, key=lambda r: r["id"]):          # the latest re-run of a name wins
        checks[run["name"]] = ((run["conclusion"] or "").lower(), run["app"])
    statuses = json.loads(_gh("api", f"repos/{repo}/commits/{sha}/status"))["statuses"]
    for st in statuses:
        checks.setdefault(st["context"], (st["state"].lower(), -1))
    return sha, checks


def _patch(repo: str, strict: bool, checks: dict[str, int]) -> None:
    body = {"strict": strict, "checks": [{"context": c, "app_id": a} for c, a in sorted(checks.items())]}
    _gh("api", "-X", "PATCH", f"repos/{repo}/branches/main/protection/required_status_checks", "--input", "-",
        stdin=json.dumps(body))


def _repo(arg: str | None) -> str:
    return arg or _gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner").strip()


# --------------------------------------------------------------------------- commands

def _safe(name: str) -> str:
    """A check name for the terminal: control characters (escape codes, newlines) shown escaped, never interpreted."""
    return "".join(ch if ch.isprintable() else ascii(ch)[1:-1] for ch in name)


def _app(app: int) -> str:
    return "[any source: UNPINNED]" if app == -1 else f"[app {app}]"


def _show(p: Proposal) -> None:
    print("main requires now:")
    for c, app in sorted(p.old.items()):
        print(f"  {_safe(c)} {_app(app)}")
    print("proposed:")
    for c, app in sorted(p.new.items()):
        mark = "" if c not in p.old else ("   (app changes)" if p.old[c] != app else "")
        print(f"  {_safe(c)} {_app(app)}{mark or ('' if c in p.old else '   (new)')}")
    for old, twins in sorted(p.replaced.items()):
        print(f"renamed: {_safe(old)} -> {', '.join(_safe(t) for t in twins)}")
    for c in sorted(set(p.old) - set(p.new) - set(p.replaced)):
        print(f"dropped: {_safe(c)}")


def _restore(repo: str, path: Path, apply_fp: str | None) -> int:
    """Put back the protection an ``--apply`` saved: shown first, then applied with its fingerprint."""
    path = path.expanduser().resolve()
    if path.parent != BACKUP_DIR.resolve() or not path.name.startswith("main-required-checks-"):
        raise Refused(f"--restore takes only a backup this script wrote in {BACKUP_DIR}")
    saved = json.loads(path.read_text(encoding="utf-8"))
    before = Proposal(old=dict(saved["after"]["checks"]), new=dict(saved["before"]["checks"]))
    if saved.get("repo") != repo:
        raise Refused(f"the backup is for {saved.get('repo')}, not {repo}")
    if fingerprint(Proposal(old=before.new, new=before.old), strict=saved["before"]["strict"], pr=saved["pr"],
                   head_sha=saved["head"]) != saved.get("fingerprint"):
        raise Refused("the backup's contents do not match its recorded fingerprint; not restoring")
    if not before.new:
        raise Refused("the backup's earlier set is empty; main must keep at least one required check")
    strict, current = _protection(repo)
    if saved["before"]["strict"] != strict:
        raise Refused(f"the backup would set strict={saved['before']['strict']}, but main has strict={strict}; "
                      "--apply never changes strict, so a backup that does is not one it wrote")
    if current != before.old:
        raise Refused("main's required checks are no longer the set that --apply wrote; not restoring")
    _show(before)
    fp = fingerprint(before, strict=strict, pr=saved["pr"], head_sha=saved["head"])
    if apply_fp is None:
        print(f"\nfingerprint {fp}\napply: python3 {shlex.quote(str(Path(__file__).resolve()))} "
              f"--restore {shlex.quote(str(path))} --apply {fp}")
        return 0
    if apply_fp != fp:
        raise Refused(f"fingerprint {apply_fp} does not match the restore shown now ({fp}); run it again")
    _patch(repo, strict, before.new)
    _, now = _protection(repo)
    if now != before.new:
        raise Refused(f"after the restore main requires {sorted(now)}")
    print(f"restored main's required checks from {path}")
    return 0


def run(argv: list[str] | None = None) -> int:
    """Command-line entry point.

    Args:
        argv: Arguments (default ``sys.argv[1:]``).

    Returns:
        Process exit status: 0 done or nothing to do, 1 refused.
    """
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", help="owner/name (default: this checkout's GitHub repository)")
    ap.add_argument("--pr", type=int, help="the open release (dev) or hotfix PR into main")
    ap.add_argument("--add", action="append", default=[], metavar="NAME", help="also require this PR check")
    ap.add_argument("--drop", action="append", default=[], metavar="NAME", help="stop requiring this check")
    ap.add_argument("--apply", metavar="FINGERPRINT", help="apply the change (or restore) the dry run printed")
    ap.add_argument("--restore", type=Path, metavar="FILE", help="put back the protection saved by an --apply")
    a = ap.parse_args(argv)
    try:
        repo = _repo(a.repo)
        if a.restore:
            return _restore(repo, a.restore, a.apply)
        if a.pr is None:
            ap.error("--pr is required unless --restore is given")
        strict, required = _protection(repo)
        head, pr_checks = _pr(repo, a.pr)
        p = propose(required, pr_checks, a.add, a.drop)
        _show(p)
        if p.new == p.old:
            print("no change needed: main already requires exactly these checks")
            return 0
        if p.not_green:
            raise Refused("not green on the PR head yet: " + ", ".join(p.not_green))
        fp = fingerprint(p, strict=strict, pr=a.pr, head_sha=head)
        if not a.apply:
            extra = "".join(f" --add {shlex.quote(n)}" for n in a.add) + "".join(f" --drop {shlex.quote(n)}" for n in a.drop)
            print(f"\nfingerprint {fp} (PR {a.pr} head {head[:12]}, strict={strict})")
            print(f"apply: python3 {shlex.quote(str(Path(__file__).resolve()))} --pr {a.pr}{extra} --apply {fp}")
            return 0
        if a.apply != fp:
            raise Refused(f"fingerprint {a.apply} does not match the current proposal {fp}: the PR head, main's "
                          "required checks or the arguments changed since the dry run; run it again")
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup = BACKUP_DIR / f"main-required-checks-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
        backup.write_text(json.dumps({
            "repo": repo, "pr": a.pr, "head": head, "fingerprint": fp,
            "before": {"strict": strict, "checks": sorted(p.old.items())},
            "after": {"strict": strict, "checks": sorted(p.new.items())},
        }, indent=2) + "\n", encoding="utf-8")
        _patch(repo, strict, p.new)
        _, now = _protection(repo)
        if now != p.new:
            raise Refused(f"after the change main requires {sorted(now)}; restore with --restore {backup}")
        print(f"main now requires the proposed set. Undo (shows it first): python3 "
              f"{shlex.quote(str(Path(__file__).resolve()))} --restore {shlex.quote(str(backup))}")
        return 0
    except Refused as exc:
        print(f"STOPPED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
