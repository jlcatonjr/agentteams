"""Remediation effectiveness: does what the daily run stages actually land?

The daily summary gains a **Remediation effectiveness** section (:func:`section`), and the operator is
alerted when remediation stalls (:func:`alerts`, :func:`agent_streak`). The section answers the questions
the scan alone cannot:

- findings fixed and new since the previous run (the net change), and, separately, findings already fixed on
  ``origin/HEAD`` that the checkout has not pulled (``Finding.fixed_upstream``; never counted as fixed);
- remediation PRs the executor opened: how many are open, the oldest one's age, how many fail CI or run no
  checks, and how many merged in the last 7 days;
- fixes staged in the spool with no PR yet, and repositories held by :mod:`.retry_guard`.

**Counts only.** The summary is published to the operator's ops repository, so this section names no
repository, PR, host or path; :func:`section` passes its text through the publish scrubber as well. PR
state comes from read-only ``gh pr view`` calls on the PR URLs recorded in the executor's ledger (at most
:data:`MAX_PR_LOOKUPS` of the newest, each with a timeout); a lookup that fails is counted as unknown,
never guessed.

**Per finding** (:func:`compare`, vfr-v V2): was each finding of a baseline run remediated by a later run? A finding
is keyed by target, kind, ecosystem and package name (and, for runtime and host findings, the detail); each later
finding is paired with one baseline finding of its key at the same place, the one whose version is the highest at or
below it (else the lowest), so two version lines of one package never match each other. Two things are not
remediations and are never counted as one: a target renamed between the runs (``aliases``, e.g. a server alias), and
a tree renamed on disk. The second is found without being told (:func:`detect_moves`): a location that vanished
while a same-named tree carrying the same finding at the same version appeared (``/root/anaconda3`` →
``/root/anaconda3.disabled-20261101``) is a move, and a finding that is still there is reported as "moved (not
remediated)". A package found again anywhere else (another tree, another lockfile) is "present elsewhere", and is
never counted as remediated either. ``path_aliases`` names such a rename explicitly. The daily section adds these
per-finding counts (counts only, like the rest); the ``effectiveness`` command (:mod:`.verify`) writes the
per-finding rows, machine-local.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentteams.vulnscan import ledger, merge_digest, ops, paths, retry_guard
from agentteams.vulnscan.report import Finding, Report

STALE_DAYS = 3
MERGED_WINDOW_DAYS = 7
MAX_PR_LOOKUPS = 50
AGENT_DOWN_RUNS = 2
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
    green: list[merge_digest.GreenPr] = field(default_factory=list)   # for the machine-local merge digest
    notes: list[str] = field(default_factory=list)


def pr_urls(rows: list[dict[str, Any]]) -> list[str]:
    """Return the distinct PR URLs of the executor's ``open-pr`` successes, newest first."""
    seen: dict[str, None] = {}
    for r in reversed(rows):
        url = ops.pr_of(r)
        if url:
            seen.setdefault(url, None)
    return list(seen)


