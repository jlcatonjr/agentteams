"""pr_management.py — PR lifecycle helpers for AgentTeamsModule.

Surface:
  - load_registry(path) -> Registry
  - list_stale_prs(label, interval_hours, *, runner=_run_gh) -> list[Pr]
  - post_reminder(pr_number, body, *, runner=_run_gh) -> Result
  - assign_recipients(pr_number, recipients, *, runner=_run_gh) -> Result
  - prompt_next_action(*, non_interactive=False, default="continue-branch")
        -> Literal["continue-branch", "push-main", "open-pr"]
  - __main__ CLI: `python -m agentteams.pr_management {prompt|remind|--dry-run}`

All gh CLI calls go through a single `_run_gh` shim so tests can inject a
mock runner; no real subprocess invocation occurs unless the caller (or the
CLI) actually runs the command.

Plan: tmp/by-week/2026-W22/pr-management-agent-system-2026-05-27.plan.md
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Literal


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "references" / "pr-recipients.json"
DEFAULT_LABEL = "pr-mgmt"
DEFAULT_INTERVAL_HOURS = 24
REMINDER_PREFIX = "[pr-reminder]"
NOTIFIER_PREFIX = "[pr-notifier]"

Action = Literal["continue-branch", "push-main", "open-pr"]
GhRunner = Callable[[list[str]], subprocess.CompletedProcess]


# ----------------------------- registry ------------------------------------

@dataclass(frozen=True)
class Recipient:
    login: str
    role: str
    default_assignee: bool = False
    opt_out: bool = False
    reminder_interval_hours: int | None = None
    notifications_confirmed: bool = False


@dataclass(frozen=True)
class Registry:
    version: int
    default_reminder_interval_hours: int
    recipients: tuple[Recipient, ...] = field(default_factory=tuple)

    def active(self) -> tuple[Recipient, ...]:
        return tuple(r for r in self.recipients if not r.opt_out)

    def default_assignees(self) -> tuple[str, ...]:
        return tuple(r.login for r in self.active() if r.default_assignee)

    def interval_for(self, login: str) -> int:
        for r in self.recipients:
            if r.login == login and r.reminder_interval_hours:
                return r.reminder_interval_hours
        return self.default_reminder_interval_hours


def load_registry(path: Path = DEFAULT_REGISTRY) -> Registry:
    """Load and lightly validate the recipient registry.

    Missing file → empty registry with defaults. Malformed JSON raises.
    """
    if not path.is_file():
        return Registry(
            version=1,
            default_reminder_interval_hours=DEFAULT_INTERVAL_HOURS,
            recipients=(),
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    return Registry(
        version=int(data.get("version", 1)),
        default_reminder_interval_hours=int(
            data.get("default_reminder_interval_hours", DEFAULT_INTERVAL_HOURS)
        ),
        recipients=tuple(
            Recipient(
                login=r["login"],
                role=r["role"],
                default_assignee=bool(r.get("default_assignee", False)),
                opt_out=bool(r.get("opt_out", False)),
                reminder_interval_hours=r.get("reminder_interval_hours"),
                notifications_confirmed=bool(r.get("notifications_confirmed", False)),
            )
            for r in data.get("recipients", [])
        ),
    )


# ----------------------------- gh wrappers ---------------------------------

def _run_gh(argv: list[str]) -> subprocess.CompletedProcess:
    """Default runner: shell out to `gh`. Tests replace this."""
    return subprocess.run(
        ["gh", *argv], capture_output=True, text=True, check=False
    )


@dataclass(frozen=True)
class Result:
    ok: bool
    code: int
    stdout: str
    stderr: str

    @classmethod
    def from_completed(cls, cp: subprocess.CompletedProcess) -> "Result":
        return cls(ok=cp.returncode == 0, code=cp.returncode,
                   stdout=cp.stdout or "", stderr=cp.stderr or "")


@dataclass(frozen=True)
class Pr:
    number: int
    title: str
    author: str
    assignees: tuple[str, ...]
    updated_at: str
    url: str
    last_comment_body: str = ""
    last_comment_created_at: str = ""


def _parse_iso(ts: str) -> _dt.datetime:
    # GitHub timestamps end with Z; fromisoformat in 3.11 accepts +00:00.
    return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def list_stale_prs(
    label: str = DEFAULT_LABEL,
    interval_hours: int = DEFAULT_INTERVAL_HOURS,
    *,
    now: _dt.datetime | None = None,
    runner: GhRunner = _run_gh,
) -> list[Pr]:
    """Return open PRs whose age exceeds `interval_hours` and whose most
    recent comment is not itself a fresh reminder/notifier ping.
    """
    cp = runner([
        "pr", "list", "--state", "open", "--label", label,
        "--json", "number,title,author,assignees,updatedAt,url,comments",
    ])
    if cp.returncode != 0:
        return []
    raw = json.loads(cp.stdout or "[]")
    now = now or _dt.datetime.now(_dt.timezone.utc)
    stale: list[Pr] = []
    for item in raw:
        updated = _parse_iso(item["updatedAt"])
        age_hours = (now - updated).total_seconds() / 3600
        if age_hours < interval_hours:
            continue
        comments = item.get("comments") or []
        last_body = comments[-1]["body"] if comments else ""
        last_ts = comments[-1]["createdAt"] if comments else ""
        # Dedup: skip if our own most-recent ping is still fresh.
        if last_body.startswith((REMINDER_PREFIX, NOTIFIER_PREFIX)) and last_ts:
            since = (now - _parse_iso(last_ts)).total_seconds() / 3600
            if since < interval_hours:
                continue
        stale.append(Pr(
            number=int(item["number"]),
            title=item["title"],
            author=(item.get("author") or {}).get("login", ""),
            assignees=tuple(a["login"] for a in item.get("assignees", [])),
            updated_at=item["updatedAt"],
            url=item["url"],
            last_comment_body=last_body,
            last_comment_created_at=last_ts,
        ))
    return stale


def post_reminder(
    pr_number: int,
    body: str,
    *,
    runner: GhRunner = _run_gh,
) -> Result:
    """Post a `[pr-reminder]`-prefixed comment to a PR."""
    if not body.startswith(REMINDER_PREFIX):
        body = f"{REMINDER_PREFIX} {body}"
    cp = runner(["pr", "comment", str(pr_number), "--body", body])
    return Result.from_completed(cp)


def assign_recipients(
    pr_number: int,
    recipients: Iterable[str],
    *,
    as_reviewers: bool = True,
    runner: GhRunner = _run_gh,
) -> Result:
    """Set assignees (and optionally reviewer-requests) on a PR."""
    logins = ",".join(sorted(set(recipients)))
    if not logins:
        return Result(ok=True, code=0, stdout="(no recipients)", stderr="")
    args = ["pr", "edit", str(pr_number), "--add-assignee", logins]
    if as_reviewers:
        args += ["--add-reviewer", logins]
    cp = runner(args)
    return Result.from_completed(cp)


# ----------------------------- end-of-task prompt --------------------------

_PROMPT_TEXT = """\
End-of-task disposition — choose one:
  [1] continue-branch   — keep working on the current branch
  [2] push-main         — commit and push directly to main
  [3] open-pr           — open a PR for human review
