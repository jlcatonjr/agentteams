"""Structural ownership of a Goose recipe's top-level ``sub_recipes:`` key on merge.

Follow-up #15. The orchestrator recipe's ``sub_recipes`` list is a top-level YAML key with no
fence, so ``--update --merge`` (which fence-merges ``orchestrator.yaml`` because its
instructions carry template fences) preserved a stale on-disk list forever: delegation to a
since-removed tool agent stayed, and a newly added roster member never appeared.

The generic fence engine cannot own this key. A new fence is spliced after the nearest
preceding section's END marker, which sits *inside* the indented ``instructions: |`` block
scalar; a column-0 line there ends the scalar and corrupts the YAML. So the key is owned
structurally instead, by a **span**: the column-0 ``sub_recipes:`` line through the line before
the next column-0 key (plus the managed comment line directly above it). The span is replaced
with the fresh render's span, appended when absent, or removed when the fresh render has none.

Deliberately conservative:

- Any shape this module does not recognise — ``sub_recipes: []``, an inline comment, a
  duplicated key, nested content it cannot classify — produces a notice and **no edit**.
- After reconciling, two checks can roll the edit back (notice emitted): a structural invariant
  (the result must re-parse to exactly one ``sub_recipes`` span equal to the new block, with every
  other line unchanged), and the shallow regex checks of
  :func:`agentteams.frameworks.goose_recipe_validate._validate_recipe_yaml`. Neither is a full YAML
  parse (stdlib only), so the conservative span rules above are the main protection.
- Idempotent: reconciling an already-reconciled file returns it byte-identical.

Stdlib only; the codebase intentionally hand-parses YAML (see ``goose.py``).
"""

from __future__ import annotations

import re

from agentteams.frameworks.goose_recipe_validate import _validate_recipe_yaml

#: Column-0 comment emitted directly above the key. Goose ignores comments; operators read it.
SUB_RECIPES_MANAGED_COMMENT = (
    "# agentteams-managed: sub_recipes (regenerated on --update; "
    "change delegation via the brief/roster)"
)
_MANAGED_PREFIX = "# agentteams-managed: sub_recipes"

_KEY_EXACT_RE = re.compile(r"^sub_recipes:[ \t]*$")
_KEY_ANY_RE = re.compile(r"^sub_recipes\s*:")
_RECIPE_MARKERS_RE = re.compile(r'^version:\s*"', re.MULTILINE)
_INSTRUCTIONS_RE = re.compile(r"^instructions:\s*\|", re.MULTILINE)
# A recognised entry line: optional list dash, a simple key, and a scalar value that is either
# quoted in full or a plain scalar carrying no `#` (an inline comment makes the shape unknown).
_SCALAR = r'(?:"(?:[^"\\]|\\.)*"|\'(?:[^\']|\'\')*\'|[^\s#"\'][^#]*?)'
_ENTRY_RE = re.compile(rf"^(?:[ \t]*-[ \t]+|[ \t]+)[A-Za-z_][\w-]*:(?:[ \t]+{_SCALAR})?[ \t]*$")
_NAME_RE = re.compile(rf"^[ \t]*-[ \t]+name:[ \t]+({_SCALAR})[ \t]*$")


class _Unrecognised(Exception):
    """Internal signal: the on-disk ``sub_recipes`` shape is not one this module edits."""


def is_goose_recipe(text: str) -> bool:
    """True when *text* looks like a Goose recipe (``version:`` + ``instructions: |``).

    Args:
        text: File content.

    Returns:
        Whether the reconcile applies to this file at all.
    """
    return bool(_RECIPE_MARKERS_RE.search(text) and _INSTRUCTIONS_RE.search(text))


def _span(lines: list[str]) -> tuple[int, int] | None:
    """Return the ``[start, end)`` line span owning ``sub_recipes``, or None when absent.

    Raises:
        _Unrecognised: on a duplicated key, a key with an inline value/comment, or a body line
            that is not a recognised ``key: scalar`` entry.
    """
    keys = [i for i, ln in enumerate(lines) if _KEY_ANY_RE.match(ln)]
    if not keys:
        return None
    if len(keys) > 1:
        raise _Unrecognised("the top-level sub_recipes key appears more than once")
    k = keys[0]
    if not _KEY_EXACT_RE.match(lines[k]):
        raise _Unrecognised(f"unrecognised key line {lines[k].strip()!r}")
    end = k + 1
    while end < len(lines):
        ln = lines[end]
        if ln.strip() and not ln[0].isspace() and not ln.startswith("-"):
            break
        end += 1
    # A column-0 comment followed by more indented/list lines means the list continues past a
    # comment this parser would otherwise treat as the end: unrecognised (never strand entries).
    nxt = end
    while nxt < len(lines) and (not lines[nxt].strip() or lines[nxt].startswith("#")):
        nxt += 1
    if nxt > end and nxt < len(lines) and any(ln.startswith("#") for ln in lines[end:nxt]) and (
            lines[nxt][0].isspace() or lines[nxt].startswith("-")):
        raise _Unrecognised("a column-0 comment interrupts the sub_recipes list")
    # Trailing blank lines belong to whatever follows, not to the list.
    while end > k + 1 and not lines[end - 1].strip():
        end -= 1
    for ln in lines[k + 1:end]:
        if ln.strip() and not _ENTRY_RE.match(ln):
            raise _Unrecognised(f"unrecognised sub_recipes entry {ln.strip()!r}")
    start = k - 1 if k > 0 and lines[k - 1].startswith(_MANAGED_PREFIX) else k
    return start, end


