"""Opt-in API-impact categories for surface outside the default scope (#451 c).

A project opts in with a ``CATEGORIES`` file in the review ledger directory, read from the tree being checked (the
commit, or the index for a staged check). One category per line, ``#`` comments:

- ``constants``: public module-level ``UPPER_CASE`` assignments count as symbols (``module::NAME``): added, removed,
  or changed when the assigned value's source text changes. They then appear in the checklist and the docs status
  like any other symbol.
- ``generated [glob ...]``: generated files whose content changed are listed in ``generated_changed``. The files are
  those under the hook directories agentteams writes (:data:`DEFAULT_GENERATED`) plus any ``fnmatch`` globs on the
  line. A body-only edit to a generated hook (a fail-closed flag flipped, say) changes no signature, so without this
  category nothing reports it.

Both are off by default, so without the file the report is what it was. An unknown category is ignored with a warning
on standard error. A change to ``CATEGORIES`` itself is reported under ``review_baseline_changed``: switching a category
off changes what later checks report, so that commit is reviewed too.
"""
from __future__ import annotations

import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Any

from agentteams._api_surface import constants

#: The ledger file that opts a project into extra categories.
CATEGORIES_FILE = "CATEGORIES"
#: The categories ``CATEGORIES`` may name.
KNOWN = ("constants", "generated")
#: Generated-file globs that ``generated`` always covers: the hook directories agentteams writes.
DEFAULT_GENERATED = (".claude/hooks/*", ".github/hooks/*", ".grok/hooks/*", ".codex/hooks/*")


@dataclass
class Categories:
    """The categories a tree opts into.

    Attributes:
        constants: Whether public ``UPPER_CASE`` constants count as symbols.
        generated: Generated-file globs; empty when ``generated`` is off.
    """

    constants: bool = False
    generated: list[str] = field(default_factory=list)


def parse(text: str) -> Categories:
    """Parse a ``CATEGORIES`` file.

    Args:
        text: The file's text.

    Returns:
        The categories it turns on.

    Raises:
        TypeError: When ``text`` is not a string.
    """
    if not isinstance(text, str):
        raise TypeError(f"text must be str, got {type(text).__name__}")
    cats = Categories()
    for raw in text.splitlines():
        words = raw.split("#", 1)[0].split()
        if not words:
            continue
        if words[0] == "constants" and len(words) == 1:
            cats.constants = True
        elif words[0] == "generated":
            cats.generated = list(dict.fromkeys([*(cats.generated or DEFAULT_GENERATED), *words[1:]]))
        else:
            print(f"agentteams: {CATEGORIES_FILE}: unknown category line ignored: {raw.strip()}", file=sys.stderr)
    return cats


def apply(imp: Any, changes: Iterable[tuple[str, str, str]], *, cats: Categories,
          blob: Callable[[str, str], str | None], old_ref: str | None, new_ref: str,
          counts: Callable[[str], bool]) -> None:
    """Add the opted-in categories' entries to ``imp``.

    Args:
        imp: The :class:`agentteams.api_impact.Impact` being built.
        changes: ``(status, old_path, new_path)`` per changed file.
        cats: The categories in force (:func:`parse`).
        blob: ``(ref, path)`` → file text, ``None`` when unreadable (``ref`` ``""`` is the index).
        old_ref: The old side's commit, ``None`` when there is none.
        new_ref: The new side's commit, ``""`` for the index.
        counts: Whether a path counts for constants (not a test, not excluded).

    Returns:
        Nothing; ``imp`` is updated in place.

    Raises:
        TypeError: When ``cats`` is not a :class:`Categories`.
    """
    if not isinstance(cats, Categories):
        raise TypeError(f"cats must be Categories, got {type(cats).__name__}")
    for status, old, new in changes:
        if cats.generated and status in "MR" and any(fnmatchcase(new, g) for g in cats.generated):
            if status == "M" or blob(old_ref or "", old) != blob(new_ref, new):
                imp.generated_changed.append(new)
        if not cats.constants or not (new.endswith(".py") or old.endswith(".py")) or not (counts(new) or counts(old)):
            continue
        old_src = blob(old_ref, old) if old_ref and status != "A" else ""
        new_src = blob(new_ref, new) if status != "D" else ""
        before = constants(old_src) if old_src is not None else None
        after = constants(new_src) if new_src is not None else None
        if before is None or after is None:
            continue  # already reported as unparseable
        module = new if status != "D" else old
        imp.symbols_added += [f"{module}::{n}" for n in sorted(after.keys() - before.keys())]
        imp.symbols_removed += [f"{module}::{n}" for n in sorted(before.keys() - after.keys())]
        imp.symbols_changed += [f"{module}::{n}" for n in sorted(before.keys() & after.keys()) if before[n] != after[n]]
