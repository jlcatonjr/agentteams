"""bridge_retire.py — ``--retire-bridge PAIR``: remove what one bridge generated, under a clearance (C-5).

A project that moves from a bridge to a native team keeps the bridge's stubs and pair-dir artifacts
until something removes them, and a bridge-generated agent stub copies its source's tools, so under
``write_policy: "orchestrator-only"`` it stays wide (the render refuses such a stub; see
:mod:`agentteams.cli.write_policy_warning`). ``--revert-migration`` only undoes ``--migrate`` and the
``--bridge-*`` modes only check, refresh or merge, so this is the governed way out.

**What it removes** — an enumerated set, never a whole directory (Rule 12):

* the known artifacts (:data:`PAIR_ARTIFACTS`) in ``references/bridges/<pair>/``; the directory goes
  only when nothing else is left in it, and any other file there is reported and kept;
* ``.claude/agents/*.md`` and ``.claude/skills/*/SKILL.md`` whose ``bridge:`` front-matter value is the
  pair (:func:`bridge_marker`, the predicate the render gate uses too).

Every candidate must sit inside the project with no symlink anywhere on its path below the project root
(a symlinked ``.claude/skills`` in a scratch clone must not reach the real project), and is re-hashed
just before it is unlinked.

**What it never touches** — the target entry files (``CLAUDE.md``, ``.claude/agent-team.md``, ...),
unmarked skills (the recall skills carry no marker) and the opt-in hooks/schedule artifacts. Entry files
are reported with the ``AGENTTEAMS-BRIDGE`` regions they still hold: a ``--merge`` render keeps those
regions, and the D3 bridge check keys on them, so the operator removes them by hand.

**Authorization** mirrors ``--branch-cleanup``: a dry run by default; ``--apply`` needs a ``@security``
PASS for ``retire-bridge:<pair>:<sha256>``, where the sha covers every (path, content sha256) in the
removal set. ``--apply`` recomputes it, so a file edited after the dry run changes the id and no
clearance matches. An unretracted HALT on the ``retire-bridge`` family, the pair id or the exact id blocks
it (C-2); the pair and exact checks matter because a retraction of any ``retire-bridge:...`` id clears a
family-level HALT. Every file is backed up (:func:`agentteams.backup.backup_output_dir`, into
``<project>/.agentteams-backups/<timestamp>/<path>.agentteams-bak``; restore by copying back) before removal.

v1 supports the ``copilot-vscode-to-claude`` pair only (:data:`SUPPORTED_PAIRS`).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

#: Pairs this command can retire; any other is refused before anything is read.
SUPPORTED_PAIRS: tuple[str, ...] = ("copilot-vscode-to-claude",)

#: The files ``bridge.run_bridge`` writes into ``references/bridges/<pair>/``.
PAIR_ARTIFACTS: tuple[str, ...] = (
    "bridge-manifest.json", "agent-inventory.md", "quickstart-snippet.md", "entrypoint.md",
    "domain-boundary.md", "bridge-check.report.md", "bridge-merge.report.md",
)

#: Entry files the claude-target bridge writes, and the skills it emits unmarked; reported, never removed.
_REPORTED_FILES: tuple[str, ...] = (
    "CLAUDE.md", ".claude/agent-team.md", ".claude/quickstart-snippet.md", ".claude/README.md",
    ".claude/skills/recall/SKILL.md", ".claude/skills/code-recall/SKILL.md",
)

#: A HALT on this family id (or any ``retire-bridge:...`` id) blocks the mode.
RETIRE_FAMILY = "retire-bridge"

#: Exit codes: 0 done (or dry run), 1 refused/failed, 3 no authorization (operator step needed).
EXIT_OK, EXIT_FAIL, EXIT_UNAUTHORIZED = 0, 1, 3

_BRIDGE_KEY_RE = re.compile(r"^bridge[ \t]*:[ \t]*(.*?)[ \t]*$", re.MULTILINE)
_BRIDGE_REGION_RE = re.compile(r"AGENTTEAMS-BRIDGE:BEGIN[ \t]+(\S+)")


class RetireError(ValueError):
    """Raised when a pair can't be retired (unsupported, or nothing to retire)."""


