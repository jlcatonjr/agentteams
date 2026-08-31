"""Cache-aware CLAUDE.md synthesis for the bridge (Phase 4).

When ``bridge:copilot-vscode-to-claude:cache-split`` is selected, the
bridge emits a richer ``CLAUDE.md`` than the default pointer-only entry.
It inlines the canonical ``.github/copilot-instructions.md`` content as
a *stable preamble* (cacheable across sessions) followed by an explicit
``SYSTEM_PROMPT_DYNAMIC_BOUNDARY`` marker and a small *dynamic section*
carrying per-build state (date, source SHA). The structure mirrors the
prompt-cache boundary pattern documented in the leaked Claude Code
source.

Semantic equivalence: the *bytes* of the stable preamble equal the
canonical ``copilot-instructions.md`` byte-for-byte. The dynamic section
is purely additive. Concatenating stable + boundary + dynamic produces
a strict superset of the source content — no information is lost.

This module is pure: it accepts strings, returns strings, and does not
read or write files. The bridge orchestrates I/O.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

DYNAMIC_BOUNDARY_MARKER = "<!-- SYSTEM_PROMPT_DYNAMIC_BOUNDARY -->"

_CACHE_HEADER = (
    "<!--\n"
    "  agentteams cache-aware CLAUDE.md (Phase 4)\n"
    "  Layout:\n"
    "    1. Stable preamble — canonical .github/copilot-instructions.md\n"
    "       (identical bytes; cache-friendly).\n"
    "    2. SYSTEM_PROMPT_DYNAMIC_BOUNDARY marker.\n"
    "    3. Dynamic section — per-build state (date, source SHA).\n"
    "  Reorder edits to the stable section by re-running --bridge-refresh.\n"
    "-->\n\n"
)

_DYNAMIC_HEADER = (
    "\n\n"
    + DYNAMIC_BOUNDARY_MARKER
    + "\n\n"
    + "## Session State (dynamic — regenerated per build)\n\n"
)


def _sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render_cache_split(
    *,
    copilot_instructions: str,
    source_relative_path: str = ".github/copilot-instructions.md",
    build_timestamp: str | None = None,
) -> str:
    """Render the full cache-split CLAUDE.md body.

    Parameters
    ----------
    copilot_instructions : str
        Verbatim content of the canonical copilot-instructions.md.
    source_relative_path : str
        Path the dynamic section uses to reference the source file.
    build_timestamp : str | None
        ISO-8601 timestamp to record in the dynamic section. Defaults to
        the current UTC time when omitted.
    """
    ts = build_timestamp or datetime.now(timezone.utc).isoformat()
    sha = _sha256_of(copilot_instructions)
    dynamic = (
        _DYNAMIC_HEADER
        + f"- Source: `{source_relative_path}`\n"
        + f"- Source SHA-256: `{sha}`\n"
        + f"- Build timestamp (UTC): `{ts}`\n"
        + "- Bridge: copilot-vscode → claude\n"
        + "\n"
        + "**Stable-preamble cache contract:** the bytes above the dynamic "
        + "boundary marker are taken verbatim from the canonical source. If "
        + "they change, re-run the bridge with `--bridge-refresh` to update "
        + "this file. Do not hand-edit the stable section.\n"
    )
    return _CACHE_HEADER + copilot_instructions.rstrip() + dynamic


def verify_equivalence(*, cache_split_text: str, original: str) -> bool:
    """Confirm the rendered cache-split file contains the original bytes verbatim.

    Used by Phase 7 regression. Returns True iff the rendered file
    contains ``original`` (stripped of trailing whitespace) as a contiguous
    substring, AND the dynamic boundary marker appears exactly once after it.
    """
    needle = original.rstrip()
    idx = cache_split_text.find(needle)
    if idx < 0:
        return False
    rest = cache_split_text[idx + len(needle):]
    return rest.count(DYNAMIC_BOUNDARY_MARKER) == 1


__all__ = [
    "DYNAMIC_BOUNDARY_MARKER",
    "render_cache_split",
    "verify_equivalence",
]
