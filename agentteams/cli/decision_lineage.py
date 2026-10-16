"""decision_lineage.py — the security-decision log's ``derives_from`` lineage, in one place.

Workstream A (Gap A): a HALT propagates along declared lineage, so renaming a HALTed action's id
does not evade C-2. These helpers read the ``derives_from`` column and walk it FAIL-CLOSED: a cycle
or a reference to an id no row defines raises rather than resolving to "no ancestor".

Carved out of ``decision_log`` (which sits near the CH-07 ceiling), the way ``decision_chain``,
``action_ids`` and ``decision_text`` were. It is integrity-pinned; ``decision_log`` re-exports these
names, so existing callers and tests are unchanged.
"""

from __future__ import annotations

import re

#: The optional column naming the action id(s) a decision derives from. The single definition:
#: ``decision_log`` imports it (its signed-axis comment explains why the column is signed).
_DERIVES_FROM_FIELD = "derives_from"


def _row_action_id(row: dict[str, str]) -> str:
    """Return a row's own action id, normalized (lowercased, stripped)."""
    return (row.get("action_reviewed") or row.get("decision") or "").strip().lower()


def _row_lineage(row: dict[str, str]) -> set[str]:
    """Return the normalized parent action ids a row declares in ``derives_from`` (may be empty)."""
    raw = row.get(_DERIVES_FROM_FIELD, "") or ""
    return {part.strip().lower() for part in re.split(r"[;,]", raw) if part.strip()}


def _build_lineage_map(rows: list[dict[str, str]]) -> dict[str, set[str]]:
    """Map each row action id to the union of parent ids it declares across all its rows."""
    lineage: dict[str, set[str]] = {}
    for row in rows:
        action_id = _row_action_id(row)
        if not action_id:
            continue
        lineage.setdefault(action_id, set()).update(_row_lineage(row))
    return lineage


def _transitive_ancestors(action: str, lineage_map: dict[str, set[str]]) -> set[str]:
    """Return the transitive ``derives_from`` ancestors of *action*, FAIL-CLOSED.

    Raises rather than returning a partial set on any lineage defect, because a relaxing action
    that evades its ancestor's HALT by a broken or fabricated lineage must refuse, not proceed:

    * **nonexistent-id** — a ``derives_from`` naming an id that no row in the log defines (not a
      key in ``lineage_map``): an untraceable / fabricated ancestor;
    * **cycle** — a ``derives_from`` chain that revisits a node on the current path.

    Args:
        action: The requested action id (normalized inside).
        lineage_map: Output of :func:`_build_lineage_map`.

    Returns:
        The set of transitive ancestor ids (excluding *action* itself).

    Raises:
        RuntimeError: A dangling reference or a cycle — traversal fails closed.
    """
    start = action.strip().lower()
    ancestors: set[str] = set()
    on_path: set[str] = set()   # nodes on the current recursion stack (cycle = edge into this)
    explored: set[str] = set()  # nodes fully descended (safe to skip RE-descending, not the edge)

    def _descend(node: str) -> None:
        on_path.add(node)
        for parent in lineage_map.get(node, set()):
            if parent in on_path:
                # A back-edge to a node on the current stack is a genuine cycle — regardless of
                # which node it was first discovered from (WS-A re-review F-4: the earlier global
                # dedup let an off-first-path cycle escape).
                raise RuntimeError(
                    f"lineage cycle detected at {parent!r} (via {node!r})"
                )
            if parent not in lineage_map:
                raise RuntimeError(
                    f"lineage reference {parent!r} (from {node!r}) names no recorded decision "
                    f"row — a relaxing action cannot derive from a nonexistent/untraceable id"
                )
            ancestors.add(parent)
            if parent not in explored:
                _descend(parent)
        on_path.discard(node)
        explored.add(node)

    _descend(start)
    return ancestors


__all__: list[str] = []
