"""
_interop_grok.py — Grok Build specifics of the CAI interop pipeline (Phase 3, Codex posture).

Carved out of ``interop.py`` (CH-07 size ceiling). Plan tmp/by-week/2026-W42/grok-integration.plan.md §13–§13.1:

* export: a Grok source agent's capabilities are its declared list only when it is exact-toolset; otherwise FULL,
  with a widening notice (@security cond. 3 / @adversarial #4); only allow-listed front-matter keys travel;
* import: a CAI's instructions reach the shared repo-root AGENTS.md strictly through the Grok co-ownership guard,
  writing exactly what the guard returns (@security / @adversarial cond. 1).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # annotations only: interop imports this module
    from agentteams.frameworks.base import FrameworkAdapter
    from agentteams.interop import InteropResult

from agentteams import capability_map as _capability_map
from agentteams.frameworks._agents_md_ownership import ABSENT, GROK, classify_agents_md, symlink_on_path
from agentteams.interop_helpers import merge_instruction_file

#: Front-matter keys a grok source may carry into CAI ``raw_front_matter`` (allow-list; everything else is Grok's).
GROK_CARRIED_FM_KEYS = frozenset({"disallowedTools"})


def grok_capabilities(content: str, slug: str) -> dict[str, Any]:
    """Return a Grok Build source agent's CAI capabilities.

    Grok widens a declared ``tools:`` list unless ``injectDefaultTools: false`` and ``mcpInheritance: none`` are
    both set, and an empty list inherits everything. Such an agent exports as FULL capabilities (honest about what
    Grok granted), with a notice; the declared list stays in ``capabilities.raw`` for a human to narrow.

    Args:
        content: The agent file text.
        slug: The agent slug (for the notice).

    Returns:
        The capabilities mapping (``capability_map.capabilities_from_tokens`` shape).

    Raises:
        Nothing.
    """
    declared = _capability_map.canonical_tools_for_claude(content)
    if declared and _capability_map.grok_exact_toolset(content):
        return _capability_map.capabilities_from_tokens(declared, "grok")
    print(f"  ⚠  {slug}: Grok source agent is not exact-toolset (injectDefaultTools/mcpInheritance unset, an empty or "
          "missing tools: list, or a list Grok widens: a scoped entry, MCPTool, or a tool both allowed and denied), "
          "so Grok granted it more than its declared tools; exported as FULL capabilities. An "
          "exact-toolset target (claude, codex) will enforce that wider grant: narrow it from capabilities.raw "
          "before importing.", file=sys.stderr)
    return _capability_map.capabilities_from_tokens(list(_capability_map.CANONICAL_TOOL_SCOPES), "grok")


def import_grok_instructions(
    adapter: FrameworkAdapter, content: str, inst_dest: Path, target_dir: Path, manifest: dict[str, Any],
    result: InteropResult,
    *, dry_run: bool, overwrite: bool, preserve_existing: bool,
) -> None:
    """Write a CAI's instructions into a Grok target strictly through the AGENTS.md co-ownership guard.

    The guard sees the REAL rendered text and returns what may be written: the full Grok render (absent or
    Grok-owned file), the existing file plus only the ``grok_runtime`` fence (Codex / agents-md / fenced owner), the
    paste-in example (hand-written file) or nothing (unsafe). Exactly those pairs are written; a merge applies only
    to a Grok-owned file, and an existing file is changed only with ``--overwrite`` or ``--merge``.

    Args:
        adapter: The Grok adapter.
        content: The CAI instructions (ownership notices and the Grok fence already stripped).
        inst_dest: The repo-root ``AGENTS.md`` path.
        target_dir: The Grok agents dir.
        manifest: The import manifest.
        result: The ``InteropResult`` to record on.
        dry_run: Record without writing.
        overwrite: Allow changing an existing file the guard permits.
        preserve_existing: Fence-merge into a Grok-owned file (and allow the guard's block-only rewrite).

    Raises:
        OSError: A permitted file cannot be read or written.
    """
    root = Path(os.path.normpath(target_dir)).parent.parent
    inst_dest = Path(os.path.normpath(inst_dest))
    owner = classify_agents_md(inst_dest, repo_root=root)
    rendered = adapter.render_instructions_file(content, manifest)
    kept, notices = adapter.guard_rendered_files([(os.path.relpath(inst_dest, target_dir), rendered)], target_dir)
    result.notices.extend(notices)
    if owner not in (ABSENT, GROK):
        result.notices.append(f"{inst_dest}: the CAI team brief was not imported (AGENTS.md is owned by {owner}).")
    for rel, text in kept:
        dest = Path(os.path.normpath(target_dir / rel))
        if dest.exists():
            if owner == GROK and dest == inst_dest and preserve_existing:
                merged, notice = merge_instruction_file(text, dest.read_text(encoding="utf-8"))
                if notice:
                    result.notices.append(f"{dest}: {notice}")
                if merged is None:
                    result.skipped.append(str(dest))
                    continue
                text = merged
            elif not (overwrite or preserve_existing):
                result.skipped.append(str(dest))
                continue
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
        result.converted.append(str(dest))


def grok_carried_front_matter(raw_front_matter: dict[str, Any]) -> dict[str, Any]:
    """Return only the allow-listed front-matter keys of a grok source agent (@security Phase 3 cond. 3).

    Args:
        raw_front_matter: The captured front matter.

    Returns:
        The keys in :data:`GROK_CARRIED_FM_KEYS`; Grok-only keys (``permissionMode``, ``hooks``, ``mcpServers``,
        ``injectDefaultTools``, ``mcpInheritance``, …) never travel to another target.

    Raises:
        Nothing.
    """
    return {k: v for k, v in raw_front_matter.items() if k in GROK_CARRIED_FM_KEYS}


def grok_symlink_refusal(adapter: FrameworkAdapter, target_dir: Path, result: InteropResult,
                         cai: dict[str, Any] | None = None) -> bool:
    """Refuse an import into a Grok team when ANY destination it would write is (under) a symlink.

    Checked up front, before anything is written (@security Phase 3 step 13d): ``.grok``, the agents dir, the skills
    dir, every agent file the CAI names, the handoff sidecar (``.grok/references/runtime-handoffs.json``), the
    team's ``references/`` (projection marker, control-plane stubs) and the bundle output under it.

    Args:
        adapter: The Grok adapter.
        target_dir: The Grok agents dir.
        result: The ``InteropResult`` (a notice is added when refusing).
        cai: The CAI being imported (its agent slugs name the agent files).

    Returns:
        True when the import must stop (nothing is written).

    Raises:
        Nothing.
    """
    agents = Path(os.path.normpath(target_dir))
    root = agents.parent.parent
    ext = adapter.get_file_extension("agent")
    slugs = [str(a.get("slug", "")).strip() for a in ((cai or {}).get("agents") or []) if isinstance(a, dict)]
    candidates = [
        agents, Path(os.path.normpath(adapter.skills_dir(target_dir))),
        agents.parent / "references" / "runtime-handoffs.json", agents / "references" / "interop",
        *(agents / f"{slug}{ext}" for slug in slugs if slug and "/" not in slug),
    ]
    for path in candidates:
        linked = symlink_on_path(path, root)
        if linked is not None:
            result.notices.append(f"{linked} is a symlink; nothing was imported into the Grok team.")
            return True
    return False


def grok_import_parity(pending: list[tuple[Path, str]], target_dir: Path) -> list[str]:
    """Return the D4 name-parity notices for every agent file an import wrote into a Grok team.

    Args:
        pending: ``(destination, rendered)`` pairs the import wrote.
        target_dir: The Grok agents dir.

    Returns:
        Notices for Claude-twin name mismatches, unshadowed Claude agents, built-in shadowing and a gitignored
        AGENTS.md (empty when nothing was written).

    Raises:
        Nothing.
    """
    if not pending:
        return []
    from agentteams.frameworks.grok import parity_notices

    agents = Path(os.path.normpath(target_dir))
    return parity_notices([(os.path.relpath(d, target_dir), r) for d, r in pending], agents, agents.parent.parent)

