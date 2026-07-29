"""living_doc.py — living-document policy conformance over emitted agent files.

The policy is constitutional in every generated team: no dated audit snapshots,
no resolved-issue archaeology, no dated fix logs, and no hardcoded volatile
state in agent prose. Until now the module emitted the rule and shipped no
instrument for it.

Scope was set by measurement, not assumption. Over the module's own emitted
teams a date scan returns 65 signals across all files and one true violation;
restricted to unfenced ``.agent.md`` prose it returns exactly that violation and
nothing else. Reference files and fenced regions carry legitimate dated data —
CVE feeds, generation stamps — and fenced content is re-rendered on every
update, so it cannot go stale in the way the policy forbids.

The policy's fourth prohibition is deliberately not checked here. Whether a
value is *volatile* is a claim about the future, and no scan of a snapshot
settles it; that one stays with the agent's judgment.

Carved out of ``audit.py`` to keep that module under the CH-07 line ceiling.
"""

from __future__ import annotations

import re

__all__ = ["unfenced_spans", "find_dated_prose", "DATE_RE"]

FENCE_RE = re.compile(r"<!-- AGENTTEAMS:(BEGIN|END)[^>]*-->")
DATE_RE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")


def unfenced_spans(text: str) -> list[tuple[int, int]]:
    """Return (start, end) spans of *text* lying outside AGENTTEAMS fences.

    Args:
        text: Full file content.

    Returns:
        Spans outside any fence, in document order.
    """
    spans: list[tuple[int, int]] = []
    cursor = 0
    for m in FENCE_RE.finditer(text):
        if m.group(1) == "BEGIN":
            spans.append((cursor, m.start()))
        else:
            cursor = m.end()
    spans.append((cursor, len(text)))
    return spans


def find_dated_prose(file_map: dict[str, str]) -> list[tuple[str, str, str]]:
    """Find dated content in unfenced agent prose.

    Args:
        file_map: Rendered file content keyed by relative path.

    Returns:
        Tuples of (path, date, containing line), at most one per date per file.
    """
    hits: list[tuple[str, str, str]] = []
    for path, content in sorted(file_map.items()):
        if not path.endswith(".agent.md"):
            continue
        if "references/" in path or ".reference." in path:
            continue
        spans = unfenced_spans(content)
        seen: set[str] = set()
        for m in DATE_RE.finditer(content):
            if not any(a <= m.start() < b for a, b in spans):
                continue
            stamp = m.group(0)
            if stamp in seen:
                continue
            seen.add(stamp)
            start = content.rfind("\n", 0, m.start()) + 1
            end = content.find("\n", m.end())
            line = content[start : end if end > 0 else len(content)]
            hits.append((path, stamp, line.strip()))
    return hits
