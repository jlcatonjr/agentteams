"""Remediation handoff to the target repository's own orchestrator (plan phase H: H1, H3, H4).

Under a handoff delivery the executor's staged fix is reviewed by the repository's own agent team before any PR is
opened. The operator side writes a handoff packet into ``<home>/.vulnscan-handoff/`` (git-excluded, never tracked)
and issues a signed management directive (scope ``review-vulnscan-handoff-<id>``, review only: the directive denylist
refuses push and merge scopes); the repository's orchestrator reviews the staged branch and appends a verdict row to
``<home>/.vulnscan-handoff/verdicts.csv``::

    handoff_id,verdict,branch,notes

``verdict`` is one of :data:`VERDICTS`. Only ``approve-staged`` lets the executor's ``open-pr`` push the vulnscan
branch (H3, :func:`approval_problem`): this module reads the verdict, the executor enforces it. Nothing an agent in
the vulnscan container writes can satisfy it: the verdicts live in the repository's handoff directory, which the
container never mounts.

A *home* is the checkout itself or one of its linked worktrees under ``<repo>.worktrees/<name>/`` (where an
operator can render the repository's goose team without touching the checkout). Every home's handoff directory is
read (:func:`handoff_dirs`). agentteams ships the reading side only: writing packets and issuing the directive is
operator tooling, outside this package.

A verdict applies to a staged fix by its **content digest**, the suffix of the branch name
``vulnscan/<YYYYMMDD>-<digest>``: a fix re-staged on a later day is the same change under a new branch name. The
newest row for a digest wins, so a later ``reject`` or ``defer`` withdraws an earlier approval.

Delivery is ``handoff`` when the project's brief says ``vulnerability_scanning.delivery: handoff``, or for every
repository when the operator's ``vulnscan.json`` sets ``"handoff_required": true`` (:func:`required`).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HANDOFF_DIR = ".vulnscan-handoff"
VERDICTS_FILE = "verdicts.csv"
VERDICTS = ("approve-staged", "own-pr", "defer", "reject")
APPROVE = "approve-staged"
#: A verdicts file larger than this is not read (it is written by an agent session; never trust its size).
MAX_VERDICT_BYTES = 1_000_000
_BRANCH = re.compile(r"^vulnscan/\d{8}-([0-9a-f]{6,64})$")
_HANDOFF_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
#: A handoff packet is ``<YYYYMMDD><6 digits>.md``; anything else in the directory (review notes, plans) is not one.
_PACKET_ID = re.compile(r"^\d{14}$")
#: The launcher a home carries, relative to it.
LAUNCHER_REL = Path("sandbox") / "confine-run.sh"
#: The worktree under ``<repo>.worktrees/`` where the operator's handoff tooling renders a repository's goose team.
GOOSE_TEAM_WORKTREE = "goose-agent-team"


@dataclass(frozen=True)
class Verdict:
    """One row of a verdicts file."""

    handoff_id: str
    verdict: str
    branch: str
    notes: str
    source: str   # the verdicts file it came from

    @property
    def digest(self) -> str:
        """The content digest of the branch it names, or ""."""
        return digest_of(self.branch)


@dataclass
class RepoHandoff:
    """The handoff state of one repository (:func:`status`)."""

    repo: str
    required: bool
    staged_branch: str = ""
    verdict: str = ""                # the newest verdict for the staged fix's digest, or ""
    verdict_handoff: str = ""
    homes: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)   # packet ids with no verdict row
    problems: list[str] = field(default_factory=list)
    launcher: str = ""               # the sandbox/confine-run.sh the handoff's goose session would run under, or ""
    launcher_state: str = ""         # agentteams.launcher_lineage state, or "unknown" (lineage unreadable)
    launcher_detail: str = ""

    @property
    def launcher_warning(self) -> str:
        """A warning line unless the launcher is ``current`` (informational: the launcher's own checks gate)."""
        if self.launcher_state == "current":
            return ""
        if self.launcher_state == "missing":
            return "no sandbox/confine-run.sh: the handoff refuses to run goose unconfined"
        if self.launcher_state == "older":
            return f"the launcher is an older release ({self.launcher_detail}); regenerate the team"
        if self.launcher_state == "modified":
            return f"the launcher matches no released version ({self.launcher_detail}); review it"
        return f"the launcher could not be checked ({self.launcher_detail})"

    @property
    def state(self) -> str:
        """``no staged fix``, ``approved``, ``awaiting review`` or the newest verdict word."""
        if not self.staged_branch:
            return "no staged fix"
        if self.verdict == APPROVE:
            return "approved"
        return self.verdict or "awaiting review"

    def to_dict(self) -> dict[str, Any]:
        """Return the record as plain data (the ``handoff status --json`` shape)."""
        return {"repo": self.repo, "required": self.required, "state": self.state, "staged_branch": self.staged_branch,
                "verdict": self.verdict, "verdict_handoff": self.verdict_handoff, "homes": self.homes,
                "pending": self.pending, "problems": self.problems, "launcher": self.launcher,
                "launcher_state": self.launcher_state, "launcher_detail": self.launcher_detail}


def digest_of(branch: str) -> str:
    """Return the content digest of a ``vulnscan/<YYYYMMDD>-<digest>`` branch name.

    Args:
        branch: A branch name.

    Returns:
        The digest, or "" for any other name.
    """
    hit = _BRANCH.match(branch.strip())
    return hit[1] if hit else ""


def homes(repo: Path) -> list[Path]:
    """Return the repository's homes: the checkout, then its linked worktrees under ``<repo>.worktrees/``.

    Args:
        repo: Canonical repository path.

    Returns:
        The checkout first, then each worktree directory (symlinks skipped), sorted.
    """
    side = repo.parent / f"{repo.name}.worktrees"
    found = [repo]
    if side.is_dir() and not side.is_symlink():
        found += sorted(p for p in side.iterdir() if p.is_dir() and not p.is_symlink())
    return found


def handoff_dirs(repo: Path) -> list[Path]:
    """Return every existing ``.vulnscan-handoff`` directory of the repository's homes (symlinks skipped).

    Args:
        repo: Canonical repository path.

    Returns:
        The directories, in :func:`homes` order.
    """
    return [d for d in (h / HANDOFF_DIR for h in homes(repo)) if d.is_dir() and not d.is_symlink()]


def read_verdicts(directory: Path) -> tuple[list[Verdict], list[str]]:
    """Read ``<directory>/verdicts.csv``; return (rows in file order, problems).

    The file is written by an agent session, so it is read defensively: opened with ``O_NOFOLLOW`` and
    ``O_NONBLOCK`` (a symlink or FIFO is refused, not followed or waited on), checked to be a regular file on the open
    descriptor, and read up to :data:`MAX_VERDICT_BYTES` (a larger file is ignored). A file git tracks in its home is
    ignored too: a verdict committed to the repository, or checked out in a ``<repo>.worktrees/`` worktree, would
    approve a fix in advance (the digest is predictable). Rows with an unknown verdict word, a malformed handoff id or
    a branch that is not a vulnscan branch are skipped and reported; a malformed CSV is reported, never raised.

    Args:
        directory: A handoff directory.

    Returns:
        The valid rows and one line per problem.
    """
    f = directory / VERDICTS_FILE
    try:
        fd = os.open(f, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return [], []
    except OSError as exc:   # ELOOP: a symlink
        return [], [f"{f}: not a regular file or unreadable ({exc.strerror}); ignored"]
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return [], [f"{f}: not a regular file; ignored"]
        data = os.read(fd, MAX_VERDICT_BYTES + 1)
    except OSError as exc:
        return [], [f"{f}: unreadable ({exc.strerror})"]
    finally:
        os.close(fd)
    if len(data) > MAX_VERDICT_BYTES:
        return [], [f"{f}: larger than {MAX_VERDICT_BYTES} bytes; ignored"]
    if _tracked(directory.parent, f"{HANDOFF_DIR}/{VERDICTS_FILE}"):
        return [], [f"{f}: tracked by git, or git could not tell (committed or checked out, not written by a "
                    "review); ignored"]
    rows, problems = [], []
    try:
        for n, row in enumerate(csv.DictReader(io.StringIO(data.decode("utf-8", errors="replace"))), start=2):
            hid, word, branch = (str(row.get(k) or "").strip() for k in ("handoff_id", "verdict", "branch"))
            if word not in VERDICTS or not _HANDOFF_ID.match(hid) or not digest_of(branch):
                problems.append(f"{f}:{n}: not a verdict row (handoff_id, one of {', '.join(VERDICTS)}, a vulnscan "
                                "branch); skipped")
                continue
            rows.append(Verdict(hid, word, branch, str(row.get("notes") or "")[:2000], str(f)))
    except csv.Error as exc:   # e.g. a field over the csv module's size limit
        return [], [f"{f}: not a readable CSV ({exc}); ignored"]
    return rows, problems


def _tracked(home: Path, rel: str) -> bool:
    """Return whether ``rel`` must be treated as tracked by git in ``home`` (fails closed).

    Only ``git ls-files --error-unmatch``'s "did not match" exit (1) counts as untracked. Any other outcome (tracked,
    a home git will not open, ``safe.directory``, a confinement failure, a timeout) counts as tracked, so the verdict
    file is ignored: a failure can only withhold an approval, never let a committed one through.

    Args:
        home: The checkout or worktree the handoff directory is in.
        rel: The file, relative to ``home``.

    Returns:
        False only when git positively reports the file as untracked.
    """
    import subprocess

    from agentteams.vulnscan import executor   # lazy: executor imports this module

    try:
        proc = executor.query_git(home, "ls-files", "--error-unmatch", "--", rel)
    except (OSError, subprocess.SubprocessError):   # incl. subproc_confine.Unavailable and a timeout
        return True
    return proc.returncode != 1


def latest_verdict(repo: Path, branch: str) -> tuple[Verdict | None, list[str]]:
    """Return the newest verdict for ``branch``'s content digest across the repository's homes, and the problems.

    Newest means last in file order; across homes, the file modified last is read last.

    Args:
        repo: Canonical repository path.
        branch: The staged branch (``vulnscan/<YYYYMMDD>-<digest>``).

    Returns:
        ``(verdict or None, problems)``.
    """
    want = digest_of(branch)
    if not want:
        return None, [f"{branch!r} is not a vulnscan branch"]
    dirs = sorted(handoff_dirs(repo), key=lambda d: _mtime(d / VERDICTS_FILE))
    found: Verdict | None = None
    problems: list[str] = []
    for d in dirs:
        rows, probs = read_verdicts(d)
        problems += probs
        for row in rows:
            if row.digest == want:
                found = row
    return found, problems


def _mtime(path: Path) -> float:
    try:
        return path.lstat().st_mtime
    except OSError:
        return 0.0


def required(cfg: dict[str, Any] | None, repo: Path) -> bool:
    """Return whether ``repo`` is under handoff delivery.

    Args:
        cfg: Daily config: ``"handoff_required": true`` applies to every repository.
        repo: Canonical repository path: its brief's ``vulnerability_scanning.delivery: handoff`` applies to it.

    Returns:
        True under handoff delivery.
    """
    if isinstance(cfg, dict) and cfg.get("handoff_required") is True:
        return True
    from agentteams.vulnscan import project_policy   # lazy: briefs are read only for repo ops

    return project_policy.read_policy(repo).delivery == "handoff"


def approval_problem(repo: Path, branch: str) -> str:
    """Return why ``open-pr`` may not push ``branch`` under handoff delivery, or "" when its orchestrator approved it.

    Args:
        repo: The repository (canonical path).
        branch: The staged branch.

    Returns:
        "" when the newest verdict for the branch's content digest is ``approve-staged``; else the reason.
    """
    found, _problems = latest_verdict(repo, branch)
    if found is None:
        return (f"{repo.name}: handoff delivery — no verdict from the repository's orchestrator for {branch} yet "
                f"(expected an {APPROVE} row in {HANDOFF_DIR}/{VERDICTS_FILE})")
    if found.verdict != APPROVE:
        return (f"{repo.name}: handoff delivery — the repository's orchestrator answered {found.verdict} for this fix "
                f"(handoff {found.handoff_id}); no PR")
    return ""


def pending_packets(repo: Path) -> list[str]:
    """Return the ids of handoff packets (``<YYYYMMDD><6 digits>.md``) that no verdicts file answers yet.

    Args:
        repo: Canonical repository path.

    Returns:
        Sorted packet ids.
    """
    answered: set[str] = set()
    packets: list[str] = []
    for d in handoff_dirs(repo):
        rows, _problems = read_verdicts(d)
        answered |= {r.handoff_id for r in rows}
        packets += [p.stem for p in sorted(d.glob("*.md")) if _PACKET_ID.match(p.stem) and not p.is_symlink()]
    return sorted({p for p in packets if p not in answered})


def staged_branch(repo: Path) -> str:
    """Return the branch the executor has staged for ``repo`` (its spool record).

    Args:
        repo: Canonical repository path.

    Returns:
        The branch, or "" when nothing is staged (or the record is unreadable or names no vulnscan branch).
    """
    from agentteams.vulnscan import executor   # lazy: executor imports this module

    try:
        data = json.loads(executor.staged_file(repo).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    branch = data.get("branch") if isinstance(data, dict) else None
    return branch if isinstance(branch, str) and digest_of(branch) else ""


def goose_home(repo: Path) -> Path | None:
    """Return where the repository's goose team lives, as the operator's handoff tooling finds it.

    Args:
        repo: Canonical repository path.

    Returns:
        The checkout when it has ``.goose/recipes/orchestrator.yaml``, else the
        ``<repo>.worktrees/goose-agent-team`` worktree when that has it, else None.
    """
    for home in (repo, repo.parent / f"{repo.name}.worktrees" / GOOSE_TEAM_WORKTREE):
        if (home / ".goose" / "recipes" / "orchestrator.yaml").is_file():
            return home
    return None


def fallback_launcher(cfg: dict[str, Any] | None) -> Path | None:
    """Return the operator's fallback launcher: ``<agent.team_dir>/sandbox/confine-run.sh``.

    The handoff tooling runs goose under the home's own ``sandbox/confine-run.sh`` and falls back to the vulnscan
    team's (``agent.team_dir``) when the home has none.

    Args:
        cfg: Daily config.

    Returns:
        The path, or None when ``agent.team_dir`` is not set.
    """
    agent = cfg.get("agent") if isinstance(cfg, dict) else None
    team = str(agent.get("team_dir") or "").strip() if isinstance(agent, dict) else ""
    return Path(team).expanduser() / LAUNCHER_REL if team else None


def handoff_launcher(repo: Path, fallback: Path | None) -> Path | None:
    """Return the launcher a handoff's goose session for ``repo`` would actually run under.

    Args:
        repo: Canonical repository path.
        fallback: The operator's fallback launcher (:func:`fallback_launcher`), or None.

    Returns:
        The goose home's (:func:`goose_home`, else the checkout's) ``sandbox/confine-run.sh`` when it is a file,
        else *fallback* when that is a file, else None (the handoff refuses to run goose).
    """
    own = (goose_home(repo) or repo) / LAUNCHER_REL
    if own.is_file():   # like the tooling's [[ -f ]]: a symlink to a file counts (and is then reported modified)
        return own
    return fallback if fallback is not None and fallback.is_file() else None


def _launcher_fields(launcher: Path | None) -> dict[str, str]:
    from agentteams import launcher_lineage   # lazy: read only for status

    if launcher is None:
        return {"launcher": "", "launcher_state": "missing", "launcher_detail": "no launcher in the home or fallback"}
    try:
        found = launcher_lineage.launcher_state(launcher)
    except (OSError, ValueError) as exc:
        return {"launcher": str(launcher), "launcher_state": "unknown",
                "launcher_detail": f"launcher lineage unreadable: {exc}"}
    return {"launcher": str(launcher), "launcher_state": found.state, "launcher_detail": found.detail}


def status(cfg: dict[str, Any] | None, repos: list[Path], *, fallback: Path | None = None) -> list[RepoHandoff]:
    """Return the handoff state of each repository (H4: what the summary and ``handoff status`` show).

    Each record also names the launcher the handoff would run goose under (:func:`handoff_launcher`) and its
    :mod:`agentteams.launcher_lineage` state. That is informational and changes no gate.

    Args:
        cfg: Daily config.
        repos: Canonical repository paths.
        fallback: The operator's fallback launcher; None derives it from ``agent.team_dir``
            (:func:`fallback_launcher`).

    Returns:
        One record per repository, in order.
    """
    fallback = fallback if fallback is not None else fallback_launcher(cfg)
    out = []
    for repo in repos:
        rec = RepoHandoff(repo=str(repo), required=required(cfg, repo), staged_branch=staged_branch(repo),
                          homes=[str(h) for h in homes(repo) if (h / HANDOFF_DIR).is_dir()],
                          pending=pending_packets(repo), **_launcher_fields(handoff_launcher(repo, fallback)))
        if rec.staged_branch:
            found, rec.problems = latest_verdict(repo, rec.staged_branch)
            if found is not None:
                rec.verdict, rec.verdict_handoff = found.verdict, found.handoff_id
        out.append(rec)
    return out


def repos_with_staged_fixes() -> list[Path]:
    """Return the repositories the executor has a staged fix for (its spool records).

    Returns:
        Sorted repository paths.
    """
    from agentteams.vulnscan import executor

    return sorted({Path(r["repo"]) for r in executor.staged_records() if isinstance(r.get("repo"), str) and r["repo"]})


def markdown(records: list[RepoHandoff]) -> str:
    """Return the summary's ``## Handoffs to repository orchestrators`` section (H2).

    Args:
        records: :func:`status` results.

    Returns:
        Markdown naming repositories by directory name only, or "" when none has a staged fix or a packet.
    """
    shown = [r for r in records if r.staged_branch or r.pending]
    if not shown:
        return ""
    lines = ["## Handoffs to repository orchestrators", ""]
    for r in shown:
        mode = "handoff delivery" if r.required else "direct delivery"
        bits = [f"{r.state}"]
        if r.staged_branch:
            bits.append(f"staged {r.staged_branch}")
        if r.pending:
            bits.append(f"{len(r.pending)} packet(s) awaiting a verdict")
        if r.launcher_state and r.launcher_state != "current":
            bits.append(f"launcher {r.launcher_state}")
        lines.append(f"- {Path(r.repo).name} ({mode}): " + "; ".join(bits))
    lines.append("- details: `python -m agentteams.vulnscan handoff status`")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- CLI: handoff status

COMMANDS = ("handoff",)


def register(sub: Any) -> None:
    """Add ``handoff status [--repo PATH ...] [--json]`` to the vulnscan parser.

    Args:
        sub: The parser's subparsers action.
    """
    sp = sub.add_parser("handoff", help="remediation handoffs to each repository's own orchestrator")
    sp.add_argument("action", choices=("status",))
    sp.add_argument("--repo", action="append", default=[], type=Path,
                    help="a repository (default: every repository with a staged fix)")
    sp.add_argument("--json", action="store_true", help="one JSON object per repository")


def dispatch(args: argparse.Namespace, load_cfg: Callable[[], dict[str, Any]]) -> int:
    """Run ``handoff status``.

    Each repository shows its delivery (handoff or direct), the staged branch, the newest verdict for that fix
    (``approved`` means ``open-pr`` may push it) and the packets no verdict answers yet.

    Args:
        args: Parsed arguments (``repo``, ``json``).
        load_cfg: Loads the daily config.

    Returns:
        0 printed, 2 an unusable config.
    """
    from agentteams.vulnscan import paths

    try:
        cfg = load_cfg() if paths.config_file().exists() else {}
    except (OSError, ValueError) as exc:
        print(f"config unusable: {exc}", file=sys.stderr)
        return 2
    repos = [Path(p).expanduser().resolve() for p in args.repo] or repos_with_staged_fixes()
    records = status(cfg, repos)
    if args.json:
        for rec in records:
            print(json.dumps(rec.to_dict()))
        return 0
    if not records:
        print("no repository has a staged fix")
    for rec in records:
        mode = "handoff delivery" if rec.required else "direct delivery"
        print(f"{rec.repo}: {rec.state} ({mode})")
        if rec.staged_branch:
            print(f"  staged: {rec.staged_branch}")
        if rec.verdict:
            print(f"  verdict: {rec.verdict} (handoff {rec.verdict_handoff})")
        if rec.pending:
            print(f"  packets awaiting a verdict: {', '.join(rec.pending)}")
        print(f"  launcher: {rec.launcher or '—'} ({rec.launcher_state}: {rec.launcher_detail})")
        if rec.launcher_warning:
            print(f"  warning: {rec.launcher_warning}")
        for p in rec.problems:
            print(f"  problem: {p}")
    return 0
