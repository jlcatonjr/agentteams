"""Project commands: enrol this machine's projects, show the effective scan roots, and one project's status.

| Command | What it does |
|---|---|
| ``enroll [DIR ...] [--discover DIR] [--level L]`` | Operator only (a terminal, never CI): group the projects by owner, answer their level in bulk, write the registry |
| ``roots`` | Each candidate scan root and its source (registry level, ``repo_roots``, excluded, opted out, unanswered, duplicate) |
| ``status --project DIR [--json]`` | Read-only, no network: identity, enrolment, coverage state (:data:`coverage.STATES`, including ``nothing to scan`` for a project with no dependency manifest), last scan and whether it was partial, open findings, recorded fix branches and PRs, grant coverage |

``enroll`` is the only writer of the registry (:mod:`.registry`). Levels live in each project's brief
(:mod:`.project_policy`); ``enroll`` copies them, and writes one into a brief only through
``project_policy.set_level``, which shows the diff and needs its own confirmation. A brief whose block is
invalid is skipped with its problem shown (the maintainer fixes it); it is never answered over.

The registry only narrows what is scanned and proposed; it never authorizes a change (signed grants do).
The terminal check keeps ``enroll`` off CI and out of pipes; it is not authentication.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentteams.vulnscan import coverage, paths, project_policy, registry
from agentteams.vulnscan.report import Report

COMMANDS = ("enroll", "roots", "status")
POLICY_LEVELS = project_policy.LEVELS


def register(sub: Any) -> None:
    """Add the project commands to the vulnscan argument parser.

    Args:
        sub: The ``add_subparsers`` object of the main parser.
    """
    sp = sub.add_parser("enroll", help="enrol this machine's projects in the daily scan (operator only)")
    sp.add_argument("dirs", nargs="*", type=Path, help="project directories")
    sp.add_argument("--discover", action="append", default=[], type=Path, metavar="DIR",
                    help="find every agentteams workspace under DIR")
    sp.add_argument("--level", choices=POLICY_LEVELS, default=None,
                    help="the level offered for unanswered projects (default: asked)")
    sub.add_parser("roots", help="the effective scan roots and where each comes from")
    sp = sub.add_parser("status", help="one project's scan status (read-only, no network)")
    sp.add_argument("--project", type=Path, default=Path("."))
    sp.add_argument("--json", action="store_true")


def _policy(repo: Path) -> tuple[str, str | None, str]:
    """Return (level or "unanswered", brief path or None, problem) from the project's brief."""
    pol = project_policy.read_policy(repo)
    brief = str(pol.brief_path) if pol.brief_path else None
    return (pol.level or "unanswered"), brief, pol.problem or ""


# --- enroll -----------------------------------------------------------------------------------------

def _gather(args: argparse.Namespace) -> dict[str, set[Path]]:
    groups: dict[str, set[Path]] = defaultdict(set)
    for d in args.dirs:
        real = d.expanduser().resolve()
        groups[registry.identity(real)].add(real)
    for root in args.discover:
        for ident, found in registry.discover(root).items():
            groups[ident].update(found)
    return groups


def _choose_fix(ident: str, checkouts: list[Path], current: Path, ask: Callable[[str], str]) -> Path:
    if len(checkouts) < 2:
        return current
    print(f"  {ident}:")
    for n, c in enumerate(checkouts, 1):
        print(f"    {n}. {c}{'  (fix checkout)' if c == current else ''}")
    pick = ask("    fix checkout number (Enter keeps it): ").strip()
    return checkouts[int(pick) - 1] if pick.isdigit() and 1 <= int(pick) <= len(checkouts) else current


def _answer_unanswered(rows: list[dict[str, Any]], level: str | None, ask: Callable[[str], str]) -> None:
    """Write a level into each unanswered project's brief via ``set_level`` (diff shown, confirmed).

    A brief whose block is invalid (or unreadable) is not unanswered: it is never rewritten here.
    """
    open_rows = [r for r in rows if r["level"] == "unanswered" and not r["problem"]]
    if not open_rows:
        print("  every project here already has a level")
        return
    while level not in POLICY_LEVELS:
        level = ask(f"  level for the {len(open_rows)} unanswered project(s) (off/scan/remediate): ").strip()
    yes_all = False

    def confirm(diff: str) -> bool:
        nonlocal yes_all
        print(diff)
        if yes_all:
            return True
        ans = ask("  apply? [y]es / [n]o / [a]ll remaining in this owner: ").strip().lower()
        yes_all = ans == "a"
        return ans in ("y", "a")

    for r in open_rows:
        print(f"  {r['identity']}: {r['fix']}")
        if project_policy.set_level(r["fix"], level, confirm=confirm):
            r["level"], r["brief"], r["problem"] = _policy(r["fix"])