def _gh_view(url: str) -> dict[str, Any] | None:
    # Lazy: daily imports this module and the executor; importing the executor (and publish, below) at load time
    # would make this module's import pull in the whole executor stack for a read-only gh lookup.
    from agentteams.vulnscan import executor

    try:
        proc = subprocess.run(["gh", "pr", "view", url, "--json",
                               "state,createdAt,mergedAt,statusCheckRollup,mergeable,headRefOid"],
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
        m = ops.PR_URL.match(url)
        ready = merge_digest.green(f"{m[1]}/{m[2]}/{m[3]}", int(m[4]), data) if m else None
        if ready:
            st.green.append(ready)
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


def section(total: Report, previous: Report | None, *, stats: PrStats | None = None,
            scanned: set[str] | None = None) -> tuple[str, PrStats]:
    """Return the summary's **Remediation effectiveness** section (counts only) and the PR counts behind it.

    Args:
        total: This run's report.
        previous: The previous run's report, or None (then nothing counts as fixed).
        stats: Pre-computed PR counts (tests); default: looked up from the ledger's PR URLs.
        scanned: The targets this run has a scan record for (:func:`scanned_targets`). When given with a previous
            run, the section adds the previous run's findings per outcome (:func:`compare`); None leaves them out
            (an unscanned target must never read as remediated).

    Returns:
        (markdown, PR counts).
    """
    if stats is None:
        try:
            rows = ledger.read()
        except ValueError:   # an untrusted ledger is reported by the review; count nothing from it
            rows = []
        stats = pr_stats(pr_urls(rows))
    # Range findings (unpinned requirements) are not counted: switching ``unpinned_ranges`` off, or a cap, would
    # "fix" them without any change to a repository. They have a line of their own.
    now_keys = {f.key() for f in total.findings if f.kind != "range"}
    before = {f.key() for f in previous.findings if f.kind != "range"} if previous else set()
    ranges = sum(1 for f in total.findings if f.kind == "range")
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
        "| Findings already fixed upstream (checkout behind; not counted as fixed) | "
        f"{sum(1 for f in total.findings if f.fixed_upstream)} |",
        f"| Requirement-range findings (not counted above: no installed version is known) | {ranges} |",
        f"| Remediation PRs open | {stats.open} (oldest {oldest}) |",
        f"| … open longer than {STALE_DAYS} days | {stats.stale} |",
        f"| … failing CI | {stats.failing} |",
        f"| … with no CI checks | {stats.no_checks} |",
        f"| Remediation PRs merged in the last {MERGED_WINDOW_DAYS} days | {stats.merged_recent} |",
        f"| Fixes staged with no PR yet | {staged_without_pr()} |",
        f"| PRs ready to merge (open, mergeable, every check passed) | {len(stats.green)} |",
        f"| Repositories held at scan time (nothing stageable; see the held reasons) | {retry_guard.held_count()} |",
    ]
    if stats.unknown:
        lines.append(f"| PR lookups that failed (state unknown) | {stats.unknown} |")
    if previous is not None and scanned is not None:
        prev_targets = {f.target for f in previous.findings}
        per = compare(previous, total, scanned=assessed(prev_targets, scanned)).counts()
        lines += [f"| Previous run's findings, per finding: {s} | {per[s]} |" for s in OUTCOMES]
    lines.append("")
    lines += [f"- {n}" for n in stats.notes]
    lines.append("Merging stays with the operator: this run's directory holds `merge-digest.sh`, which asks before "
                 "each merge and runs with the operator's own credentials; nothing merges automatically.")
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


# ------------------------------------------------------------------ per finding (vfr-v V2)

REMEDIATED, PARTLY, OPEN, MOVED, ELSEWHERE, NOT_ASSESSED = (
    "remediated", "partly remediated", "open", "moved (not remediated)", "present elsewhere (not remediated)",
    "not assessed")
#: Every per-finding status, in report order. Only :data:`REMEDIATED` is a remediation.
OUTCOMES: tuple[str, ...] = (REMEDIATED, PARTLY, OPEN, MOVED, ELSEWHERE, NOT_ASSESSED)
#: A renamed tree keeps its name and adds a suffix after one of these (``anaconda3`` → ``anaconda3.disabled-1101``).
_RENAME_SEPARATORS = ".-_~+"
_MIRROR_TARGET = re.compile(r"\.worktrees/[^/]+$")
_AUDIT_DONE = re.compile(r"^RESULT (clean|action-needed|COMPROMISE)", re.M)


@dataclass
class FindingOutcome:
    """What became of one baseline finding in a later run."""

    finding: Finding
    status: str
    note: str = ""


@dataclass
class Comparison:
    """A baseline run's findings, each with its outcome in a later run (:func:`compare`)."""

    outcomes: list[FindingOutcome] = field(default_factory=list)
    new: list[Finding] = field(default_factory=list)   # later findings paired with none: a new key, or a new place
    moves: dict[str, dict[str, str]] = field(default_factory=dict)      # target -> {new prefix: old prefix}

    def counts(self) -> dict[str, int]:
        """Return the number of baseline findings per status (every status of :data:`OUTCOMES`, zeros included)."""
        out = dict.fromkeys(OUTCOMES, 0)
        for o in self.outcomes:
            out[o.status] += 1
        return out


def _vkey(version: str) -> tuple[int, ...] | None:
    parts = re.findall(r"\d+", (version or "").split("+")[0])
    return tuple(int(p) for p in parts) if parts else None


def _at_or_below(later: str, base: str) -> bool:
    a, b = _vkey(later), _vkey(base)
    return later == base if a is None or b is None else a <= b


