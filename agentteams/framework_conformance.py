"""Standard agent-infrastructure framework-conformance check (≤24h, no-fetch).

Split out of ``agentteams.framework_research`` (CH-07 module-size ceiling). This module holds the
agent-side complement to ``.github/workflows/framework-auto-update.yml``: the ledger-gated
``run_agent_conformance_check`` that routes ``@framework-adapters-expert`` to the Stage-2 adapter
triage in ``references/provider-adapter-refresh.procedure.md`` §7, plus its report renderer. It
never fetches and never edits adapter code (Constitutional C-4); its only write is a small
best-effort local ledger. It imports the snapshot helpers from ``framework_research`` (one
direction only — ``framework_research`` never imports this module at load time).
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any

from agentteams.framework_research import (
    _load_snapshot,
    _snapshot_age_hours,
    _snapshot_path,
    _utcnow,
)


# ---------------------------------------------------------------------------
# Standard agent-infrastructure conformance check (no-fetch, ≤24h cadence).
#
# The agent-side complement to ``.github/workflows/framework-auto-update.yml``: where the cron
# *detects and records* upstream drift into an observation-stanza PR, this lets the agent
# infrastructure (``@orchestrator`` → ``@framework-adapters-expert``) decide, at most once per
# window, whether observed drift warrants routing the Stage-2 adapter triage in
# ``references/provider-adapter-refresh.procedure.md``. It NEVER fetches (that is the cron's job)
# and NEVER edits adapter code — detection + routing only (Constitutional C-4).
# ---------------------------------------------------------------------------

#: Agent-conformance-check ledger (gitignored — operator-local, one per checkout). ``last_checked``
#: records when the AGENT infrastructure last ran this triage; it is a DISTINCT axis from the
#: snapshot's ``generated_at`` (when upstream docs were last fetched, restamped daily by the cron),
#: because gating on snapshot age alone could never signal "the agent has not acted."
AGENT_CHECK_LEDGER_REL = "tmp/daily-pipeline/framework-research/agent-check-ledger.json"  # gitignored — operator-local

#: Default ≤24h cadence for the standard agent conformance check.
AGENT_CHECK_WINDOW_HOURS = 24.0

#: Short retry throttle applied ONLY to inconclusive (stale-snapshot) outcomes, so an offline run
#: does not advance the 24h clock (which would mask real drift for a full day) yet also does not
#: re-attempt on every session.
AGENT_CHECK_ATTEMPT_THROTTLE_HOURS = 1.0

#: fetch_status values that are a real 200-level integrity finding (relocated / stub page), as
#: opposed to the transient skipped/failed states. Mirrors ``provider-adapter-refresh`` §3.
_FETCH_ISSUE_STATES = {"moved", "empty"}


def _agent_check_ledger_path(repo_root: Path) -> Path:
    return repo_root / AGENT_CHECK_LEDGER_REL


def _load_agent_check_ledger(repo_root: Path) -> dict[str, Any]:
    path = _agent_check_ledger_path(repo_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_agent_check_ledger(repo_root: Path, ledger: dict[str, Any]) -> bool:
    """Persist the ledger. Best-effort: a read-only module tree (e.g. an ``agentteams`` package
    installed into read-only ``site-packages``) must not crash the check — it degrades to a
    non-persisted result. Returns True when the ledger was written, False when it could not be."""
    path = _agent_check_ledger_path(repo_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def _hours_since(iso_ts: str | None, now: _dt.datetime) -> float | None:
    """Whole+fractional hours between an ISO-8601 UTC stamp and ``now``; ``None`` if unparseable."""
    if not iso_ts:
        return None
    try:
        dt = _dt.datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return (now - dt).total_seconds() / 3600.0


def run_agent_conformance_check(
    repo_root: Path,
    *,
    window_hours: float = AGENT_CHECK_WINDOW_HOURS,
    now: _dt.datetime | None = None,
) -> dict[str, Any]:
    """Run the standard agent-infrastructure conformance check (no-fetch, ≤``window_hours`` cadence).

    Reads the already-produced upstream snapshot and decides whether observed drift or a relocated
    doc warrants routing ``@framework-adapters-expert`` to the procedure's Stage 2. It never fetches
    (``refresh_snapshot`` is the cron's job) and never writes ``agentteams/frameworks/*.py``. The
    decision is recorded in a per-checkout ledger so the check runs at most once per ``window_hours``.

    Classification (see ``provider-adapter-refresh.procedure.md`` §3/§5): a framework is *drifting*
    when its ``keys_diff.missing_upstream`` is non-empty (a token we rely on vanished), and a
    *fetch-issue* when its ``fetch_status`` is ``moved``/``empty`` (relocated/stub doc).
    ``new_upstream`` is excluded — the scan only searches ``expected_keys`` so it is structurally
    always empty. Routing is idempotent per distinct drift: a locally-computed ``drift_signature``
    over the actionable ``missing_upstream`` tokens + fetch-issue set is stored, so the same drift
    is not re-routed every window (deduping against the cron's own PR handling).

    Args:
        repo_root: Repository root holding the snapshot and ledger under
            ``tmp/daily-pipeline/`` (gitignored, operator-local).
        window_hours: Skip when the ledger's ``last_checked`` is younger than this. ``0`` forces a
            check regardless of the ledger (used by ``--force`` and the daily cron step).
        now: Injectable current UTC time (for tests); defaults to :func:`_utcnow`.

    Returns:
        A JSON-safe dict with ``status`` (``fresh-skip`` | ``attempt-throttled`` | ``stale-snapshot``
        | ``no-drift`` | ``drift``), ``ran``, ``needs_agent_action``, ``route`` (action needed AND
        not already routed for this ``drift_signature``), ``already_routed``, ``drift_frameworks``,
        ``fetch_issue_frameworks``, ``drift_signature``, ``snapshot_generated_at``,
        ``hours_since_last_check`` and a human ``message``. The payload carries only status codes,
        framework ids and configured source URLs — never fetched page text (Constitutional C-4).
    """
    now = now or _utcnow()
    ledger = _load_agent_check_ledger(repo_root)
    since_check = _hours_since(ledger.get("last_checked"), now)

    # 1) 24h gate — measured against the AGENT ledger, not the snapshot's generated_at.
    if window_hours > 0 and since_check is not None and since_check < window_hours:
        return {
            "status": "fresh-skip",
            "ran": False,
            "needs_agent_action": False,
            "route": False,
            "already_routed": False,
            "drift_frameworks": ledger.get("drift_frameworks", []),
            "fetch_issue_frameworks": ledger.get("fetch_issue_frameworks", []),
            "drift_signature": ledger.get("last_routed_hash"),
            "snapshot_generated_at": ledger.get("snapshot_generated_at"),
            "hours_since_last_check": since_check,
            "message": f"conformance check ran {since_check:.1f}h ago (< {window_hours:.0f}h window); skipping.",
        }

    # 2) Load the snapshot the cron produced — NEVER fetch here (read-only in-session, C-4/adv F4).
    #    Snapshot freshness is judged against a bound INDEPENDENT of the force/ledger window: --force
    #    bypasses the 24h *ledger* gate (re-check now) but must not launder a month-old snapshot into
    #    an authoritative "no-drift" verdict. A window<=0 (force) still requires a snapshot younger
    #    than the default 24h to be usable.
    snapshot = _load_snapshot(_snapshot_path(repo_root))
    snapshot_age = _snapshot_age_hours(snapshot, now=now) if snapshot else None
    snapshot_max_age = window_hours if window_hours > 0 else AGENT_CHECK_WINDOW_HOURS
    snapshot_usable = (
        snapshot is not None
        and snapshot_age is not None
        and snapshot_age <= snapshot_max_age
    )

    # 3) Inconclusive: no fresh snapshot to judge (cron down / offline). Do NOT advance the 24h
    #    clock — that would buy 24h of silence exactly when the safety net is needed (adv F2). Use a
    #    short ``last_attempted`` throttle so we also do not re-attempt on every session.
    if not snapshot_usable:
        since_attempt = _hours_since(ledger.get("last_attempted"), now)
        if (
            window_hours > 0
            and since_attempt is not None
            and since_attempt < AGENT_CHECK_ATTEMPT_THROTTLE_HOURS
        ):
            return {
                "status": "attempt-throttled",
                "ran": False,
                "needs_agent_action": False,
                "route": False,
                "already_routed": False,
                "drift_frameworks": [],
                "fetch_issue_frameworks": [],
                "drift_signature": None,
                "snapshot_generated_at": snapshot.get("generated_at") if snapshot else None,
                "hours_since_last_check": since_check,
                "message": "no usable snapshot; retry throttled (recently attempted).",
            }
        ledger["last_attempted"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        _write_agent_check_ledger(repo_root, ledger)
        return {
            "status": "stale-snapshot",
            "ran": True,
            "needs_agent_action": False,
            "route": False,
            "already_routed": False,
            "drift_frameworks": [],
            "fetch_issue_frameworks": [],
            "drift_signature": None,
            "snapshot_generated_at": snapshot.get("generated_at") if snapshot else None,
            "hours_since_last_check": since_check,
            "message": (
                "no snapshot younger than the window; the 24h clock was NOT advanced. Rely on the "
                "daily cron, or refresh explicitly with `python scripts/research_claude_code_docs.py`."
            ),
        }

    # 4) Fresh snapshot → classify actionable drift.
    frameworks = snapshot.get("frameworks") or {}
    drift_frameworks = sorted(
        fid for fid, entry in frameworks.items()
        if (entry.get("keys_diff") or {}).get("missing_upstream")
    )
    fetch_issue_frameworks = sorted(
        fid for fid, entry in frameworks.items()
        if entry.get("fetch_status") in _FETCH_ISSUE_STATES
    )
    needs_action = bool(drift_frameworks or fetch_issue_frameworks)

    drift_signature: str | None = None
    if needs_action:
        import hashlib as _hashlib

        sig_payload = {
            "drift": {
                fid: sorted((frameworks[fid].get("keys_diff") or {}).get("missing_upstream", []))
                for fid in drift_frameworks
            },
            "fetch_issue": fetch_issue_frameworks,
        }
        drift_signature = _hashlib.sha256(
            json.dumps(sig_payload, sort_keys=True).encode()
        ).hexdigest()[:12]

    already_routed = bool(
        needs_action and drift_signature and ledger.get("last_routed_hash") == drift_signature
    )
    route = bool(needs_action and not already_routed)

    ledger.update({
        "schema": 1,
        "last_checked": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "snapshot_generated_at": snapshot.get("generated_at", ""),
        "drift_frameworks": drift_frameworks,
        "fetch_issue_frameworks": fetch_issue_frameworks,
    })
    if route:
        ledger["last_routed_hash"] = drift_signature
    _write_agent_check_ledger(repo_root, ledger)

    if not needs_action:
        message = "no upstream drift or relocation observed across the six frameworks; no action."
    elif already_routed:
        message = f"drift already routed for signature {drift_signature}; no new routing needed."
    else:
        affected = sorted(set(drift_frameworks) | set(fetch_issue_frameworks))
        sources = {fid: frameworks[fid].get("source_url", "") for fid in affected}
        message = (
            "ROUTE @framework-adapters-expert → provider-adapter-refresh.procedure.md §5 Stage 2 "
            f"for: {', '.join(affected)}. Sources: {sources}"
        )

    return {
        "status": "drift" if needs_action else "no-drift",
        "ran": True,
        "needs_agent_action": needs_action,
        "route": route,
        "already_routed": already_routed,
        "drift_frameworks": drift_frameworks,
        "fetch_issue_frameworks": fetch_issue_frameworks,
        "drift_signature": drift_signature,
        "snapshot_generated_at": snapshot.get("generated_at", ""),
        "hours_since_last_check": since_check,
        "message": message,
    }


def render_agent_check_report(result: dict[str, Any]) -> str:
    """Render the human + machine-readable lines for a :func:`run_agent_conformance_check` result.

    Shared by ``scripts/research_claude_code_docs.py --agent-check`` (the repo daily-pipeline wrapper)
    and the portable ``agentteams --agent-check`` CLI so both surfaces print identical
    ``STATUS=`` / ``NEEDS_AGENT_ACTION=`` / ``ROUTE=`` lines. The output carries only status codes,
    framework ids and the procedure anchor — never fetched page text (Constitutional C-4).
    """
    lines = [
        result["message"],
        f"STATUS={result['status']}",
        f"NEEDS_AGENT_ACTION={'true' if result['needs_agent_action'] else 'false'}",
        f"ROUTE={'true' if result['route'] else 'false'}",
    ]
    if result["route"]:
        lines += [
            "ROUTE_TARGET=@framework-adapters-expert",
            f"DRIFT_FRAMEWORKS={','.join(result['drift_frameworks'])}",
            f"FETCH_ISSUE_FRAMEWORKS={','.join(result['fetch_issue_frameworks'])}",
            "PROCEDURE=references/provider-adapter-refresh.procedure.md#7-the-agent-infrastructure-standard-check-24h-trigger",
        ]
    return "\n".join(lines)

