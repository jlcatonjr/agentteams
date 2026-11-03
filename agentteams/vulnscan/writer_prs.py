"""A writer's open PR holds its packages back from the executor, until the Rule S-VM deadline (vfr-d D6).

:mod:`.open_prs` leaves a package to an open Dependabot / Renovate PR on the repository itself; anyone else's PR
was never trusted (a title is only text). D6 adds a second, narrower kind of hold, for a person's PR
(``@security`` 2026-11-02, conditions in brackets):

- The PR is **on the repository itself** (``isCrossRepository`` false), not the executor's (``vulnscan/*``), not a
  Dependabot / Renovate one, and its author has **write permission right now**: the collaborator-permission API
  (``gh api repos/OWNER/REPO/collaborators/LOGIN/permission``: ``admin`` or ``write``), never
  ``author_association`` [live collaborator-permission API].
- Its packages come from **its diff** (``gh pr diff``), read as untrusted data [diff parsed as data]: only the
  files the executor edits (``requirements*.txt``, ``package-lock.json``, ``npm-shrinkwrap.json``), only plain
  names and versions (:data:`_VERSION`, :data:`.npm_groups.NPM_NAME`, :data:`.manifests.REQ_PIN`), a diff over
  :data:`MAX_DIFF_BYTES` or naming over :data:`MAX_NAMES` packages holds nothing [cap names].
- A row is held only when the PR changes **the same file** and moves the package to the row's fixed version **or
  later** (every version the diff writes for it) [hold only if the diff moves to a fixed version].
- The hold **expires at the Rule S-VM deadline** [hold expires at the S-VM deadline]: 24 h for a critical
  advisory, 7 days for high (and for an unscored one, which counts as high), 30 days for moderate and low, counted
  from the first time the executor held the package in that repository, by whichever PR (a :data:`CLOCK_KIND`
  row in the MAC-chained ledger: no unsigned file can move it, and a new PR for the package never restarts it).
  An advisory that cannot be read counts as critical (the shortest hold).
  CISA KEV is not wired into the scanner, so a KEV listing does not shorten a hold below its severity's window.
- The fix plan says **"held by #N"** with the deadline, and the PR body lists it under "left to other open PRs"
  [report held by #N, Rule 12]; an expired hold says so on the row the fix then takes.

Every failure means **no hold, the fix proceeds** (a gh failure, an unreadable permission or diff, an unreadable
ledger): a hold only ever delays a fix, so it needs positive evidence. This is staging's side of
:func:`staging_claims`' split (fail open at stage); ``open-pr``'s fail-closed duplicate check
(:func:`.open_prs.refuse_duplicate`) is unchanged and covers the Dependabot / Renovate claims only.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from agentteams.vulnscan import _http, open_prs, osv
from agentteams.vulnscan.executor_env import ExecError, gh_cwd, gh_push_env
from agentteams.vulnscan.manifests import REQ_NAME, REQ_PIN
from agentteams.vulnscan.npm_groups import NPM_NAME
from agentteams.vulnscan.versions import version_key

#: A diff larger than this holds nothing (it is read whole; a PR is untrusted content).
MAX_DIFF_BYTES = 2_000_000
#: A PR whose diff names more packages than this holds nothing.
MAX_NAMES = 50
#: At most this many writer PRs are looked at per staging (each costs two gh calls).
MAX_PRS = 20
CLOCK_KIND = "writer-hold"   # ledger row: the executor first held ``name`` for PR ``number`` of ``target``
#: Rule S-VM's remediation deadlines by severity rank (:data:`.osv.SEVERITY_RANK`; 0, unscored, counts as high).
DEADLINES = {4: timedelta(hours=24), 3: timedelta(days=7), 2: timedelta(days=30), 1: timedelta(days=30),
             0: timedelta(days=7)}
WRITE_PERMISSIONS = frozenset({"admin", "write"})
_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_VERSION = re.compile(r"[0-9][0-9A-Za-z.+-]{0,63}")
_NPM_LOCKS = ("package-lock.json", "npm-shrinkwrap.json")
_FILE = re.compile(r"^diff --git a/\S+ b/(\S+)$")
_ENTRY = re.compile(r'^[ +-]\s*"((?:[^"]*/)?node_modules/(?:@[^"/]+/)?[^"/]+)"\s*:\s*\{')
_LOCK_VERSION = re.compile(r'^\+\s*"version"\s*:\s*"([^"]*)"')


@dataclass(frozen=True)
class Hold:
    """One package a writer's open PR moves in one file."""

    number: int
    path: str                  # the repository-relative file the PR changes
    versions: tuple[str, ...]  # every version the diff writes for the package there
    since: datetime            # the hold's first ledger row


