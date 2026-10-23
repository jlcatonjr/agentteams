"""Operator commands for the R6 loop: see what is blocked, resolve it, talk to the agents, publish.

| Command | What it does |
|---|---|
| ``halts`` | Open escalations and any ``@security`` HALT on the vulnscan gate actions, with how to clear each |
| ``resolve ID --evidence TEXT`` | Resolve an escalation: ``other`` with a confirmed operator note, serious kinds with an Ed25519 signature |
| ``say TEXT`` / ``say --from-gh REF`` / ``say --list`` / ``say --remove ID`` | Operator notes folded into the next run's task |
| ``publish-grant sign\\|list\\|revoke`` | The operator-signed grant that lets the executor push reports to a private ops repo |
| ``publish [--run DATE]`` | Publish a run's report, review and summary now (needs the publish grant) |
| ``agent-chat [--run DATE]`` | Resume a past run's goose session interactively (read-only spool: it can explain, not act) |

A decision-log HALT is never cleared here (C-2): ``halts`` prints the ``HALT-RETRACTED`` spec the
operator signs with ``agentteams --sign-decision``. Notes are operator text, so ``say`` never runs
inside an agent session; the agents cannot reach this CLI from their container.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from agentteams.vulnscan import escalations, executor, grant, notes, paths, publish
from agentteams.vulnscan.executor import ACTION_ID
from agentteams.vulnscan.publish import PUBLISH_ACTION

COMMANDS = ("halts", "resolve", "say", "publish-grant", "publish", "agent-chat")


def register(sub: Any) -> None:
    """Add the operator commands to the vulnscan argument parser.

    Args:
        sub: The ``add_subparsers`` object of the main parser.
    """
    sub.add_parser("halts", help="what is blocking remediation, and how to clear it")
    sp = sub.add_parser("resolve", help="resolve an escalation (serious kinds need your Ed25519 signature)")
    sp.add_argument("esc_id")
    sp.add_argument("--evidence", required=True, help="why it no longer applies (recorded in the ledger)")
    sp.add_argument("--key-id", default="", help="operator verify-key id (serious kinds)")
    sp.add_argument("--confirm-review-sha256", default=None)
    sp.add_argument("--allow-checkout-signing", action="store_true")
    sp = sub.add_parser("say", help="leave a note for the agents' next run")
    sp.add_argument("text", nargs="?", default=None)
    sp.add_argument("--target", default=None, help="repo, host or package the note is about")
    sp.add_argument("--days", type=int, default=None, help="expire after N days (default: never)")
    sp.add_argument("--from-gh", default=None, help="import a `/vulnscan note` PR comment (URL, or id with --repo)")
    sp.add_argument("--repo", default=None, help="<owner>/<repo> for a numeric --from-gh id")
    sp.add_argument("--list", action="store_true")
    sp.add_argument("--remove", default=None, metavar="NOTE_ID")
    sp = sub.add_parser("publish-grant", help="operator-signed grant to push reports to a private ops repo")
    sp.add_argument("action", choices=("sign", "list", "revoke"))
    sp.add_argument("--ops-repo", default="")
    sp.add_argument("--remote-url", default="", help="default: the ops repo's remote.origin.url")
    sp.add_argument("--node-id", default="", help="gh repo view <owner/repo> --json id")
    sp.add_argument("--owner-id", type=int, default=0, help="gh api users/<owner> --jq .id")
    sp.add_argument("--governance-dir", default="")
    sp.add_argument("--days", type=int, default=30)
    sp.add_argument("--max-uses", type=int, default=60)
    sp.add_argument("--key-id", default="")
    sp.add_argument("--grant-id", default="")
    sp.add_argument("--confirm-review-sha256", default=None)
    sp.add_argument("--allow-checkout-signing", action="store_true")
    for name, text in (("publish", "publish a run's report now"), ("agent-chat", "talk to a past run's agent")):
        sp = sub.add_parser(name, help=text)
        sp.add_argument("--run", default=None, type=iso_date, help="run date YYYY-MM-DD (default: the latest run)")


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def iso_date(text: str) -> str:
    """Argparse type for a run date: exactly ``YYYY-MM-DD`` and a real calendar date.

    Run dates name directories under the state dir, so anything else (``../x``) is refused.

    Args:
        text: The argument.

    Returns:
        ``text`` unchanged.

    Raises:
        argparse.ArgumentTypeError: Not a ``YYYY-MM-DD`` date.
    """
    try:
        ok = bool(_DATE_RE.match(text)) and date.fromisoformat(text).isoformat() == text
    except ValueError:
        ok = False
    if not ok:
        raise argparse.ArgumentTypeError(f"expected a date YYYY-MM-DD, not {text!r}")
    return text


def _run_dir(day: str | None) -> Path | None:
    state = paths.state_dir()
    if day:
        if not _DATE_RE.match(day):   # also for callers that bypass the parser
            return None
        d = state / day
        return d if d.is_dir() else None
    runs = paths.run_dirs(state)
    return runs[-1] if runs else None


def _governance_dirs(cfg: dict[str, Any] | None) -> list[Path]:
    found = [g["governance_dir"] for g in grant.load_active()[0]] + [g["governance_dir"] for g in publish.load_active()[0]]
    if cfg and cfg.get("governance_dir"):
        found.append(cfg["governance_dir"])
    return sorted({Path(str(d)).expanduser().resolve() for d in found})


_HALT_ID_RE = re.compile(r"latest decision for action '([^']+)' is HALT")


def _halt_lines(gov: Path) -> list[tuple[str, str, str]]:
    """Return ``(action, reason, halt_id)`` for each gate action the decision log in ``gov`` blocks.

    ``halt_id`` is the HALT row's own action id (a retraction must name it), or ``""`` when what
    blocks is a log the gate cannot use (missing, a symlink, malformed, a broken chain).
    """
    from agentteams.cli import decision_log

    log = gov / "references" / "security-decisions.log.csv"
    if not log.is_file() or log.is_symlink():   # the executor and publish refuse everything then
        why = f"decision log {log} is missing or a symlink, so the HALT check cannot run"
        return [(ACTION_ID, why, ""), (PUBLISH_ACTION, why, "")]
    out = []
    for action, check in ((ACTION_ID, lambda: decision_log._assert_no_unretracted_halt(gov, action=ACTION_ID)),
                          (PUBLISH_ACTION, lambda: decision_log._assert_no_unretracted_halt(gov, action=PUBLISH_ACTION))):
        try:
            check()
        except RuntimeError as exc:
            m = _HALT_ID_RE.search(str(exc))
            out.append((action, str(exc)[:400], m.group(1) if m else ""))
    return out


def _retraction_spec(action: str) -> str:
    spec = {"date": datetime.now(UTC).date().isoformat(), "action_reviewed": action, "verdict": "HALT-RETRACTED",
            "evidence": "<why the HALT no longer applies — required>", "key_id": "<your verify-key id>",
            "author": "<an approver named in references/security-approvers.txt>"}
    return json.dumps(spec, indent=2, ensure_ascii=False)


def cmd_halts(cfg: dict[str, Any] | None) -> int:
    """Print what blocks remediation or publishing, and how to clear each.

    Args:
        cfg: Daily config, or None when there is none.

    Returns:
        0 when nothing blocks, 1 otherwise.
    """
    blocked = False
    try:
        open_ = escalations.open_escalations()
    except ValueError as exc:
        print(f"LEDGER INTEGRITY FAILURE: {exc} — every action is refused until the ledger is repaired")
        return 1
    print("## Escalations raised by the agents")
    for esc in open_:
        blocked = True
        how = ("sign: vulnscan resolve {id} --evidence '...' --key-id <id>" if esc.kind in escalations.SERIOUS
               else "vulnscan resolve {id} --evidence '...'").format(id=esc.id)
        print(f"- {escalations.describe(esc)}\n  blocks: {'everything' if esc.scope == escalations.ALL_SCOPE else esc.scope}"
              f"\n  resolve: {how}")
    if not open_:
        print("- none")
    print("\n## @security HALTs (decision log)")
    govs = _governance_dirs(cfg)
    halted = False
    for gov in govs:
        for action, reason, halt_id in _halt_lines(gov):
            blocked = halted = True
            print(f"- {gov}: {action} blocked: {reason}")
            if halt_id:
                print(f"  A HALT is final (C-2); only a signed HALT-RETRACTED row clears it. Save this spec, fill "
                      f"it in,\n  then run: agentteams --sign-decision <spec.json> --output {gov}\n"
                      f"{_retraction_spec(halt_id)}")
            else:
                print("  The gate cannot read this decision log, so it fails closed; repair the log.")
    if not govs:
        print("- no governance dir configured (no grant): nothing can run anyway")
    elif not halted:   # each section says "none" for itself, whatever the other one holds
        print("- none")
    return 1 if blocked else 0


def cmd_resolve(args: argparse.Namespace, *, ask: Callable[[str], str] = input) -> int:
    """Resolve one escalation.

    Args:
        args: Parsed arguments (``esc_id``, ``evidence``, ``key_id``, signing flags).
        ask: Prompt function (tests).

    Returns:
        0 resolved, 1 not resolved, 2 refused input.
    """
    try:
        esc = next((e for e in escalations.open_escalations() if e.id == args.esc_id), None)
    except ValueError as exc:
        print(f"ledger integrity failure: {exc}", file=sys.stderr)
        return 2
    if esc is None:
        print(f"{args.esc_id} is not an open escalation (see: vulnscan halts). Decision-log HALTs are cleared "
              "only by a signed HALT-RETRACTED row.", file=sys.stderr)
        return 2
    print(escalations.describe(esc))
    try:
        if esc.kind not in escalations.SERIOUS:
            if not sys.stdin.isatty() and ask is input:
                print("refused: resolving needs an interactive confirmation", file=sys.stderr)
                return 2
            if ask(f"Resolve {esc.id} and resume remediation for {esc.scope}? [y/N] ").strip().lower() != "y":
                return 1
            row = escalations.resolve_other(esc.id, args.evidence)
        else:
            rec = escalations.build_resolution(esc.id, args.evidence, args.key_id)
            signed = escalations.sign_resolution(rec, confirm_sha256=args.confirm_review_sha256,
                                                 allow_checkout=args.allow_checkout_signing)
            if signed is None:
                return 1
            row = escalations.apply_signed_resolution(signed)
    except ValueError as exc:   # EscalationError is a ValueError
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(f"resolved {esc.id} ({row.get('method')}, ledger row {row.get('seq')})")
    return 0


def _confirm_note(shown: dict[str, Any], *, ask: Callable[[str], str] = input) -> bool:
    """Show an imported comment in full and ask before it becomes a note (refuses without a terminal)."""
    if not sys.stdin.isatty() and ask is input:
        print("refused: importing a GitHub note needs an interactive confirmation", file=sys.stderr)
        return False
    print(f"From {shown['author_login']} — {shown['html_url']}\n----\n{shown['text']}\n----")
    return ask("Store this as a note for the agents' next run? [y/N] ").strip().lower() == "y"


def cmd_say(args: argparse.Namespace) -> int:
    """Add, import, list or remove operator notes.

    Args:
        args: Parsed arguments.

    Returns:
        0 on success, 1 when nothing matched, 2 on refused input.
    """
    try:
        if args.list:
            active, problems = notes.active()
            for n in active:
                first = str(n["text"]).splitlines()[0][:100] if n["text"] else ""
                print(f"{n['id']}  created {n['created']}  expires {n.get('expires') or 'never'}  "
                      f"source {n.get('source')}  target {n.get('target') or '-'}\n    {first}")
            for pr in problems:
                print(f"PROBLEM {pr}", file=sys.stderr)
            return 0
        if args.remove:
            ok = notes.remove(args.remove)
            print("removed" if ok else "no such note")
            return 0 if ok else 1
        if args.from_gh:
            note = notes.from_gh(args.from_gh, repo=args.repo, target=args.target, days=args.days,
                                 confirm=_confirm_note)
        elif args.text:
            note = notes.add(args.text, target=args.target, days=args.days)
        else:
            print("say needs TEXT, --from-gh, --list or --remove", file=sys.stderr)
            return 2
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(f"note {note['id']} (sha256 {note['sha256'][:12]}) will be folded into the next run's task")
    return 0


def _origin_url(repo: str) -> str:
    try:
        return executor.git(Path(repo).expanduser(), "config", "--get", "remote.origin.url").strip()
    except executor.ExecError:
        return ""


def cmd_publish_grant(args: argparse.Namespace) -> int:
    """Sign, list or revoke the publish grant.

    Args:
        args: Parsed arguments.

    Returns:
        0 on success, 1 not done, 2 refused.
    """
    if args.action == "list":
        valid, problems = publish.load_active()
        for g in valid:
            print(json.dumps({**{k: g[k] for k in publish.FIELDS}, "uses": publish.uses(g["grant_id"])}))
        for pr in problems:
            print(f"INVALID {pr}", file=sys.stderr)
        return 0
    if args.action == "revoke":
        ok = publish.revoke(args.grant_id)
        print("revoked" if ok else "no such publish grant")
        return 0 if ok else 1
    try:
        rec = publish.build(ops_repo=args.ops_repo, remote_url=args.remote_url or _origin_url(args.ops_repo),
                            node_id=args.node_id, owner_id=args.owner_id, governance_dir=args.governance_dir,
                            days=args.days, max_uses=args.max_uses, key_id=args.key_id)
    except publish.PublishError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    problems = publish.preflight(rec)
    if problems:
        print("refused — the ops repository fails preflight:\n" + "\n".join(f"- {p}" for p in problems), file=sys.stderr)
        return 2
    signed = publish.sign(rec, confirm_sha256=args.confirm_review_sha256, allow_checkout=args.allow_checkout_signing)
    if signed is None:
        return 1
    try:
        publish.verify(signed)
    except publish.PublishError as exc:
        print(f"refused: the signed publish grant does not verify: {exc}", file=sys.stderr)
        return 2
    print(f"installed {publish.install(signed)}")
    return 0


def _status_run_id(run_dir: Path) -> str:
    """Return the run id recorded in ``<run>/agent/status.json``, or "" when it is absent or malformed."""
    status = run_dir / "agent" / "status.json"
    if not status.is_file():
        return ""
    try:
        data = json.loads(status.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:   # a malformed status file: publish under a new run id
        print(f"ignoring {status}: {exc}", file=sys.stderr)
        return ""
    rid = data.get("run_id") if isinstance(data, dict) else None
    return rid if isinstance(rid, str) else ""


def cmd_publish(cfg: dict[str, Any], args: argparse.Namespace) -> int:
    """Publish one run's files now.

    Args:
        cfg: Daily config.
        args: Parsed arguments (``run``).

    Returns:
        0 published, 1 no grant, 2 no such run, 3 failed or refused.
    """
    from agentteams.vulnscan import daily

    run_dir = _run_dir(args.run)
    if run_dir is None:
        print("no such run", file=sys.stderr)
        return 2
    res = daily.publish_run(cfg, run_dir, _status_run_id(run_dir) or executor.new_run_id(run_dir))
    if res is None:
        print("no active publish grant (see: vulnscan publish-grant sign)", file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2))
    return 0 if res.get("status") == "done" else 3


def dispatch(args: argparse.Namespace, load_cfg: Callable[[], dict[str, Any]]) -> int:
    """Run one operator command.

    Args:
        args: Parsed arguments (``args.cmd`` in :data:`COMMANDS`).
        load_cfg: Loads the daily config; raises FileNotFoundError when there is none and
            ValueError (or another OSError) when it is unusable.

    Returns:
        Exit code.
    """
    if args.cmd == "resolve":
        return cmd_resolve(args)
    if args.cmd == "say":
        return cmd_say(args)
    if args.cmd == "publish-grant":
        return cmd_publish_grant(args)
    try:
        cfg = load_cfg()
    except FileNotFoundError:
        if args.cmd == "halts":
            return cmd_halts(None)
        print(f"no config at {paths.config_file()} — run `python -m agentteams.vulnscan init`", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:   # halts too: without the config it could miss a governance dir
        print(f"config {paths.config_file()} is unusable: {exc}", file=sys.stderr)
        return 2
    if args.cmd == "halts":
        return cmd_halts(cfg)
    if args.cmd == "publish":
        return cmd_publish(cfg, args)
    from agentteams.vulnscan import debrief

    run_dir = _run_dir(args.run)
    if run_dir is None:
        print("no such run", file=sys.stderr)
        return 2
    return debrief.run_debrief(cfg, run_dir)
