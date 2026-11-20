"""Fence and USER-EDITABLE line maps for fleet's content audit (carved from ``fleet.py`` under CH-07).

``_fence_and_useredit_lines`` maps which lines of a file are generated (inside a real ``AGENTTEAMS`` or
``AGENTTEAMS-BRIDGE`` fence) and which sit in a USER-EDITABLE region; ``_fence_imbalances`` is the
fence-pairing gate over a run's changed markdown files. Both are re-exported from ``agentteams.fleet``.
"""
from __future__ import annotations

import re
from pathlib import Path

from agentteams.fences import _BRIDGE_FENCE_BEGIN_RE as _BRIDGE_FENCE_RE

# REAL fence markers (HTML-comment form only), for the fence-balance gate and the USER-EDITABLE detector.
# A bare-substring match would miscount a marker quoted in prose or displayed as data.
_REAL_FENCE_BEGIN_RE = re.compile(r"<!--\s*AGENTTEAMS:BEGIN\s")
_REAL_FENCE_END_RE = re.compile(r"<!--\s*AGENTTEAMS:END\s")
_USER_EDIT_END = re.compile(r"END USER-EDITABLE|/USER-EDITABLE|END USER EDITABLE")
#: What opens a user region: a blockquote notice naming USER-EDITABLE (Project-Specific Notes' and the
#: orchestrator's "This section is USER-EDITABLE"), a heading naming it, or a BEGIN comment. Plain prose doesn't.
_USER_EDIT_MARKER = re.compile(r"^\s*>.*USER[- ]EDITABLE|^#{1,6}\s.*USER[- ]EDITABLE|BEGIN USER[- ]EDITABLE")
_BRIDGE_FENCE_END_RE = re.compile(r"<!--\s*AGENTTEAMS-BRIDGE:END\s")   # a bridge fence is generated text too


def _fence_and_useredit_lines(text: str) -> tuple[set[int], set[int]]:
    """Return the line numbers inside AGENTTEAMS fences, and those inside USER-EDITABLE regions.

    A user region starts only at a real marker: a blockquote notice naming USER-EDITABLE (the one that opens
    ``## Project-Specific Notes``, ``fences.PROJECT_NOTES_SECTION``, or the orchestrator's project-rules
    notice), a heading naming it, or an explicit ``BEGIN USER-EDITABLE`` comment. It runs to the next real
    fence BEGIN, an end marker, or the end of the file, so a user's own ``## `` heading inside it doesn't
    hide later deletions. Prose that merely mentions USER-EDITABLE (agent-updater's own instructions, for one)
    opens nothing. Fences are the real HTML-comment markers only (a quoted marker in prose is not a fence),
    ``AGENTTEAMS`` and ``AGENTTEAMS-BRIDGE`` alike (rc2 soak: a bridge entry's install hint after a user region
    read as a user deletion), and a fenced line is never user-editable.
    """
    fenced, useredit = set(), set()
    in_f = in_u = False
    resume_u = None   # the user region a bridge block interrupted, resumed at its END (IM @security, #554)
    for i, ln in enumerate(text.splitlines(), 1):
        if _BRIDGE_FENCE_RE.search(ln) and not in_f:
            resume_u, in_f, in_u = in_u, True, False
        elif _REAL_FENCE_BEGIN_RE.search(ln):
            in_f = True
            in_u = False
        if in_u and _USER_EDIT_END.search(ln):
            in_u = False
        if not in_f and _USER_EDIT_MARKER.search(ln) and not _USER_EDIT_END.search(ln):
            in_u = True
        if in_f:
            fenced.add(i)
        elif in_u:
            useredit.add(i)
        if _BRIDGE_FENCE_END_RE.search(ln) and resume_u is not None:
            in_f, in_u, resume_u = False, resume_u, None
        elif _REAL_FENCE_END_RE.search(ln):
            in_f = False
    return fenced, useredit


def _fence_imbalances(ws: Path, files: list[str]) -> list[str]:
    """Changed markdown files whose REAL fence markers do not pair up.

    Mirrors the researchteam autosync fence-pairing gate so a malformed-fence render is
    surfaced on the fleet path too (previously it had no such check and could be pushed
    undetected). Counts only real HTML-comment markers — a marker quoted in prose or shown
    as data is not a fence — for AGENTTEAMS and AGENTTEAMS-BRIDGE fences separately. Generated bridge report/inventory artifacts under
    ``references/bridges/`` are exempt: they legitimately DISPLAY marker syntax as table
    data (e.g. ``agent-inventory.md`` lists each agent's BEGIN marker), so a balance check
    over them is a false positive.

    Args:
        ws: Workspace root.
        files: Repo-relative changed paths from the diff.

    Returns:
        ``["<path> (<begin>/<end>)", ...]`` for each imbalanced file; empty when all pair.
    """
    out: list[str] = []
    for f in files:
        if not f.endswith(".md"):
            continue
        if f.startswith("references/bridges/") or "/references/bridges/" in f:
            continue
        try:
            text = (ws / f).read_text(encoding="utf-8")
        except OSError:
            continue
        b = len(_REAL_FENCE_BEGIN_RE.findall(text))
        e = len(_REAL_FENCE_END_RE.findall(text))
        bb, be = len(_BRIDGE_FENCE_RE.findall(text)), len(_BRIDGE_FENCE_END_RE.findall(text))
        if b != e or bb != be:   # an unclosed bridge BEGIN would fence the rest of the file (#554)
            out.append(f"{f} ({b}/{e})" + (f" (bridge {bb}/{be})" if bb != be else ""))
    return out