def _target(f: Finding, aliases: dict[str, str]) -> str:
    return aliases.get(f.target, f.target)


def _key(f: Finding, aliases: dict[str, str]) -> tuple[str, ...]:
    k = (_target(f, aliases), f.kind, f.ecosystem or "", f.name.lower())
    return k + (f.detail or "",) if f.kind in ("runtime", "host") else k


def _unalias(loc: str, prefixes: dict[str, str]) -> tuple[str, bool]:
    """Map a later location's renamed prefix back to the baseline's: (location, whether a prefix was mapped)."""
    for new, old in prefixes.items():
        if loc == new or loc.startswith(new + "/"):
            return old + loc[len(new):], True
    return loc, False


def _renamed(old: str, new: str) -> tuple[str, str] | None:
    """Return ``(old prefix, new prefix)`` when ``new`` is ``old`` with one path component renamed in place.

    The component keeps its name and gains (or loses) a suffix after a separator, and nothing else differs:
    ``/root/anaconda3/lib`` → ``/root/anaconda3.disabled-20261101/lib``. ``python3.10`` → ``python3.12`` is not one.
    """
    a, b = old.split("/"), new.split("/")
    if len(a) != len(b):
        return None
    diff = [i for i, (x, y) in enumerate(zip(a, b, strict=True)) if x != y]
    if len(diff) != 1:
        return None
    i = diff[0]
    short, long_ = sorted((a[i], b[i]), key=len)
    if not short or not long_.startswith(short) or long_[len(short)] not in _RENAME_SEPARATORS:
        return None
    return "/".join(a[:i + 1]), "/".join(b[:i + 1])


def _has_prefix(loc: str, prefix: str) -> bool:
    return loc == prefix or loc.startswith(prefix + "/")


def detect_moves(baseline: Iterable[Finding], later: Iterable[Finding], *, aliases: dict[str, str] | None = None,
                 path_aliases: dict[str, str] | None = None) -> dict[str, dict[str, str]]:
    """Find trees renamed on disk between two runs (a rename is not a remediation, Rule 12).

    For each target, a location of the baseline that the later run no longer has, and a location the later run
    has and the baseline did not, are a candidate move when one path component was renamed in place
    (:func:`_renamed`). The move is accepted when the renamed tree carries at least one of the old tree's findings
    unchanged (same kind, ecosystem, package and version). That is deliberately weaker than "the same package
    set": a rename combined with one upgrade must still read as a move, not as a remediation of everything else.

    Args:
        baseline: The baseline run's findings.
        later: The later run's findings.
        aliases: Later target name -> baseline target name (a renamed host).
        path_aliases: Later location prefix -> baseline prefix, already known (applied first; never re-detected).

    Returns:
        ``{baseline target: {new prefix: old prefix}}``, only targets with a move.
    """
    aliases, path_aliases = aliases or {}, path_aliases or {}
    base_locs: dict[str, set[str]] = defaultdict(set)
    later_locs: dict[str, set[str]] = defaultdict(set)
    base_items: dict[str, list[tuple[tuple[str, ...], set[str]]]] = defaultdict(list)
    later_items: dict[str, list[tuple[tuple[str, ...], set[str]]]] = defaultdict(list)
    for f in baseline:
        t = _target(f, aliases)
        base_locs[t] |= set(f.locations)
        base_items[t].append(((f.kind, f.ecosystem, f.name.lower(), f.version), set(f.locations)))
    for g in later:
        t = _target(g, aliases)
        locs = {_unalias(loc, path_aliases)[0] for loc in g.locations}
        later_locs[t] |= locs
        later_items[t].append(((g.kind, g.ecosystem, g.name.lower(), g.version), locs))
    moves: dict[str, dict[str, str]] = {}
    for t, before in base_locs.items():
        gone, came = before - later_locs.get(t, set()), later_locs.get(t, set()) - before
        pairs = {p for v in gone for a in came if (p := _renamed(v, a))}
        for old, new in sorted(pairs):
            had = {k for k, locs in base_items[t] if any(_has_prefix(loc, old) for loc in locs)}
            has = {k for k, locs in later_items[t] if any(_has_prefix(loc, new) for loc in locs)}
            if had & has and new not in moves.get(t, {}):
                moves.setdefault(t, {})[new] = old
    return moves


