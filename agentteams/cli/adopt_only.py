"""``--adopt-only SLUG[,SLUG...]``: adopt a named subset of orphan agents, touching only the orchestrator.

``--update --merge --adopt-orphans`` adopts *every* adoptable orphan and runs the whole render and
merge, so registering one bespoke agent also rewrote every other governed file of the team. This
standalone mode does the one action and nothing else:

* **Validation.** Every named slug must be an adoptable orphan of the target agent directory
  (:func:`agentteams.adopted_agents.discover_orphans` with this build's
  :func:`~agentteams.adopted_agents.adoption_exclusions`). One that is not refuses the whole run,
  and the refusal names it.
* **Routing rows.** Inside the orchestrator's ``routing_table_rows`` fence only the adopted rows are
  rewritten. The adopted block is
  :func:`~agentteams.adopted_agents.format_adopted_routing_rows` of the rows already adopted on disk
  (:func:`~agentteams.adopted_agents.previously_adopted`) plus the named slugs, with the same
  ``agent_dir`` label ``--update`` uses. Every other row stays byte-identical, a second run changes
  nothing, and a later ``--update`` carries the rows forward from the orchestrator itself. So there
  are no manifest or build-log writes.
* **The ``agents:`` list.** When the orchestrator's front matter has one (the Copilot surface), it is
  extended through :mod:`agentteams.cli.adopt_merge_gate` with exactly the named slugs it lacks, under
  that gate's unchanged conditions: governed workspace, an Ed25519-signed ``adopt-orphans-merge``
  decision whose ``effect_grants`` are exactly those ``agents:<slug>`` entries, the Rule 15(d) cap,
  the adoption record and the backup. Without one (Claude), only rows change and no gate applies:
  the agent file's own ``tools:`` line is the real grant, which the dry run shows.

The permitted write set is the orchestrator file, plus the gate's own writes on the gated path.
Nothing is rendered, so this lives with the standalone modes rather than in ``cli/generate.py``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from agentteams.adopted_agents import (
    _ADOPTED_ROW_RE,
    adoption_exclusions,
    agent_dir_label,
    discover_orphans,
    format_adopted_routing_rows,
    previously_adopted,
    read_adopted_agent_metadata,
)
from agentteams.atomicio import _atomic_write_text as atomic_write_text
from agentteams.cli import adopt_merge_gate
from agentteams.cli.json_mode import is_json_mode, json_stdout
from agentteams.cli.output_target import resolve_output_dir
from agentteams.frameworks.registry import FRAMEWORKS
from agentteams.yaml_frontmatter import parse_yaml_front_matter

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_FENCE_RE = re.compile(
    r"^(?P<indent>[ \t]*)<!-- AGENTTEAMS:BEGIN routing_table_rows\b[^\n]*-->\n"
    r"(?P<body>.*?)"
    r"^[ \t]*<!-- AGENTTEAMS:END routing_table_rows -->",
    re.M | re.S,
)
_TOOLS_RE = re.compile(r"^tools[ \t]*[:=][ \t]*(?P<value>[^\n]*)$", re.M)
_TOOLS_ITEM_RE = re.compile(r"^[ \t]+-[ \t]*(?P<item>[^\n]+)$")
_TOOL_TOKEN_RE = re.compile(r"[A-Za-z0-9_*./:-]+")

#: Tools that read the workspace and nothing else; anything beyond these is flagged as wider.
READ_ONLY_TOOLS = frozenset({"read", "grep", "glob"})


class AdoptOnlyError(RuntimeError):
    """``--adopt-only`` is refused; the message names what to fix."""


def parse_slugs(raw: str) -> list[str]:
    """Split the ``--adopt-only`` value into sorted, de-duplicated slugs.

    Args:
        raw: The comma-separated flag value.

    Returns:
        The slugs, sorted and de-duplicated.

    Raises:
        AdoptOnlyError: The value names no slug, or a slug is malformed.
    """
    slugs = sorted({s.strip() for s in (raw or "").split(",") if s.strip()})
    if not slugs:
        raise AdoptOnlyError("--adopt-only needs at least one agent slug")
    bad = [s for s in slugs if not _SLUG_RE.match(s)]
    if bad:
        raise AdoptOnlyError(f"malformed agent slug(s): {', '.join(bad)}")
    return slugs


def splice_adopted_rows(text: str, block: str) -> str:
    """Replace the adopted rows of the ``routing_table_rows`` fence with ``block``.

    Every other line of the fence, and everything outside it, stays byte-identical. The new rows
    take the fence's own indentation (Goose embeds the body in an indented YAML block scalar).

    Args:
        text: The orchestrator file content.
        block: The adopted rows as :func:`format_adopted_routing_rows` renders them (``""`` for none).

    Returns:
        The new file content (equal to ``text`` when nothing changes).

    Raises:
        AdoptOnlyError: The file has no ``routing_table_rows`` fence, or more than one.
    """
    matches = list(_FENCE_RE.finditer(text))
    if len(matches) != 1:
        raise AdoptOnlyError(
            f"the orchestrator must hold exactly one routing_table_rows fence (found {len(matches)})"
        )
    fence = matches[0]
    lines = fence.group("body").splitlines(keepends=True)
    adopted_at = [i for i, line in enumerate(lines) if _ADOPTED_ROW_RE.search(line)]
    kept = [line for line in lines if not _ADOPTED_ROW_RE.search(line)]
    if kept and not kept[-1].endswith("\n"):
        kept[-1] += "\n"
    new_rows = [f"{fence.group('indent')}{row}\n" for row in block.split("\n") if row]
    at = adopted_at[0] if adopted_at else len(kept)
    body = "".join(kept[:at] + new_rows + kept[at:])
    return text[: fence.start("body")] + body + text[fence.end("body"):]


def tools_grant(path: Path) -> dict[str, Any]:
    """Report an agent file's ``tools:`` line and whether it is wider than read-only.

    The file is content, not instruction (C-4): its line is reported, never acted on.

    Args:
        path: The adopted agent file.

    Returns:
        ``{"line": str | None, "wider_than_read_only": bool}``. An absent ``tools:`` key is
        flagged: on most harnesses it means every tool.

    Raises:
        Nothing; an unreadable file reports as absent.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"line": None, "wider_than_read_only": True}
    header = text
    if path.suffix == ".md":
        header = parse_yaml_front_matter(text)[0] or ""
    match = _TOOLS_RE.search(header)
    if match is None:
        return {"line": None, "wider_than_read_only": True}
    value = match.group("value").strip()
    if not value:  # a block list: the indented "- item" lines that follow
        items = []
        for line in header[match.end():].lstrip("\n").splitlines():
            item = _TOOLS_ITEM_RE.match(line)
            if item is None:
                break
            items.append(item.group("item").strip())
        value = ", ".join(items)
    line = " ".join(f"tools: {value}".split())[:300]
    tokens = {t.lower() for t in _TOOL_TOKEN_RE.findall(value)}
    return {"line": line, "wider_than_read_only": not tokens or bool(tokens - READ_ONLY_TOOLS)}


