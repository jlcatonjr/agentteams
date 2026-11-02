"""post_build.py — the artifact writes every successful generate and ``--update`` run ends with.

The generate and update paths of :mod:`agentteams.cli.generate` each carried their own copy of the eval-suite and
memory-index writes; one copy here keeps the two paths alike and ``generate.py`` clear of the CH-07 ceiling. It also
projects the project's rules into every provider's entry file and the key agents' notes after each run
(:mod:`agentteams.rules_projection`; the operator chose projection on every ``--update``, 2026-11-02).
"""

from __future__ import annotations

import sys
from pathlib import Path

from agentteams.cli.artifacts import EvalSuiteError, MemoryIndexError, _write_eval_suite, _write_memory_index


def write_suite_and_index(manifest: dict, output_dir: Path, *, after: str) -> None:
    """Write the eval suite and the memory index; a failure of either is reported, never fatal.

    Args:
        manifest: The team manifest.
        output_dir: The agents directory.
        after: What already happened, for the failure messages ("team generated", "build-log healed").

    Returns:
        None.

    Raises:
        Nothing.
    """
    try:
        _write_eval_suite(manifest, output_dir)
    except (OSError, EvalSuiteError) as exc:
        print(f"  !  Eval suite write failed ({after}): {exc}", file=sys.stderr)
    try:
        _write_memory_index(manifest, output_dir)
    except (OSError, MemoryIndexError) as exc:
        print(f"  !  Memory index write failed: {exc}", file=sys.stderr)


def project_root_of(manifest: dict, output_dir: Path) -> Path | None:
    """The project root a team's agents directory belongs to: ``existing_project_path``, else the standard layout.

    Args:
        manifest: The team manifest.
        output_dir: The agents directory.

    Returns:
        The root, or ``None`` for a non-standard layout.

    Raises:
        Nothing.
    """
    from agentteams.prompt_roots import team_project_root

    if manifest.get("existing_project_path"):
        return Path(manifest["existing_project_path"]).resolve()
    found = team_project_root(output_dir)
    return found[0] if found else None


def project_rules(manifest: dict, output_dir: Path, *, write: bool = True) -> bool:
    """Project the rules into every provider's entry file and the key agents' notes, and print what happened.

    Args:
        manifest: The team manifest (``project_rules_source``, ``frozen_surfaces``).
        output_dir: The agents directory.
        write: Write; ``False`` only reports drift (``--check``).

    Returns:
        True when a copy or pointer is out of date or was refused (``--check`` then exits 1).

    Raises:
        Nothing.
    """
    from agentteams import rules_projection

    root = project_root_of(manifest, output_dir)
    if root is None:
        return False
    if write:  # the agent-surface matrix (baseAgent P3), reported, never enforced
        from agentteams.surface_matrix import write_matrix

        try:
            matrix_path, written = write_matrix(root)
            if written:
                print(f"  ✓  Agent-surface matrix: {matrix_path}")
        except OSError as exc:
            print(f"  !  Agent-surface matrix not written: {exc}", file=sys.stderr)
    configured = manifest.get("project_rules_source") or None
    frozen = manifest.get("frozen_surfaces") or None
    try:
        report = rules_projection.project(root, configured=configured, frozen=frozen, write=write)
        pointers = rules_projection.sync_pointers(root, configured=configured, frozen=frozen, write=write)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        print(f"  ✗  Project rules not projected: {exc}", file=sys.stderr)
        return True
    if report.source is None:
        if configured:
            print(f"  !  project_rules_source {configured} has no Project-Specific Rules to project.", file=sys.stderr)
        return bool(configured)
    stale = False
    diff = next((o.diff for o in report.outcomes if o.diff), "")
    if diff and write:  # what is about to bind every provider is shown, never applied silently
        print(f"  ↻  Project rules changed in {report.source}; every provider's copy now says:\n{diff}")
    for outcome in report.outcomes + pointers:
        if outcome.status == "current" or (write and outcome.status == "skipped"):
            continue
        verb = outcome.status
        if not write and outcome.status in ("updated", "added"):
            verb = "out of date" if outcome.status == "updated" else "missing"
        failing = outcome.status == "refused" or (not write and outcome.status in ("updated", "added"))
        detail = f" ({outcome.detail})" if outcome.detail else ""
        print(f"  {'✗' if failing else '↻'}  Project rules from {report.source}: {outcome.path} {verb}{detail}",
              file=sys.stderr if failing else sys.stdout)
        stale = stale or failing
    return stale


def run_sync_project_rules(project: str | None, *, dry_run: bool = False) -> int:
    """``--sync-project-rules``: project the rules for the project at *project* (else the working directory).

    The brief (found as for the retrieval commands) supplies ``project_rules_source`` and ``frozen_surfaces``. It
    writes only into the project it is run from: a ``--project`` other than the working directory is refused, since
    a write into another repository needs that repository's own run (C-5).

    Args:
        project: The ``--project`` value.
        dry_run: Report what would change; write nothing.

    Returns:
        0; 1 when a copy or pointer was refused (or, with *dry_run*, is out of date), when the source is refused or
        has no rules though the brief names it, when the brief can't be read, or when *project* is another directory.

    Raises:
        Nothing.
    """
    import json

    from agentteams.memory_index_surfaces import resolve_brief

    root = Path(project or ".").resolve()
    if root != Path.cwd().resolve():
        print(f"  ✗  --sync-project-rules writes only the project it runs in; run it from {root}.", file=sys.stderr)
        return 1
    brief: dict = {}
    found = resolve_brief(root)
    if found is not None:
        try:
            loaded = json.loads(found.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"  ✗  Brief {found} unreadable ({exc}).", file=sys.stderr)
            return 1
        brief = loaded if isinstance(loaded, dict) else {}
    manifest = {"existing_project_path": str(root), "project_rules_source": brief.get("project_rules_source") or "",
                "frozen_surfaces": brief.get("frozen_surfaces") or []}
    return 1 if project_rules(manifest, root, write=not dry_run) else 0


__all__ = ["project_root_of", "project_rules", "run_sync_project_rules", "write_suite_and_index"]
