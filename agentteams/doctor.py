"""doctor.py — ``--doctor``: one report on whether a project's providers agree (baseAgent P3–P11).

``--check`` asks whether one team matches its templates. ``--doctor`` asks the cross-surface question: do all of
the project's providers see the same rules, the same memory, and the same agents? It reads only, and reports:

* **Rules projection** (FAIL): a copy of the Project-Specific Rules or a notes pointer that is out of date, or was
  refused (hand-edited, a duplicate region) — :mod:`agentteams.rules_projection`.
* **Memory index** (WARN): surfaces whose copies were built from different sources (``source_fingerprint``) or that
  are stale — :mod:`agentteams.memory_index_surfaces`.
* **Copilot body limit** (FAIL): operator notes past 30,000 characters — :mod:`agentteams.copilot_body_limit`.
* **Agents** (INFO): agents missing on some surface, notes that differ by surface, skills on some surfaces only —
  :mod:`agentteams.surface_matrix`.
* **Shared AGENTS.md** (WARN): an ``AGENTS.md`` whose constitutional core is older than the other entry files' (it
  was frozen with Codex while the others moved on, P11).

Exit code 1 when any FAIL is reported.

Stdlib only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_CORE_RE = re.compile(r"<!-- AGENTTEAMS:BEGIN constitutional_core v=(\d+) -->")
_ENTRY_FILES = (".github/copilot-instructions.md", "CLAUDE.md", ".claude/CLAUDE.md", "AGENTS.override.md")


def _brief(root: Path) -> dict:
    from agentteams.memory_index_surfaces import resolve_brief

    found = resolve_brief(root)
    if found is None:
        return {}
    try:
        data = json.loads(found.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _rules(root: Path, brief: dict) -> tuple[list[str], int]:
    from agentteams import rules_projection

    configured = brief.get("project_rules_source") or None
    frozen = brief.get("frozen_surfaces") or None
    report = rules_projection.project(root, configured=configured, frozen=frozen, write=False)
    if report.source is None:
        return ["  ℹ  No instruction file holds Project-Specific Rules; nothing to project."], 0
    pointers = rules_projection.sync_pointers(root, configured=configured, frozen=frozen, write=False)
    lines = [f"  source: {report.source}"]
    fails = 0
    for o in report.outcomes + pointers:
        if o.status in ("updated", "added", "refused"):
            fails += 1
            state = {"updated": "out of date", "added": "missing"}.get(o.status, "refused")
            lines.append(f"  ✗  FAIL {o.path}: {state}" + (f" ({o.detail})" if o.detail else ""))
        elif o.status == "skipped":
            lines.append(f"  ℹ  {o.path}: skipped ({o.detail})")
        else:
            lines.append(f"  ✓  {o.path}: current")
    if fails:
        lines.append("     Run `agentteams --sync-project-rules` (a refused copy needs its hand edit moved to the source).")
    return lines, fails


def _indexes(root: Path, brief: dict) -> list[str]:
    from agentteams.memory_index_surfaces import index_surfaces

    surfaces = index_surfaces(root, brief.get("frozen_surfaces") or None)  # frozen copies are never rebuilt
    if not surfaces:
        return ["  ℹ  No memory index on any surface."]
    seen: dict[str, list[str]] = {}
    lines: list[str] = []
    for agents_dir in surfaces:
        rel = agents_dir.relative_to(root).as_posix()
        try:
            head = json.loads((agents_dir / "references" / "memory-index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            lines.append(f"  !  WARN {rel}: index unreadable ({exc})")
            continue
        fingerprint = str(head.get("source_fingerprint", "?"))
        seen.setdefault(fingerprint, []).append(rel)
        lines.append(f"  ·  {rel}: built {head.get('built_at', '?')} from {head.get('source_count', '?')} source(s)")
    if len(seen) > 1:
        lines.append(f"  !  WARN the {len(surfaces)} copies were built from {len(seen)} different source sets; run "
                     "`agentteams --refresh-index` to rebuild them together.")
    return lines


def _body_limit(root: Path) -> tuple[list[str], int]:
    from agentteams.copilot_body_limit import body_limit_findings

    folder = root / ".github" / "agents"
    if not folder.is_dir():
        return [], 0
    failures, advisories = body_limit_findings(folder)
    return ([f"  ✗  FAIL {f}" for f in failures] + [f"  ℹ  {a}" for a in advisories]), len(failures)


def _agents(root: Path) -> list[str]:
    from agentteams.surface_matrix import MATRIX_REL, build_matrix

    matrix = build_matrix(root)
    if not matrix.surfaces:
        return ["  ℹ  No agent files found."]
    gaps = matrix.gaps()
    lines = [f"  ·  {len(matrix.agents)} agents on {len(matrix.surfaces)} surfaces ({', '.join(matrix.surfaces)}); "
             f"{len(gaps)} missing on at least one surface (see {MATRIX_REL})."]
    lines += [f"  ℹ  {slug}: missing on {', '.join(missing)}" for slug, missing in gaps.items()]
    differ = matrix.notes_differ()
    if differ:
        lines.append(f"  ℹ  Notes differ by surface for: {', '.join(differ)}")
    return lines


def _shared_agents_md(root: Path) -> list[str]:
    agents_md = root / "AGENTS.md"
    if not agents_md.is_file():
        return []
    m = _CORE_RE.search(agents_md.read_text(encoding="utf-8", errors="replace"))
    others = [int(x.group(1)) for rel in _ENTRY_FILES if (root / rel).is_file()
              for x in [_CORE_RE.search((root / rel).read_text(encoding="utf-8", errors="replace"))] if x]
    if m and others and int(m.group(1)) < max(others):
        return [f"  !  WARN AGENTS.md carries constitutional_core v={m.group(1)}, the other entry files v={max(others)}. "
                "It was likely frozen (a Codex freeze holds the shared AGENTS.md) while they moved on; Goose and "
                "other AGENTS.md readers see the older core until it is updated."]
    return []


def _backup_stubs(root: Path) -> tuple[list[str], int]:
    """FAIL per backed-up agent file a runtime would still load (IM item 01): it shadows a live agent."""
    from agentteams.backup import runtime_loadable_backups

    stubs = runtime_loadable_backups(root)
    lines = [f"  ✗  FAIL {p.relative_to(root)} loads as a live agent; run any --update/--bridge-merge (it is "
             "renamed inert, never deleted) or rename it to *.agentteams-bak" for p in stubs]
    return lines, len(stubs)


def run_doctor(project: str | None) -> int:
    """``--doctor``: print the cross-surface report for the project at *project* (else the working directory).

    Args:
        project: The ``--project`` value.

    Returns:
        1 when any FAIL is reported, else 0.

    Raises:
        Nothing. A section that can't be read prints the error and counts as a FAIL.
    """
    root = Path(project or ".").resolve()
    brief = _brief(root)
    fails = 0
    print(f"agentteams doctor — {root}")
    sections = (("Rules projection", lambda: _rules(root, brief)), ("Memory index", lambda: (_indexes(root, brief), 0)),
                ("Copilot body limit", lambda: _body_limit(root)), ("Agents", lambda: (_agents(root), 0)),
                ("Shared AGENTS.md", lambda: (_shared_agents_md(root), 0)), ("Backup stubs", lambda: _backup_stubs(root)))
    for title, section in sections:
        try:
            lines, failed = section()
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            lines, failed = [f"  ✗  FAIL could not check: {exc}"], 1
        if lines:
            print(f"\n{title}")
            print("\n".join(lines))
        fails += failed
    print(f"\n{'✗' if fails else '✓'}  {fails} failure(s).")
    return 1 if fails else 0


__all__ = ["run_doctor"]