def _names(lines: list[str]) -> list[str]:
    """Return the ``- name:`` values inside a span, unquoted."""
    out: list[str] = []
    for ln in lines:
        m = _NAME_RE.match(ln)
        if m:
            out.append(m.group(1).strip().strip("\"'"))
    return out


def reconcile_sub_recipes(fresh: str, merged: str) -> tuple[str, list[str]]:
    """Make *merged*'s top-level ``sub_recipes`` span match *fresh*'s.

    Args:
        fresh: The freshly rendered recipe (authoritative for ``sub_recipes``).
        merged: The fence-merged on-disk recipe about to be written.

    Returns:
        ``(text, notices)``. ``text`` is *merged* unchanged when nothing differs, when either
        side is not a Goose recipe, when the on-disk shape is unrecognised, or when the edit
        would introduce a validation violation (rolled back). ``notices`` name the added and
        removed sub-recipe names, or why the key was left alone.

    Raises:
        Nothing: an unrecognised shape is reported as a notice, never raised.
    """
    if not (is_goose_recipe(fresh) and is_goose_recipe(merged)):
        return merged, []
    fresh_lines = fresh.splitlines()
    merged_lines = merged.splitlines()
    try:
        fresh_span = _span(fresh_lines)
    except _Unrecognised as exc:
        return merged, [f"sub_recipes: fresh render not reconcilable ({exc}); left unchanged"]
    try:
        disk_span = _span(merged_lines)
    except _Unrecognised as exc:
        return merged, [
            f"sub_recipes: on-disk shape not recognised ({exc}); left unchanged — "
            "rewrite it as a block list or delete the key and re-run --update --merge"
        ]
    if fresh_span is None and disk_span is None:
        return merged, []

    new_block: list[str] = []
    if fresh_span is not None:
        body = fresh_lines[fresh_span[0]:fresh_span[1]]
        if not body[0].startswith(_MANAGED_PREFIX):
            body = [SUB_RECIPES_MANAGED_COMMENT] + body
        new_block = body
    old_block = merged_lines[disk_span[0]:disk_span[1]] if disk_span else []
    if new_block == old_block:
        return merged, []

    if disk_span is None:
        out_lines = merged_lines + new_block
    else:
        out_lines = merged_lines[:disk_span[0]] + new_block + merged_lines[disk_span[1]:]
    text = "\n".join(out_lines).rstrip("\n") + "\n"

    try:  # structural invariant: exactly the new span, every other line untouched
        check = text.splitlines()
        span = _span(check)
        intact = (span is not None and check[span[0]:span[1]] == new_block
                  and check[:span[0]] + check[span[1]:]
                  == [ln for i, ln in enumerate(merged_lines)
                      if not (disk_span and disk_span[0] <= i < disk_span[1])])
    except _Unrecognised:
        intact = False
    if new_block and not intact:
        return merged, ["sub_recipes: reconcile rolled back — the result failed the structural "
                        "invariant; left unchanged"]

    before = set(_validate_recipe_yaml(merged))
    introduced = [v for v in _validate_recipe_yaml(text) if v not in before]
    if introduced:
        return merged, [
            f"sub_recipes: reconcile rolled back — it would introduce {introduced}; left unchanged"
        ]

    old_names, new_names = _names(old_block), _names(new_block)
    added = [n for n in new_names if n not in old_names]
    removed = [n for n in old_names if n not in new_names]
    notices: list[str] = []
    if added:
        notices.append(f"sub_recipes: added {', '.join(added)}")
    if removed:
        notices.append(
            f"sub_recipes: removed {', '.join(removed)} (kept in the merge backup; the key is "
            "agentteams-managed — change delegation via the brief/roster)"
        )
    if not added and not removed:
        notices.append("sub_recipes: entries regenerated from the current roster")
    return text, notices


__all__ = ["SUB_RECIPES_MANAGED_COMMENT", "is_goose_recipe", "reconcile_sub_recipes"]
