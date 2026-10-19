"""frozen_surfaces.py — the paths a frozen surface owns, and the unfreeze vocabulary (Rule 15).

The shared contract with researchteam (frozen-surfaces design note, points 2 and 4) in one stdlib-only module, so
:mod:`agentteams.cli.effect_classifier` can derive an unfreeze without importing the framework registry into the trust
base. Its keys are the registry's framework ids; ``tests/test_effect_classifier_unfreeze.py`` pins them equal.

**Which frozen set a caller passes.** The target axis needs the frozen set in force *before* a row's change, so a freeze
row stays an ordinary clearance. That "before" set must come from the recorded freeze state (the committed brief at
the last reviewed release lock, or the freeze decision rows), never from the working brief, which an agent can edit:
a lowered baseline would hide an unfreeze. A surface id present before and absent after is itself an unfreeze, even
when no row names it (:func:`unfrozen_ids`).
"""

from __future__ import annotations

import posixpath
import re

#: The paths each frozen-surface id owns (contract point 4). A path ending in ``/`` owns everything under it; any
#: other path owns that file only. ``AGENTS.md`` is owned by ``codex``, ``goose`` and ``agents-md`` alike, because
#: over-freezing only narrows writes.
FROZEN_SURFACE_PATHS: dict[str, tuple[str, ...]] = {
    "codex": (".codex/", ".agents/skills/", "AGENTS.md"),
    "goose": (".goose/", "AGENTS.md", ".goosehints", "scripts/goose-run-resilient.py",
              "scripts/goose-openrouter-route-proxy.py", "scripts/goose-coordination-mcp.py"),
    "agents-md": ("AGENTS.md",),
    "claude": (".claude/agents/",),
    "copilot-vscode": (".github/agents/",),
    "copilot-cli": (".github/agents/",),
    "grok": (".grok/",),
}

#: Words that name an unfreeze, matched at a word start: ``unfreeze``/``--unfreeze``/``unfrozen`` and the hyphen,
#: underscore and space spellings, and ``thaw``. Shared with the directive denylist.
UNFREEZE_TOKENS: tuple[str, ...] = ("unfreez", "unfroz", "un-freez", "un-froz", "un_freez", "un_froz",
                                    "un freez", "un froz", "thaw")
_UNFREEZE_RE = re.compile(r"(?<![a-z])(?:" + "|".join(re.escape(t) for t in UNFREEZE_TOKENS) + ")")


def _validated(surfaces: object) -> list[str]:
    """Return ``surfaces`` as a list of distinct known ids (contract point 2), or raise.

    Raises:
        ValueError: ``surfaces`` is not a list of distinct known surface ids.
    """
    if surfaces is None:
        return []
    if not isinstance(surfaces, list) or not all(isinstance(s, str) for s in surfaces):
        raise ValueError("frozen_surfaces must be a list of surface ids")
    if len(set(surfaces)) != len(surfaces):
        raise ValueError(f"frozen_surfaces lists a surface twice: {surfaces!r}")
    unknown = [s for s in surfaces if s not in FROZEN_SURFACE_PATHS]
    if unknown:
        raise ValueError(
            f"frozen_surfaces names unknown surface id(s) {unknown!r}; known: {sorted(FROZEN_SURFACE_PATHS)}")
    return surfaces


def frozen_owned_paths(surfaces: object) -> tuple[str, ...]:
    """Return the paths owned by a ``frozen_surfaces`` list, for the effect classifier's target axis.

    An unknown id, a non-list value or a duplicate is refused, never ignored. An absent key (``None``) or ``[]``
    freezes nothing.

    Args:
        surfaces: The ``frozen_surfaces`` value of the recorded freeze state (see the module docstring).

    Returns:
        The owned paths, de-duplicated, in a stable order.

    Raises:
        ValueError: ``surfaces`` is not a list of distinct known surface ids.
    """
    return tuple(dict.fromkeys(p for s in _validated(surfaces) for p in FROZEN_SURFACE_PATHS[s]))


def unfrozen_ids(before: object, after: object) -> tuple[str, ...]:
    """Return the surface ids frozen in ``before`` and not in ``after``: each is an unfreeze, named by a row or not.

    Args:
        before: The recorded freeze state's ``frozen_surfaces``.
        after: The proposed ``frozen_surfaces``.

    Returns:
        The dropped ids, in ``before``'s order.

    Raises:
        ValueError: Either value is not a list of distinct known surface ids.
    """
    after_ids = set(_validated(after))
    return tuple(s for s in _validated(before) if s not in after_ids)


def names_unfreeze(text: str) -> bool:
    """Return True when ``text`` names an unfreeze (any :data:`UNFREEZE_TOKENS` spelling, at a word start).

    Args:
        text: A scope, action or decision text.

    Returns:
        Whether the text names an unfreeze.
    """
    return _UNFREEZE_RE.search((text or "").lower()) is not None


def _norm_rel(path: str) -> str:
    """Return ``path`` as a normalised repository-relative path: ``/``-separated, ``..`` resolved, no leading
    ``./`` or ``/``. A path that climbs out of the repository keeps its leading ``..``."""
    p = (path or "").strip().replace("\\", "/").lstrip("/")
    p = posixpath.normpath(p) if p else "."
    return "" if p == "." else p


def touches_frozen(target: str, frozen_paths: tuple[str, ...]) -> bool:
    """Return True when a write ``target`` is, is under, or contains one of ``frozen_paths``.

    Case-insensitive, as macOS is. A target that contains a frozen path (``.``, the repository root, or ``.agents/``
    when ``.agents/skills/`` is frozen) authorizes writes into it, so it counts.

    Args:
        target: A row's write-target path.
        frozen_paths: The owned paths of the frozen surfaces (:func:`frozen_owned_paths`).

    Returns:
        Whether the target touches a frozen surface.
    """
    t = _norm_rel(target).lower()
    for owned in frozen_paths:
        o = _norm_rel(owned).lower()
        if t == o or not t or o.startswith(t + "/"):  # the path itself, the root, or a directory containing it
            return True
        if owned.endswith("/") and t.startswith(o + "/"):  # under a frozen directory
            return True
    return False


__all__ = [
    "FROZEN_SURFACE_PATHS",
    "UNFREEZE_TOKENS",
    "frozen_owned_paths",
    "names_unfreeze",
    "touches_frozen",
    "unfrozen_ids",
]