def cmd_enroll(args: argparse.Namespace, *, ask: Callable[[str], str] = input,
               interactive: Callable[[], bool] = lambda: sys.stdin.isatty() and sys.stdout.isatty()) -> int:
    """Enrol projects: group by owner, answer in bulk, write the registry once at the end.

    Args:
        args: ``dirs``, ``discover``, ``level``.
        ask: Prompt function (tests).
        interactive: Whether a person is at a terminal (tests).

    Returns:
        0 written, 1 nothing to enrol or aborted, 2 refused (CI, no terminal, unreadable registry).
    """
    if registry.is_ci() or not interactive():
        print("refused: `vulnscan enroll` is an operator command: it needs a terminal and never runs on CI",
              file=sys.stderr)
        return 2
    try:
        existing = {e.identity: e for e in registry.load()}
    except (OSError, ValueError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    groups = _gather(args)
    if not groups:
        print("nothing to enrol: name project directories or --discover DIR", file=sys.stderr)
        return 1
    by_owner: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ident, found in sorted(groups.items()):
        old = existing.get(ident)
        checkouts = sorted(found | {Path(c) for c in (old.checkouts if old else [])})
        fix = Path(old.fix_checkout) if old and Path(old.fix_checkout) in checkouts else \
            registry.pick_fix_checkout([c for c in checkouts if c.exists()] or checkouts)
        level, brief, problem = _policy(fix)
        by_owner[registry.owner_of(ident)].append({"identity": ident, "fix": fix, "checkouts": checkouts,
                                                   "level": level, "brief": brief, "problem": problem})
    decided: list[dict[str, Any]] = []
    for owner in sorted(by_owner):
        rows = by_owner[owner]
        print(f"\n{owner} ({len(rows)} project(s))")
        for r in rows:
            more = f"; {len(r['checkouts']) - 1} more checkout(s)" if len(r["checkouts"]) > 1 else ""
            print(f"  {r['identity']}: brief level {r['level']}{more}\n    fix checkout {r['fix']}"
                  + (f"\n    brief problem: {r['problem']}" if r["problem"] else ""))
        while True:
            ans = ask("[e]nrol at their brief levels / [s]et a level for unanswered ones / "
                      "[c]hoose fix checkouts / s[k]ip: ").strip().lower()
            if ans == "c":
                for r in rows:
                    r["fix"] = _choose_fix(r["identity"], r["checkouts"], r["fix"], ask)
                    r["level"], r["brief"], r["problem"] = _policy(r["fix"])
                continue
            if ans in ("e", "s", "k"):
                break
        if ans == "s":
            _answer_unanswered(rows, args.level, ask)
        for r in rows:
            if r["problem"]:   # an invalid block is not an answer to fill in: the maintainer fixes the brief
                print(f"  skipped {r['identity']}: fix its brief first ({r['problem']})")
        decided += [{**r, "skip": ans == "k" or bool(r["problem"])} for r in rows]
    return _write(existing, decided)


def _write(existing: dict[str, registry.Entry], decided: list[dict[str, Any]]) -> int:
    entries = dict(existing)
    for r in decided:
        old = existing.get(r["identity"])
        if r["skip"] and (old or r["problem"]):
            continue   # skipped: an enrolled project keeps its entry; an invalid brief is not enrolled
        entries[r["identity"]] = registry.Entry(
            identity=r["identity"], fix_checkout=str(r["fix"]), checkouts=[str(c) for c in r["checkouts"]],
            level="unanswered" if r["skip"] else r["level"], owner=registry.owner_of(r["identity"]),
            enrolled=old.enrolled if old else registry.now_iso(), brief=r["brief"])
    try:
        path = registry.save(entries.values())
    except (OSError, ValueError) as exc:   # PermissionError on CI is an OSError
        print(f"registry not written: {exc}", file=sys.stderr)
        return 2
    counts: dict[str, int] = defaultdict(int)
    for e in entries.values():
        counts[e.level] += 1
    print(f"\nregistry {path}: " + ", ".join(f"{lvl} {counts[lvl]}" for lvl in registry.LEVELS if counts[lvl]))
    print("the daily run scans the scan and remediate ones; see `python -m agentteams.vulnscan roots`")
    return 0


# --- roots ------------------------------------------------------------------------------------------

def cmd_roots(cfg: dict[str, Any]) -> int:
    """Print every candidate root with its source.

    Args:
        cfg: Daily config (defaults when there is none).

    Returns:
        0, or 2 when the registry is unreadable.
    """
    try:
        entries = registry.load()
    except (OSError, ValueError) as exc:
        print(f"project registry unreadable: {exc}", file=sys.stderr)
        return 2
    roots = coverage.effective_roots(cfg, entries)
    for r in roots:
        print(f"{'SCAN' if r.scanned else 'SKIP'}  {r.label}  [{r.source}]" + (f"  {r.detail}" if r.detail else ""))
    print(f"{sum(r.scanned for r in roots)} of {len(roots)} roots scanned")
    return 0


# --- status -----------------------------------------------------------------------------------------

def _latest_findings(checkouts: list[str]) -> tuple[str | None, dict[str, int], int | None]:
    """Return (last scan, open findings by severity, how many parts the scan left unscanned or None)."""
    from agentteams.vulnscan.osv import SEVERITY_NAME

    runs = paths.run_dirs()
    if not runs:
        return None, {}, None
    try:
        rep = Report.from_dict(json.loads((runs[-1] / "summary.json").read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return f"{runs[-1].name} (summary unreadable: {type(exc).__name__})", {}, None
    counts: dict[str, int] = defaultdict(int)
    for f in rep.findings:
        if f.kind == "package" and Path(f.target).expanduser().is_absolute() \
                and any(coverage.within(f.target, c) for c in checkouts):
            counts["MALICIOUS" if f.malicious else SEVERITY_NAME.get(f.severity, "UNRATED")] += 1
    return rep.generated_at or runs[-1].name, dict(counts), len(rep.feed_errors)


def _recorded_fixes(checkouts: list[str]) -> tuple[list[dict[str, str]], str]:
    from agentteams.vulnscan import ledger

    try:
        rows, note = ledger.read(), ""
    except (OSError, ValueError) as exc:   # an unreachable or unsafe key, or a broken chain: show rows, flagged
        try:
            rows, note = ledger.read(verify=False), f"ledger not verified: {exc}"
        except (OSError, ValueError) as again:
            return [], f"ledger unreadable: {again}"
    out = []
    for r in rows:
        if r.get("kind") != "done" or r.get("op") not in ("stage-fix", "open-pr"):
            continue
        target = str(r.get("target", ""))
        if not Path(target).is_absolute() or not any(coverage.within(target, c) for c in checkouts):
            continue
        try:
            detail = json.loads(r.get("detail") or "{}")
        except ValueError:
            detail = {}
        detail = detail if isinstance(detail, dict) else {}
        out.append({"ts": str(r.get("ts", "")), "op": str(r["op"]), "branch": str(detail.get("branch", "")),
                    "pr": str(detail.get("pr", ""))})
    return out, note


def project_status(project: Path, cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Collect one project's status without the network.

    Args:
        project: Any checkout of the project.
        cfg: Daily config, or None when there is none.

    Returns:
        The status object (what ``status --json`` prints).
    """
    from agentteams.vulnscan import grant

    ident = registry.identity(project)
    out: dict[str, Any] = {"project": str(project.expanduser().resolve()), "identity": ident,
                           "owner": registry.owner_of(ident), "level": "not enrolled", "fix_checkout": None}
    problems: list[str] = []
    entry = None
    try:
        entry = registry.find(registry.load(), project)
    except (OSError, ValueError) as exc:
        problems.append(f"registry unreadable: {exc}")
    checkouts = [str(project.expanduser().resolve())]
    if entry:
        out.update(level=entry.level, fix_checkout=entry.fix_checkout, enrolled=entry.enrolled, brief=entry.brief)
        checkouts = sorted({*entry.checkouts, entry.fix_checkout, *checkouts})
    if cfg is not None and entry:
        root = next((r for r in coverage.effective_roots(cfg, [entry])
                     if r.label == entry.fix_checkout), None)
        out["scanned_daily"] = bool(root and (root.scanned or root.source == "duplicate"))
    out["last_scan"], out["open_findings"], unscanned = _latest_findings(checkouts)
    out["last_scan_partial"] = bool(unscanned)   # feed errors: part of the run was unscanned (exit 3)
    out["last_scan_unscanned"] = unscanned or 0
    out["brief_level"], _brief, brief_problem = _policy(project)
    if brief_problem:
        problems.append(f"brief: {brief_problem}")
    elif out["brief_level"] == "off" and out["level"] in registry.SCANNED_LEVELS:
        problems.append(f"mismatch: {coverage.MISMATCH_OFF}")
    out["recorded_fixes"], note = _recorded_fixes(checkouts)
    if note:
        problems.append(note)
    grants, grant_problems = grant.load_active()
    target = out["fix_checkout"] or out["project"]
    out["grant_covers_fix_checkout"] = any(grant.covers(g, "stage-fix", target) for g in grants)
    out["coverage_state"] = coverage.project_state(cfg, entry, project.expanduser().resolve(), grants)
    out["problems"] = problems + [f"grant: {p}" for p in grant_problems]
    return out


def cmd_status(args: argparse.Namespace, cfg: dict[str, Any] | None) -> int:
    """Print one project's status (``--json`` for the object).

    Args:
        args: ``project``, ``json``.
        cfg: Daily config, or None.

    Returns:
        0, or 2 when the project directory does not exist.
    """
    if not args.project.expanduser().is_dir():
        print(f"no such directory: {args.project}", file=sys.stderr)
        return 2
    st = project_status(args.project, cfg)
    if args.json:
        print(json.dumps(st, indent=2))
        return 0
    findings = ", ".join(f"{k} {v}" for k, v in sorted(st["open_findings"].items())) or "none"
    partial = f" (PARTIAL: {st['last_scan_unscanned']} part(s) unscanned)" if st["last_scan_partial"] else ""
    empty = " (no dependency manifest found: not a coverage gap)" if st["coverage_state"] == "nothing to scan" else ""
    lines = [f"project      {st['project']}", f"identity     {st['identity']}", f"level        {st['level']}",
             f"coverage     {st['coverage_state']}{empty}",
             f"fix checkout {st['fix_checkout'] or '—'}", f"last scan    {st['last_scan'] or 'never'}{partial}",
             f"findings     {findings}",
             f"grant        {'covers the fix checkout' if st['grant_covers_fix_checkout'] else 'does not cover it'}"]
    if "scanned_daily" in st:
        lines.append(f"daily scan   {'yes' if st['scanned_daily'] else 'no'}")
    lines += [f"fix          {f['ts']} {f['op']} {f['branch']} {f['pr']}".rstrip() for f in st["recorded_fixes"]]
    lines += [f"problem      {p}" for p in st["problems"]]
    print("\n".join(lines))
    return 0


def dispatch(args: argparse.Namespace, load_cfg: Callable[[], dict[str, Any]]) -> int:
    """Run one project command.

    Args:
        args: Parsed arguments (``args.cmd`` in :data:`COMMANDS`).
        load_cfg: Loads the daily config (FileNotFoundError when there is none).

    Returns:
        Exit code.
    """
    if args.cmd == "enroll":
        return cmd_enroll(args)
    try:
        cfg: dict[str, Any] | None = load_cfg()
    except FileNotFoundError:
        cfg = None
    except (OSError, ValueError) as exc:
        print(f"config {paths.config_file()} is unusable: {exc}", file=sys.stderr)
        return 2
    if args.cmd == "roots":
        from agentteams.vulnscan.daily import DEFAULT_CONFIG

        return cmd_roots(cfg if cfg is not None else dict(DEFAULT_CONFIG))
    return cmd_status(args, cfg)