def bridge_marker(path: Path) -> str | None:
    """The raw ``bridge:`` front-matter value of a Markdown agent or skill file.

    The one predicate for "a bridge generated this file": ``--retire-bridge`` removes a file whose value
    equals the pair, and the write-policy render gate refuses a wide file that has the key at all, so the
    two can never disagree about which files a bridge owns. It lives in this integrity-pinned module
    because it decides what gets deleted.

    Args:
        path: A Markdown file with YAML front matter.

    Returns:
        The value, unquoted (``""`` when the key is present but empty); None when the file has no
        top-level ``bridge:`` key in its front matter, isn't Markdown, or can't be read.

    Raises:
        Nothing.
    """
    if path.suffix != ".md":
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    if not lines or lines[0].rstrip() != "---":
        return None
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].rstrip() == "---")
    except StopIteration:
        return None
    match = _BRIDGE_KEY_RE.search("\n".join(lines[1:end]))
    if not match:
        return None
    value = match.group(1)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return value


@dataclass
class RetirePlan:
    """The removal set for one pair, computed from disk.

    Attributes:
        pair: The bridge pair, e.g. ``copilot-vscode-to-claude``.
        removals: ``(project-relative posix path, content sha256)``, sorted.
        kept: Other files in the pair dir, left in place.
        reported: Entry files and unmarked skills the operator must handle.
    """

    pair: str
    removals: list[tuple[str, str]] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    reported: list[str] = field(default_factory=list)

    @property
    def sha256(self) -> str:
        """The digest binding a clearance to exactly this removal set."""
        body = json.dumps({"pair": self.pair, "removals": self.removals}, sort_keys=True)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    @property
    def action(self) -> str:
        """The clearance action id, ``retire-bridge:<pair>:<sha256>``."""
        return f"{RETIRE_FAMILY}:{self.pair}:{self.sha256}"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contained(project: Path, path: Path) -> bool:
    """True when *path* is a regular file inside *project* with no symlink on its path below it."""
    if not path.resolve().is_relative_to(project):
        return False
    part = path
    while part != project:
        if part.is_symlink():
            return False
        if part.parent == part:
            return False
        part = part.parent
    return path.is_file()


def build_plan(project: Path, pair: str) -> RetirePlan:
    """Compute the removal set for *pair* in *project*. Read-only.

    Args:
        project: The project root the bridge wrote into.
        pair: The bridge pair.

    Returns:
        The plan.

    Raises:
        RetireError: The pair isn't supported, or there is nothing to retire.
    """
    if pair not in SUPPORTED_PAIRS:
        raise RetireError(f"--retire-bridge supports {', '.join(SUPPORTED_PAIRS)} only (not {pair!r})")
    project = project.resolve()
    plan = RetirePlan(pair=pair)
    candidates: list[Path] = []
    pair_dir = project / "references" / "bridges" / pair
    if pair_dir.is_dir():
        for path in sorted(pair_dir.iterdir()):
            if path.name in PAIR_ARTIFACTS and _contained(project, path):
                candidates.append(path)
            else:
                plan.kept.append(path.relative_to(project).as_posix())
    claude = project / ".claude"
    for path in [*sorted((claude / "agents").glob("*.md")), *sorted((claude / "skills").glob("*/SKILL.md"))]:
        if bridge_marker(path) == pair and _contained(project, path):
            candidates.append(path)
    if not candidates:
        raise RetireError(f"nothing to retire for {pair!r} in {project}: no pair-dir artifacts and no file "
                          f"marked 'bridge: {pair}' (inside the project, on a path without symlinks)")
    plan.removals = sorted((p.relative_to(project).as_posix(), _sha(p)) for p in candidates)
    removed = {rel for rel, _ in plan.removals}
    for rel in _REPORTED_FILES:
        path = project / rel
        if rel in removed or not path.is_file():
            continue
        regions = sorted(set(_BRIDGE_REGION_RE.findall(path.read_text(encoding="utf-8", errors="ignore"))))
        note = f" (AGENTTEAMS-BRIDGE regions to remove by hand: {', '.join(regions)})" if regions else ""
        plan.reported.append(f"{rel}{note}")
    return plan


def _unverified(project: Path, plan: RetirePlan) -> list[str]:
    """Removal-set files that left the project, gained a symlink, or changed since the plan."""
    bad = []
    for rel, sha in plan.removals:
        path = project / rel
        if not _contained(project, path) or _sha(path) != sha:
            bad.append(rel)
    return bad


