"""stale_surface_paths.py — notice unfenced lines that name another surface's agents directory (F10, 1.3.0).

A ``--update --merge`` replaces fence bodies and keeps every unfenced line as it is, because unfenced text is the
project's. That is right for text a project wrote, and it leaves behind text an *older render* wrote outside the
fences that the current template no longer emits. The case that prompted this (IM findings #13/#14): renders before
the per-surface conflict-log path fix wrote ``.github/agents/references/conflict-log.csv`` into the Codex and Claude
teams, and those lines survived every later merge, silently, alongside the corrected fenced text.

:func:`stale_lines` finds such residue in one file: a line outside every fence and outside the user-editable regions
(``## Project-Specific Notes`` / ``## Project-Specific Rules``) that names another registered surface's agents
directory, and that the new render does not contain. It is advisory only: nothing is refused, rewritten or dropped.

Stdlib only.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path, PurePosixPath

from agentteams.unfenced import _FENCE_BEGIN_RE, _FENCE_END_RE
from agentteams.user_regions import HEADINGS, find_region

#: How many stale lines one notice quotes; the rest are counted.
MAX_QUOTED = 3
#: How much of each quoted line a notice shows.
MAX_LINE_CHARS = 120



@lru_cache(maxsize=1)
def surface_dirs() -> tuple[str, ...]:
    """Every registered framework's agents directory, project-relative and POSIX (e.g. ``.codex/agents``).

    Returns:
        The distinct directories, longest first (so ``.agentteams-wasm/agents`` is matched before a shorter one).

    Raises:
        Nothing.
    """
    from agentteams.frameworks.registry import FRAMEWORK_IDS, agents_dir_for

    root = Path("/__root__")
    dirs = {agents_dir_for(f, root).relative_to(root).as_posix() for f in FRAMEWORK_IDS}
    return tuple(sorted(dirs, key=lambda d: (-len(d), d)))


def own_surface(output_dir: Path) -> str | None:
    """The agents directory *output_dir* is, or None when it is none of the registered ones.

    Args:
        output_dir: The directory being rendered into.

    Returns:
        The matching entry of :func:`surface_dirs`, or None (then :func:`stale_lines` reports nothing).

    Raises:
        Nothing.
    """
    parts = PurePosixPath(Path(output_dir).as_posix())
    for d in surface_dirs():
        n = len(PurePosixPath(d).parts)
        if parts.parts[-n:] == PurePosixPath(d).parts:
            return d
    return None


def _masked_spans(text: str) -> list[tuple[int, int]]:
    """Character spans of every fenced block (``unfenced``'s markers) and every user region (``user_regions``)."""
    spans: list[tuple[int, int]] = []
    for begin in _FENCE_BEGIN_RE.finditer(text):
        end = next((e for e in _FENCE_END_RE.finditer(text, begin.end()) if e.group("sid") == begin.group("sid")), None)
        spans.append((begin.start(), end.end() if end else len(text)))   # an unclosed fence masks to the end
    for heading in HEADINGS:
        region = find_region(text, heading)
        if region:
            spans.append((region[0], region[1]))
    return spans


def _project_owned_lines(text: str) -> list[tuple[int, str]]:
    """``(line number, line)`` for each line outside every fence and every user-editable region."""
    spans = _masked_spans(text)
    out: list[tuple[int, str]] = []
    offset = 0
    for number, line in enumerate(text.splitlines(keepends=True), start=1):
        if not any(start <= offset < end for start, end in spans):
            out.append((number, line.rstrip("\r\n")))
        offset += len(line)
    return out


def stale_lines(existing: str, fresh: str, own: str | None) -> list[tuple[int, str, str]]:
    """Unfenced lines of *existing* that name another surface's agents dir and are not in *fresh*.

    Args:
        existing: The file on disk before the merge.
        fresh: The new render of the same file.
        own: This file's surface (:func:`own_surface`); None reports nothing.

    Returns:
        ``(line number, other surface dir, line)`` for each stale line, in file order.

    Raises:
        Nothing.
    """
    if not own:
        return []
    others = [d for d in surface_dirs() if d != own and not own.startswith(d + "/") and not d.startswith(own + "/")]
    fresh_lines = {line.strip() for line in fresh.splitlines()}
    found: list[tuple[int, str, str]] = []
    for number, line in _project_owned_lines(existing):
        stripped = line.strip()
        if not stripped or stripped in fresh_lines:
            continue
        hit = next((d for d in others if re.search(rf"(?<![\w-])(?:\./)?{re.escape(d)}(?=[/`\s),;:]|\.(?:\s|$)|$)", line)),
                   None)
        if hit:
            found.append((number, hit, stripped))
    return found


def notice(rel_path: str, existing: str, fresh: str, own: str | None) -> str | None:
    """One merge notice for *rel_path*'s stale cross-surface lines, or None.

    Args:
        rel_path: The file's path relative to the output directory.
        existing: The file on disk before the merge.
        fresh: The new render.
        own: This file's surface (:func:`own_surface`).

    Returns:
        The notice text, or None when there is nothing stale.

    Raises:
        Nothing.
    """
    found = stale_lines(existing, fresh, own)
    if not found:
        return None
    quoted = "; ".join(f"line {n} names {d}: {line[:MAX_LINE_CHARS]!r}" for n, d, line in found[:MAX_QUOTED])
    more = f" (+{len(found) - MAX_QUOTED} more)" if len(found) > MAX_QUOTED else ""
    return (f"{rel_path}: {len(found)} unfenced line(s) name another surface's agents directory and the new render "
            f"no longer has them: {quoted}{more}. A merge keeps unfenced text, so this is probably left from an older "
            f"render; fix it with `--update --overwrite` (it keeps the Project-Specific Notes/Rules) or by hand.")


__all__ = ["MAX_LINE_CHARS", "MAX_QUOTED", "notice", "own_surface", "stale_lines", "surface_dirs"]
