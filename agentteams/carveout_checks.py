"""carveout_checks.py — the carve-out registry's checks against the team directory and git history.

Split from :mod:`agentteams.carveouts` (CH-07): the pinned team directory (no symlinks; its decisions log and
exception registry must exist), the constraint-relaxing rule's clauses by number, rollback against the registry's git
history, HALTs on a carve-out and its lineage (C-2), declared clearances, and the Rule 15 aggregate cap.

Stdlib only.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import datetime as _dt

from agentteams.carveouts import (
    _CLAUSE_RE, GATE_FAMILY, REGISTRY_REL, RELAXABLE_RULE15_CLAUSES, _RULE15_HEADING_RE, TEAM_DIRS, Problem, _date, _git,
    _str_list, decision_dir, load_registry)


def team_dir_problem(root: Path, registry: dict) -> Problem | None:
    """Why the pinned team directory can't be trusted: a symlink on the way, or a missing log or exception registry.

    Args:
        root: The project root.
        registry: The parsed (validated) registry.

    Returns:
        A problem, or ``None``.
    """
    from agentteams.cli.exception_registry import EXCEPTION_REGISTRY_REL

    team = decision_dir(root, registry)
    if not team.is_dir() or team.resolve() != team:
        return Problem("registry", "bad-decision-log", f"{registry.get('decision_log')} is missing or reached through "
                                                       "a symlink")
    marker = team / "references" / "build-log.json"
    if marker.is_symlink() or not marker.is_file():
        # The emitted sandbox write-protects a team's key store only when the team exists (its build log) at start
        # (adversarial round 3): a store an agent creates in a team that was never generated is not protected.
        return Problem("registry", "not-a-team", f"{registry.get('decision_log')} holds no generated team "
                                                 "(references/build-log.json); its key store would not be protected")
    for rel, code in (("references/security-decisions.log.csv", "no-decision-log"),
                      (EXCEPTION_REGISTRY_REL, "no-exception-registry")):
        path = team / rel
        if path.is_symlink() or not path.is_file():
            return Problem("registry", code, f"{registry.get('decision_log')}/{rel} must exist (a regular file): the "
                                             "carve-outs' HALT checks and aggregate cap read it, and fail closed")
    return None


def rule15_numbers(root: Path) -> set[int]:
    """The number(s) this project's constitution gives the constraint-relaxing-authorizations rule.

    Args:
        root: The project root.

    Returns:
        Every number found across the entry files (they should agree).
    """
    found: set[int] = set()
    for rel in (".claude/CLAUDE.md", "CLAUDE.md", ".github/copilot-instructions.md", "AGENTS.md", "AGENTS.override.md",
                ".goosehints"):
        path = root / rel
        if path.is_file() and not path.is_symlink():
            found.update(int(m.group(1)) for m in _RULE15_HEADING_RE.finditer(path.read_text(encoding="utf-8")))
    return found


def clause_problems(root: Path, registry: dict) -> list[Problem]:
    """Clauses of the constraint-relaxing rule that no carve-out may lift (anything but (b)), by number.

    Args:
        root: The project root.
        registry: The parsed registry.

    Returns:
        The problems.
    """
    numbers = rule15_numbers(root)
    if not numbers:  # fail closed: without the number, (a), (c) and (d) could be lifted under any wording
        return [Problem("registry", "no-rule15", "no entry file numbers the constraint-relaxing-authorizations rule "
                                                 "('<n>. **Constraint-relaxing authorizations'), so the clauses that "
                                                 "may never be lifted can't be identified")]
    out: list[Problem] = []
    for entry in registry["carveouts"]:
        if not isinstance(entry, dict):
            continue
        for clause in _str_list(entry.get("relaxes")) or []:
            m = _CLAUSE_RE.match(clause)
            if m and int(m.group("num")) in numbers and m.group("letter") not in RELAXABLE_RULE15_CLAUSES:
                out.append(Problem(str(entry.get("id")), "unrelaxable", f"'{clause}' lifts a clause of the "
                                   "constraint-relaxing-authorizations rule other than (b); never allowed"))
    return out


def rollback_problem(root: Path, registry: dict, *, depth: int = 500) -> Problem | None:
    """Refuse an older signed registry restored over a newer one: every earlier commit's ``sequence`` must be lower.

    Args:
        root: The project root.
        registry: The parsed (current, committed) registry.
        depth: How many commits of the registry's history to read.

    Returns:
        A problem, or ``None``.
    """
    shallow = _git(root, "rev-parse", "--is-shallow-repository")
    if shallow.returncode != 0 or shallow.stdout.strip() != "false":
        return Problem("registry", "rollback", "the clone is shallow (or git can't say), so the registry's history "
                                               "can't be checked for a newer sequence; fetch the full history")
    # --full-history: a merge that keeps an older side's file must not prune the newer commits from the log (@security
    # round 3); --no-replace-objects: replace refs and grafts must not rewrite what history says.
    log = _git(root, "--no-replace-objects", "log", "--full-history", f"-{depth}", "--format=%H", "--", REGISTRY_REL)
    commits = log.stdout.split() if log.returncode == 0 else []
    try:
        current_bytes = (root / REGISTRY_REL).read_bytes()
    except OSError:
        current_bytes = b""
    current = registry.get("sequence")
    high = _high_water(root, registry)
    if isinstance(current, int) and high is not None and current < high:
        return Problem("registry", "rollback", f"the team dir records sequence {high} as already rendered (this one "
                                               f"is {current}): an older registry can't be restored")
    for commit in commits:
        shown = _git(root, "--no-replace-objects", "show", f"{commit}:{REGISTRY_REL}", text=False)
        if shown.returncode != 0 or shown.stdout == current_bytes:
            continue  # this very version (or a deletion): it can't outrank itself
        try:
            earlier = json.loads(shown.stdout).get("sequence")
        except ValueError:
            earlier = None  # an unparsable old version can't outrank this one
        if isinstance(earlier, int) and not isinstance(earlier, bool) and isinstance(current, int) and earlier >= current:
            return Problem("registry", "rollback", f"commit {commit[:7]} already had sequence {earlier} (this one is "
                                                   f"{current}): an older registry can't be restored; re-sign with a "
                                                   "higher sequence")
    return None


def _logged_actions(team_dir: Path) -> set[str]:
    with (team_dir / "references" / "security-decisions.log.csv").open(encoding="utf-8", newline="") as fh:
        return {(r.get("action_reviewed") or "").strip().lower() for r in csv.DictReader(fh)}


#: Where the highest sequence ever rendered is kept, in the pinned team directory (defence in depth beside git
#: history: a reset or rewritten history can't lower it).
HIGH_WATER_REL = "references/carveout-high-water.json"


def _high_water(root: Path, registry: dict) -> int | None:
    path = decision_dir(root, registry) / HIGH_WATER_REL
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("sequence") if path.is_file() else None
    except (OSError, ValueError):
        value = 1 << 62  # an unreadable high-water mark fails closed: nothing can be shown to be newer
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def record_high_water(root: Path, registry: dict) -> str | None:
    """Raise the team dir's high-water mark to the registry's sequence after a successful render.

    Args:
        root: The project root.
        registry: The rendered registry.

    Returns:
        ``None``, or why the mark could not be written (reported; the render itself stands).
    """
    seq = registry.get("sequence")
    high = _high_water(root, registry)
    if not isinstance(seq, int) or (high is not None and high >= seq):
        return None
    path = decision_dir(root, registry) / HIGH_WATER_REL
    try:
        path.write_text(json.dumps({"sequence": seq}) + "\n", encoding="utf-8")
    except OSError as exc:
        return f"{HIGH_WATER_REL} not updated: {exc}"
    return None


def check_lineage(root: Path, registry: dict, entries: list[dict]) -> list[Problem]:
    """Each lineage id must be logged in the pinned decision log with no unretracted HALT (C-2), ancestors included.

    Every HALT along the lineage passes down, whatever its basis: stricter than a rule that lets some HALT bases lapse.

    Args:
        root: The project root.
        registry: The parsed registry.
        entries: The entries to check.

    Returns:
        The problems.
    """
    from agentteams.cli.decision_log import _assert_no_unretracted_halt

    wanted = [(e, lid) for e in entries for lid in (_str_list(e.get("lineage")) or [])]
    if not wanted:
        return []
    team_dir = decision_dir(root, registry)
    if not (team_dir / "references" / "security-decisions.log.csv").is_file():
        return [Problem(e["id"], "no-decision-log", f"lineage is declared but {team_dir.name}/references has no "
                                                    "security-decisions log")
                for e in {id(e): e for e, _ in wanted}.values()]
    try:
        logged = _logged_actions(team_dir)
    except (OSError, csv.Error) as exc:
        return [Problem("registry", "decision-log-unreadable", str(exc))]
    out: list[Problem] = []
    for entry, lid in wanted:
        if lid.lower() not in logged:
            out.append(Problem(entry["id"], "lineage-missing", f"lineage '{lid}' is not an action in the decision log"))
            continue
        try:
            _assert_no_unretracted_halt(team_dir, action=lid)
        except RuntimeError as exc:
            out.append(Problem(entry["id"], "lineage-halt", f"lineage '{lid}': {exc}"))
    return out


def check_own_halts(root: Path, registry: dict, entries: list[dict]) -> list[Problem]:
    """A HALT on ``carveout:<id>`` (C-2) stops that carve-out, whatever its lineage.

    Args:
        root: The project root.
        registry: The parsed registry.
        entries: The entries to check.

    Returns:
        The problems.
    """
    from agentteams.cli.decision_log import _assert_no_unretracted_halt

    out: list[Problem] = []
    for entry in entries:
        try:
            own_action = f"{GATE_FAMILY}:{entry['id']}"
            _assert_no_unretracted_halt(decision_dir(root, registry), action=own_action)
        except RuntimeError as exc:
            out.append(Problem(entry["id"], "halted", str(exc)))
    return out


def check_aggregate_cap(root: Path, registry: dict, entries: list[dict]) -> list[Problem]:
    """Every active carve-out (in scope or not) counts against the team's Rule 15 exception-registry cap.

    Args:
        root: The project root.
        registry: The parsed registry.
        entries: Every active, valid entry.

    Returns:
        The problems.
    """
    from agentteams.cli import exception_registry as er

    team_dir = decision_dir(root, registry)
    if not entries:
        return []
    try:
        exceptions = er.load_registry(team_dir)
        cap = er._configured_cap(exceptions)
        held = len(er.active_relaxing_exceptions(exceptions))
    except (RuntimeError, ValueError, OSError) as exc:
        return [Problem("registry", "aggregate-cap", f"the exception registry can't be read: {exc}")]
    if held + len(entries) > cap:
        return [Problem("registry", "aggregate-cap", f"{held} active relaxing exception(s) plus {len(entries)} "
                                                     f"carve-out(s) exceed the Rule 15 aggregate cap of {cap}")]
    return []


def active_carveouts_for(team_dir: Path, *, today: _dt.date | None = None) -> int:
    """How many carve-outs count against *team_dir*'s Rule 15 exception cap: those its project's registry pins there.

    Counts every entry whose status is ``active`` and whose ``expires`` is not past, valid or not and signed or not:
    counting more is the fail-closed direction for a cap. An entry with no readable ``expires`` counts.

    Args:
        team_dir: A team directory (one of :data:`~agentteams.carveouts.TEAM_DIRS` under a project root).
        today: The current date (default: today).

    Returns:
        The count; 0 when *team_dir* is not a team directory, the project has no registry, or the registry pins
        another team directory.

    Raises:
        CarveoutError: The registry exists but can't be read (callers refuse: a guard never treats it as empty).
    """
    team = Path(team_dir).resolve()
    root = team.parent.parent  # every TEAM_DIRS entry is two levels below the project root
    if not any((root / rel).resolve() == team for rel in TEAM_DIRS):
        return 0
    registry = load_registry(root)
    if registry is None or decision_dir(root, registry).resolve() != team:
        return 0
    today = today or _dt.date.today()
    count = 0
    for entry in registry["carveouts"]:
        if not isinstance(entry, dict) or entry.get("status") != "active":
            continue
        expires = _date(entry.get("expires"))
        count += expires is None or expires >= today
    return count


def check_clearances(root: Path, registry: dict, entries: list[dict]) -> list[Problem]:
    """Each entry's declared ``clearance`` must have a verified PASS bound to its file's current sha256.

    Args:
        root: The project root.
        registry: The parsed registry.
        entries: The entries to check.

    Returns:
        The problems.
    """
    from agentteams.cli.clearance_binding import check_file_clearance

    out: list[Problem] = []
    for entry in entries:
        clearance = entry.get("clearance")
        if isinstance(clearance, dict):
            ok, reason = check_file_clearance(decision_dir(root, registry), clearance["scope"],
                                              root / clearance["file"])
            if not ok:
                out.append(Problem(entry["id"], "not-cleared", reason))
    return out
