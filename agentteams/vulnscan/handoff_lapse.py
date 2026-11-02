"""The ``own-pr`` lapse (vfr-b.B1b, operator decision OD10: "ask the owner, then lapse").

Under handoff delivery (:mod:`.handoff`) a repository's orchestrator may answer ``own-pr`` for a staged fix: it will
open its own PR. Colorado-collectors answered so twice for a csv-parse 7 branch it prepared and never pushed, so the
fix waited indefinitely. The rule:

- **The clock** starts the first time the executor sees ``own-pr`` as the newest verdict for a staged fix's content
  digest (:func:`clock`, recorded in ``<state>/handoff-lapse.json``). A different digest (a re-staged, changed fix)
  or a changed fix plan (:func:`.retry_guard.plan_digest`) restarts it.
- **While it runs** the executor keeps deferring (``open-pr`` is refused as before) and the refusal, the summary and
  ``handoff status`` name the lapse date. :func:`packet_ask` is the text each run's handoff packet carries to ask the
  orchestrator for its PR: for a repository with no Claude session (colorado), the packet is the only channel. The
  packets themselves are written by the operator's handoff tooling, outside this package.
- **After** :data:`LAPSE_DAYS`, when no open owner PR updates the staged packages (:func:`.open_prs.owner_prs`),
  :func:`lapsed` lets ``open-pr`` proceed under the existing grant, and ``executor.authorize`` logs a
  ``handoff-lapsed`` ledger row. No new op, grant or capability: the PR is the executor's ordinary ``open-pr``.

What the lapse newly permits (Rule 12): only ``own-pr`` lapses. ``defer`` and ``reject`` never do, and a later verdict
for the same digest (``reject``, ``defer``) withdraws the ``own-pr`` and stops the clock from mattering. Every
uncertainty keeps deferring: an unreadable state file restarts the clock, and a gh failure or a staged record without
packages or worktree counts as "an owner PR may exist".
"""

from __future__ import annotations

import json
import subprocess
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from agentteams.vulnscan import handoff, paths

#: Days an ``own-pr`` verdict defers the executor's PR before it lapses (OD10).
LAPSE_DAYS = 7
OWN_PR = "own-pr"
LAPSED_KIND = "handoff-lapsed"   # the ledger row executor.authorize writes when open-pr proceeds on a lapse


def _state_file() -> Path:
    return paths.state_dir() / "handoff-lapse.json"


def _read() -> dict[str, Any]:
    try:
        data = json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict[str, Any]) -> None:
    path = _state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def clock(repo: Path, branch: str, *, today: date | None = None) -> dict[str, str] | None:
    """Start or read the ``own-pr`` clock of the fix staged on ``branch`` (records a start; reads nothing else).

    Args:
        repo: Canonical repository path.
        branch: The staged branch (``vulnscan/<YYYYMMDD>-<digest>``).
        today: The local date (tests); default today.

    Returns:
        ``{"digest", "plan", "since", "lapses", "handoff_id"}`` (ISO dates) when the newest verdict for the branch's
        digest is ``own-pr``, else None.
    """
    from agentteams.vulnscan import retry_guard   # lazy: retry_guard imports daily lazily, and the executor this

    found, _problems = handoff.latest_verdict(repo, branch)
    if found is None or found.verdict != OWN_PR:
        return None
    today = today or date.today()
    plan = retry_guard.plan_digest(str(repo))
    data = _read()
    rec = data.get(str(repo))
    if not (isinstance(rec, dict) and rec.get("digest") == found.digest and rec.get("plan") == plan
            and _day(rec.get("since"))):
        rec = {"digest": found.digest, "plan": plan, "since": today.isoformat(), "handoff_id": found.handoff_id}
        data[str(repo)] = rec
        _write(data)
    since = _day(rec["since"]) or today   # checked above; never None here
    return {"digest": str(rec["digest"]), "plan": str(rec["plan"]), "since": since.isoformat(),
            "lapses": (since + timedelta(days=LAPSE_DAYS)).isoformat(), "handoff_id": str(rec.get("handoff_id", ""))}


