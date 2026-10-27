"""Remediation effectiveness: does what the daily run stages actually land?

The daily summary gains a **Remediation effectiveness** section (:func:`section`), and the operator is
alerted when remediation stalls (:func:`alerts`, :func:`agent_streak`). The section answers the questions
the scan alone cannot:

- findings fixed and new since the previous run (the net change);
- remediation PRs the executor opened: how many are open, the oldest one's age, how many fail CI or run no
  checks, and how many merged in the last 7 days;
- fixes staged in the spool with no PR yet, and repositories held by :mod:`.retry_guard`.

**Counts only.** The summary is published to the operator's ops repository, so this section names no
repository, PR, host or path; :func:`section` passes its text through the publish scrubber as well. PR
state comes from read-only ``gh pr view`` calls on the PR URLs recorded in the executor's ledger (at most
:data:`MAX_PR_LOOKUPS` of the newest, each with a timeout); a lookup that fails is counted as unknown,
never guessed.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentteams.vulnscan import ledger, paths, retry_guard
from agentteams.vulnscan.report import Report

STALE_DAYS = 3
MERGED_WINDOW_DAYS = 7
MAX_PR_LOOKUPS = 50
AGENT_DOWN_RUNS = 2
_PR_URL = re.compile(r"^https://[A-Za-z0-9.-]+/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+$")
_FAILING = {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}
_AGENT_STATE = re.compile(r"^## Agent run\s*\n\s*\n- state: \*\*([a-z-]+)\*\*", re.MULTILINE)


@dataclass
class PrStats:
    """Counts over the remediation PRs the executor opened (no names)."""

    open: int = 0
    stale: int = 0
    failing: int = 0
    no_checks: int = 0
    merged_recent: int = 0
    unknown: int = 0
    oldest_days: int | None = None
    lookups: int = 0
    notes: list[str] = field(default_factory=list)


def pr_urls(rows: list[dict[str, Any]]) -> list[str]:
    """Return the distinct PR URLs of the executor's ``open-pr`` successes, newest first."""
    seen: dict[str, None] = {}
    for r in reversed(rows):
        url = _pr_of(r) if r.get("op") == "open-pr" and r.get("kind") == "done" else ""
        if _PR_URL.match(url):
            seen.setdefault(url, None)
    return list(seen)


def _pr_of(row: dict[str, Any]) -> str:
    """Return the ``pr`` URL in an ``open-pr`` row's JSON detail, or ""."""
    try:
        url = json.loads(str(row.get("detail") or "{}")).get("pr", "")
    except (ValueError, AttributeError):   # a detail that is not a JSON object names no PR
        return ""
    return url if isinstance(url, str) else ""


def _gh_view(url: str) -> dict[str, Any] | None:
    # Lazy: daily imports this module and the executor; importing the executor (and publish, below) at load time
    # would make this module's import pull in the whole executor stack for a read-only gh lookup.
    from agentteams.vulnscan import executor

    try:
        proc = subprocess.run(["gh", "pr", "view", url, "--json", "state,createdAt,mergedAt,statusCheckRollup"],
                              cwd=executor.gh_cwd(), capture_output=True, text=True, env=executor.gh_env(),
                              timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode:
        return None
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _age_days(stamp: object, now: datetime) -> float | None:
    try:
        return (now - datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))).total_seconds() / 86400
    except ValueError:
        return None


def pr_stats(urls: list[str], *, now: datetime | None = None, view: Any = None) -> PrStats:
    """Look up each PR (newest :data:`MAX_PR_LOOKUPS`) and count states; never raises on a failed lookup.

    Args:
        urls: PR URLs, newest first.
        now: Clock (tests).
        view: ``url -> dict | None`` lookup (default: read-only ``gh pr view``).

    Returns:
        The counts.
    """
    now = now or datetime.now(UTC)
    view = view or _gh_view
    st = PrStats()
    if len(urls) > MAX_PR_LOOKUPS:
        st.notes.append(f"only the newest {MAX_PR_LOOKUPS} of {len(urls)} remediation PRs were looked up")
    for url in urls[:MAX_PR_LOOKUPS]:
        st.lookups += 1
        data = view(url)
        if data is None:
            st.unknown += 1
            continue
        state = str(data.get("state", "")).upper()
        if state == "MERGED":
            age = _age_days(data.get("mergedAt"), now)
            st.merged_recent += age is not None and age <= MERGED_WINDOW_DAYS
            continue
        if state != "OPEN":
            continue
        st.open += 1
        age = _age_days(data.get("createdAt"), now)
        if age is not None:
            st.oldest_days = max(st.oldest_days or 0, int(age))
            st.stale += age > STALE_DAYS
        checks = data.get("statusCheckRollup") or []
        results = {str(c.get("conclusion") or c.get("state") or "").upper() for c in checks if isinstance(c, dict)}
        if not checks:
            st.no_checks += 1
        elif results & _FAILING:
            st.failing += 1
    return st


