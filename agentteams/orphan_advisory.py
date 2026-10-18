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
* ``rostered`` — listed in the on-disk orchestrator's ``agents:`` roster, as merge-mode adoption
  (``--update --merge --adopt-orphans``, gated by ``cli.adopt_merge_gate``) leaves it. Each run rebuilds the
  manifest from the brief, so a later plain ``--update`` has no ``adopted_agents`` for these and used to call them
  orphans to "delete manually" (found by baseAgent: 17 adopted agents). They are printed under their own heading,
  labelled "(roster in front matter, unverified)", and not persisted as orphan events. Rule 12: the roster is a
  front-matter key, so a slug added there by hand leaves the orphan log; that is why the label says unverified, the
  file is still listed, and the orchestrator's own build-log hash makes such an edit show as MODIFIED in
  ``--verify-integrity``.

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
ROSTERED = "rostered"

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
        ``{ORPHANED: [...], BESPOKE: [...], BRIDGE: [...], ROSTERED: [...]}``; each entry is ``(filename, label)``
        sorted by filename. ``label`` is empty for a plain orphan.
    """
    from agentteams.bridge_subagents_goose import _RESERVED_SLUGS

    emitted_names = {Path(p).name for p, _ in final_rendered if p.endswith(agent_ext)}
    # Adopted orphans (--adopt-orphans) are deliberately not emitted but are roster members.
    adopted_names = {f"{s}{agent_ext}" for s in manifest.get("adopted_agents", [])}
    # Tool docs are never agents; a legacy tool-<slug> agent is handled by the caller's migration.
    tool_doc_names = {f"{ta['slug']}{agent_ext}" for ta in manifest.get("tool_agents", [])}
    roster = set(manifest.get("agent_slug_list", [])) | set(manifest.get("selected_archetypes", []))
    on_disk_roster = _orchestrator_roster(output_dir, agent_ext)

    buckets: dict[str, list[tuple[str, str]]] = {ORPHANED: [], BESPOKE: [], BRIDGE: [], ROSTERED: []}
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
        elif slug in on_disk_roster:
            buckets[ROSTERED].append((name, "in the orchestrator's agents: roster (roster in front matter, unverified)"))
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
    if buckets[ROSTERED]:
        print(
            f"\n  ℹ  {len(buckets[ROSTERED])} agent file(s) are listed in the orchestrator's agents: roster but not in "
            "this run's brief (adopted by merge). Pass --adopt-orphans to record them; delete one only by also "
            "removing it from the roster:",
            file=sys.stderr,
        )
        for name, label in buckets[ROSTERED]:
            print(f"       {name}  — {label}", file=sys.stderr)
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


def _orchestrator_roster(output_dir: Path, agent_ext: str) -> set[str]:
    """Slugs in the on-disk orchestrator's front-matter ``agents:`` block list; empty when absent or unparseable.

    Reuses ``cli.adopt_merge_gate.existing_agents``, the same parser the gated merge writes with, so the two agree on
    what the roster is. A roster this parser refuses to read is treated as empty: the files stay ORPHANED, never
    silently kept.
    """
    from agentteams.cli.adopt_merge_gate import AdoptMergeError, existing_agents

    try:
        text = (output_dir / f"orchestrator{agent_ext}").read_text(encoding="utf-8")
        return set(existing_agents(text) or [])
    except (OSError, UnicodeDecodeError, AdoptMergeError):
        return set()


__all__ = ["BESPOKE", "BRIDGE", "ORPHANED", "ROSTERED", "classify_orphan_agent_files", "report_orphan_agent_files"]