"""

_PROMPT_CHOICES: dict[str, Action] = {
    "1": "continue-branch", "continue-branch": "continue-branch",
    "2": "push-main",       "push-main":       "push-main",
    "3": "open-pr",         "open-pr":         "open-pr",
}


def prompt_next_action(
    *,
    non_interactive: bool = False,
    default: Action = "continue-branch",
    reader: Callable[[], str] = input,
    writer: Callable[[str], None] = lambda s: print(s, end=""),
) -> Action:
    """Return the operator's chosen next action.

    `non_interactive=True` returns `default` without reading stdin —
    suitable for CI / autonomous loops.
    """
    if non_interactive:
        return default
    writer(_PROMPT_TEXT)
    writer(f"Choice [{default}]: ")
    try:
        raw = reader().strip().lower()
    except EOFError:
        return default
    if not raw:
        return default
    return _PROMPT_CHOICES.get(raw, default)


# ----------------------------- CLI -----------------------------------------

def _cli_remind(args: argparse.Namespace) -> int:
    interval = int(os.environ.get("REMINDER_INTERVAL_HOURS", str(DEFAULT_INTERVAL_HOURS)))
    if args.dry_run:
        # Synthesize a fake stale PR so the end-to-end path is exercised
        # offline. See plan §A3 + plan-audit §K2.
        fake = Pr(
            number=0, title="(dry-run synthetic PR)", author="dry-run",
            assignees=("jlcatonjr",),
            updated_at="2026-05-26T00:00:00Z",
            url="https://example.invalid/pr/0",
        )
        print(f"[dry-run] would remind PR #{fake.number} ({fake.title})")
        print(f"[dry-run] interval_hours={interval}")
        return 0
    registry = load_registry()
    stale = list_stale_prs(interval_hours=interval)
    reminded = 0
    for pr in stale:
        active = [a for a in pr.assignees
                  if not any(r.login == a and r.opt_out for r in registry.recipients)]
        if not active:
            continue
        body = "This PR has been open for >{0}h. Assignees: {1}.".format(
            interval, ", ".join(f"@{a}" for a in active),
        )
        res = post_reminder(pr.number, body)
        if res.ok:
            reminded += 1
        else:
            print(f"[warn] PR #{pr.number} reminder failed code={res.code}: {res.stderr[:120]}",
                  file=sys.stderr)
    print(f"reminded={reminded} scanned={len(stale)}")
    return 0


def _cli_prompt(args: argparse.Namespace) -> int:
    action = prompt_next_action(
        non_interactive=args.non_interactive,
        default=args.default,  # type: ignore[arg-type]
    )
    print(action)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m agentteams.pr_management")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_remind = sub.add_parser("remind", help="Post reminders for stale PRs.")
    p_remind.add_argument("--dry-run", action="store_true")
    p_remind.set_defaults(func=_cli_remind)

    p_prompt = sub.add_parser("prompt", help="End-of-task disposition prompt.")
    p_prompt.add_argument("--non-interactive", action="store_true")
    p_prompt.add_argument(
        "--default", default="continue-branch",
        choices=("continue-branch", "push-main", "open-pr"),
    )
    p_prompt.set_defaults(func=_cli_prompt)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