def _day(value: object) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _staged(repo: Path) -> dict[str, Any]:
    from agentteams.vulnscan import executor   # lazy: executor imports this module

    try:
        data = json.loads(executor.staged_file(repo).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def owner_pr(repo: Path) -> list[int] | None:
    """Return the open owner PRs that update the staged fix's packages, or None when that cannot be told.

    Args:
        repo: Canonical repository path (its staged record names the packages and the executor's worktree).

    Returns:
        PR numbers (``[]``: none); None when the staged record has no packages or worktree, or gh fails.
    """
    from agentteams.vulnscan import executor, open_prs   # lazy: executor imports this module
    from agentteams.vulnscan.executor_env import ExecError

    st = _staged(repo)
    packages = [p for p in st.get("packages", []) if isinstance(p, str)] if isinstance(st.get("packages"), list) else []
    wt = st.get("worktree")
    if not packages or not isinstance(wt, str) or not wt:
        return None
    try:
        return open_prs.owner_prs(open_prs.list_open(executor._origin_repo(Path(wt))), packages)
    except (ExecError, OSError, subprocess.SubprocessError):   # incl. a git or gh timeout
        return None


def lapsed(repo: Path, branch: str, *, today: date | None = None) -> str:
    """Return why the ``own-pr`` verdict on ``branch`` has lapsed (``open-pr`` may proceed), or "".

    Args:
        repo: Canonical repository path.
        branch: The staged branch.
        today: The local date (tests); default today.

    Returns:
        The ledger detail when :data:`LAPSE_DAYS` have passed since the clock started and no open owner PR updates
        the staged packages; "" otherwise (no ``own-pr``, too early, an owner PR, or unknown).
    """
    rec = clock(repo, branch, today=today)
    if rec is None or (today or date.today()).isoformat() < rec["lapses"]:
        return ""
    prs = owner_pr(repo)
    if prs is None or prs:
        return ""
    names = ", ".join(_staged(repo).get("packages", []))[:300]
    return (f"own-pr verdict (handoff {rec['handoff_id']}) on fix {rec['digest']} since {rec['since']}: no owner PR "
            f"updates {names} after {LAPSE_DAYS} days, so the executor opens its staged PR (OD10)")


def gate(repo: Path, branch: str, problem: str, *, request_id: str = "") -> str:
    """Return ``open-pr``'s handoff refusal after the lapse rule; "" lets it proceed. Call inside ``ledger.locked()``.

    Args:
        repo: Canonical repository path.
        branch: The staged branch ("" when nothing is staged).
        problem: :func:`.handoff.approval_problem`'s reason ("" when approved).
        request_id: The request id, for the ledger row.

    Returns:
        "" when ``problem`` is "" or the ``own-pr`` lapsed (then a :data:`LAPSED_KIND` ledger row is written);
        else ``problem`` with the lapse date appended for an ``own-pr`` (:func:`pending_detail`).
    """
    if not problem or not branch:
        return problem
    if lapse := lapsed(repo, branch):
        from agentteams.vulnscan import ledger   # lazy: only an actual lapse writes

        ledger.append(LAPSED_KIND, op="open-pr", target=str(repo), request_id=request_id, detail=lapse[:1000])
        return ""
    return problem + pending_detail(repo, branch)


def pending_detail(repo: Path, branch: str) -> str:
    """Return the lapse date to add to an ``own-pr`` refusal (``; lapses on <date> ...``), or "".

    Args:
        repo: Canonical repository path.
        branch: The staged branch.

    Returns:
        The suffix, or "" when the newest verdict is not ``own-pr``.
    """
    rec = clock(repo, branch)
    if rec is None:
        return ""
    return (f"; the own-pr lapses on {rec['lapses']} unless an open PR of the owner updates these packages "
            "(then the executor opens its own)")


def packet_ask(repo: Path, branch: str) -> str:
    """Return the request each run's handoff packet carries while an ``own-pr`` clock runs, or "".

    The packet is written by the operator's handoff tooling (outside this package); this is its text, so the
    repository's orchestrator (the only channel to a repository with no Claude session) is asked for its PR and
    told the lapse date. Only host-derived values go in: dates, the digest and package names from the staged record.

    Args:
        repo: Canonical repository path.
        branch: The staged branch.

    Returns:
        One paragraph, or "" when no ``own-pr`` clock runs for that fix.
    """
    rec = clock(repo, branch)
    if rec is None:
        return ""
    names = ", ".join(p for p in _staged(repo).get("packages", []) if isinstance(p, str))[:300] or "the staged packages"
    return (f"You answered own-pr on {rec['since']} for the staged fix {branch} (handoff {rec['handoff_id']}). Please "
            f"open your own PR updating {names} and push it. If no open PR of yours updates them by {rec['lapses']}, "
            "the executor opens its staged PR instead (lapse rule OD10).")