class Claims(dict):  # type: ignore[type-arg]
    """:func:`.open_prs.claimed`'s ``{normalized name: [PR numbers]}``, plus the writer holds (``holds``).

    ``held`` collects, per row package name, the writer PRs :func:`leave_to_prs` held it for.
    """

    def __init__(self, base: dict[str, list[int]] | None = None, holds: dict[str, list[Hold]] | None = None) -> None:
        super().__init__(base or {})
        self.holds: dict[str, list[Hold]] = holds or {}
        self.held: dict[str, list[int]] = {}


def parse_diff(text: str) -> dict[tuple[str, str], list[str]]:
    """Return ``{(path, normalized name): [versions]}`` for the packages a PR diff moves in executor-edited files.

    Args:
        text: ``gh pr diff`` output (untrusted).

    Returns:
        The added versions per file and package; a name or version of unusual form is skipped.
    """
    out: dict[tuple[str, str], list[str]] = {}
    path, entry = "", ""
    for line in text.splitlines():
        if m := _FILE.match(line):
            path, entry = m[1], ""
            continue
        base = path.rsplit("/", 1)[-1]
        if REQ_NAME.match(base):
            pin = REQ_PIN.match(line[1:]) if line.startswith("+") and not line.startswith("+++") else None
            if pin and _VERSION.fullmatch(pin[3]):
                out.setdefault((path, open_prs.normalize(pin[1])), []).append(pin[3])
        elif base in _NPM_LOCKS:
            if m := _ENTRY.match(line):
                entry = m[1].rsplit("node_modules/", 1)[-1]
            elif (v := _LOCK_VERSION.match(line)) and entry and NPM_NAME.fullmatch(entry) and _VERSION.fullmatch(v[1]):
                out.setdefault((path, open_prs.normalize(entry)), []).append(v[1])
    return out


