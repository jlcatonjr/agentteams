"""bridge_refresh.py — ``--bridge-refresh`` for a target file whose render is only bridge fences (baseAgent P12).

``--bridge-refresh`` writes each target entry file whole. ``.goosehints`` is rendered as nothing but the
``goose-bridge-hints`` fence, so a whole write erased any project text an instance added around it (only the
backup kept it). Carved out of :mod:`agentteams.bridge` for the CH-07 length ceiling.

Stdlib only.
"""

from __future__ import annotations


#: Target files whose bridge render is only fences and which a project commonly extends. Others that also
#: render fence-only (``.claude/agent-team.md``, ``.claude/quickstart-snippet.md``) are bridge-owned and keep
#: the whole write, so a refresh still resets them (adversarial review, 2026-11-02).
PROJECT_EXTENDED_FILES = frozenset({".goosehints"})
_BRIDGE_BEGIN = "AGENTTEAMS-BRIDGE:BEGIN"
_BRIDGE_END = "AGENTTEAMS-BRIDGE:END"


def refresh_keeping_project_text(name: str, existing: str, rendered: str) -> tuple[str, str | None]:
    """The ``--bridge-refresh`` content for one target file.

    For a file in :data:`PROJECT_EXTENDED_FILES` whose render is nothing but ``AGENTTEAMS-BRIDGE`` fences, the
    project's own text around the fences survives a refresh, as under ``--bridge-merge``: the fences are
    replaced in place, or appended when the file has none. Every other file is replaced whole (the documented
    ``--bridge-refresh`` behaviour; see ``references/bridge-refresh-safety.md``), and so is a file whose bridge
    markers don't pair up, since an in-place replacement could then span project text.

    Args:
        name: The target file's name (``.goosehints``).
        existing: The file on disk.
        rendered: The fresh bridge render.

    Returns:
        ``(content, notice)``: the content to write, and a notice when project text was kept or could not be.

    Raises:
        Nothing.
    """
    from agentteams.bridge import _extract_fence_regions, _merge_target_file  # bridge imports this module

    if name not in PROJECT_EXTENDED_FILES:
        return rendered, None
    regions = _extract_fence_regions(rendered)
    outside = rendered
    for block in regions.values():
        outside = outside.replace(block, "")
    if not regions or outside.strip():
        return rendered, None
    project = existing
    for block in _extract_fence_regions(existing).values():
        project = project.replace(block, "")
    kept = [line for line in project.splitlines() if line.strip()]
    if not kept:
        return rendered, None
    if existing.count(_BRIDGE_BEGIN) != existing.count(_BRIDGE_END) or existing.count(_BRIDGE_BEGIN) != len(
            _extract_fence_regions(existing)):
        return rendered, (f"its AGENTTEAMS-BRIDGE markers don't pair up, so it was rewritten whole and "
                          f"{len(kept)} line(s) of project text went to the backup only; restore them from "
                          "there")
    merged, status = _merge_target_file(existing=existing, rendered=rendered)
    if status != "merged":
        merged = existing.rstrip("\n") + "\n\n" + rendered
    else:
        # A rendered fence the file lacks would otherwise be dropped by the in-place merge.
        missing = [b for rid, b in regions.items() if rid not in _extract_fence_regions(merged)]
        if missing:
            merged = merged.rstrip("\n") + "\n\n" + "".join(missing)
    return merged, (f"kept {len(kept)} line(s) of project text outside the AGENTTEAMS-BRIDGE fence (the render "
                    "owns only the fence); review them if unexpected")


__all__ = ["PROJECT_EXTENDED_FILES", "refresh_keeping_project_text"]