def _remove(project: Path, plan: RetirePlan) -> tuple[bool, list[str]]:
    from agentteams import backup

    rels = [rel for rel, _ in plan.removals]
    backup.backup_output_dir(project, files_to_backup=rels, reason=f"retire-bridge-{plan.pair}", framework="claude")
    lines = []
    for rel, sha in plan.removals:
        path = project / rel
        if not _contained(project, path) or _sha(path) != sha:  # re-checked at the last moment
            lines.append(f"Refused: {rel} changed or moved after the clearance was checked; stopped (the files "
                         "listed above were removed and are in the backup).")
            return False, lines
        path.unlink()
        lines.append(f"  ✓  removed {rel}")
        if path.name == "SKILL.md" and not any(path.parent.iterdir()):
            path.parent.rmdir()
    pair_dir = project / "references" / "bridges" / plan.pair
    if pair_dir.is_dir() and not pair_dir.is_symlink() and not any(pair_dir.iterdir()):
        pair_dir.rmdir()
    return True, lines


def run_retire(project: Path, pair: str, *, team_dir: Path, apply: bool) -> tuple[int, list[str]]:
    """``--retire-bridge``: report the removal set, then (with ``apply``) remove it under clearance.

    Args:
        project: The project root.
        pair: The bridge pair.
        team_dir: The team agents dir holding the security-decisions log.
        apply: Remove; otherwise a dry run that changes and consumes nothing.

    Returns:
        ``(exit code, report lines)``.

    Raises:
        RetireError: The pair isn't supported, or there is nothing to retire.
    """
    from agentteams.cli import security_gate

    project = project.resolve()
    plan = build_plan(project, pair)
    report = [f"Retire bridge {pair}: {len(plan.removals)} file(s); clearance action {plan.action!r}",
              f"  security log: {team_dir / 'references' / 'security-decisions.log.csv'}"]
    report += [f"  ·  remove {rel}  (sha256 {sha[:12]})" for rel, sha in plan.removals]
    report += [f"  -  keep {rel}  (not a known bridge artifact, or reached through a symlink)" for rel in plan.kept]
    report += [f"  !  not touched: {rel}" for rel in plan.reported]
    if not apply:
        report.append(f"Dry run: nothing changed. Re-run with --apply after @security records a PASS for action "
                      f"{plan.action!r}.")
        return EXIT_OK, report
    # C-2. The family id also matches every retire-bridge:<pair>... HALT, but a retraction of any
    # retire-bridge:... id clears a family-level HALT, so the pair and exact ids are checked too.
    for action in (RETIRE_FAMILY, f"{RETIRE_FAMILY}:{pair}", plan.action):
        try:
            security_gate._assert_no_unretracted_halt(team_dir, action=action)
        except RuntimeError as exc:
            report.append(f"Refused: an unretracted @security HALT is in force (C-2): {exc}")
            return EXIT_FAIL, report
    bad = _unverified(project, plan)
    if bad:  # unreachable unless a file changes between build_plan and here; nothing is consumed
        report.append(f"Refused: changed since the plan was computed: {', '.join(bad)}")
        return EXIT_FAIL, report
    action = plan.action
    ok, reason = security_gate.check_clearance(team_dir, action=action)
    if not ok:
        report.append(f"Refused: no usable clearance for {action!r}: {reason}")
        return EXIT_UNAUTHORIZED, report
    try:
        security_gate._assert_destructive_action_allowed(team_dir, action=action)  # consumes it
    except RuntimeError as exc:
        report.append(f"Refused: clearance for {action!r} could not be consumed: {exc}")
        return EXIT_UNAUTHORIZED, report
    done, lines = _remove(project, plan)
    report += lines
    if not done:
        return EXIT_FAIL, report
    report.append("Next: remove the AGENTTEAMS-BRIDGE regions listed above from the entry files by hand (a --merge "
                  "render keeps them, and the bridge check keys on them), then re-render the native team: "
                  "agentteams --framework claude --update --materialize-native --merge.")
    return EXIT_OK, report


__all__ = [
    "EXIT_FAIL", "EXIT_OK", "EXIT_UNAUTHORIZED", "PAIR_ARTIFACTS", "RETIRE_FAMILY", "RetireError", "RetirePlan",
    "SUPPORTED_PAIRS", "bridge_marker", "build_plan", "run_retire",
]
