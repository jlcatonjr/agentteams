"""``known propose|sign|list|remove``: known-finding entries (:mod:`.known`). The agent proposes, the operator signs.

| Command | Who | What it does |
|---|---|---|
| ``known propose --run DATE --finding KEY … --reason TEXT --decision-plan SLUG --decision-step STEP --key-id ID --governance-dir DIR`` | anyone | Writes an **unsigned** proposal from a run's findings and prints the command the operator runs to sign it. Never signs, never reads key material |
| ``known sign PROPOSAL`` | operator, outside every agent session | Shows the entry, asks for the review digest, signs it with the operator Ed25519 key, installs it and appends its row to the governance dir's index; it is in force once that row is committed |
| ``known list`` | anyone | Every entry and its status (in force, void with why, renewed) |
| ``known remove ENTRY_ID`` | operator | Deletes an entry (a narrowing; no signature) |

``--from-note NOTE_ID`` migrates an operator note (:mod:`.notes`): its text becomes the reason. ``--renews
ENTRY_ID`` proposes a renewal: a new id whose lineage names the old one (counted and shown).
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any

from agentteams.vulnscan import known, known_report, paths
from agentteams.vulnscan.report import Report

COMMANDS = ("known",)


def register(sub: Any) -> None:
    """Add ``known`` to the vulnscan argument parser.

    Args:
        sub: The ``add_subparsers`` object of the main parser.
    """
    sp = sub.add_parser("known", help="known-finding entries: propose (unsigned), sign (operator only), list, remove")
    sp.add_argument("action", choices=("propose", "sign", "list", "remove"))
    sp.add_argument("proposal", nargs="?", default=None, help="sign: the proposal file; remove: the entry id")
    sp.add_argument("--run", default=None, help="propose: run date YYYY-MM-DD (default: the latest run)")
    sp.add_argument("--finding", action="append", default=[], help="propose: a finding key (repeat for a group)")
    sp.add_argument("--reason", default="")
    sp.add_argument("--from-note", default=None, metavar="NOTE_ID", help="propose: take the reason from this note")
    sp.add_argument("--decision-plan", default="", help="propose: plan_slug of the cleared decision-log row")
    sp.add_argument("--decision-step", default="", help="propose: step of that row")
    sp.add_argument("--days", type=int, default=known.MAX_DAYS)
    sp.add_argument("--key-id", default="")
    sp.add_argument("--governance-dir", default="")
    sp.add_argument("--renews", default="", metavar="ENTRY_ID")
    sp.add_argument("--out", default=None, type=Path, help="propose: where to write the proposal")
    sp.add_argument("--confirm-review-sha256", default=None)
    sp.add_argument("--allow-checkout-signing", action="store_true")


def _load_run(day: str | None) -> Report | None:
    from agentteams.vulnscan.operator_cli import _run_dir

    run_dir = _run_dir(day)
    if run_dir is None or not (run_dir / "summary.json").is_file():
        return None
    return Report.from_dict(json.loads((run_dir / "summary.json").read_text(encoding="utf-8")))


def _reason(args: argparse.Namespace, target: str) -> str:
    """Return the proposal's reason: ``--reason``, or the ``--from-note`` note's text (its target must match)."""
    if not args.from_note:
        return args.reason
    from agentteams.vulnscan import notes

    active, _problems = notes.active()
    note = next((n for n in active if n["id"] == args.from_note), None)
    if note is None:
        raise known.KnownError(f"no active note {args.from_note}")
    if note.get("target") != target:
        raise known.KnownError(f"note {note['id']} is about {note.get('target') or '(all)'!r}, not the findings' "
                               f"target {target!r}")
    text = f"(from operator note {note['id']}) {args.reason or note['text']}".strip()
    return text[:known.REASON_MAX_CHARS]


def _renewed(entry_id: str) -> dict[str, Any] | None:
    """Return the store's entry ``entry_id`` when its signature verifies as of its own issue time."""
    if not entry_id:
        return None
    from datetime import datetime

    path = known.store_dir() / f"{entry_id}.json"
    try:
        rec = known.read_entry(path)
        known.verify(rec, now=datetime.fromisoformat(rec["issued"]))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise known.KnownError(f"cannot renew {entry_id}: {exc}") from exc
    return rec


