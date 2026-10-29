"""Remediation readiness: what would stop the executor from acting on this run's findings (plan B1, B2, B4).

The daily summary's coverage section says which workspaces are scanned and which a grant covers. This module adds
what the executor needs beyond that, checked on the host after the scan, so a blocker shows up in the summary and
one notification instead of as a refusal on every request (2026-10-26: an unbound tools container and then a
blanket escalation refused every stage-fix, open-pr and server op for a day):

- the agentteams version that ran (B4; a reinstall changes the tools image tag, :func:`subproc_confine.image_tag`);
- the schedule drift (:func:`schedule.drift`, B3);
- the tools container's binding when subprocess confinement is on (:func:`subproc_confine.unavailable_reason`, whose
  probe is :mod:`.docker_readiness`: inside a daily run, that run's one check), with its reason code, and the run's
  per-layer Docker record (``docker``);
- open escalations (:func:`escalations.open_escalations`): each blocks its scope until the operator resolves it;
- the active grants, those expiring within :data:`EXPIRY_WARN_DAYS`, the grant directory's problems, and the
  configured servers no active grant names for ``server-audit`` / ``server-dryrun``.

:func:`collect` never raises: an unreadable source becomes a ``blocked`` or ``grant_problems`` line. :func:`notify` sends the fixed-text
``grant-expiring`` and ``remediation-blocked`` messages (identifiers and counts only, :mod:`.notify`).
"""

from __future__ import annotations

import importlib.metadata
import subprocess
from datetime import UTC, datetime
from typing import Any

from agentteams.vulnscan import notify as _notify

#: A grant expiring within this many days is reported (renewing needs the operator's key).
EXPIRY_WARN_DAYS = 7
SERVER_OPS = ("server-audit", "server-dryrun")


