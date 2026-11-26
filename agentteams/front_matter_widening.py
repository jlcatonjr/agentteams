"""Whether taking a template's front-matter value widens a capability grant (C-3), read strictly.

Used by ``--reconcile-apply`` (:mod:`agentteams.front_matter_reconcile`) to decide which divergences apply on the
flag alone and which need a ``reconcile-apply-widen`` clearance. The merge engine keeps its own, more lenient
reader (``front_matter_merge._parse_capability_list``); this one accepts a list only when every item in it is
well formed, because an item a reader silently drops is a grant nobody reviewed.
"""

from __future__ import annotations

import re

from agentteams.front_matter_merge import CAPABILITY_FRONT_MATTER_KEYS, _parse_capability_list

#: One ``- item`` line of a block list: an optional matching quote pair, no comment, no space inside.
_BLOCK_ITEM_RE = re.compile(r"""[ \t]+-[ \t]*(['"]?)([^'"\s#]+)\1[ \t]*""")
#: One quoted flow-list item: a matching quote pair around text with no quote, bracket or comma, not padded.
_FLOW_ITEM_RE = re.compile(r"""(['"])([^'"\[\],\s](?:[^'"\[\],]*[^'"\[\],\s])?)\1""")


def parse_items(key: str, value: str | None) -> tuple[str, ...] | None:
    """Return the items of a capability list value in order, or None unless every item is well formed.

    Three shapes: a quoted flow list (``['read', 'search']``), a bare comma list (``Read, Grep``) and a block
    list (``- item`` lines). A list with any item outside its shape (an unquoted item in a flow list, a trailing
    comma, a nested bracket, an empty item, a comment) is None, and so is an empty list, which some frameworks
    read as every tool.

    Args:
        key: The front-matter key (a scalar key such as ``model`` is never a list).
        value: The value as the reconcile map holds it (a block value starts with a newline), or None.

    Returns:
        The items, or None.
    """
    if value is None:
        return None
    if value.startswith("\n"):
        lines = [line for line in value.splitlines() if line.strip()]
        matches = [_BLOCK_ITEM_RE.fullmatch(line) for line in lines]
        return tuple(m.group(2) for m in matches) if lines and all(matches) else None
    text = value.strip()
    if "[" in text or "]" in text:
        flow = re.fullmatch(r"\[(.*)\]", text, re.S)
        if flow is None:
            return None
        matches = [_FLOW_ITEM_RE.fullmatch(part.strip()) for part in flow.group(1).split(",")]
        return tuple(m.group(2) for m in matches) if all(matches) else None
    if _parse_capability_list(text, key=key) is None:
        return None
    return tuple(part.strip() for part in text.split(","))


def classify(key: str, deployed: str | None, template: str) -> str:
    """Return ``"safe"`` or ``"widening"`` for replacing *deployed* with *template* under *key*.

    Safe: a non-capability key, or a capability list whose template items are a subset of the deployed ones
    (for the ``disallowedTools`` deny list, a superset). Everything else among capability keys widens: more
    items, a key absent on disk, any ``model`` change, and any value either side cannot read strictly
    (:func:`parse_items`), ``mcpServers`` and ``hooks`` blocks among them. So the test fails closed.

    Args:
        key: The front-matter key.
        deployed: The deployed value, or None when the key is absent on disk.
        template: The template's value.

    Returns:
        ``"safe"`` or ``"widening"``.
    """
    if key not in CAPABILITY_FRONT_MATTER_KEYS:
        return "safe"
    new, old = parse_items(key, template), parse_items(key, deployed)
    if new is None or old is None:
        return "widening"
    narrows = set(new) >= set(old) if key == "disallowedTools" else set(new) <= set(old)
    return "safe" if narrows else "widening"


def safe_part(key: str, deployed: str | None, template: str) -> str | None:
    """Return the narrowing part of a widening list change, written in the template's shape, or None.

    For an allow list the safe part is the deployed items the template keeps (their intersection, in template
    order): what the template removes goes at once, and only what it adds waits on a clearance. For the
    ``disallowedTools`` deny list it is the mirror, the union (template items, then the deployed ones it drops):
    new denials apply at once, and only a dropped denial waits. None when either side is not a strictly read list
    (:func:`parse_items`), when the safe part changes nothing, or when an allow list would be left empty: an empty
    ``tools`` list is read as every tool by Grok and Goose (``available_tools: []``), and Claude treats a missing
    grant as every tool, so an empty intersection refuses the whole key.

    Args:
        key: The front-matter key.
        deployed: The deployed value, or None when the key is absent on disk.
        template: The template's value.

    Returns:
        The value text to write now, or None.
    """
    if key not in CAPABILITY_FRONT_MATTER_KEYS:
        return None
    new, old = parse_items(key, template), parse_items(key, deployed)
    if new is None or old is None:
        return None
    if key == "disallowedTools":
        items = [*dict.fromkeys(new), *(i for i in dict.fromkeys(old) if i not in set(new))]
    else:
        items = [i for i in dict.fromkeys(new) if i in set(old)]
    if not items or set(items) == set(old):
        return None
    return _render_like(template, items)


def _render_like(template: str, items: list[str]) -> str:
    """Write *items* in the shape of *template*: a block list, a quoted flow list or a comma list."""
    if template.startswith("\n"):
        first = next(line for line in template.splitlines() if line.strip())
        indent = first[: len(first) - len(first.lstrip())]
        return "".join(f"\n{indent}- {item}" for item in items)
    text = template.strip()
    if text.startswith("["):
        quote = text[1:].lstrip()[0]
        return "[" + ", ".join(f"{quote}{item}{quote}" for item in items) + "]"
    return ", ".join(items)
