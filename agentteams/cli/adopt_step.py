"""Generate-pipeline glue for adopted agents (Step 4·adopt and the post-emit merge-mode apply).

Carved out of ``cli/generate.py`` (CH-07: that module sits near the 1000-line ceiling). Three modes:

* ``--overwrite``/``--migrate --adopt-orphans`` — full adoption: roster plus routing rows, cleared
  by the ordinary ``overwrite`` gate later in the pipeline.
* ``--update --adopt-orphans`` (merge) — routing rows for every adoptable agent, and a gated,
  append-only extension of the orchestrator's ``agents:`` list (:mod:`agentteams.cli.adopt_merge_gate`),
  cleared and applied here, before render/emit (the update path can return before emit).
* plain ``--update`` — carry forward rows an earlier adopt run rendered. Rows only: body text read
  back from disk is not a clearance, so it never reaches the roster.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from agentteams import analyze, emit
from agentteams.adopted_agents import (
    _ADOPTED_ROW_RE,
    adoption_exclusions,
    agent_dir_label,
    discover_orphans,
    previously_adopted,
    read_adopted_agent_metadata,
    set_adopted_rows,
)
from agentteams.cli import adopt_merge_gate


def run_adopt_step(
    args: argparse.Namespace,
    manifest: dict[str, Any],
    output_dir: Path,
    project_root: Path,
    agent_ext: str,
) -> tuple[adopt_merge_gate.AdoptMergePlan | None, int | None]:
    """Run Step 4·adopt: render routing rows and, in merge mode, clear the ``agents:`` extension.

    Args:
        args: Parsed CLI namespace.
        manifest: The team manifest (mutated: roster and/or routing-row placeholder).
        output_dir: The framework agent directory.
        project_root: The target project's root.
        agent_ext: The framework's agent file extension.

    Returns:
        ``(merge_plan, exit_code)``. ``exit_code`` is 1 when the merge-mode gate refuses, else
        ``None``; ``merge_plan`` is set only for a merge-mode run with slugs to add (already
        applied unless ``--dry-run``).

    Raises:
        Nothing; a refusal is reported on stderr and returned as exit code 1.
    """
    if not output_dir.exists():
        return None, None
    adopt = getattr(args, "adopt_orphans", False)
    agent_dir = agent_dir_label(output_dir, project_root)
    if not adopt:
        if getattr(args, "update", False):
            prev = previously_adopted(output_dir, agent_ext)
            meta = {s: read_adopted_agent_metadata(output_dir / f"{s}{agent_ext}") for s in prev}
            set_adopted_rows(manifest, prev, meta, agent_dir=agent_dir, agent_ext=agent_ext)
        elif args.overwrite:
            return None, _refuse_dropped_rows(manifest, output_dir, agent_ext, dry_run=args.dry_run)
        return None, None
    # Discovery reads only name/description, from files already in output_dir (C-4).
    slugs, meta = discover_orphans(output_dir, agent_ext, adoption_exclusions(manifest))
    if args.overwrite or args.migrate:
        adopted = analyze.adopt_orphan_agents(manifest, slugs, meta, agent_dir=agent_dir, agent_ext=agent_ext)
        print(f"  Adopted {len(adopted)} orphan agent(s) into roster: {', '.join(adopted)}"
              if adopted else "  --adopt-orphans: no orphan agent files to adopt.")
        return None, (_refuse_dropped_rows(manifest, output_dir, agent_ext, dry_run=args.dry_run)
                      if args.overwrite else None)
    set_adopted_rows(manifest, slugs, meta, agent_dir=agent_dir, agent_ext=agent_ext)
    try:
        return adopt_merge_gate.run(
            output_dir, slugs, agent_ext, dry_run=args.dry_run, backup=not getattr(args, "no_backup", False)
        ), None
    except adopt_merge_gate.AdoptMergeError as exc:
        print(f"[SEC-GATE/DESTRUCTIVE:{adopt_merge_gate.ACTION}] blocked: {exc}", file=sys.stderr)
        return None, 1



def _refuse_dropped_rows(manifest: dict, output_dir: Path, agent_ext: str, *, dry_run: bool = False) -> int | None:
    """Refuse an ``--overwrite`` that would drop an adopted agent's routing row (CA-033).

    The on-disk orchestrator's ``*(adopted)*`` rows whose agent file still exists must all be in the fresh
    render, unless the agent is now one of the brief's own agents. A plain ``--overwrite`` (no ``--update``)
    used to rebuild the routing fence without them, silently, in every orchestrator.

    Returns:
        1 (refused, reported on stderr) when a row would be dropped, else ``None``.
    """
    rendered = set(_ADOPTED_ROW_RE.findall(
        (manifest.get("auto_resolved_placeholders") or {}).get("ADOPTED_AGENT_ROUTING_ROWS", "")))
    own = set(manifest.get("agent_slug_list") or []) - set(manifest.get("adopted_agents") or [])
    # Slugs adoption never takes (emitted files, tool agents) can't be re-adopted, so their rows may drop;
    # otherwise every --overwrite would refuse with no way through but deleting the file.
    own |= set(adoption_exclusions(manifest))
    dropped = [s for s in previously_adopted(output_dir, agent_ext) if s not in rendered and s not in own]
    if not dropped:
        return None
    message = (f"--overwrite would drop the adopted routing row(s) of {', '.join(dropped)} from the "
               "orchestrator (their agent files still exist). Re-run with --adopt-orphans so they are "
               "re-adopted, or use --update --merge, which carries them. To drop a row on purpose, remove "
               "the agent's file (or --prune it), or delete the row from the orchestrator by hand.")
    if dry_run:
        print(f"  [ADOPTED-ROWS] dry run: {message}", file=sys.stderr)
        return None
    print(f"[ADOPTED-ROWS] blocked: {message}", file=sys.stderr)
    return 1


def attach_dry_run_plan(merge_plan: adopt_merge_gate.AdoptMergePlan | None, result: emit.EmitResult) -> None:
    """Put a dry-run merge-mode plan into the ``--dry-run --json`` report.

    Args:
        merge_plan: The plan from :func:`run_adopt_step`, or ``None``.
        result: The emit result.

    Returns:
        None.

    Raises:
        Nothing.
    """
    if merge_plan is not None and result.dry_run_report is not None:
        result.dry_run_report.adopt_orphans_merge = merge_plan.as_report()