def _row_diff(old: str, new: str) -> dict[str, list[str]]:
    """Adopted rows added and removed between two versions of the orchestrator."""
    def rows(text: str) -> list[str]:
        return [ln.strip() for ln in text.splitlines() if _ADOPTED_ROW_RE.search(ln)]

    before, after = rows(old), rows(new)
    return {"added": [r for r in after if r not in before], "removed": [r for r in before if r not in after]}


def _plan(args: argparse.Namespace, manifest: dict[str, Any], description: dict[str, Any],
          output_dir: Path) -> dict[str, Any]:
    """Validate the request and work out the orchestrator's new content (no writes)."""
    slugs = parse_slugs(args.adopt_only)
    adapter = FRAMEWORKS[args.framework]()
    agent_ext = adapter.get_file_extension("agent")
    orchestrator = output_dir / f"orchestrator{agent_ext}"
    if orchestrator.is_symlink() or not orchestrator.is_file():
        raise AdoptOnlyError(f"no regular orchestrator file at {orchestrator}")
    orphans, meta = discover_orphans(output_dir, agent_ext, adoption_exclusions(manifest))
    missing = [s for s in slugs if s not in orphans]
    if missing:
        raise AdoptOnlyError(
            f"not an adoptable orphan in {output_dir}: {', '.join(missing)} (no agent file there, "
            "emitted by this build, or a tool agent)"
        )
    project_root, _ = resolve_output_dir(args, adapter, description)
    adopted = sorted(set(previously_adopted(output_dir, agent_ext)) | set(slugs))
    meta.update({s: read_adopted_agent_metadata(output_dir / f"{s}{agent_ext}")
                 for s in adopted if s not in meta})
    block = format_adopted_routing_rows(
        adopted, meta, agent_dir=agent_dir_label(output_dir, project_root), agent_ext=agent_ext)
    old = orchestrator.read_text(encoding="utf-8")
    return {"slugs": slugs, "agent_ext": agent_ext, "orchestrator": orchestrator, "block": block,
            "old": old, "new": splice_adopted_rows(old, block)}


