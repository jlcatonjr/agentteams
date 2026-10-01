"""Orphan-agent-file advisory: agent files on disk the current team no longer emits.

Moved out of ``build_team.py`` (follow-up #15, CH-07 ceiling); ``build_team._report_orphan_agent_files``
remains as a shim that supplies the event persister, so every caller and test is unchanged.

``--prune`` handles removals the build log recorded since the last build; these are older
orphans the log no longer records, so without this advisory they accumulate invisibly.

**The suffix is the framework's, not a constant.** It filtered on ``.agent.md`` until
2026-08-03; only copilot-vscode uses that extension, so the advisory was blind on every other
framework. ``agent_ext`` is keyword-only and required — a default is what let the suffix go
unexamined.

**Relabel, never silence** (follow-up #15). Files that are on disk on purpose were reported as
"orphaned … delete manually". Each candidate now lands in one bucket:

* ``orphaned`` — the original wording; the only bucket persisted to the daily orphan-events log.
  A file whose front matter merely *claims* bridge ownership (``bridge:`` / ``source_sha256:``)
  stays here, labelled "claims bridge-managed (unverified)": a front-matter key is writable by
  anyone, so it earns visibility, never a "keep" (sec P3-2).
* ``bespoke`` — "bespoke roster member (no template): keep": the slug is in ``agent_slug_list``
  or ``selected_archetypes`` but no template rendered it.
* ``bridge`` — "bridge-managed: keep": a reserved bridge slug
  (``bridge_subagents_goose._RESERVED_SLUGS``, e.g. ``bridge-orchestrator.yaml``).

Every bucket is still printed, so nothing on disk becomes invisible.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Callable

ORPHANED = "orphaned"
BESPOKE = "bespoke"
BRIDGE = "bridge"

_BRIDGE_CLAIM_RE = re.compile(r"^(?:bridge|source_sha256):", re.MULTILINE)
_CLAIM_LABEL = "claims bridge-managed (unverified)"


def _claims_bridge(path: Path) -> bool:
    """True when the file's front matter (or a recipe's top-level keys) claims bridge ownership.

    Markdown: only the leading ``---`` block is inspected. YAML recipes: column-0 keys. The
    claim is self-asserted and therefore unverified — callers must not treat it as "keep".
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    if path.suffix in {".yaml", ".yml", ".toml"}:
        return bool(_BRIDGE_CLAIM_RE.search(text))
    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    return end != -1 and bool(_BRIDGE_CLAIM_RE.search(text[3:end]))


def classify_orphan_agent_files(
    final_rendered: list[tuple[str, str]],
    output_dir: Path,
    manifest: dict[str, Any],
    *,
    agent_ext: str,
) -> dict[str, list[tuple[str, str]]]:
    """Bucket the agent files on disk that the current run does not emit.

    Args:
        final_rendered: ``(rel_path, content)`` pairs the current run will write.
        output_dir: Root agents directory.
        manifest: Team manifest, read for ``adopted_agents``, ``tool_agents``,
            ``agent_slug_list`` and ``selected_archetypes``.
        agent_ext: The framework's agent-file extension (``adapter.get_file_extension("agent")``).

    Returns:
        ``{ORPHANED: [...], BESPOKE: [...], BRIDGE: [...]}``; each entry is ``(filename, label)``
        sorted by filename. ``label`` is empty for a plain orphan.
    """
    from agentteams.bridge_subagents_goose import _RESERVED_SLUGS

    emitted_names = {Path(p).name for p, _ in final_rendered if p.endswith(agent_ext)}
    # Adopted orphans (--adopt-orphans) are deliberately not emitted but are roster members.
    adopted_names = {f"{s}{agent_ext}" for s in manifest.get("adopted_agents", [])}
    # Tool docs are never agents; a legacy tool-<slug> agent is handled by the caller's migration.
    tool_doc_names = {f"{ta['slug']}{agent_ext}" for ta in manifest.get("tool_agents", [])}
    roster = set(manifest.get("agent_slug_list", [])) | set(manifest.get("selected_archetypes", []))

    buckets: dict[str, list[tuple[str, str]]] = {ORPHANED: [], BESPOKE: [], BRIDGE: []}
    for f in sorted(output_dir.glob(f"*{agent_ext}")):
        name = f.name
        # SETUP-REQUIRED.md is a build artifact, not an agent (the `.agent.md` suffix excluded
        # it for free; a bare `.md` does not) — same carve-out as bridge_sources.py.
        if (
            name == "SETUP-REQUIRED.md"
            or name in emitted_names
            or name in adopted_names
            or name in tool_doc_names
        ):
            continue
        slug = name[: -len(agent_ext)]
        if slug in _RESERVED_SLUGS:
            buckets[BRIDGE].append((name, "bridge-managed: keep"))
        elif slug in roster:
            buckets[BESPOKE].append((name, "bespoke roster member (no template): keep"))
        else:
            buckets[ORPHANED].append((name, _CLAIM_LABEL if _claims_bridge(f) else ""))
    return buckets


def report_orphan_agent_files(
    final_rendered: list[tuple[str, str]],
    output_dir: Path,
    manifest: dict[str, Any],
    *,
    agent_ext: str,
    persist: Callable[[list[str], dict[str, Any], Path], None] | None = None,
) -> list[str]:
    """Print the orphan advisory and persist the orphaned bucket.

    Args:
        final_rendered: ``(rel_path, content)`` pairs the current run will write.
        output_dir: Root agents directory.
        manifest: Team manifest (see :func:`classify_orphan_agent_files`).
        agent_ext: The framework's agent-file extension. Required, keyword-only.
        persist: Called with the orphaned filenames when any exist (the daily orphan-events
            log). Only the orphaned bucket is ever passed.

    Returns:
        The sorted orphaned filenames (the keep buckets are excluded).
    """
    buckets = classify_orphan_agent_files(final_rendered, output_dir, manifest, agent_ext=agent_ext)
    orphans = [name for name, _ in buckets[ORPHANED]]
    if orphans:
        print(
            f"\n  ⚠  {len(orphans)} agent file(s) on disk are not part "
            "of the current team (orphaned by past team-config changes):",
            file=sys.stderr,
        )
        for name, label in buckets[ORPHANED]:
            print(f"       {name}" + (f"  — {label}" if label else ""), file=sys.stderr)
        # --prune deletes only what the build-log diff records as removed; these orphans are
        # found by globbing, which --prune never consults.
        print(
            "     These are not updated by --update, and --prune cannot remove "
            "them (detection-only today). Review and delete manually if obsolete.",
            file=sys.stderr,
        )
        if persist is not None:
            persist(orphans, manifest, output_dir)
    kept = buckets[BESPOKE] + buckets[BRIDGE]
    if kept:
        print(
            f"\n  ℹ  {len(kept)} agent file(s) on disk are not emitted by this run but are "
            "expected to be there:",
            file=sys.stderr,
        )
        for name, label in sorted(kept):
            print(f"       {name}  — {label}", file=sys.stderr)
    return orphans


__all__ = ["BESPOKE", "BRIDGE", "ORPHANED", "classify_orphan_agent_files", "report_orphan_agent_files"]
