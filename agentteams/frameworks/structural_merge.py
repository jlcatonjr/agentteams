"""Post-fence-merge steps that own unfenced structure (follow-ups #15 and #16).

Called once from :func:`agentteams.emit.emit_all`'s merge path, right after the fence merge and
the front-matter merge, for every merged file. Each step is a no-op for files it does not own:

* Goose recipes (``*.yaml``): the top-level ``sub_recipes`` key is reconciled from the fresh
  render (:func:`agentteams.frameworks.goose_recipe_merge.reconcile_sub_recipes`).
* Every markdown agent file and goose recipe: an ``AGENTTEAMS-LEARNED`` block the fence merge
  dropped is carried back from the fresh render (which :func:`agentteams.emit.emit_all` already
  seeded from the on-disk file) — :func:`agentteams.learned_blocks.carry_block_text`.
* Repo-root ``AGENTS.md``: a remaining unfenced Constitutional Rules list beside the fenced
  baseline is reported on every run
  (:func:`agentteams.frameworks._agents_md_rules.duplicate_rules_notice`).

Kept out of ``emit.py`` so that module stays clear of the CH-07 ceiling margin.
"""

from __future__ import annotations

from pathlib import Path

from agentteams.frameworks._agents_md_rules import duplicate_rules_notice
from agentteams.frameworks.goose_recipe_merge import reconcile_sub_recipes
from agentteams.learned_blocks import carry_block_text


def post_merge_structural(rel_path: str, fresh: str, merged: str) -> tuple[str, list[str]]:
    """Apply the structural merge steps for *rel_path*.

    Args:
        rel_path: Output path relative to the agents directory.
        fresh: The fresh render of the file.
        merged: The fence-merged content about to be written.

    Returns:
        ``(content, notices)`` — *merged* (possibly reconciled) and any operator notices.
    """
    # Only agent files directly in the agents dir (emit already carried the block into `fresh`
    # for those alone); never references/, CLAUDE.md or AGENTS.md.
    notices: list[str] = []
    if "/" not in rel_path and Path(rel_path).name not in ("CLAUDE.md", "AGENTS.md"):
        merged, notices = carry_block_text(rel_path, fresh, merged)
    if rel_path.endswith(".yaml"):
        merged, more = reconcile_sub_recipes(fresh, merged)
        return merged, notices + list(more)
    if Path(rel_path).name == "AGENTS.md":
        notice = duplicate_rules_notice(fresh, merged)
        return merged, notices + ([notice] if notice else [])
    return merged, notices


__all__ = ["post_merge_structural"]