def compare(baseline: Report, later: Report, *, scanned: set[str] | None = None,
            aliases: dict[str, str] | None = None, path_aliases: dict[str, str] | None = None) -> Comparison:
    """Return, for each finding of ``baseline``, whether ``later`` shows it remediated.

    Requirement-range findings are left out (they have no installed version; switching ``unpinned_ranges`` off
    would "fix" them). Statuses: :data:`REMEDIATED` (no finding of its key at its place at or below its version),
    :data:`PARTLY` (some of its advisories are gone, or it is now at a higher, still vulnerable, version),
    :data:`OPEN`, :data:`MOVED` (still there, but only under a tree :func:`detect_moves` found renamed),
    :data:`ELSEWHERE` (nothing at its place, but the later run has its key at a place no baseline finding of that
    key had: ``/root/anaconda3`` → ``/opt/anaconda3``, or a package moved to another lockfile; never remediated)
    and :data:`NOT_ASSESSED` (its target is not in ``scanned``).

    Args:
        baseline: The earlier run.
        later: The later run.
        scanned: Targets (baseline names) the later run scanned (:func:`scanned_targets`); None = every target.
        aliases: Later target name -> baseline target name (e.g. ``{"jlc-prod": "vk-prod"}``).
        path_aliases: Later location prefix -> baseline prefix, an explicit rename (still reported as open).

    Returns:
        The outcomes, the later findings paired with no baseline finding (``new``: a new key, or a key at a new
        place) and the moves detected.
    """
    aliases, explicit = aliases or {}, path_aliases or {}
    base = [f for f in baseline.findings if f.kind != "range"]
    after = [g for g in later.findings if g.kind != "range"]
    moves = detect_moves(base, after, aliases=aliases, path_aliases=explicit)
    by_key: dict[tuple[str, ...], list[Finding]] = defaultdict(list)
    for f in base:
        by_key[_key(f, aliases)].append(f)
    paired: dict[int, list[tuple[Finding, str]]] = defaultdict(list)   # id(baseline finding) -> [(later, moved to)]
    unpaired: dict[tuple[str, ...], list[Finding]] = defaultdict(list)   # later findings at no baseline place
    for g in after:
        k = _key(g, aliases)
        plain = {_unalias(loc, explicit)[0] for loc in g.locations}
        moved_to = ""
        mapped = set()
        for loc in plain:
            back, hit = _unalias(loc, moves.get(k[0], {}))
            mapped.add(back)
            if hit:
                moved_to = moved_to or next(n for n in moves[k[0]] if _has_prefix(loc, n))
        peers = []
        for f in by_key.get(k, []):
            locs = set(f.locations)
            if not locs or not plain or locs & plain:
                peers.append((f, ""))
            elif locs & mapped:
                peers.append((f, moved_to))
        if not peers:
            unpaired[k].append(g)
            continue
        below = [p for p in peers if _at_or_below(p[0].version, g.version)]
        pick = (max(below, key=lambda p: _vkey(p[0].version) or ()) if below
                else min(peers, key=lambda p: _vkey(p[0].version) or ()))
        paired[id(pick[0])].append((g, pick[1]))
    out = Comparison(moves=moves)
    for f in base:
        ids = set(f.ids)
        if scanned is not None and _target(f, aliases) not in scanned:
            out.outcomes.append(FindingOutcome(f, NOT_ASSESSED, "target has no scan record in the later run"))
            continue
        cands = paired.get(id(f), [])
        still = [(g, m) for g, m in cands if _at_or_below(g.version, f.version) and (not ids or ids & set(g.ids))]
        kept = {id(g) for g, _ in still}
        higher = [(g, m) for g, m in cands if id(g) not in kept]
        if still:
            gone = ids - set().union(*(set(g.ids) for g, _ in still)) if ids else set()
            where = sorted({m for _, m in still if m})
            if all(m for _, m in still):
                status, note = MOVED, f"moved to {', '.join(where)}; still at {still[0][0].version}"
            elif gone:
                status, note = PARTLY, f"{len(gone)} of {len(ids)} advisories gone"
            else:
                status, note = OPEN, f"still at {still[0][0].version}"
        elif higher:
            where = sorted({m for _, m in higher if m})
            status = PARTLY
            note = "moved to " + ", ".join(sorted({g.version for g, _ in higher}))
            note += f" (under {', '.join(where)})" if where else ""
        elif elsewhere := unpaired.get(_key(f, aliases)):
            places = sorted({loc for g in elsewhere for loc in g.locations} or {"(no location)"})
            status = ELSEWHERE
            note = (f"found at {', '.join(places[:3])}{' …' if len(places) > 3 else ''} "
                    f"({', '.join(sorted({g.version for g in elsewhere}))})")
        else:
            status, note = REMEDIATED, ""
        out.outcomes.append(FindingOutcome(f, status, note))
    out.new = [g for gs in unpaired.values() for g in gs]
    return out


