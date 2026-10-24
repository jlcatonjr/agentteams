"""After-action review: what the unattended run found, decided and did, for the operator's check-in.

Autonomous agents act without asking, so evaluation happens here, after the fact. The review
draws only on executor-owned evidence: the MAC-verified ledger, daily summaries, spool
results and the grants. The agents' own narration is shown only as quoted, untrusted
rationale next to what the executor actually did.

A review is open until the operator runs ``vulnscan review --ack``, which writes an ``ack`` row
covering everything up to that point.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agentteams.vulnscan import (
    escalations,
    grant,
    ledger,
    notes,
    paths,
    publish,
    report_store,
    run_records,
)
from agentteams.vulnscan.report import Report

#: Days an action may stay unacknowledged before the review calls it overdue.
OVERDUE_DAYS = 3


def _headline(detail: str) -> str:
    """Return the most useful field of a ``done`` row's JSON detail (PR URL, report, branch, log)."""
    if not detail.startswith("{"):
        return detail
    try:
        d = json.loads(detail)
    except ValueError:
        return detail
    return str(d.get("pr") or d.get("report") or d.get("branch") or d.get("log") or detail)


def _since_last_ack(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    last = max((i for i, r in enumerate(rows) if r.get("kind") == "ack"), default=-1)
    return rows[last + 1:]


def _line(text: Any, cap: int = 300) -> str:
    """Return untrusted text as one sanitised line (no controls, no ``|``), capped."""
    flat = " ".join(escalations.sanitize_evidence(str(text), limit=cap * 4).split()).replace("|", "/")
    return flat if len(flat) <= cap else flat[:cap - 3] + "..."


def _code(text: Any) -> str:
    """Return executor-validated text safe inside an inline code span."""
    return _line(text, 300).replace("`", "'")


def _same_path(target: Any) -> str:
    """Comparable form of a target: scan targets keep the config's ``~`` spelling, executor targets are resolved."""
    text = str(target or "")
    return str(Path(text).expanduser().resolve()) if text.startswith(("/", "~")) else text


def _untrusted(entries: list[str], title: str) -> list[str]:
    """Return ``title`` plus one fenced (inert) block holding every untrusted entry, or nothing."""
    if not entries:
        return []
    body = "\n".join(escalations.sanitize_evidence(e, limit=4000) for e in entries)
    return ["", title, "", report_store.fence(body).rstrip("\n")]


def _escalation_section(rows: list[dict[str, Any]], window: list[dict[str, Any]], for_publish: bool) -> list[str]:
    """Render open escalations (all of them, not just the window) and those resolved in the window."""
    out = ["## Open escalations (remediation is blocked for their scope)", ""]
    opened = escalations.open_escalations(rows)
    evidence: list[str] = []
    for esc in opened:
        need = ("SERIOUS: needs an operator-signed resolution" if esc.kind in escalations.SERIOUS
                else "other: operator resolution in the MAC'd ledger")
        out.append(f"- `{_code(esc.id)}` [{_code(esc.kind)}] scope `{_code(esc.scope)}`; opened {_code(esc.opened_ts)}; "
                   f"run `{_code(esc.run_id)}`; {need} — `vulnscan resolve {_code(esc.id)}`")
        if for_publish:
            evidence.append(f"{esc.id}: {esc.evidence or '(no evidence)'}")
        else:
            out.append(f"  - evidence (untrusted agent text): {_line(esc.evidence or '(no evidence)', 200)}")
    if not opened:
        out.append("- none")
    out += _untrusted(evidence, "Escalation evidence (untrusted agent text):")
    resolved = [r for r in window if r.get("kind") == escalations.ROW_RESOLVED]
    out += ["", "Resolved in this window:", ""]
    notes_: list[str] = []
    for r in resolved:
        line = (f"- `{_code(r.get('esc_id'))}` [{_code(r.get('esc_kind'))}] scope `{_code(r.get('scope'))}`; "
                f"resolved {_code(r.get('ts'))} by `{_code(r.get('method'))}`")
        if for_publish:
            notes_.append(f"{r.get('esc_id')}: {r.get('evidence', '')}")
        else:
            line += f"; reason: {_line(r.get('evidence', ''), 200)}"
        out.append(line)
    if not resolved:
        out.append("- none")
    out += _untrusted(notes_, "Resolution reasons:")
    return out + [""]


def _actions_section(window: list[dict[str, Any]], for_publish: bool) -> list[str]:
    """Render the done/failed rows; in a published review the untrusted columns move into a fence."""
    if not for_publish:
        out = ["## Actions taken", "", "| When | Op | Target | Result | Detail | Agent rationale (untrusted) |",
               "|---|---|---|---|---|---|"]
        for r in window:
            if r["kind"] in ("done", "failed"):
                detail = _line(_headline(r.get("detail", "")), 200)   # one line: a newline would break the table
                rat = _line(r.get("rationale") or "", 200)
                out.append(f"| {_code(r['ts'])} | {_code(r['op'])} | `{_code(r['target'])}` | **{r['kind']}** | "
                           f"{detail} | {rat} |")
        return out + [""]
    out = ["## Actions taken", "", "| # | When | Op | Target | Result |", "|---|---|---|---|---|"]
    fenced: list[str] = []
    for r in window:
        if r["kind"] in ("done", "failed"):
            n = len(fenced) + 1
            out.append(f"| A{n} | {_code(r['ts'])} | {_code(r.get('op'))} | `{_code(r.get('target'))}` | **{r['kind']}** |")
            fenced.append(f"A{n} detail: {_line(_headline(r.get('detail', '')), 200)}\n"
                          f"A{n} rationale: {_line(r.get('rationale') or '', 200)}")
    return out + _untrusted(fenced, "Detail and agent rationale per action (untrusted, inert):") + [""]


def _publish_section(rows: list[dict[str, Any]], window: list[dict[str, Any]], for_publish: bool) -> list[str]:
    """Render the publish grant(s) and this window's publish rows."""
    out = ["## Report publishing", ""]
    try:
        grants, problems = publish.load_active()
    except OSError as exc:
        grants, problems = [], [f"could not read publish grants: {exc}"]
    if not grants:
        out.append("- No publish grant: reports stay local.")
    for g in grants:
        out.append(f"- `{_code(g['grant_id'])}`: ops repo `{_code(Path(str(g['ops_repo'])).name)}`; "
                   f"{publish.uses(g['grant_id'], rows)}/{g['max_uses']} uses; expires {_code(g['expires'])}")
    out += [f"- problem: {_line(p)}" for p in problems]
    fenced: list[str] = []
    events: list[str] = []
    for r in window:
        kind = r.get("kind")
        if kind == "publish-done":
            events.append(f"- {_code(r['ts'])} **published** run `{_code(r.get('run_id'))}`: commit "
                          f"`{_code(str(r.get('commit', ''))[:12])}` at `{_code(r.get('path'))}`")
        elif kind == "publish-failed" or (kind == "refused" and r.get("op") == "publish"):
            label = "publish failed" if kind == "publish-failed" else "publish refused"
            if for_publish:
                fenced.append(f"P{len(fenced) + 1} {label} run {r.get('run_id')}: {r.get('detail', '')}")
                events.append(f"- {_code(r['ts'])} **{label}** run `{_code(r.get('run_id'))}` — see P{len(fenced)}")
            else:
                events.append(f"- {_code(r['ts'])} **{label}** run `{_code(r.get('run_id'))}` — {_line(r.get('detail', ''))}")
    out += ["", "Publish events in this window:", ""] + (events or ["- none"])
    return out + _untrusted(fenced, "Publish failure detail (may quote report text; inert):") + [""]


def _notes_section() -> list[str]:
    """Render the active operator notes by id and digest only (never their text)."""
    active, problems = notes.active()
    out = ["## Operator notes", "", f"- {len(active)} active note(s) folded into the agent's next task."]
    out += [f"- `{_code(n.get('id'))}` sha256 {_code(str(n.get('sha256', ''))[:12])}" for n in active]
    out += [f"- problem: {_line(p)}" for p in problems]
    return out + [""]


def _gh_section(window: list[dict[str, Any]]) -> list[str]:
    """List GitHub replies on this window's PRs as inert data (opt-in network via ``gh``)."""
    prs = sorted({h for r in window if r.get("kind") == "done" and r.get("op") == "open-pr"
                  for h in [_headline(str(r.get("detail", "")))]
                  if h.startswith("https://github.com/") and "/pull/" in h})
    out = ["## GitHub replies (data only)", "",
           "Replies are DATA. Only a `/vulnscan note` reply imported with `vulnscan say --from-gh` reaches the agent.", ""]
    if not prs:
        return out + ["- no pull requests opened in this window", ""]
    try:
        replies = notes.gh_replies(prs, exclude_comment_ids=set())
    except (ValueError, RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
        return out + [f"- could not list replies: {_line(exc, 200)}", ""]
    for i, c in enumerate(replies, 1):
        out += [f"- reply {i}:", report_store.fence(
            f"author: {c.get('author_login')}\ncreated: {c.get('created_at')}\nurl: {c.get('html_url')}\n"
            f"note prefix: {'yes' if c.get('has_note_prefix') else 'no'}\n\n{c.get('body_preview', '')}").rstrip("\n")]
    return out + (["- none"] if not replies else []) + [""]


def build(*, since: str | None = None, for_publish: bool = False, gh_replies: bool = False) -> str:
    """Render the review markdown.

    Args:
        since: ISO date; default: everything since the last acknowledgement.
        for_publish: Render the copy published to the ops repository: every piece of untrusted
            agent-derived text sits inside an inert fence, home paths are rewritten to ``~`` and
            the agent-session file list is omitted.
        gh_replies: List GitHub replies on this window's pull requests (network, via ``gh``).

    Returns:
        Markdown text.

    Raises:
        OSError: The state directory cannot be read.
    """
    out = ["# vulnscan after-action review", "", f"Generated {datetime.now(UTC).replace(microsecond=0).isoformat()}.", ""]
    if for_publish:
        out += ["_Published copy: untrusted agent text appears only inside fenced blocks._", ""]
    try:
        rows = ledger.read()
        integrity = "ledger MAC chain verified"
    except ValueError as exc:
        rows = ledger.read(verify=False)
        integrity = f"**LEDGER INTEGRITY FAILURE: {exc}** — treat everything below as untrusted"
    window = [r for r in rows if r["ts"][:10] >= since] if since else _since_last_ack(rows)
    out += [f"- Ledger: {integrity}; {len(window)} rows in this review window.", ""]
    out += _confinement_notice()
    out += _overdue_notice(rows)
    out += _escalation_section(rows, window, for_publish)

    grants, problems = grant.load_active()
    out += ["## Standing grants", ""]
    if not grants:
        out.append("- **No valid grant.** Agents could triage and plan only; every action was refused.")
    for g in grants:
        used = ledger.uses(g["grant_id"], rows)
        out.append(f"- `{g['grant_id']}`: ops {', '.join(g['ops'])}; {used}/{g['max_uses']} uses; expires {g['expires']}; "
                   f"roots {len(g['repo_roots'])}, hosts {', '.join(g['hosts']) or '—'}")
    out += [f"- problem: {_line(p)}" for p in problems] + [""]
    out += _coverage_section(for_publish)
    out += _dlp_skip_section(for_publish)

    by_kind: dict[str, list[dict[str, Any]]] = {}
    for r in window:
        by_kind.setdefault(r["kind"], []).append(r)
    out += _actions_section(window, for_publish)
    refused = by_kind.get("refused", [])
    out += ["## Refused requests (outside the grant, HALTed, malformed or replayed)", ""]
    if for_publish:
        out += [f"- {len(refused)} refused request(s)."] if refused else ["- none"]
        out += _untrusted([f"{r['ts']} {r.get('op')} {r.get('target')} — {_line(r.get('detail', ''))}" for r in refused],
                          "Refused requests (may quote agent text; inert):")
    else:
        out += [f"- {_code(r['ts'])} {_code(r.get('op'))} `{_code(r.get('target'))}` — {_line(r.get('detail', ''))}"
                for r in refused] or ["- none"]
    begun = {r["request_id"] for r in by_kind.get("begun", [])}
    closed = {r["request_id"] for r in window if r["kind"] in ("done", "failed")}
    dangling = sorted(begun - closed)
    out += ["", "## Anomalies", ""]
    anomalies = [f"- action begun but never finished: {rid}" for rid in dangling]
    latest_run = paths.run_dirs()[-1:]
    anomalies += _gateway_dlp_lines(latest_run[0] / "gateway" / "spend.json") if latest_run else []
    anomalies += _gateway_note_lines(latest_run[0] / "gateway" / "dlp" / "notes") if latest_run else []
    anomalies += _removed_key_lines(latest_run[0] / "agent" / "status.json") if latest_run else []
    rejected =sorted((paths.state_dir() / "spool" / "processed").glob("rejected-*")) \
        if (paths.state_dir() / "spool" / "processed").exists() else []
    names = [p.name for p in rejected[-20:]]   # file names the agent may have chosen: untrusted
    if for_publish:
        anomalies += [f"- {len(rejected)} rejected spool file(s) (possible injection or bypass of the MCP server)"] \
            if rejected else []
        out += (anomalies or ["- none"]) + _untrusted(names, "Rejected spool file names (inert):")
    else:
        anomalies += [f"- rejected spool file (possible injection or bypass of the MCP server): {_code(n)}" for n in names]
        out += anomalies or ["- none"]

    runs = paths.run_dirs()
    if runs:
        latest = Report.from_dict(json.loads((runs[-1] / "summary.json").read_text(encoding="utf-8")))
        acted = {_same_path(r.get("target")) for r in window if r["kind"] == "done"}
        open_findings = [f for f in latest.findings
                         if f.kind == "package" and f.fix and _same_path(f.target) not in acted]
        out += ["", f"## Findings not acted on (latest scan {runs[-1].name})", ""]
        found = [f"{f.target}: {f.name} {f.version} → {f.fix}" for f in open_findings[:50]]   # repo-derived data
        unscored = latest.unscored_findings()
        if unscored:   # counts only: the separate list is in the run's summary.md
            out += [f"- {len(unscored)} finding(s) UNSCORED (no CVSS vector or severity label), counted as "
                    f"{latest.unscored_as.upper()} (`unscored_as`): see the run's summary.md"]
        if for_publish:
            out += [f"- {len(open_findings)} finding(s)."] if found else ["- none"]
            out += _untrusted(found, "Findings (scanner data; inert):")
        else:
            out += [f"- {_line(x)}" for x in found] or ["- none"]
        try:
            out += ["", report_store.render_for_review(runs[-1]).rstrip("\n")]
        except (ValueError, OSError) as exc:
            out += ["", report_store.REVIEW_HEADING, "", f"- could not load the agent report: {_line(exc, 300)}"]
        agent_dir = runs[-1] / "agent"
        if agent_dir.is_dir() and not for_publish:
            out += ["", "## Agent session", ""] + [f"- `{p}`" for p in sorted(agent_dir.iterdir())
                                                   if p.name != run_records.RUNS_DIR]
            earlier = run_records.run_dirs_of(runs[-1])   # earlier agent runs on this date, kept (not this run's)
            out += ["", "Earlier agent runs on this date:", ""] + [f"- `{p}`" for p in earlier] if earlier else []
    out += [""] + _publish_section(rows, window, for_publish) + _notes_section()
    if gh_replies:
        out += _gh_section(window)
    out += ["_Acknowledge with `python -m agentteams.vulnscan review --ack` (recorded in the ledger)._", ""]
    text = "\n".join(out)
    if for_publish:
        text = publish.rewrite_paths(text)
    return text


#: DLP class names as the gateway writes them (anything else in spend.json is shown as ``other``).
_DLP_CLASS = re.compile(r"[a-z][a-z0-9-]{1,40}")


def _gateway_dlp_lines(spend_path: Path) -> list[str]:
    """Return anomaly lines for the latest run's gateway DLP counts (``spend.json``): class names and counts only.

    A blocked request (``dlp_blocks``) or a secret flagged in a reply (``dlp_response_flags``) is an anomaly;
    redactions (``dlp_redactions``) are reported beside them. The matched text is never in ``spend.json``.
    """
    try:
        spend = json.loads(spend_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):   # absent or unreadable: agent_run records that as spend_problem
        return []
    if not isinstance(spend, dict):
        return []

    def counts(key: str) -> str:
        raw = spend.get(key)
        items = raw.items() if isinstance(raw, dict) else ()
        shown = [f"{k if isinstance(k, str) and _DLP_CLASS.fullmatch(k) else 'other'} {v}"
                 for k, v in sorted(items, key=lambda kv: str(kv[0])) if isinstance(v, int) and v > 0]
        return ", ".join(shown)

    out = []
    if counts("dlp_blocks"):
        out.append(f"- gateway DLP refused request(s) carrying a secret or key (by class): {counts('dlp_blocks')}")
    if counts("dlp_response_flags"):
        out.append(f"- gateway DLP saw a secret or key in model replies (by class): {counts('dlp_response_flags')}")
    if counts("dlp_redactions"):
        out.append(f"- gateway DLP redactions in requests (by class; not an anomaly): {counts('dlp_redactions')}")
    return out


def _gateway_note_lines(notes_path: Path) -> list[str]:
    """Return anomaly lines from the launcher's DLP notes (``<run>/gateway/dlp/notes``: counts only, never names)."""
    from agentteams.vulnscan import gateway_context   # lazy: it pulls in the registry and the ssh helpers

    try:
        text = notes_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):   # absent (no gateway start yet) or unreadable: nothing to report
        return []
    notes = {k: int(v) for k, _, v in (ln.partition("=") for ln in text.splitlines()) if v.isdigit()}
    count = notes.get("hosts_conf_unredacted", 0)
    out = [f"- gateway DLP: {gateway_context.unredacted_note(count)}"] if count else []
    if notes.get("secrets_problems"):   # R5: the run was refused (not-run); the paths were on the terminal
        out.append(f"- gateway DLP: {notes['secrets_problems']} secrets director(ies) or file(s) the gateway could not "
                   "use; the agent did not run")
    if notes.get("secrets_skip_ok"):
        out.append(f"- gateway DLP: {notes['secrets_skip_ok']} secrets file(s) not checked (acknowledged in "
                   "agent.dlp_skip_ok): their secrets are not blocked")
    if notes.get("secrets_skip_ok_unmatched"):
        out.append(f"- gateway DLP: {notes['secrets_skip_ok_unmatched']} agent.dlp_skip_ok entr(ies) match no current "
                   "file (moved, or edited since)")
    return out


def _removed_key_lines(status_path: Path) -> list[str]:
    """Return the anomaly line for agent-definition keys the run's copy removed (``status.json``; R1, key names only)."""
    try:
        entry = json.loads(status_path.read_text(encoding="utf-8")).get("agent_definition") or {}
    except (OSError, ValueError, AttributeError):   # absent (no agent run yet) or unreadable: nothing to report
        return []
    keys = [k for k in entry.get("removed_keys") or [] if isinstance(k, str) and k.replace("-", "_").isidentifier()]
    if not keys:
        return []
    return [f"- agent definition: the run's copy removed {', '.join(keys[:20])} from the front matter "
            "(vulnscan's own flags apply instead)"]


def _dlp_skip_section(for_publish: bool) -> list[str]:
    """Return the ``agent.dlp_skip_ok`` entries (R5): path and sha256 on the terminal, a count when published."""
    from agentteams.vulnscan import agent_settings, daily, gateway_context   # lazy, as in _coverage_section

    try:
        entries = gateway_context.validate_skip_ok(agent_settings.agent_cfg(daily.load_config())["dlp_skip_ok"])
    except FileNotFoundError:   # no config yet: no acknowledgements
        return []
    except (OSError, ValueError) as exc:   # shown, not hidden: the coverage section reports the config too
        return ["## DLP skip acknowledgements", "", f"- config unusable, entries unknown: {_line(exc)}", ""]
    if not entries:
        return []
    out = ["## DLP skip acknowledgements (`agent.dlp_skip_ok`)", ""]
    if for_publish:
        return [*out, f"- {len(entries)} secrets file version(s) the DLP check may skip.", ""]
    return [*out, *(f"- `{_code(path)}` sha256 `{digest}`" for path, digest in entries), ""]


def _confinement_notice() -> list[str]:
    """Return a loud warning when the operator opted out of the tools container (``confine.subprocesses: false``)."""
    from agentteams.vulnscan import daily, subproc_confine   # lazy, as in _coverage_section

    try:
        cfg = daily.load_config()
    except (OSError, ValueError):   # a missing or unusable config is reported by the coverage section
        return []
    if not subproc_confine.opted_out(cfg):
        return []
    return [f"> **{subproc_confine.OPT_OUT_WARNING}.** Every executor op, apply-worktree staging and server-kit audit in "
            "this window ran untrusted-repository tooling with this account's files and network. Remove the opt-out once "
            "`tools-selftest` passes.", ""]


def _coverage_section(for_publish: bool) -> list[str]:
    """Return the coverage section (:mod:`.coverage`); the published copy has totals only, no project names."""
    from agentteams.vulnscan import coverage, daily   # lazy: daily imports this module's callers

    try:
        cfg = daily.load_config()
    except FileNotFoundError:
        cfg = dict(daily.DEFAULT_CONFIG)
    except (OSError, ValueError) as exc:
        return ["## Coverage", "", f"- config unusable, coverage unknown: {_line(exc)}", ""]
    runs = paths.run_dirs()
    latest = None   # the latest scan adds the per-repository brief counts (summary.json never names private packages)
    try:
        latest = Report.from_dict(json.loads((runs[-1] / "summary.json").read_text(encoding="utf-8"))) if runs else None
    except (OSError, ValueError, KeyError, TypeError):
        latest = None   # counts are then left out; the coverage states do not depend on them
    return [*coverage.section(cfg, names=not for_publish, report=latest).rstrip("\n").split("\n"), ""]


def acknowledge(note: str = "") -> dict[str, Any]:
    """Record that the operator reviewed everything up to now.

    Args:
        note: Optional operator note.

    Returns:
        The ledger row.
    """
    with ledger.locked():
        return ledger.append("ack", detail=note[:1000])


def _unacked_actions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in _since_last_ack(rows) if r.get("kind") in ("done", "failed")]


def overdue(days: int = OVERDUE_DAYS, rows: list[dict[str, Any]] | None = None) -> bool:
    """Return whether actions have gone unreviewed for more than ``days``.

    Args:
        days: Threshold.
        rows: Ledger rows already read (default: read and verify the ledger).

    Returns:
        True when the oldest unacknowledged action is older than the threshold, or the ledger
        cannot be read or verified.
    """
    if rows is None:
        try:
            rows = ledger.read()
        except (ValueError, OSError):
            return True
    acts = _unacked_actions(rows)
    if not acts:
        return False
    return datetime.fromisoformat(acts[0]["ts"]) < datetime.now(UTC) - timedelta(days=days)


def _overdue_notice(rows: list[dict[str, Any]]) -> list[str]:
    """Return the review's "overdue" call-out (executor-owned counts and times only), or nothing."""
    if not overdue(rows=rows):
        return []
    acts = _unacked_actions(rows)
    line = (f"- **After-action review overdue:** {len(acts)} unacknowledged action(s), the oldest at "
            f"{_code(acts[0]['ts'])} (more than {OVERDUE_DAYS} days ago). Review them, then run "
            "`python -m agentteams.vulnscan review --ack`.")
    return [line, ""]

