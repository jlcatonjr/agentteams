"""One unattended remediation attempt's outcome and its ``remediation.md`` (plan vulnscan-docker-readiness T2-B).

``scripts/vulnscan-remediation/vulnscan-remediate-auto.sh`` runs every 30 minutes after the daily scan. Each attempt
calls ``python -m agentteams.vulnscan remediation-report LEDGER SINCE`` and decides from its answer whether the run
is done or blocked (retried on the next tick). The answer comes from the executor's ledger, not from log text.

Every ``refused`` / ``failed`` ledger row whose ``ts`` is at or after the attempt's start is listed under *Needs you*
and classified:

- **infra** (retryable) only when its ``detail`` *starts with* the executor's own prefix
  :data:`INFRA_DETAIL_PREFIX` (``subproc_confine.Unavailable``), or its ``reason_code`` *starts with*
  :data:`INFRA_REASON_PREFIX`. A row without ``reason_code``, or with a non-string one, has none.
- **policy** (final) otherwise.

The match is anchored, never a substring: a detail can echo repository or orchestrator text (C-4), and a substring
match would let that text relabel a policy refusal as retryable. A ledger that cannot be read fails closed: it
counts as one infra refusal, so the attempt is retried instead of marked done. Stdlib only; no side effects except
the CLI's ``--out`` file.

With ``--agent-since`` (the day's agent run start, from ``<run>/agent/status.json``), the rows from then up to the
attempt's start (the agent's own executor requests) are reported in their own section and their refusals listed
under *Needs you*. They never count toward ``infra_count``: a remediation retry does not redo the agent's requests.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: The executor's refusal text when the tools container cannot be used (``subproc_confine.Unavailable``).
INFRA_DETAIL_PREFIX = "tools container unavailable:"
#: Reason codes of the ``infra.*`` family (``vulnscan-ensure-docker.sh``, and the executor once it records them).
INFRA_REASON_PREFIX = "infra."
#: How much of a row's detail a Needs-you line keeps.
DETAIL_CHARS = 200

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_PR_URL = re.compile(r"https://github\.com/\S+/pull/\d+")
_REPO_LINE = re.compile(r"^[\w.-]+: (stage-fix|handoff|findings|no goose|already|vulnscan/)")

COMMANDS = ("remediation-report",)


@dataclass
class Outcome:
    """What one attempt's ledger rows say.

    Attributes:
        done: Executor operations that completed.
        infra: Needs-you lines for refusals and failures caused by infrastructure (retryable).
        policy: Needs-you lines for every other refusal or failure (final).
        error: Why the ledger could not be read, or None.
        skipped: Ledger lines that are not a JSON object with a parseable ``ts`` (reported, not classified).
    """

    done: int = 0
    infra: list[str] = field(default_factory=list)
    policy: list[str] = field(default_factory=list)
    error: str | None = None
    skipped: int = 0

    @property
    def infra_count(self) -> int:
        """Infra refusals, counting an unreadable ledger as one (fail closed)."""
        return len(self.infra) + (1 if self.error else 0)

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form the remediation script reads.

        Returns:
            ``done``, ``infra``, ``policy``, ``error``, ``skipped`` and ``infra_count``.
        """
        return {"done": self.done, "infra": list(self.infra), "policy": list(self.policy), "error": self.error,
                "skipped": self.skipped, "infra_count": self.infra_count}


def parse_ts(value: str) -> datetime:
    """Parse an ISO timestamp; one without an offset is UTC.

    Args:
        value: ``2026-10-29T13:00:00Z``, ``...+00:00``, ``...-04:00`` or naive.

    Returns:
        An aware datetime.

    Raises:
        ValueError: ``value`` is not an ISO timestamp.
    """
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _row_ts(row: dict[str, Any]) -> datetime | None:
    try:
        return parse_ts(str(row["ts"]))
    except (ValueError, KeyError, TypeError):
        return None


def _parse_line(line: str) -> dict[str, Any] | None:
    try:
        row = json.loads(line)
    except ValueError:
        return None
    return row if isinstance(row, dict) else None


def _one_line(text: str) -> str:
    return _CONTROL.sub(" ", text).strip()


def classify(row: dict[str, Any]) -> str | None:
    """Classify one ledger row.

    Args:
        row: A parsed ledger row.

    Returns:
        ``done``, ``infra`` or ``policy``; None for every other kind.
    """
    kind = row.get("kind")
    if kind == "done":
        return "done"
    if kind not in ("refused", "failed"):
        return None
    detail = row.get("detail")
    code = row.get("reason_code")
    if (isinstance(detail, str) and detail.startswith(INFRA_DETAIL_PREFIX)) or \
       (isinstance(code, str) and code.startswith(INFRA_REASON_PREFIX)):
        return "infra"
    return "policy"


