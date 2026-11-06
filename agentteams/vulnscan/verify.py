"""Same-day verification: re-scan what changed after the morning run, and compare finding by finding (vfr-v V1).

The daily scan runs once in the morning; fixes merged later that day (the auto-merge job, a person) or a host
audited again after an executor op are invisible until the next morning. ``vulnscan verify``, scheduled after the
last auto-merge (``vulnscan-daily-wrapped.sh --verify``, installed by ``vulnscan-remediate-auto.sh --install``):

1. takes the remediation lock (:func:`daily.remediation_lock_path`) and the daily run lock
   (:func:`daily.run_lock_path`), in that order and without waiting: while a remediation attempt, a deploy or a
   daily run holds either, it writes nothing and exits 3 (the job fires again later the same day);
2. runs the operator's scan-mirror refresh script (``--mirror-refresh``), and re-scans, from its refreshed mirror,
   each root whose mirror moved from one ``ok`` commit to another since the morning (:mod:`.scan_mirror`): a
   repository whose default branch moved today. A root without an ``ok`` record before and after is not re-scanned
   (its morning scan read something else, or the refresh failed), and says so;
3. runs the server kit's read-only L1 audit (the audit the daily scan and the executor's ``server-audit`` op run) on
   each configured server an executor ``server-*`` op ran against today, by the ledger;
4. compares the morning run's findings of the re-scanned targets with the new scan (:func:`effectiveness.compare`:
   per finding, version lines paired, renamed trees reported as moved), and writes ``<run>/verify.json``.

It stages nothing, writes no fix plan, opens no PR, starts no agent and sends no notification (the
wrapper's Docker check stays quiet under ``--verify`` too); ``summary.*``, the
heartbeat and the morning's ``repos-*.json`` / ``server-*.log`` are untouched (the audits' logs go to
``<run>/verify/``). ``verify.json`` names repositories, hosts and packages: it is machine-local and never published.
The ``effectiveness BASE LATER`` command writes the same per-finding comparison for any two runs.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import fcntl
import io
import json
import os
import stat
import subprocess
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from agentteams.vulnscan import coverage, daily, effectiveness, ledger, paths, registry, scan_mirror
from agentteams.vulnscan import subproc_confine
from agentteams.vulnscan.report import Report

COMMANDS = ("verify", "effectiveness")
VERIFY_FILE = "verify.json"
#: ``<run>/verify/``: the verification's audit logs and the morning's mirror record (:data:`MORNING_MIRRORS`).
WORK_DIR = "verify"
MORNING_MIRRORS = "mirrors-morning.json"
SCHEMA = 1
#: Seconds the mirror refresh script may run.
REFRESH_TIMEOUT = 1800


@contextlib.contextmanager
def _locks() -> Iterator[str]:
    """Hold the remediation lock, then the daily run lock (the deploy's order); yield "" or what holds one."""
    with contextlib.ExitStack() as stack:
        for path, what in ((daily.remediation_lock_path(), "a remediation attempt or a deploy"),
                           (daily.run_lock_path(), "a daily run or a deploy")):
            fh = stack.enter_context(open(path, "a+", encoding="utf-8"))   # noqa: SIM115  (held for the block)
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield f"{what} holds {path}"
                return
            stack.callback(fcntl.flock, fh, fcntl.LOCK_UN)
        yield ""


def _morning_stamp(day: str, work: Path) -> dict[str, str]:
    """Return the mirror record the morning scan read, kept in ``work`` by the day's first verification.

    The refresh rewrites ``<state>/scan-mirrors/<day>.json``, so a second verification the same day (after a
    partial first one) must compare with the morning's commits, not with the first verification's.
    """
    kept = work / MORNING_MIRRORS
    if kept.is_file():
        return scan_mirror.read_stamp(day, kept)
    record = scan_mirror.read_stamp(day)
    daily.write_atomic(kept, json.dumps(record, indent=1))
    return record


class RefreshRefused(ValueError):
    """The mirror refresh script may not be run (:func:`open_refresh`)."""


def _owner_mode_problem(st: os.stat_result, what: str, *, root_ok: bool) -> str:
    owners = (os.getuid(), 0) if root_ok else (os.getuid(),)
    if st.st_uid not in owners:
        return f"{what} is not owned by you{' or root' if root_ok else ''}"
    if st.st_mode & 0o022:
        return f"{what} is writable by group or others"
    return ""


def _symlinked_parent(script: Path) -> str:
    """Name the first directory of the script's parent path that is a symlink, from ``$HOME`` down (``/`` outside it).

    The ancestor walk checks resolved directories, so a symlinked directory would be checked where it points, not
    where the link lives; it is refused instead. ``$HOME`` itself (and what is above it) is the operator's setup.
    """
    parent = Path(os.path.normpath(script.parent))   # lexical: with no symlink allowed below, it is the real path
    home = Path(os.path.normpath(Path.home()))
    d = home if parent.is_relative_to(home) else Path("/")
    for part in parent.relative_to(d).parts:
        d = d / part
        if stat.S_ISLNK(os.lstat(d).st_mode):
            return f"directory {d} is a symlink"
    return ""


def open_refresh(script: Path) -> int:
    """Open the mirror refresh script for running, refusing anything another user could have written.

    The job runs it unattended with the operator's git credentials. The file is opened with ``O_NOFOLLOW`` (a
    symlink is refused) and checked **on the open descriptor**: a regular file the operator owns that neither the
    group nor others can write. No directory of its parent path below the home directory (below ``/`` outside it)
    may be a symlink. Every directory above it, up to the home directory (or ``/`` outside it), must be
    owned by the operator or root and not group- or world-writable, and the path must still name the file that was
    opened. :func:`_refresh` then runs that descriptor (``bash /dev/fd/N``), never the path again, so a file swapped
    in after the checks is not what runs.

    Args:
        script: The refresh script (``vauto-a-mirrors.sh``, run as ``bash SCRIPT refresh``).

    Returns:
        An open read-only descriptor; the caller closes it.

    Raises:
        RefreshRefused: Any check failed (the message names it); nothing is left open.
    """
    if not script.is_absolute():
        raise RefreshRefused(f"{script} is not an absolute path")
    try:
        fd = os.open(script, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise RefreshRefused(f"{script} is a symlink" if os.path.islink(script)
                             else f"{script}: {exc.strerror or exc}") from exc
    try:
        st = os.fstat(fd)
        why = ("" if stat.S_ISREG(st.st_mode) else f"{script} is not a regular file") or \
            _owner_mode_problem(st, str(script), root_ok=False)
        why = why or _symlinked_parent(script)
        home = Path(os.path.realpath(Path.home()))
        d = Path(os.path.realpath(script.parent))
        while not why:
            why = _owner_mode_problem(os.stat(d), f"directory {d}", root_ok=True)
            if d == home or d.parent == d:
                break
            d = d.parent
        if not why:
            now = os.stat(script, follow_symlinks=False)
            if (now.st_dev, now.st_ino) != (st.st_dev, st.st_ino):
                why = f"{script} was replaced while it was checked"
    except OSError as exc:
        why = f"{script}: {exc.strerror or exc}"
    if why:
        os.close(fd)
        raise RefreshRefused(why)
    return fd


def refresh_problem(script: Path) -> str:
    """Return why ``script`` may not be run as the mirror refresh (:func:`open_refresh`), or "" when it may.

    Args:
        script: The refresh script.

    Returns:
        The problem, or "".
    """
    try:
        os.close(open_refresh(script))
    except RefreshRefused as exc:
        return str(exc)
    return ""


def _refresh(script: Path | None) -> str:
    if script is None:
        return "not run (no --mirror-refresh)"
    try:
        fd = open_refresh(script)
    except RefreshRefused as exc:
        return f"refused: {exc}"
    try:   # the checked descriptor itself, never the path: no check-then-exec race
        proc = subprocess.run(["/bin/bash", f"/dev/fd/{fd}", "refresh"], pass_fds=(fd,), capture_output=True,
                              text=True, timeout=REFRESH_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return f"failed: timed out after {REFRESH_TIMEOUT} s"
    except OSError as exc:
        return f"failed: {exc}"
    finally:
        os.close(fd)
    last = (proc.stdout.strip().splitlines() or [""])[-1][:300]
    return f"ok: {last}" if proc.returncode == 0 else f"failed: exit {proc.returncode}: {last}"


def _local_day(ts: object) -> str:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone().date().isoformat()
    except ValueError:
        return ""


def audited_hosts(rows: list[dict[str, Any]], day: str) -> list[str]:
    """Return the hosts an executor ``server-*`` op ran against on ``day`` (local date), by its ledger rows.

    Args:
        rows: Ledger rows (:func:`ledger.read`).
        day: ``YYYY-MM-DD``.

    Returns:
        The distinct targets of ``done`` or ``failed`` ``server-*`` rows, sorted.
    """
    return sorted({str(r.get("target", "")) for r in rows
                   if str(r.get("op", "")).startswith("server-") and r.get("kind") in ("done", "failed")
                   and r.get("target") and _local_day(r.get("ts")) == day})


def _rescan_repos(cfg: dict[str, Any], day: str, before: dict[str, str], later: Report,
                  out: dict[str, Any]) -> set[str]:
    """Re-scan every root whose mirror moved today; return the labels re-scanned without an error."""
    after = scan_mirror.read_stamp(day)
    try:
        entries = registry.load()
    except (OSError, ValueError) as exc:
        later.feed_errors.append(f"project registry unreadable, enrolled projects not re-scanned: {exc}")
        entries = []
    exclude, opted_out = coverage.operator_skips(cfg, entries)
    done: set[str] = set()
    for spec in coverage.effective_roots(cfg, entries):
        if not spec.scanned or not spec.path.is_dir():
            continue
        was = before.get(str(spec.path), before.get(str(spec.path.resolve()), ""))
        now = after.get(str(spec.path), after.get(str(spec.path.resolve()), ""))
        if not (was.startswith("ok ") and now.startswith("ok ")):
            if was or now:
                out["not_rescanned"].append({"root": spec.label, "before": was, "after": now})
            continue
        if was == now:
            continue   # nothing merged today
        mirror = scan_mirror.mirror_for(cfg, spec.path, day)
        row = {"root": spec.label, "before": was[3:], "after": now[3:]}
        if mirror is None:
            out["not_rescanned"].append({**row, "reason": "no usable mirror (scan_mirror off, or not a directory)"})
            continue
        try:
            rep = scan_mirror.scan_root(cfg, spec.path, mirror, exclude, opted_out)
        except daily.ITEM_ERRORS as exc:
            later.feed_errors.append(f"re-scan of {spec.label} stopped: {type(exc).__name__}: {exc}"[:400])
            continue
        coverage.drop_excluded(cfg, spec.path, rep)
        for f in rep.findings:
            f.target = coverage.under_root(spec.label, f.target)
        later.findings += rep.findings
        if rep.feed_errors:
            later.feed_errors += rep.feed_errors
        else:
            done.add(spec.label)
        out["rescanned"].append(row)
    return done


def _reaudit_hosts(cfg: dict[str, Any], day: str, run_dir: Path, later: Report, out: dict[str, Any]) -> set[str]:
    """Run the L1 audit on today's executor-op hosts that are configured servers; return those audited cleanly."""
    try:
        hosts = audited_hosts(ledger.read(), day)
    except ValueError as exc:   # an untrusted ledger names no host
        later.feed_errors.append(f"ledger unreadable, no host re-audited: {exc}"[:400])
        return set()
    servers = set(cfg.get("servers", []))
    out["hosts_skipped"] = [h for h in hosts if h not in servers]   # not a configured server: no morning baseline
    done: set[str] = set()
    for host in (h for h in hosts if h in servers):
        try:
            with subproc_confine.scope(cfg):
                rep = daily.scan_server(host, paths.server_kit_dir(), run_dir / WORK_DIR)
        except subproc_confine.Unavailable as exc:
            later.feed_errors.append(f"{host}: not re-audited: {exc}")
            continue
        later.findings += rep.findings
        if rep.feed_errors:
            later.feed_errors += rep.feed_errors
        else:
            done.add(host)
        out["hosts"].append(host)
    return done


def run_verify(cfg: dict[str, Any], *, today: str | None = None, mirror_refresh: Path | None = None,
               force: bool = False) -> tuple[dict[str, Any], int]:
    """Re-scan what changed since this morning's run and write ``<run>/verify.json``.

    Args:
        cfg: Daily config (:func:`daily.load_config`).
        today: The run date (tests); default today.
        mirror_refresh: The operator's scan-mirror refresh script, run as ``bash SCRIPT refresh`` first.
        force: Verify again although today's ``verify.json`` is already complete.

    Returns:
        (the verification record, exit code): 0 complete, 3 partial, busy (a lock held) or no morning run today.
        Nothing is written unless the locks were taken and the morning's ``summary.json`` is readable.
    """
    day = today or date.today().isoformat()
    run_dir = paths.state_dir() / day
    try:
        baseline = effectiveness.load_run(run_dir)
    except (OSError, ValueError) as exc:
        return {"day": day, "state": "no-run", "detail": f"no readable morning run today: {exc}"[:300]}, 3
    target = run_dir / VERIFY_FILE
    if not force:
        try:
            old = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):   # none yet, or unreadable: verify
            old = None
        if isinstance(old, dict) and old.get("complete"):
            return {**old, "state": "already-verified"}, 0
    paths.state_dir().mkdir(parents=True, exist_ok=True)
    with _locks() as busy:
        if busy:
            return {"day": day, "state": "busy", "detail": busy}, 3
        return _verify_locked(cfg, day, run_dir, baseline, mirror_refresh)


def _verify_locked(cfg: dict[str, Any], day: str, run_dir: Path, baseline: Report,
                   mirror_refresh: Path | None) -> tuple[dict[str, Any], int]:
    out: dict[str, Any] = {"schema": SCHEMA, "day": day, "started": datetime.now(UTC).isoformat(timespec="seconds"),
                           "rescanned": [], "not_rescanned": [], "hosts": [], "hosts_skipped": []}
    later = Report(target="verify")
    try:
        (run_dir / WORK_DIR).mkdir(exist_ok=True)
        before = _morning_stamp(day, run_dir / WORK_DIR)
    except OSError as exc:   # nothing can be kept or logged: verify what can be, and say so
        later.feed_errors.append(f"could not keep the morning's mirror record in {run_dir / WORK_DIR}: {exc}")
        before = scan_mirror.read_stamp(day)
    out["mirror_refresh"] = _refresh(mirror_refresh)
    if out["mirror_refresh"].startswith(("failed", "refused")):
        later.feed_errors.append(f"mirror refresh {out['mirror_refresh']}"[:400])
    scanned = _rescan_repos(cfg, day, before, later, out) if cfg.get("scan_mirror") else set()
    if not cfg.get("scan_mirror"):
        out["not_rescanned"].append({"root": "*", "reason": "scan_mirror is off: repositories are not re-scanned"})
    scanned |= _reaudit_hosts(cfg, day, run_dir, later, out)
    later.apply_unscored(str(cfg.get("unscored_as", "high")))
    covered = effectiveness.assessed({f.target for f in baseline.findings}, scanned)
    base = Report(target=baseline.target, findings=[f for f in baseline.findings if f.target in covered])
    cmp = effectiveness.compare(base, later, scanned=covered)
    out.update({"finished": datetime.now(UTC).isoformat(timespec="seconds"), "complete": not later.feed_errors,
                "feed_errors": later.feed_errors, "assessed": sorted(scanned), "counts": cmp.counts(),
                "moves": cmp.moves, "findings": effectiveness.outcome_rows(cmp),
                "new": [{"target": g.target, "kind": g.kind, "ecosystem": g.ecosystem, "name": g.name,
                         "version": g.version, "fix": g.fix} for g in cmp.new]})
    try:
        daily.write_atomic(run_dir / VERIFY_FILE, json.dumps(out, indent=2))
    except OSError as exc:
        out["complete"] = False
        out["feed_errors"].append(f"could not write {VERIFY_FILE}: {exc}")
    return {**out, "state": "verified" if out["complete"] else "partial"}, 0 if out["complete"] else 3


# ------------------------------------------------------------------ CLI


def _pairs(values: list[str] | None, flag: str) -> dict[str, str]:
    """``OLD=NEW`` options -> ``{NEW: OLD}`` (later name -> baseline name); a path alias drops a trailing ``/``."""
    def strip(v: str) -> str:
        return v.rstrip("/") if flag == "--path-alias" else v

    out: dict[str, str] = {}
    for v in values or []:
        old, sep, new = v.partition("=")
        if not (sep and old and new):
            raise ValueError(f"{flag} takes OLD=NEW (got {v!r})")
        out[strip(new)] = strip(old)
    return out


def comparison_markdown(cmp: effectiveness.Comparison, *, baseline: str, later: str,
                        aliases: dict[str, str], path_aliases: dict[str, str]) -> str:
    """Return the per-finding comparison as Markdown: counts, per target, moves, CRITICAL/HIGH not remediated.

    Machine-local (it names targets and packages); the published summary carries counts only.

    Args:
        cmp: A :func:`effectiveness.compare` result.
        baseline: The baseline run's name.
        later: The later run's name.
        aliases: The target aliases applied (later -> baseline).
        path_aliases: The explicit path aliases applied (later -> baseline).

    Returns:
        The report text.
    """
    counts = cmp.counts()
    lines = [f"# Remediation per finding: {baseline} → {later}", "",
             f"{len(cmp.outcomes)} baseline findings; {len(cmp.new)} later findings paired with none (a new key, "
             "or a known one at a new place; not regressions by themselves: coverage may have widened)."]
    lines += [f"- target alias: `{n}` = `{o}`" for n, o in aliases.items()]
    lines += [f"- path alias: `{n}` = `{o}`" for n, o in path_aliases.items()]
    lines += ["", "| Status | Count |", "|---|---|", *[f"| {s} | {counts[s]} |" for s in effectiveness.OUTCOMES]]
    if cmp.moves:
        lines += ["", "## Trees renamed between the runs (not remediations)", ""]
        lines += [f"- `{t}`: `{o}` → `{n}`" for t, m in sorted(cmp.moves.items()) for n, o in sorted(m.items())]
    per: dict[str, dict[str, int]] = {}
    for o in cmp.outcomes:
        per.setdefault(o.finding.target, dict.fromkeys(effectiveness.OUTCOMES, 0))[o.status] += 1
    lines += ["", "## By target", "", "| Target | " + " | ".join(effectiveness.OUTCOMES) + " |",
              "|---|" + "---|" * len(effectiveness.OUTCOMES)]
    lines += [f"| `{t}` | " + " | ".join(str(c[s]) for s in effectiveness.OUTCOMES) + " |"
              for t, c in sorted(per.items())]
    lines += ["", "## CRITICAL and HIGH not remediated", "", "| Severity | Target | Item | Installed | Fix | Status |",
              "|---|---|---|---|---|---|"]
    for r in effectiveness.outcome_rows(cmp):
        if r["status"] != effectiveness.REMEDIATED and r["severity"].startswith(("CRITICAL", "HIGH", "UNSCORED")):
            note = f": {r['note']}" if r["note"] else ""
            lines.append(f"| {r['severity']} | `{r['target']}` | {r['name']} ({r['ecosystem'] or r['kind']}) | "
                         f"{r['version']} | {r['fix'] or '—'} | {r['status']}{note} |")
    return "\n".join(lines) + "\n"


def register(sub: Any) -> None:
    """Add ``verify [--mirror-refresh SCRIPT] [--force] [--date DATE]`` and
    ``effectiveness BASE LATER [--alias OLD=NEW]… [--path-alias OLD=NEW]… [--csv FILE] [--out FILE]``.

    Args:
        sub: The parser's subparsers action.
    """
    sp = sub.add_parser("verify", help="re-scan what changed since this morning's run; write <run>/verify.json")
    sp.add_argument("--mirror-refresh", type=Path, default=None,
                    help="the scan-mirror refresh script, run as `bash SCRIPT refresh` first")
    sp.add_argument("--force", action="store_true", help="verify again although today's verify.json is complete")
    sp.add_argument("--date", default=None, help="the run to verify, YYYY-MM-DD (default today)")
    sp = sub.add_parser("effectiveness", help="per finding: was each finding of one run remediated by a later one")
    sp.add_argument("baseline", type=Path, help="the baseline run directory")
    sp.add_argument("later", type=Path, help="the later run directory")
    sp.add_argument("--alias", action="append", metavar="OLD=NEW", help="a target renamed between the runs")
    sp.add_argument("--path-alias", action="append", metavar="OLD=NEW", help="a location prefix renamed on disk")
    sp.add_argument("--csv", type=Path, default=None, help="also write one row per baseline finding")
    sp.add_argument("--out", type=Path, default=None, help="write the Markdown here (default stdout)")


def dispatch(args: argparse.Namespace, load_cfg: Callable[[], dict[str, Any]]) -> int:
    """Run ``verify`` or ``effectiveness``.

    Args:
        args: Parsed arguments.
        load_cfg: :func:`daily.load_config` (``verify`` only).

    Returns:
        ``verify``: 0 complete (or already complete), 3 partial / busy / no morning run, 2 no config or a bad
        ``--date``. ``effectiveness``: 0 written, 2 an unreadable run or a bad alias.
    """
    if args.cmd == "verify":
        try:
            cfg = load_cfg()
            if args.date is not None:
                date.fromisoformat(args.date)
        except (OSError, ValueError) as exc:
            print(f"verify: {exc}", file=sys.stderr)
            return 2
        rec, code = run_verify(cfg, today=args.date, mirror_refresh=args.mirror_refresh, force=args.force)
        print(json.dumps({k: rec.get(k) for k in ("day", "state", "detail", "counts", "mirror_refresh",
                                                  "feed_errors") if k in rec}))
        return code
    try:
        aliases, path_aliases = _pairs(args.alias, "--alias"), _pairs(args.path_alias, "--path-alias")
        base, later = effectiveness.load_run(args.baseline), effectiveness.load_run(args.later)
    except (OSError, ValueError) as exc:
        print(f"effectiveness: {exc}", file=sys.stderr)
        return 2
    scanned = {aliases.get(t, t) for t in effectiveness.scanned_targets(args.later)}
    cmp = effectiveness.compare(base, later, aliases=aliases, path_aliases=path_aliases,
                                scanned=effectiveness.assessed({f.target for f in base.findings}, scanned))
    md = comparison_markdown(cmp, baseline=args.baseline.name, later=args.later.name, aliases=aliases,
                             path_aliases=path_aliases)
    try:
        if args.csv is not None:
            buf = io.StringIO()
            rows = effectiveness.outcome_rows(cmp)
            w = csv.DictWriter(buf, fieldnames=list(rows[0]) if rows else ["status"])
            w.writeheader()
            w.writerows(rows)
            args.csv.write_text(buf.getvalue(), encoding="utf-8")
        if args.out is not None:
            args.out.write_text(md, encoding="utf-8")
    except OSError as exc:
        print(f"effectiveness: {exc}", file=sys.stderr)
        return 2
    if args.out is None:
        print(md, end="")
    else:
        print(", ".join(f"{s} {n}" for s, n in cmp.counts().items()) + f"; unpaired later findings {len(cmp.new)}")
    return 0
