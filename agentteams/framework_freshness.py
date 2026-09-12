"""Cross-framework render-freshness scan.

Targets one specific slice of the silent render-staleness failure class: an operator
regenerates one provider render for a project (e.g. ``copilot-vscode`` under
``.github/agents/``) and never regenerates a sibling render (e.g. ``claude`` under
``.claude/agents/``), so the forgotten render's build-log baseline predates a template
change with **no signal**. The per-framework ``--check`` catches this only if someone
remembers to run it against that specific framework; nothing surfaces the divergence
across a project's renders at once.

This module walks a project tree, finds every provider render (each carries its own
``references/build-log.json``), and reuses :func:`agentteams.drift.detect_drift` to
report which renders have drifted behind the current templates — and which sibling
render is the freshest current one, so the lagging ones are obvious. Strictly read-only:
it never renders templates and never writes files.

**Scope limitation (be honest — this is template-hash-based).** ``detect_drift`` compares
a render's build-log ``template_hashes`` against the current template files. It therefore
detects a render whose build-log genuinely *predates* a template change. It does **not**
detect the case where an ``--update --merge`` refreshed the build-log to current hashes but
``preserve_on_shrink`` kept a stale operator-enriched fenced body: the recorded hash then
equals the current hash and this scan reports the render "current" while a fenced region is
in fact behind. Catching *that* requires a fence-version-aware signal (a separate, deeper
change to the merge engine — see ``tmp/by-week/2026-W37/agentteams-fence-version-staleness.plan.md``
and the remediation log). A render with a legacy build-log that predates hash tracking is
reported as **unverifiable**, not stale (it cannot be compared).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentteams import drift

# Directory name parts that are never a *live* provider render: backups, snapshots,
# git worktrees (separate checkouts), the canonical hub (source-of-truth, not a render),
# scratch/working trees, VCS/build output, and virtualenvs. A path is skipped if ANY of
# its parts is in this set (or ends in ``.bak``). This keeps the scan to the renders an
# operator actually maintains, not the copies the tool makes of them.
_EXCLUDE_DIR_PARTS: frozenset[str] = frozenset(
    {
        ".agentteams-backups",
        "canonical",
        "worktrees",
        ".worktrees",
        "backups",
        "tmp",
        ".git",
        "node_modules",
        "build",
        "dist",
        "__pycache__",
        ".venv",
        "_site",
    }
)


def _is_excluded(log_path: Path, project_root: Path) -> bool:
    """Return True when *log_path* lives under a non-live-render tree.

    Only the path parts **relative to project_root** are examined — testing the
    absolute parts would drop every render in a project that merely happens to be
    checked out under a directory named e.g. ``tmp`` or ``build``, silently returning
    zero renders (a false "all current"). A part outside project_root is not our
    business.

    Args:
        log_path:     A discovered ``references/build-log.json`` path.
        project_root: The scan root the path was discovered under.

    Returns:
        True if any relative path part is an excluded directory name or ends in ``.bak``.
    """
    try:
        rel = log_path.relative_to(project_root)
    except ValueError:
        rel = log_path
    return any(
        part in _EXCLUDE_DIR_PARTS or part.endswith(".bak")
        for part in rel.parts
    )


@dataclass
class FrameworkFreshness:
    """Freshness verdict for a single provider render directory.

    Attributes:
        framework:          Framework name recorded in the render's build-log
                            (``"unknown"`` when the log omits it).
        agents_dir:         The render's agents directory (parent of ``references/``).
        generated_at:       ISO-8601 timestamp the render recorded, or ``None`` for
                            an older build-log that predates provenance stamping.
        agentteams_version: Generator version the render recorded, or ``None``.
        changed_templates:  Template paths whose content changed since this render's
                            build-log baseline (i.e. this render is behind them).
        missing_templates:  Templates recorded by the render but absent on disk now.
        unverifiable:       True when the build-log predates template-hash tracking
                            (no ``template_hashes``), so drift cannot be computed — the
                            render is neither confirmed current nor confirmed stale.
    """

    framework: str
    agents_dir: Path
    generated_at: str | None
    agentteams_version: str | None
    changed_templates: list[str]
    missing_templates: list[str]
    unverifiable: bool = False

    @property
    def is_stale(self) -> bool:
        """Return True when this render provably lags the current templates.

        An ``unverifiable`` render (legacy build-log) is never reported stale — it
        cannot be compared, so it is surfaced separately rather than crying wolf.
        """
        if self.unverifiable:
            return False
        return bool(self.changed_templates or self.missing_templates)


@dataclass
class FrameworkFreshnessReport:
    """Aggregate freshness verdict across every render found in a project."""

    renders: list[FrameworkFreshness] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def stale(self) -> list[FrameworkFreshness]:
        """Return the renders that lag the current templates."""
        return [r for r in self.renders if r.is_stale]

    @property
    def unverifiable(self) -> list[FrameworkFreshness]:
        """Return renders whose legacy build-log cannot be compared to templates."""
        return [r for r in self.renders if r.unverifiable]

    @property
    def has_stale(self) -> bool:
        """Return True when at least one render is behind the templates."""
        return bool(self.stale)

    @property
    def freshest(self) -> FrameworkFreshness | None:
        """Return the newest **non-stale** stamped render, if any.

        Restricted to current renders so :func:`print_report` never points the operator
        at a render that is itself behind. Falls back to None when no current render
        carries a ``generated_at`` stamp.
        """
        stamped = [r for r in self.renders if r.generated_at and not r.is_stale]
        if not stamped:
            return None
        return max(stamped, key=lambda r: r.generated_at or "")

    def exit_code(self) -> int:
        """Return the process exit code: 1 when any render is stale, else 0."""
        return 1 if self.has_stale else 0


def discover_render_dirs(project_root: Path) -> list[Path]:
    """Find every provider render directory beneath *project_root*.

    A render directory is any directory containing ``references/build-log.json``
    (the per-render provenance the build pipeline writes). Backup, VCS, and build
    directories are excluded.

    Args:
        project_root: The project root to scan.

    Returns:
        Sorted list of agents directories (the parent of each ``references/``),
        deduplicated.
    """
    found: set[Path] = set()
    for log_path in project_root.rglob("references/build-log.json"):
        if _is_excluded(log_path, project_root):
            continue
        found.add(log_path.parent.parent)
    return sorted(found)


def scan(project_root: Path, templates_dir: Path) -> FrameworkFreshnessReport:
    """Scan *project_root* for provider renders and report which lag the templates.

    Reuses :func:`agentteams.drift.detect_drift` per render directory — read-only,
    no rendering, no writes.

    Args:
        project_root:  Project root to scan for renders.
        templates_dir: Current templates directory to compare each render against.

    Returns:
        A :class:`FrameworkFreshnessReport` with one entry per discovered render
        plus any per-render load errors (which never abort the whole scan).
    """
    report = FrameworkFreshnessReport()
    for agents_dir in discover_render_dirs(project_root):
        try:
            build_log = drift.load_build_log(agents_dir)
        except (FileNotFoundError, ValueError) as exc:
            report.errors.append(f"{agents_dir}: {exc}")
            continue
        # A build-log without template_hashes predates hash tracking; detect_drift's
        # legacy branch would return ALL templates as "changed", which is not real drift.
        # Classify it as unverifiable rather than crying wolf.
        unverifiable = not build_log.get("template_hashes")
        try:
            dreport = drift.detect_drift(agents_dir, templates_dir, build_log=build_log)
        except FileNotFoundError as exc:
            report.errors.append(f"{agents_dir}: {exc}")
            continue
        report.renders.append(
            FrameworkFreshness(
                framework=str(build_log.get("framework", "unknown")),
                agents_dir=agents_dir,
                generated_at=_opt_str(build_log.get("generated_at")),
                agentteams_version=_opt_str(build_log.get("agentteams_version")),
                changed_templates=(
                    []
                    if unverifiable
                    else [str(e.get("template", "?")) for e in dreport.changed_templates]
                ),
                missing_templates=[] if unverifiable else list(dreport.missing_templates),
                unverifiable=unverifiable,
            )
        )
    return report


def _opt_str(value: Any) -> str | None:
    """Return *value* as a string, or None if it is None/empty."""
    if value is None or value == "":
        return None
    return str(value)


def print_report(report: FrameworkFreshnessReport, *, project_root: Path) -> None:
    """Print a human-readable freshness report to stdout.

    Args:
        report:       The report from :func:`scan`.
        project_root: Project root, used to render render paths relative to it.
    """
    if not report.renders and not report.errors:
        print("No provider renders found (no references/build-log.json under the project).")
        return

    freshest = report.freshest
    print(f"Framework render freshness for {project_root}:")
    for r in sorted(report.renders, key=lambda x: str(x.agents_dir)):
        try:
            rel = r.agents_dir.relative_to(project_root)
        except ValueError:
            rel = r.agents_dir
        if r.unverifiable:
            status = "unverifiable (legacy build-log, no template hashes)"
        elif r.is_stale:
            detail = []
            if r.changed_templates:
                detail.append(f"{len(r.changed_templates)} template(s) changed")
            if r.missing_templates:
                detail.append(f"{len(r.missing_templates)} template(s) missing")
            status = "STALE  — " + ", ".join(detail)
        else:
            status = "current"
        stamp = f" (generated_at={r.generated_at})" if r.generated_at else " (no provenance stamp)"
        print(f"  [{status:<40}] {r.framework:<14} {rel}{stamp}")

    if report.has_stale and freshest is not None:
        print(
            f"\n  Freshest render: {freshest.framework} "
            f"(generated_at={freshest.generated_at}). Re-run "
            f"`agentteams --update --merge` for each STALE framework to bring it current."
        )
    for err in report.errors:
        print(f"  ! {err}")

    if report.has_stale:
        print(f"\n{len(report.stale)} render(s) behind the current templates.")
    else:
        print("\nAll renders are current with the templates.")