def needs_line(row: dict[str, Any]) -> str:
    """Return the Needs-you line for a refused or failed row (one line, detail truncated).

    Args:
        row: A parsed ledger row.

    Returns:
        ``<op> <kind> for <target>: <detail>``, with control characters replaced so ledger text cannot add lines.
    """
    detail = row.get("detail")
    text = detail if isinstance(detail, str) else json.dumps(detail)
    code = row.get("reason_code")
    tag = f" [{_one_line(code)}]" if isinstance(code, str) and code else ""
    return _one_line(f"{row.get('op')} {row.get('kind')} for {row.get('target')}{tag}: {text[:DETAIL_CHARS]}")


def outcome_from_rows(rows: Iterable[dict[str, Any]], since: str, until: str | None = None) -> Outcome:
    """Classify the rows at or after ``since`` (and before ``until``, when given).

    Args:
        rows: Parsed ledger rows; a row without a parseable ``ts`` is counted in ``skipped``.
        since: The attempt's start (ISO; UTC or with an offset).
        until: Exclusive end (ISO), or None for no end.

    Returns:
        The attempt's :class:`Outcome`.

    Raises:
        ValueError: ``since`` or ``until`` is not an ISO timestamp.
    """
    start = parse_ts(since)
    end = parse_ts(until) if until is not None else None
    res = Outcome()
    for row in rows:
        ts = _row_ts(row)
        if ts is None:
            res.skipped += 1
            continue
        if ts < start or (end is not None and ts >= end):
            continue
        cls = classify(row)
        if cls == "done":
            res.done += 1
        elif cls == "infra":
            res.infra.append(needs_line(row))
        elif cls == "policy":
            res.policy.append(needs_line(row))
    return res


def outcome(ledger: str | Path, since: str, until: str | None = None) -> Outcome:
    """Read ``ledger`` (JSON lines) and classify this attempt's rows.

    Args:
        ledger: Path of ``ledger.jsonl``.
        since: The attempt's start (ISO; UTC or with an offset).
        until: Exclusive end (ISO), or None for no end.

    Returns:
        The :class:`Outcome`; an unreadable ledger sets ``error`` (fail closed) rather than raising.

    Raises:
        ValueError: ``since`` or ``until`` is not an ISO timestamp.
    """
    if not isinstance(since, str):
        raise ValueError(f"since must be an ISO timestamp string, not {type(since).__name__}")
    parse_ts(since)   # a bad SINCE is a usage error, not an unreadable ledger
    if until is not None:
        parse_ts(until)
    try:
        text = Path(ledger).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return Outcome(error=f"ledger unreadable: {type(exc).__name__}")
    parsed = [_parse_line(line) for line in text.splitlines() if line.strip()]
    res = outcome_from_rows([r for r in parsed if r is not None], since, until)
    res.skipped += sum(1 for r in parsed if r is None)
    return res


def render_markdown(res: Outcome, *, run_name: str, attempt: int, log_text: str = "",
                    agent: Outcome | None = None) -> str:
    """Render ``remediation.md`` for one attempt.

    Args:
        res: The attempt's outcome.
        run_name: The scan run's name (its date).
        attempt: The attempt number for that run.
        log_text: This attempt's part of ``remediation.log`` (PR links, per-repo lines, warnings).
        agent: The day's agent run's executor rows (before the attempt), or None.

    Returns:
        The markdown text, newline-terminated.
    """
    log = _ANSI.sub("", log_text)
    prs = sorted(set(_PR_URL.findall(log)))
    lines = [ln.strip() for ln in log.splitlines()]
    latest: dict[str, str] = {}   # one line per repo: its last status this attempt (steps run in order)
    for ln in lines:
        if _REPO_LINE.match(ln):
            repo = ln.split(":", 1)[0]
            latest.pop(repo, None)
            latest[repo] = ln
    skipped = [ln for ln in lines if "operator-only, skipped" in ln or ln.startswith(("WARN", "PROBLEM"))]
    needs = list(dict.fromkeys(skipped))
    needs += [f"refused (infra, retried): {ln}" for ln in res.infra]
    needs += [f"refused (policy): {ln}" for ln in res.policy]
    if res.error:
        needs.append(f"remediation outcome unknown: {res.error}")
    if res.skipped:
        needs.append(f"{res.skipped} ledger line(s) could not be read (not classified; check the ledger)")
    if agent is not None:
        needs += [f"agent run refused: {ln}" for ln in agent.infra + agent.policy]
        if agent.error:
            needs.append(f"agent run rows not listed: {agent.error}")
    out = [f"# Vulnscan remediation — {_one_line(run_name)} (attempt {attempt})", "",
           f"PRs opened or claimed this run: {len(prs)}",
           f"Executor operations this attempt: {res.done} done, {len(res.infra)} refused for infrastructure, "
           f"{len(res.policy)} refused by policy"]
    out += [f"- {p}" for p in prs] + ["", "## Per repo", ""] + [f"- {ln}" for ln in sorted(latest.values())]
    if agent is not None:
        out += ["", "## Agent run (executor rows)", "",
                f"Before this attempt, the agent's requests: {agent.done} done, "
                f"{len(agent.infra) + len(agent.policy)} refused (listed under Needs you; never retried here)"]
    out += ["", "## Needs you", ""] + ([f"- {ln}" for ln in needs] or ["- nothing"])
    return "\n".join(out) + "\n"


