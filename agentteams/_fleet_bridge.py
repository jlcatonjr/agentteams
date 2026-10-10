"""_fleet_bridge.py — which source framework a fleet bridge target refreshes from (2026-10-09).

A bridge's entry file records its source framework inside the ``AGENTTEAMS-BRIDGE`` fence ("Use source framework
`goose` as canonical agent infrastructure."). Fleet used to bridge from ``.github/agents`` regardless, which
rewrote a goose-canonical repo's entry to copilot-vscode (found by the 2026-10-09 pilot content audit,
securityInfrastructure). Fleet now reads the recorded source and bridges from that framework's directory, or
skips the target when it can't. Stdlib only.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentteams.fences import _BRIDGE_FENCE_BEGIN_RE

#: Bridge source framework -> the project-relative directory fleet bridges from.
BRIDGE_SOURCE_DIRS: dict[str, str] = {
    "copilot-vscode": ".github/agents",
    "goose": ".goose/recipes",
    "claude": ".claude/agents",
}
#: The bridge entry files each bridge target writes (``references/bridge-refresh-safety.md``).
_BRIDGE_ENTRY_FILES: dict[str, tuple[str, ...]] = {
    "claude-bridge": ("CLAUDE.md",),
    "goose-bridge": ("AGENTS.md", ".goosehints"),
}
_BRIDGE_SOURCE_RE = re.compile(r"Use source framework `([A-Za-z0-9_-]+)`")
_BRIDGE_FENCE_END = "AGENTTEAMS-BRIDGE:END"
#: Returned for an entry file that exists but can't be read; not a framework, so fleet skips the target.
UNREADABLE = "<unreadable>"


def recorded_bridge_source(ws: Path, target: str) -> str | None:
    """Return the source framework an existing bridge entry records, or None when there is none.

    Only text inside an ``AGENTTEAMS-BRIDGE`` fence counts, so prose that quotes the sentence elsewhere
    can't redirect the bridge.

    Args:
        ws: The workspace.
        target: ``claude-bridge`` or ``goose-bridge``.

    Returns:
        The recorded framework id; :data:`UNREADABLE` when an entry file exists but can't be read; or None
        when no entry records a source.

    Raises:
        Nothing.
    """
    for name in _BRIDGE_ENTRY_FILES.get(target, ()):
        entry = ws / name
        if not entry.exists():
            continue
        try:
            text = entry.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # An entry that exists but can't be read must not default to .github/agents: the caller
            # treats this unknown source as a SKIP.
            return UNREADABLE
        inside = False
        for line in text.splitlines():
            if _BRIDGE_FENCE_BEGIN_RE.search(line):
                inside = True
            elif _BRIDGE_FENCE_END in line:
                inside = False
            elif inside:
                m = _BRIDGE_SOURCE_RE.search(line)
                if m:
                    return m.group(1)
    return None