def cmd_propose(args: argparse.Namespace) -> int:
    """Write an unsigned proposal and print the operator's signing command. Never signs.

    Args:
        args: Parsed arguments.

    Returns:
        0 written, 2 refused.
    """
    rep = _load_run(args.run)
    if rep is None:
        print("no such run (or it has no summary.json)", file=sys.stderr)
        return 2
    by_key = {f.key(): f for f in rep.findings}
    missing = [k for k in args.finding if k not in by_key]
    if not args.finding or missing:
        print(f"refused: name each finding by its key (Finding.key()); not in this run: {missing or '(none given)'}",
              file=sys.stderr)
        return 2
    try:
        renews = _renewed(args.renews)
        if renews is None and known_report.active_count() >= known.MAX_ACTIVE:
            raise known.KnownError(f"{known.MAX_ACTIVE} entries are in force (the cap): a new entry is refused; "
                                   "renew or remove one")
        rec = known.build([by_key[k] for k in args.finding], reason=_reason(args, by_key[args.finding[0]].target),
                          decision_ref={"plan_slug": args.decision_plan, "step": args.decision_step},
                          days=args.days, key_id=args.key_id, governance_dir=args.governance_dir, renews=renews)
    except known.KnownError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    out = args.out or (paths.state_dir() / "known-proposals" / f"{rec['entry_id']}.json")
    out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2, sort_keys=True)
    print(f"unsigned proposal {rec['entry_id']} ({rec['kind']}, {len(rec['members'])} member(s), "
          f"class {known.derived_class(rec)}) written to {out}")
    print("Nothing is suppressed until the operator signs it. The operator reviews it and runs, outside any agent "
          "session:")
    print(f"  python -m agentteams.vulnscan known sign {shlex.quote(str(out))}")
    print(f"then commits {known.INDEX_REL} in {rec['governance_dir']}.")
    return 0


def cmd_sign(args: argparse.Namespace) -> int:
    """Sign a proposal (operator only), install it and append its index row.

    Args:
        args: Parsed arguments.

    Returns:
        0 signed, 1 not signed, 2 refused.
    """
    if not args.proposal:
        print("known sign needs the proposal file", file=sys.stderr)
        return 2
    try:
        rec = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
        if not isinstance(rec, dict) or "signature" in rec:
            raise known.KnownError("not an unsigned proposal")
        rec = known.remint(rec)
        known.check_unsigned(rec)
        if (known.store_dir() / f"{rec['entry_id']}.json").exists():
            raise known.KnownError(f"{rec['entry_id']} is already in the store")
        if not rec["lineage"] and known_report.active_count() >= known.MAX_ACTIVE:
            raise known.KnownError(f"{known.MAX_ACTIVE} entries are in force (the cap): a new entry is refused")
        signed = known.sign(rec, confirm_sha256=args.confirm_review_sha256, allow_checkout=args.allow_checkout_signing)
        if signed is None:
            return 1
        known.verify(signed)
        path = known.install(signed)
        index = known.append_index_row(signed)
    except (OSError, ValueError, KeyError, TypeError) as exc:   # KnownError is a ValueError
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(f"installed {path}\nappended its row to {index}: commit it; the entry is in force once committed")
    return 0


def cmd_list() -> int:
    """Print every entry and its status.

    Returns:
        0.
    """
    rep = Report(target="known")
    rows = known_report.apply(rep)
    for r in rows:
        print(json.dumps(r, ensure_ascii=True))
    if not rows:
        print("no known entries")
    return 0


def dispatch(args: argparse.Namespace) -> int:
    """Run one ``known`` action.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    if args.action == "propose":
        return cmd_propose(args)
    if args.action == "sign":
        return cmd_sign(args)
    if args.action == "list":
        return cmd_list()
    ok = known.remove(args.proposal or "")
    print("removed" if ok else "no such entry")
    return 0 if ok else 1
