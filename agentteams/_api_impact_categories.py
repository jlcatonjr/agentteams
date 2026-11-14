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
on standard error. A category counts when either side of the diff turns it on, so the commit that switches one off is
still checked with it, and a change to ``CATEGORIES`` itself is reported under ``review_baseline_changed``.

``generated`` lists every added, modified, renamed, type-changed or deleted file that matches a glob (a deleted hook
is listed by its old path), with no path left out: the globs are taken as given.
"""
from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import TYPE_CHECKING

from agentteams._api_surface import constants

if TYPE_CHECKING:
    from agentteams.api_impact import Impact

#: The ledger file that opts a project into extra categories.
CATEGORIES_FILE = "CATEGORIES"
#: The categories ``CATEGORIES`` may name.
KNOWN = ("constants", "generated")
#: Generated-file globs that ``generated`` always covers: the hook directories agentteams writes, and the gate policy
#: and carve-out registry under ``.agentteams/``. The rest of ``.agentteams/`` (the append-only proposal ledger, the
#: canonical export) changes on every run and is left out.
DEFAULT_GENERATED = (".claude/hooks/*", ".github/hooks/*", ".grok/hooks/*", ".codex/hooks/*",
                     ".agentteams/gate-policy.json", ".agentteams/carveouts.json")


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


def _blob(repo: Path, ref: str, path: str) -> str | None:
    """``<ref>:<path>`` as text (``ref`` ``""`` is the index), ``None`` when absent."""
    proc = subprocess.run(["git", "-C", str(repo), "cat-file", "-p", f"{ref}:{path}"], capture_output=True, check=False)
    return proc.stdout.decode("utf-8", errors="replace") if proc.returncode == 0 else None


def read(repo: Path, old_ref: str | None, new_ref: str, rel: str, *, touched: bool = True) -> Categories | None:
    """The categories in force for one diff: those either side's ``CATEGORIES`` turns on.

    Args:
        repo: Repository root.
        old_ref: The old side's commit, ``None`` when there is none.
        new_ref: The new side's commit, ``""`` for the index.
        rel: The ``CATEGORIES`` file's repo-relative path.
        touched: Whether the diff changes ``rel``. When it does not, both sides hold the same file, so only the new
            side is read (one git call per commit).

    Returns:
        The union, or ``None`` when neither side has the file.

    Raises:
        TypeError: When an argument has the wrong type.
    """
    if not isinstance(repo, Path):
        raise TypeError(f"repo must be a Path, got {type(repo).__name__}")
    if not isinstance(new_ref, str) or not isinstance(rel, str) or not isinstance(old_ref, (str, type(None))):
        raise TypeError("old_ref, new_ref and rel must be str (old_ref may be None)")
    old = _blob(repo, old_ref, rel) if old_ref and touched else None
    sides = [t for t in (old, _blob(repo, new_ref, rel)) if t]
    if not sides:
        return None
    parsed = [parse(t) for t in sides]
    return Categories(any(c.constants for c in parsed), list(dict.fromkeys(g for c in parsed for g in c.generated)))


def apply(imp: Impact, changes: Iterable[tuple[str, str, str]], *, cats: Categories, repo: Path,
          old_ref: str | None, new_ref: str,
          counts: Callable[[str], bool]) -> None:
    """Add the opted-in categories' entries to ``imp``.

    Args:
        imp: The :class:`agentteams.api_impact.Impact` being built.
        changes: ``(status, old_path, new_path)`` per changed file.
        cats: The categories in force (:func:`parse`).
        repo: Repository root.
        old_ref: The old side's commit, ``None`` when there is none.
        new_ref: The new side's commit, ``""`` for the index.
        counts: Whether a path counts for constants (not a test, not excluded).

    Returns:
        Nothing; ``imp`` is updated in place.

    Raises:
        TypeError: When an argument has the wrong type.
    """
    if not isinstance(cats, Categories):
        raise TypeError(f"cats must be Categories, got {type(cats).__name__}")
    if not isinstance(repo, Path) or not callable(counts) or not hasattr(imp, "generated_changed"):
        raise TypeError("imp must be an Impact, repo a Path and counts callable")
    for status, old, new in changes:
        path = old if status == "D" else new
        if any(fnmatchcase(path, g) or fnmatchcase(old, g) for g in cats.generated):
            imp.generated_changed.append(path)
        if not cats.constants or not (new.endswith(".py") or old.endswith(".py")) or not (counts(new) or counts(old)):
            continue
        old_src = _blob(repo, old_ref, old) if old_ref and status != "A" else ""
        new_src = _blob(repo, new_ref, new) if status != "D" else ""
        before = constants(old_src) if old_src is not None else None
        after = constants(new_src) if new_src is not None else None
        if before is None or after is None:
            continue  # already reported as unparseable
        module = new if status != "D" else old
        imp.symbols_added += [f"{module}::{n}" for n in sorted(after.keys() - before.keys())]
        imp.symbols_removed += [f"{module}::{n}" for n in sorted(before.keys() - after.keys())]
        imp.symbols_changed += [f"{module}::{n}" for n in sorted(before.keys() & after.keys()) if before[n] != after[n]]