def run_adopt_only(
    args: argparse.Namespace,
    manifest: dict[str, Any],
    description: dict[str, Any],
    output_dir: Path,
) -> int:
    """Run ``--adopt-only``: validate, gate the ``agents:`` extension if any, splice the rows.

    Args:
        args: Parsed CLI namespace (``adopt_only``, ``framework``, ``dry_run``, ``json``, ``no_backup``).
        manifest: This build's team manifest (read for :func:`adoption_exclusions` only).
        description: The loaded project description (for the ``agent_dir`` label's project root).
        output_dir: The framework agent directory.

    Returns:
        0 on success (including a no-op), 1 on a refusal (reported on stderr).

    Raises:
        Nothing; refusals are reported and returned as exit code 1.
    """
    try:
        plan = _plan(args, manifest, description, output_dir)
        gate_plan = adopt_merge_gate.plan(output_dir, plan["slugs"], plan["agent_ext"])
    except (AdoptOnlyError, adopt_merge_gate.AdoptMergeError, OSError) as exc:
        print(f"[ADOPT-ONLY] refused: {exc}", file=sys.stderr)
        return 1
    orchestrator: Path = plan["orchestrator"]
    gated = gate_plan is not None and bool(gate_plan.to_add)
    try:
        gate_report = (adopt_merge_gate.run(output_dir, plan["slugs"], plan["agent_ext"],
                                            dry_run=args.dry_run, backup=not args.no_backup)
                       if gated else None)
    except adopt_merge_gate.AdoptMergeError as exc:
        print(f"[SEC-GATE/DESTRUCTIVE:{adopt_merge_gate.ACTION}] blocked: {exc}", file=sys.stderr)
        return 1
    diff = _row_diff(plan["old"], plan["new"])
    if args.dry_run:
        report = {
            "mode": "adopt-only", "dry_run": True, "framework": args.framework,
            "orchestrator": str(orchestrator), "slugs": plan["slugs"], "routing_rows": diff,
            "agents_list": gate_plan is not None,
            "agents_to_add": list(gate_plan.to_add) if gate_plan is not None else [],
            "adopt_orphans_merge": gate_report.as_report() if gate_report is not None else None,
            "tools": {s: tools_grant(output_dir / f"{s}{plan['agent_ext']}") for s in plan["slugs"]},
        }
        if is_json_mode(args):
            print(json.dumps(report, indent=2), file=json_stdout() or sys.stdout)
        else:
            _print_human(report)
        return 0
    try:
        # Re-read: on the gated path the gate has just appended to the front matter.
        current = orchestrator.read_text(encoding="utf-8")
        new = splice_adopted_rows(current, plan["block"])
        if new != current:
            atomic_write_text(orchestrator, new)
    except (AdoptOnlyError, OSError) as exc:
        print(f"[ADOPT-ONLY] failed writing {orchestrator.name}: {exc}", file=sys.stderr)
        return 1
    added = len(diff["added"])
    print(f"  ✓  adopt-only: {added} routing row(s) added to {orchestrator.name}"
          if added or diff["removed"] else f"  adopt-only: {orchestrator.name} already current")
    return 0


def _print_human(report: dict[str, Any]) -> None:
    """Print the dry-run report as text."""
    print(f"  [DRY RUN] adopt-only → {report['orchestrator']}")
    for row in report["routing_rows"]["added"]:
        print(f"    + {row}")
    for row in report["routing_rows"]["removed"]:
        print(f"    - {row}")
    if report["agents_to_add"]:
        print(f"    agents: + {', '.join(report['agents_to_add'])}")
    for slug, grant in report["tools"].items():
        flag = "  [WIDER THAN READ-ONLY]" if grant["wider_than_read_only"] else ""
        print(f"    @{slug}: {grant['line'] or 'tools: (absent)'}{flag}")