def _gh(argv: list[str]) -> str:
    """Run a read-only gh query (hardened environment, empty cwd) and return its stdout, or raise ExecError."""
    try:
        proc = subprocess.run(["gh", *argv], cwd=gh_cwd(), capture_output=True, text=True, env=gh_push_env(),
                              timeout=120, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExecError(f"gh {argv[0]} failed: {exc}") from exc
    if proc.returncode:
        raise ExecError(f"gh {argv[0]} failed: {(proc.stderr or proc.stdout).strip()[:200]}")
    return proc.stdout


def can_write(target_repo: str, login: str, *, gh: Callable[[list[str]], str] | None = None) -> bool:
    """Return whether ``login`` has write permission on ``target_repo`` now (False on any doubt).

    Args:
        target_repo: ``HOST/OWNER/REPO``.
        login: The PR author's login.
        gh: Runs one gh command and returns stdout (default: the hardened gh; tests pass a fake).

    Returns:
        True for ``admin`` or ``write`` (GitHub reports ``maintain`` as ``write``).
    """
    gh = gh or _gh
    host, _, slug = target_repo.partition("/")
    if not _LOGIN.fullmatch(login) or slug.count("/") != 1:
        return False
    try:
        data = json.loads(gh(["api", "--hostname", host, f"repos/{slug}/collaborators/{login}/permission"]))
    except (ExecError, ValueError):
        return False
    return isinstance(data, dict) and data.get("permission") in WRITE_PERMISSIONS


def _candidates(prs: list[dict[str, Any]]) -> list[tuple[int, str]]:
    """(number, login) of the same-repository, non-executor, non-bot PRs, at most :data:`MAX_PRS`."""
    out = []
    for pr in prs:
        num, author, branch = pr.get("number"), pr.get("author"), pr.get("headRefName")
        login = author.get("login") if isinstance(author, dict) else None
        if (isinstance(num, int) and not isinstance(num, bool) and pr.get("isCrossRepository") is False
                and isinstance(login, str) and login not in open_prs.TRUSTED_AUTHORS
                and not (isinstance(branch, str) and branch.startswith("vulnscan/"))):
            out.append((num, login))
    return out[:MAX_PRS]


def _since(target_repo: str, number: int, name: str, rows: list[dict[str, Any]]) -> datetime:
    """Return the package's hold start in this repository: its first :data:`CLOCK_KIND` row, written now when there is
    none (inside the lock).

    The clock is keyed by repository and package only, never by PR: closing a PR and opening another for the same
    package keeps the first start, so a writer cannot renew a hold past its S-VM deadline. ``number`` is recorded on a
    new row for the audit trail and never read back.

    Raises:
        ValueError: The row has no readable ``ts`` (the caller then holds nothing).
    """
    from agentteams.vulnscan import ledger   # lazy: the ledger is the executor's

    row = next((r for r in rows if r.get("kind") == CLOCK_KIND and r.get("target") == target_repo
                and r.get("name") == name), None)
    if row is None:
        row = ledger.append(CLOCK_KIND, target=target_repo, number=number, name=name)
        rows.append(row)
    return datetime.fromisoformat(str(row.get("ts")))   # no ts: ValueError, so no hold


def holds(target_repo: str, prs: list[dict[str, Any]], *,
          gh: Callable[[list[str]], str] | None = None) -> dict[str, list[Hold]]:
    """Return ``{normalized name: [Hold]}`` for the packages writers' open PRs move. Call inside ``ledger.locked()``.

    Args:
        target_repo: ``HOST/OWNER/REPO``.
        prs: :func:`.open_prs.list_open`'s result.
        gh: Runs one gh command and returns stdout (default: the hardened gh; tests pass a fake).

    Returns:
        The holds; ``{}`` when nothing qualifies or the ledger cannot be read (no hold: the fix proceeds).
    """
    from agentteams.vulnscan import ledger

    gh = gh or _gh
    found: list[tuple[int, str, str, tuple[str, ...]]] = []   # (PR, path, name, versions)
    writers: dict[str, bool] = {}
    for number, login in _candidates(prs):
        if login not in writers:
            writers[login] = can_write(target_repo, login, gh=gh)
        if not writers[login]:
            continue
        try:
            text = gh(["pr", "diff", str(number), "--repo", target_repo])
        except ExecError:
            text = ""   # no diff: nothing to hold for this PR
        moved = parse_diff(text) if len(text.encode("utf-8", "replace")) <= MAX_DIFF_BYTES else {}
        if len({name for _path, name in moved}) <= MAX_NAMES:
            found += [(number, path, name, tuple(versions)) for (path, name), versions in moved.items()]
    out: dict[str, list[Hold]] = {}
    try:
        rows = ledger.read()
        starts = {name: _since(target_repo, number, name, rows) for number, _path, name, _v in found}
    except (OSError, ValueError):   # an unreadable or untrusted ledger: no hold, never a guessed clock
        return {}
    for number, path, name, versions in found:
        out.setdefault(name, []).append(Hold(number, path, versions, starts[name]))
    return out


def staging_claims(target_repo: Callable[[], str], *, gh: Callable[[list[str]], str] | None = None) -> Claims:
    """Return the Dependabot / Renovate claims and the writer holds for staging (any failure: none of either).

    Args:
        target_repo: Returns ``HOST/OWNER/REPO`` (``executor.origin_repo``); it may raise :class:`ExecError`.
        gh: Runs one gh command (default: the hardened gh; tests pass a fake).

    Returns:
        The :class:`Claims`.
    """
    try:
        repo = target_repo()
        prs = open_prs.list_open(repo)
    except ExecError:   # not blocking: open-pr lists the PRs again and fails closed (open_prs.refuse_duplicate)
        return Claims()
    return Claims(open_prs.claimed(prs), holds(repo, prs, gh=gh))


def deadline(hold: Hold, ids: list[str]) -> datetime:
    """Return when ``hold`` expires: its start plus the Rule S-VM window of the row's worst advisory.

    Args:
        hold: The hold.
        ids: The row's advisory ids.

    Returns:
        The expiry (UTC).
    """
    try:
        rank = max((osv.severity(osv.details(i)) for i in ids), default=4)
    except _http.FeedError:
        rank = 4   # unknown severity: the shortest hold
    return hold.since.astimezone(UTC) + DEADLINES.get(rank, DEADLINES[4])


def leave_to_prs(rows: list[Any], skip: dict[str, list[int]] | None, *, now: datetime | None = None) -> None:
    """Mark rows another open PR already updates as ``pr-held`` (D1: "left to #N"; D6: "held by #N").

    Args:
        rows: :class:`.fix.PinChange` rows (``fix``, ``major-held`` and ``stage-cargo`` ones are considered).
        skip: :func:`.open_prs.claimed`'s map, or a :class:`Claims` that also carries writer holds.
        now: The clock (tests).
    """
    now = now or datetime.now(UTC)
    for c in rows:
        if c.action not in ("fix", "major-held", "stage-cargo"):
            continue
        name = open_prs.normalize(c.name)
        if nums := (skip or {}).get(name):
            c.action, c.major = "pr-held", False
            c.note = f"left to {', '.join(f'#{n}' for n in nums)} (an open PR already updates it; not duplicated)"
            continue
        moving = [h for h in getattr(skip, "holds", {}).get(name, []) if h.path == c.path and c.new and h.versions
                  and all(version_key(v) >= version_key(c.new) for v in h.versions)]
        live = [(h, deadline(h, c.ids)) for h in moving]
        if held := [(h, d) for h, d in live if d > now]:
            nums = sorted({h.number for h, _d in held})
            until = min(d for _h, d in held).strftime("%Y-%m-%d %H:%M UTC")
            c.action, c.major = "pr-held", False
            c.note = (f"held by {', '.join(f'#{n}' for n in nums)} (a writer's open PR moves it to ≥ {c.new}; the hold "
                      f"expires {until}, Rule S-VM)")
            held_for = skip.held.setdefault(c.name, [])  # type: ignore[union-attr]   # holds imply a Claims
            held_for += [n for n in nums if n not in held_for]
        elif live:
            nums = sorted({h.number for h, _d in live})
            c.note += f"; the hold by {', '.join(f'#{n}' for n in nums)} expired (Rule S-VM deadline): fixed here"
