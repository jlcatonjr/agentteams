"""
fences.py — section-fencing internals (regexes, MergeResult, fence extraction/merge,
shrink detection, and lost-fence sidecars) for emit. Carved from emit.py (CH-07).
emit.py re-exports these so importers (drift, fence_inject, tests) resolve them from
agentteams.emit unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentteams.atomicio import _atomic_write_text

_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]\s|\d+\.\s)", re.MULTILINE)
_PATH_RE = re.compile(r"[A-Za-z0-9_./-]+\.(?:py|md|json|yaml|yml|toml|csv|tsv|sql|sh)\b")
_BACKTICK_IDENT_RE = re.compile(r"`([^`\n]+)`")


@dataclass
class MergeResult:
    """Result of a single fenced-content merge operation.

    Attributes:
        sections_replaced:  section_ids whose content was updated from the new render.
        sections_added:     section_ids present in new render but absent in existing file.
        sections_orphaned:  section_ids present in existing file but absent in new render.
        sections_preserved: section_ids whose new render would have shrunk the
                            existing body, kept unchanged under shrink_policy
                            "preserve" (respectful update — no content lost).
        parse_errors:       Human-readable messages for parse failures.
        unchanged:          section_ids that were identical in both files (no write needed).
        merged_content:     The final merged file content (empty string on parse failure).
        shrink_notices:     Per-section human-readable Notices (Plan 3) when a
                            regenerated fence body is materially shorter / less
                            specific than the existing on-disk body.
    """
    sections_replaced: list[str] = field(default_factory=list)
    sections_added: list[str] = field(default_factory=list)
    sections_orphaned: list[str] = field(default_factory=list)
    sections_preserved: list[str] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    merged_content: str = ""
    shrink_notices: list[str] = field(default_factory=list)
    # W22 data-loss recovery: full pre-merge body of every fence that fired
    # a shrink notice, keyed by section_id. Persisted as a .lost.<sid>.md
    # sidecar inside the backup dir by emit_all when backup_path is provided.
    lost_fence_bodies: dict[str, str] = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        return bool(self.parse_errors)

    @property
    def content_changed(self) -> bool:
        return bool(self.sections_replaced or self.sections_added)


# ---------------------------------------------------------------------------
# Section-fencing internals
# ---------------------------------------------------------------------------

_FENCE_BEGIN_RE = re.compile(
    r"<!-- AGENTTEAMS:BEGIN (?P<sid>[a-z][a-z0-9_]*) v=\d+ -->",
)
_FENCE_END_RE = re.compile(
    r"<!-- AGENTTEAMS:END (?P<sid>[a-z][a-z0-9_]*) -->",
)
# W2: detect AGENTTEAMS-BRIDGE fences (written by --bridge-refresh) so the
# --merge path can emit a targeted notice instead of the generic "legacy file" warning.
_BRIDGE_FENCE_BEGIN_RE = re.compile(r"<!--\s*AGENTTEAMS-BRIDGE:BEGIN\s+")
_YAML_FM_RE = re.compile(r"^(---\n.+?\n---\n)", re.DOTALL)

_MACHINE_MANAGED_MERGE_OVERWRITE_PATHS: frozenset[str] = frozenset([
    "references/security-vulnerability-watch.json",
    # Sentinel-merge handled in vscode_tasks.py before this path reaches emit;
    # emit must overwrite (not fence-merge) so stale JSON is fully replaced.
    "../../.vscode/tasks.json",
    # Generated SVG diagrams are raw XML (no AGENTTEAMS content fence); emit must
    # overwrite them wholesale — auto-fencing would inject a comment before <?xml>
    # (invalid XML) and merge would skip them as unmanaged (stale forever).
    "references/pipeline-graph.svg",
    "references/pipeline-handoffs.svg",
    "references/architecture-graph.svg",
    "references/architecture-modules.svg",
    # The graph .md documents are 100% machine-generated ("Auto-generated. Do not
    # edit manually") with no user-editable region. Under --merge, fence-merging
    # their single `content` block preserved the stale body, so the .md drifted
    # behind its companion .svg (which IS overwritten) — the roster table would show
    # a different agent count than the diagram. Full-replace keeps the two in lockstep.
    "references/pipeline-graph.md",
    "references/architecture-graph.md",
])

# Fences whose body is refreshed each run from an upstream live feed
# (CISA KEV, NVD CVSS, OSV.dev, etc.). Content "loss" in these fences reflects
# normal feed rotation, not user-content deletion — suppress the shrink-warn
# heuristic so structural --update runs don't emit alarming false positives.
# The canonical history for these feeds is the cache JSON, not the embedded
# snapshot. Real user content sits in adjacent operator-managed fences.
_LIVE_DATA_FENCES: frozenset[str] = frozenset([
    "threat_intelligence",
    "threat_data",
])


def _fence_body(block: str) -> str:
    """Strip the BEGIN/END marker lines from a fenced block — returns body only."""
    lines = block.splitlines(keepends=True)
    if not lines:
        return ""
    body = lines[1:-1] if len(lines) >= 2 else lines
    return "".join(body)


def _detect_fence_shrink(sid: str, existing_block: str, new_block: str) -> str | None:
    """Plan 3: return a Notice string when the new fence body is materially
    shorter or less specific than the existing body (rules a/b/c), else None.

    Rules (any one triggers):
      (a) new body length < 50% of existing body length;
      (b) new body has >= 3 fewer markdown list items than existing;
      (c) existing body contained concrete file paths or backtick-quoted
          identifiers that the new body does not.

    Live-feed fences (`_LIVE_DATA_FENCES`) are exempt: their bodies are
    refreshed each run from upstream feeds and rotation is expected. The
    sidecar mechanism in `lost_fence_bodies` still preserves the prior body
    on disk if real recovery is ever needed.
    """
    if sid in _LIVE_DATA_FENCES:
        return None
    existing = _fence_body(existing_block)
    new = _fence_body(new_block)
    if not existing.strip():
        return None  # nothing to shrink from
    ex_len, new_len = len(existing), len(new)
    if ex_len == 0:
        return None

    reasons: list[str] = []
    # (a) length shrink > 50%
    if ex_len > 0 and new_len < ex_len / 2:
        reasons.append(
            f"body shrank {ex_len}->{new_len} bytes (>{50}% reduction)"
        )
    # (b) list-item delta >= 3
    ex_items = len(_LIST_ITEM_RE.findall(existing))
    new_items = len(_LIST_ITEM_RE.findall(new))
    if ex_items - new_items >= 3:
        reasons.append(f"lost {ex_items - new_items} list item(s) ({ex_items}->{new_items})")
    # (c) lost concrete paths / backtick identifiers
    ex_paths = set(_PATH_RE.findall(existing)) | set(_BACKTICK_IDENT_RE.findall(existing))
    new_paths = set(_PATH_RE.findall(new)) | set(_BACKTICK_IDENT_RE.findall(new))
    lost = ex_paths - new_paths
    if lost:
        sample = sorted(lost)[:3]
        more = f" (+{len(lost) - 3} more)" if len(lost) > 3 else ""
        reasons.append(f"lost concrete refs: {', '.join(sample)}{more}")

    if not reasons:
        return None
    return f"fence '{sid}': " + "; ".join(reasons)


def _extract_fenced_regions(content: str) -> dict[str, str] | str:
    """Extract all fenced regions from *content*.

    Returns a dict mapping ``section_id`` to the full fenced block (including
    the BEGIN and END markers) on success, or an error message string on failure.

    Failure conditions: unclosed BEGIN, duplicate section_id, mismatched END.
    """
    regions: dict[str, str] = {}
    lines = content.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        begin_match = _FENCE_BEGIN_RE.search(lines[i])
        if begin_match:
            sid = begin_match.group("sid")
            if sid in regions:
                return f"Duplicate section_id '{sid}'"
            block_lines = [lines[i]]
            i += 1
            closed = False
            while i < len(lines):
                end_match = _FENCE_END_RE.search(lines[i])
                if end_match:
                    end_sid = end_match.group("sid")
                    if end_sid != sid:
                        return f"Mismatched END: expected '{sid}', got '{end_sid}'"
                    block_lines.append(lines[i])
                    closed = True
                    i += 1
                    break
                # Check for nested BEGIN (not allowed)
                if _FENCE_BEGIN_RE.search(lines[i]):
                    nested_sid = _FENCE_BEGIN_RE.search(lines[i]).group("sid")
                    return f"Nested fence not allowed: '{nested_sid}' inside '{sid}'"
                block_lines.append(lines[i])
                i += 1
            if not closed:
                return f"Unclosed fence: '{sid}' has no END marker"
            regions[sid] = "".join(block_lines)
        else:
            i += 1
    return regions


def _is_machine_managed_merge_overwrite_path(rel_path: str) -> bool:
    """Return True when merge mode may safely full-replace a machine-managed file."""
    return rel_path in _MACHINE_MANAGED_MERGE_OVERWRITE_PATHS


def _merge_fenced_content(
    new_rendered: str,
    existing_on_disk: str,
    preserve_on_shrink: bool = False,
) -> MergeResult:
    """Merge fenced sections from *new_rendered* into *existing_on_disk*.

    Template-owned (fenced) regions in the existing file are replaced with
    the corresponding regions from the new render.  All content outside any
    fence marker is preserved unchanged.

    Args:
        new_rendered:     Fully rendered file content from the render phase.
        existing_on_disk: Current content of the on-disk file.
        preserve_on_shrink: When True (shrink_policy="preserve"), a fence whose
            new render would materially shrink the existing body is left
            unchanged instead of being replaced — the richer enriched body is
            kept and recorded in ``sections_preserved``. Non-shrinking fences
            still receive their template updates. This is the respectful,
            non-destructive update path: no content is lost and no whole-file
            write is blocked.

    Returns:
        MergeResult describing what changed.  ``merged_content`` is empty on
        parse failure.
    """
    result = MergeResult()

    # Parse existing file
    existing_regions = _extract_fenced_regions(existing_on_disk)
    if isinstance(existing_regions, str):
        # String return means error
        if "has no" in existing_regions and "END marker" in existing_regions:
            result.parse_errors.append(
                f"Existing file parse error: {existing_regions}"
            )
        elif not existing_regions:
            # _extract_fenced_regions returns empty dict for no fences
            pass
        else:
            result.parse_errors.append(
                f"Existing file parse error: {existing_regions}"
            )
        return result

    if not existing_regions:
        # W2: distinguish a truly unfenced legacy file from one written by
        # --bridge-refresh (AGENTTEAMS-BRIDGE namespace).  The latter needs a
        # targeted notice rather than the generic "legacy file" warning.
        if _BRIDGE_FENCE_BEGIN_RE.search(existing_on_disk):
            result.parse_errors.append(
                "No AGENTTEAMS fence markers detected — file contains AGENTTEAMS-BRIDGE "
                "fences (written by --bridge-refresh). Run --bridge-refresh to regenerate, "
                "or add AGENTTEAMS fence markers to enable --merge updates."
            )
        else:
            result.parse_errors.append(
                "No fence markers detected — legacy file. "
                "Use --overwrite to replace unconditionally, or add "
                "AGENTTEAMS fence markers manually."
            )
        return result

    # Parse new render
    new_regions = _extract_fenced_regions(new_rendered)
    if isinstance(new_regions, str):
        result.parse_errors.append(
            f"New render parse error: {new_regions}"
        )
        return result

    # Rebuild the existing file by replacing each fenced block in-place
    lines = existing_on_disk.splitlines(keepends=True)
    output_lines: list[str] = []
    i = 0
    replaced_sids: set[str] = set()

    while i < len(lines):
        begin_match = _FENCE_BEGIN_RE.search(lines[i])
        if begin_match:
            sid = begin_match.group("sid")
            # Skip the entire old fenced block
            i += 1
            while i < len(lines):
                if _FENCE_END_RE.search(lines[i]):
                    i += 1
                    break
                i += 1
            # Inject replacement or preserve orphan
            if sid in new_regions:
                if new_regions[sid] == existing_regions.get(sid, ""):
                    output_lines.append(new_regions[sid])
                    result.unchanged.append(sid)
                else:
                    # Plan 3: detect material shrink and queue a Notice.
                    notice = _detect_fence_shrink(
                        sid, existing_regions.get(sid, ""), new_regions[sid]
                    )
                    if notice and preserve_on_shrink:
                        # Respectful update: the new render would drop enriched
                        # content. Keep the existing body verbatim; surface a
                        # notice so the suppression is visible. No data lost, so
                        # no .lost.<sid>.md sidecar is needed.
                        output_lines.append(existing_regions[sid])
                        result.sections_preserved.append(sid)
                        result.shrink_notices.append(notice)
                    else:
                        output_lines.append(new_regions[sid])
                        result.sections_replaced.append(sid)
                        if notice:
                            result.shrink_notices.append(notice)
                            # W22 data-loss recovery: capture the pre-merge body
                            # so emit_all can write a .lost.<sid>.md sidecar.
                            result.lost_fence_bodies[sid] = _fence_body(
                                existing_regions.get(sid, "")
                            )
                replaced_sids.add(sid)
            else:
                # Orphaned: in existing but not in new render — leave in place
                output_lines.append(existing_regions[sid])
                result.sections_orphaned.append(sid)
        else:
            output_lines.append(lines[i])
            i += 1

    merged = "".join(output_lines)

    # Append sections that are new (in new render but not in existing file)
    for sid, block in new_regions.items():
        if sid not in replaced_sids and sid not in result.sections_orphaned:
            merged = merged.rstrip("\n") + "\n\n" + block
            if not merged.endswith("\n"):
                merged += "\n"
            result.sections_added.append(sid)

    result.merged_content = merged
    return result



# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

# W22 data-loss recovery -----------------------------------------------------

_SHRINK_NOTICE_SID_RE = re.compile(r"^fence '([^']+)':")


def _shrink_notice_sid(notice: str) -> str | None:
    """Extract the fence section_id from a shrink Notice string."""
    m = _SHRINK_NOTICE_SID_RE.match(notice)
    return m.group(1) if m else None


def _write_lost_fence_sidecars(
    backup_path: Path,
    rel_path: str,
    lost_bodies: dict[str, str],
) -> dict[str, str]:
    """Persist each lost fence body as ``<backup>/<rel_path>.lost.<sid>.md``.

    Returns a mapping of section_id → sidecar path (string, relative to repo
    root when possible, else absolute) so the caller can annotate Notices.
    Failures are non-fatal: the function returns whatever sidecars were
    written and skips the rest.
    """
    written: dict[str, str] = {}
    if not lost_bodies:
        return written
    try:
        backup_path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return written
    for sid, body in lost_bodies.items():
        if not body.strip():
            continue
        # Flatten the rel_path into the filename so sibling files never collide:
        # references/foo.md + sid=content → references/foo.md.lost.content.md
        sidecar = backup_path / f"{rel_path}.lost.{sid}.md"
        try:
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(sidecar, body)
        except OSError:
            continue
        try:
            written[sid] = str(sidecar.relative_to(Path.cwd()))
        except ValueError:
            written[sid] = str(sidecar)
    return written