def report(ledger: str | Path, since: str, *, run_name: str = "", attempt: int = 1,
           log_text: str = "", agent_since: str | None = None) -> tuple[Outcome, str]:
    """Return one attempt's outcome and its ``remediation.md`` text.

    Args:
        ledger: Path of ``ledger.jsonl``.
        since: The attempt's start (ISO; UTC or with an offset).
        run_name: The scan run's name.
        attempt: The attempt number.
        log_text: This attempt's log text.
        agent_since: The day's agent run start (ISO), or None: its rows up to ``since`` get their own section.

    Returns:
        ``(outcome, markdown)``.

    Raises:
        ValueError: ``since`` is not an ISO timestamp.
    """
    res = outcome(ledger, since)
    agent = None
    if agent_since:
        try:
            agent = outcome(ledger, agent_since, until=since)
        except ValueError:   # status.json is the agent run's file: a bad start is reported, never a usage error
            agent = Outcome(error=f"agent run start unreadable ({_one_line(agent_since)[:40]!r})")
    return res, render_markdown(res, run_name=run_name, attempt=attempt, log_text=log_text, agent=agent)


def register(sub: Any) -> None:
    """Add ``remediation-report LEDGER SINCE [--log F] [--run NAME] [--attempt N] [--out F] [--agent-since TS]``.

    Args:
        sub: The parser's subparsers action.
    """
    sp = sub.add_parser("remediation-report",
                        help="classify one remediation attempt's ledger rows and write remediation.md")
    sp.add_argument("ledger", type=Path, help="ledger.jsonl")
    sp.add_argument("since", help="the attempt's start, ISO (UTC or with an offset)")
    sp.add_argument("--log", type=Path, default=None, help="this attempt's part of remediation.log")
    sp.add_argument("--run", default="", help="the scan run's name")
    sp.add_argument("--attempt", type=int, default=1)
    sp.add_argument("--out", type=Path, default=None, help="write remediation.md here (default: not written)")
    sp.add_argument("--agent-since", default=None,
                    help="the day's agent run start (ISO): report its executor rows before SINCE, never retried")


def dispatch(args: argparse.Namespace, load_cfg: Any = None) -> int:
    """Run ``remediation-report``: print the outcome as one JSON object, and write ``--out``.

    Args:
        args: Parsed arguments.
        load_cfg: Unused (the report needs no config); accepted for the dispatch convention.

    Returns:
        0 reported (an unreadable ledger is reported, fail closed), 2 a bad SINCE or an unwritable ``--out``.
    """
    log_text = ""
    if args.log is not None:
        try:
            log_text = args.log.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log_text = f"WARN this attempt's log is unreadable: {type(exc).__name__}"
    try:
        res, md = report(args.ledger, args.since, run_name=args.run, attempt=args.attempt, log_text=log_text,
                         agent_since=getattr(args, "agent_since", None))
    except ValueError as exc:
        print(f"remediation-report: bad SINCE: {exc}", file=sys.stderr)
        return 2
    if args.out is not None:
        try:
            args.out.write_text(md, encoding="utf-8")
        except OSError as exc:
            print(f"remediation-report: cannot write {args.out}: {exc}", file=sys.stderr)
            return 2
    print(json.dumps(res.to_dict()))
    return 0