def version() -> str:
    """Return the installed agentteams version.

    Returns:
        The version string, or ``unknown`` when agentteams is not installed as a distribution.
    """
    try:
        return importlib.metadata.version("agentteams")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def collect(cfg: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Return the run's remediation readiness (see the module docstring); never raises.

    Args:
        cfg: Daily config.
        now: The time to judge expiry against (tests).

    Returns:
        ``{"agentteams_version", "schedule_drift", "tools_container", "tools_container_code", "docker",
        "open_escalations", "grants", "grants_expiring", "grant_problems", "servers_uncovered", "configured",
        "blocked"}``; ``docker`` is the daily run's :class:`.docker_readiness.Readiness` as
        ``{layer: {state, code, message}}`` ({} outside a daily run);
        ``blocked`` lists the reasons no executor op can run for some scope (empty: none found). Remediation counts
        as ``configured`` when a grant exists (valid or not), agent mode is on, or ``fix_mode`` is
        ``apply-worktree``; otherwise nothing is reported as blocked.
    """
    from agentteams.vulnscan import docker_readiness, escalations, grant, schedule, subproc_confine

    now = now or datetime.now(UTC)
    run_docker = docker_readiness.current()
    out: dict[str, Any] = {"agentteams_version": version(), "schedule_drift": "", "tools_container": "",
                           "tools_container_code": "", "docker": run_docker.to_dict() if run_docker else {},
                           "open_escalations": [], "grants": [], "grants_expiring": [], "grant_problems": [],
                           "servers_uncovered": [], "blocked": []}
    out["schedule_drift"] = schedule.drift(cfg)
    try:
        grants, problems = grant.load_active(now=now)
    except (OSError, ValueError) as exc:
        grants, problems = [], [f"grant directory unreadable: {exc}"[:300]]
    out["grant_problems"] = list(problems)
    for g in grants:
        try:   # load_active verified the record, expiry included; a malformed date is reported, never raised
            days = (datetime.fromisoformat(str(g["expires"])) - now).days
        except (KeyError, TypeError, ValueError):
            out["grant_problems"].append(f"grant {g.get('grant_id')}: unreadable expiry")
            continue
        out["grants"].append({"grant_id": g["grant_id"], "ops": sorted(g["ops"]), "expires": g["expires"],
                              "days_left": days})
        if days < EXPIRY_WARN_DAYS:
            out["grants_expiring"].append({"grant_id": g["grant_id"], "days_left": days})
    hosts = [str(h) for h in cfg.get("servers") or []]
    out["servers_uncovered"] = [h for h in hosts
                                if not any(op in g["ops"] and h in (g.get("hosts") or []) for g in grants
                                           for op in SERVER_OPS)]
    agent = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
    out["configured"] = bool(grants or problems or agent.get("enabled") or cfg.get("fix_mode") == "apply-worktree")
    if not out["configured"]:   # plan-only use: no Docker probe, no ledger read (it would create the ledger key)
        return out
    try:   # only the executor's ops need the tools container, and they need a grant (or apply-worktree staging)
        if (grants or cfg.get("fix_mode") == "apply-worktree") and not subproc_confine.opted_out(cfg):
            why = subproc_confine.unavailable_reason(subproc_confine.image_tag(subproc_confine.settings(cfg)))
            out["tools_container"], out["tools_container_code"] = str(why), getattr(why, "code", "")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        out["tools_container"] = f"could not check: {type(exc).__name__}: {exc}"[:300]
    if out["tools_container"]:
        out["blocked"].append(f"tools container: {out['tools_container']}")
    try:
        opened = escalations.open_escalations()
        out["open_escalations"] = [{"id": e.id, "kind": e.kind, "scope": e.scope} for e in opened]
        out["blocked"] += [f"escalation {e.id} ({e.kind}) blocks {'everything' if e.scope == '*' else e.scope}"
                           for e in opened]
    except (OSError, ValueError) as exc:   # an unverifiable ledger blocks the executor too
        out["blocked"].append(f"executor ledger unreadable: {exc}"[:300])
    if not grants:
        out["blocked"].append("no valid grant: the executor refuses every op")
    return out


def markdown(st: dict[str, Any]) -> str:
    """Return the summary's ``## Remediation readiness`` section.

    Args:
        st: :func:`collect`'s result.

    Returns:
        Markdown.
    """
    lines = ["## Remediation readiness", "", f"- agentteams {st['agentteams_version']}"]
    if not st.get("configured", True):
        lines.append("- remediation is not configured (no grant, agent mode off, fix_mode plan)")
    elif st["blocked"]:
        lines += ["- **blocked**:"] + [f"  - {b}" for b in st["blocked"]]
        lines.append("  - see `vulnscan halts` (escalations) and `vulnscan tools-selftest` (tools container)")
    else:
        lines.append("- nothing blocks the executor")
    for g in st["grants"]:
        warn = " — **renew it** (your key)" if g["days_left"] < EXPIRY_WARN_DAYS else ""
        lines.append(f"- grant {g['grant_id']} ({', '.join(g['ops'])}) expires in {g['days_left']} day(s){warn}")
    lines += [f"- grant directory: {p}" for p in st["grant_problems"]]
    if st["servers_uncovered"]:
        lines.append("- servers no grant names for server-audit / server-dryrun: "
                     + ", ".join(st["servers_uncovered"]))
    if st["schedule_drift"]:
        lines.append(f"- schedule: {st['schedule_drift']}")
    return "\n".join(lines) + "\n"


def notify(cfg: dict[str, Any], st: dict[str, Any]) -> None:
    """Send ``remediation-blocked`` when anything blocks the executor and ``grant-expiring`` per expiring grant.

    Fixed text with identifiers and counts only; the daily run calls this once per run (so at most once a day on
    the schedule).

    Args:
        cfg: Daily config (its ``notify`` setting).
        st: :func:`collect`'s result.
    """
    if st["blocked"]:
        _notify.send(cfg, "remediation-blocked", count=len(st["blocked"]))
    for g in st["grants_expiring"]:
        _notify.send(cfg, "grant-expiring", id=g["grant_id"], days=max(int(g["days_left"]), 0))