def staged_without_pr() -> int:
    """Return how many fixes the executor staged that have no PR yet (``<spool>/staged/*.json``)."""
    staged = paths.state_dir() / "spool" / "staged"
    return len(list(staged.glob("*.json"))) if staged.is_dir() else 0


def section(total: Report, previous: Report | None, *, stats: PrStats | None = None) -> tuple[str, PrStats]:
    """Return the summary's **Remediation effectiveness** section (counts only) and the PR counts behind it.

    Args:
        total: This run's report.
        previous: The previous run's report, or None (then nothing counts as fixed).
        stats: Pre-computed PR counts (tests); default: looked up from the ledger's PR URLs.

    Returns:
        (markdown, PR counts).
    """
    if stats is None:
        try:
            rows = ledger.read()
        except ValueError:   # an untrusted ledger is reported by the review; count nothing from it
            rows = []
        stats = pr_stats(pr_urls(rows))
    now_keys = {f.key() for f in total.findings}
    before = {f.key() for f in previous.findings} if previous else set()
    fixed = len(before - now_keys) if previous else 0
    new = len(now_keys - before) if previous else len(now_keys)
    oldest = f"{stats.oldest_days} day(s)" if stats.oldest_days is not None else "n/a"
    lines = [
        "## Remediation effectiveness",
        "",
        "| Measure | Count |",
        "|---|---|",
        f"| Findings fixed since the previous run | {fixed if previous else 'n/a (no previous run)'} |",
        f"| Findings new since the previous run | {new} |",
        f"| Net change in findings | {new - fixed:+d} |" if previous else "| Net change in findings | n/a |",
        f"| Remediation PRs open | {stats.open} (oldest {oldest}) |",
        f"| … open longer than {STALE_DAYS} days | {stats.stale} |",
        f"| … failing CI | {stats.failing} |",
        f"| … with no CI checks | {stats.no_checks} |",
        f"| Remediation PRs merged in the last {MERGED_WINDOW_DAYS} days | {stats.merged_recent} |",
        f"| Fixes staged with no PR yet | {staged_without_pr()} |",
        f"| Repositories held (nothing fixable without a major upgrade) | {retry_guard.held_count()} |",
    ]
    if stats.unknown:
        lines.append(f"| PR lookups that failed (state unknown) | {stats.unknown} |")
    lines.append("")
    lines += [f"- {n}" for n in stats.notes]
    lines.append("Merging stays with the operator: `vulnscan review` lists the PRs; nothing merges automatically.")
    from agentteams.vulnscan import publish   # lazy: publish imports the executor

    return publish.rewrite_paths("\n".join(lines) + "\n"), stats


def alerts(cfg: dict[str, Any], stats: PrStats) -> bool:
    """Notify once per run when remediation PRs are stale or failing CI; return whether one was sent."""
    if not (stats.stale or stats.failing):
        return False
    from agentteams.vulnscan import notify

    return notify.send(cfg, "stale-prs", count=stats.open, old=stats.stale, failing=stats.failing, days=STALE_DAYS)


def agent_streak(state: Path, today: str, current: str) -> int:
    """Return how many runs in a row, ending with this one, the agent did not complete (0 when it did).

    Earlier runs are read newest first from their ``summary.md`` "Agent run" state; the count stops at a
    completed run or at a run with no agent section (agent off, or the run did not reach it).

    Args:
        state: State directory.
        today: This run's directory name.
        current: This run's agent state.

    Returns:
        The streak length.
    """
    if current == "completed":
        return 0
    streak = 1
    for run in reversed([d for d in paths.run_dirs(state) if d.name < today]):
        try:
            m = _AGENT_STATE.search((run / "summary.md").read_text(encoding="utf-8"))
        except OSError:
            break
        if m is None or m.group(1) == "completed":
            break
        streak += 1
    return streak


def agent_alert(cfg: dict[str, Any], state: Path, today: str, current: str) -> bool:
    """Notify when the agent has not completed for :data:`AGENT_DOWN_RUNS` runs in a row; return whether sent."""
    streak = agent_streak(state, today, current)
    if streak < AGENT_DOWN_RUNS:
        return False
    from agentteams.vulnscan import notify

    return notify.send(cfg, "agent-down", count=streak)