def scanned_targets(run_dir: Path, *, since: float | None = None) -> set[str]:
    """Return the targets a run directory has a complete scan record for.

    Each ``repos-*.json`` names its root (a scan mirror's ``<repo>.worktrees/<name>`` is filed under its checkout;
    a root under the home directory is also named ``~/...``),
    each ``server-<host>.log`` holding the audit's ``RESULT`` line names its host, and this machine is always scanned.
    A run's findings of a target under a scanned root are assessed too (see :func:`assessed`).

    Args:
        run_dir: A run directory (``<state>/<date>``, or a verification run's subdirectory).
        since: Ignore records last written before this time (epoch seconds): the daily run passes its own start,
            so a record an earlier run of the same day left behind (a host since removed from ``servers``) is
            never read as this run's scan.

    Returns:
        The target names.
    """
    from agentteams.vulnscan import engine   # lazy: engine is heavy and only its target name is needed

    out = {engine.LOCAL_TARGET}
    home = str(Path.home())

    def fresh(p: Path) -> bool:
        try:
            return since is None or p.stat().st_mtime >= since
        except OSError:
            return False

    def text(p: Path) -> str:   # "" for a record that cannot be read: it names nothing
        try:
            return p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    for p in filter(fresh, run_dir.glob("repos-*.json")):
        try:
            root = _MIRROR_TARGET.sub("", str(json.loads(text(p))["target"]))
        except (ValueError, KeyError, TypeError):
            root = ""
        if root:
            out.add(root)
        if root.startswith(home + "/"):   # a repo_roots entry written as ~/... labels its findings that way
            out.add("~" + root[len(home):])
    out |= {p.stem.removeprefix("server-") for p in filter(fresh, run_dir.glob("server-*.log"))
            if _AUDIT_DONE.search(text(p))}
    return out


def assessed(targets: Iterable[str], scanned: set[str]) -> set[str]:
    """Return the ``targets`` a scan covered: those in ``scanned`` and those inside a scanned root.

    Args:
        targets: Finding targets (``<root>/<repo>`` for a repository inside a scanned folder).
        scanned: Scanned roots and hosts (:func:`scanned_targets`).

    Returns:
        The covered targets.
    """
    roots = tuple(s.rstrip("/") + "/" for s in scanned)
    return {t for t in targets if t in scanned or t.startswith(roots)}


def load_run(run_dir: Path) -> Report:
    """Read a run's ``summary.json`` as a report.

    Args:
        run_dir: The run directory.

    Returns:
        The run's aggregate report.

    Raises:
        OSError: No readable ``summary.json``.
        ValueError: It is not a JSON object of the report's shape.
    """
    data = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{run_dir / 'summary.json'} is not a JSON object")
    try:
        return Report.from_dict(data)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"{run_dir / 'summary.json'} is not a report: {type(exc).__name__}: {exc}") from exc


def outcome_rows(cmp: Comparison) -> list[dict[str, Any]]:
    """Return one plain row per baseline finding (status, note and the finding's identity), for CSV or JSON.

    Args:
        cmp: A :func:`compare` result.

    Returns:
        Rows with ``status, severity, target, kind, ecosystem, name, version, fix, fixed_upstream, note``.
    """
    from agentteams.vulnscan.osv import SEVERITY_NAME

    rows = []
    for o in cmp.outcomes:
        f = o.finding
        name = SEVERITY_NAME.get(f.severity, "?")
        sev = f"UNSCORED (as {name})" if f.unscored and not f.rated and not f.malicious else name
        rows.append({"status": o.status, "severity": sev, "target": f.target, "kind": f.kind,
                     "ecosystem": f.ecosystem, "name": f.name, "version": f.version, "fix": f.fix,
                     "fixed_upstream": f.fixed_upstream, "note": o.note})
    return rows
