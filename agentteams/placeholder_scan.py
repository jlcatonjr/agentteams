"""placeholder_scan.py — find the ``{MANUAL:X}`` tokens in a rendered team and complete its checklist (IM item 13b).

``SETUP-REQUIRED.md`` was built only from ``manual_required_placeholders``, which analyze collects from the brief, so
a token a template writes literally (``{MANUAL:PROJECT_REDTEAM_NOTES}``, ``{MANUAL:ADJACENT_REPO_PATH}``) never
reached it. The audit and the enrich scan also skipped backticked tokens, which hid real placeholders written in
inline code. This module scans the emitted text itself, with one rule that every scanner shares: a token counts
unless its name is one of the :data:`INSTRUCTIONAL_TOKENS`, the generic names templates use when they explain
placeholders.

It never refuses: the checklist is complete, and the emit summary counts it. Refusing on open placeholders is a later
step (warn, then fail, as with the brief schema).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from agentteams import render

#: ``{MANUAL:NAME}`` — the same token syntax ``render`` leaves for a human to fill.
MANUAL_TOKEN_RE = re.compile(r"\{MANUAL:([A-Z][A-Z0-9_]*)\}")

#: The names templates use to *explain* placeholders, never as a slot to fill. ``tests/test_placeholder_completeness``
#: pins where each one appears, so a new instructional example, or a real placeholder given one of these names,
#: fails a test rather than silently joining or leaving the checklist.
INSTRUCTIONAL_TOKENS: frozenset[str] = frozenset({"TOKEN", "UPPER_SNAKE_CASE", "PLACEHOLDER_NAME"})

SETUP_REQUIRED_NAME = "SETUP-REQUIRED.md"
#: Manifest key for the scanned entries. The leading ``_`` keeps it out of the drift fingerprint.
SCANNED_KEY = "_scanned_manual_placeholders"
#: Longest context line copied into the checklist.
_MAX_CONTEXT = 120


def scan_manual_tokens(files: Iterable[tuple[str, str]]) -> list[dict[str, Any]]:
    """Return each open ``{MANUAL:X}`` in the emitted *files*, first occurrence per token per file.

    Args:
        files: ``(relative_path, content)`` pairs, as the render pipeline builds them.

    Returns:
        ``{"placeholder", "agent_file", "line", "context"}`` dicts, sorted by placeholder then file. ``SETUP-REQUIRED.md``
        and the :data:`INSTRUCTIONAL_TOKENS` are skipped; nothing else is (no backtick or code-fence rule).

    Raises:
        TypeError: A content item is not a ``str``.
    """
    found: list[dict[str, Any]] = []
    for rel_path, content in files:
        if not isinstance(content, str):
            raise TypeError(f"{rel_path}: content is {type(content).__name__}, not str")
        if Path(rel_path).name == SETUP_REQUIRED_NAME:
            continue
        seen: set[str] = set()
        for line_no, line in enumerate(content.splitlines(), start=1):
            for match in MANUAL_TOKEN_RE.finditer(line):
                token = match.group(1)
                if token in INSTRUCTIONAL_TOKENS or token in seen:
                    continue
                seen.add(token)
                context = line.strip()
                if len(context) > _MAX_CONTEXT:
                    context = context[: _MAX_CONTEXT - 1] + "…"
                found.append({"placeholder": token, "agent_file": rel_path, "line": line_no, "context": context})
    return sorted(found, key=lambda item: (item["placeholder"], item["agent_file"], item["line"]))


def open_count(manifest: dict[str, Any]) -> int:
    """Return how many placeholders the checklist lists: the brief's plus the scanned ones.

    Args:
        manifest: The team manifest, after :func:`complete_setup_required` ran.

    Returns:
        ``len(manual_required_placeholders) + len(_scanned_manual_placeholders)``; the scanned entries never repeat a
        listed name.

    Raises:
        Nothing.
    """
    return len(manifest.get("manual_required_placeholders", [])) + len(manifest.get(SCANNED_KEY, []))


def complete_setup_required(
    manifest: dict[str, Any],
    files: list[tuple[str, str]],
    *,
    effective: Callable[[str, str], str] | None = None,
) -> list[tuple[str, str]]:
    """Record every emitted token the checklist lacks, and re-render ``SETUP-REQUIRED.md`` in *files*.

    A token is new when no ``manual_required_placeholders`` entry has its name (the checklist tells the operator to
    search every generated file for it, so one entry per name is enough). New entries go under
    :data:`SCANNED_KEY`, in sorted order, replacing what an earlier call stored. The key starts with ``_``, so the
    drift fingerprint skips it: ``--check`` fingerprints the manifest before render and generate after it, and the two
    must agree. ``manual_required_placeholders`` itself is not changed.

    Args:
        manifest: The team manifest; :data:`SCANNED_KEY` is set in place.
        files: The rendered ``(relative_path, content)`` pairs.
        effective: Maps ``(path, rendered)`` to the content the run will actually write. On ``--update`` this carries
            forward the values an operator filled in, so a filled token leaves the checklist. None scans *files* as is.

    Returns:
        *files*, with the ``SETUP-REQUIRED.md`` entry re-rendered when the checklist gained an item (else unchanged).

    Raises:
        TypeError: A content item is not a ``str`` (raised by :func:`scan_manual_tokens`).
    """
    listed = manifest.get("manual_required_placeholders", [])
    names = {item.get("placeholder") for item in listed}
    scanned_files = files if effective is None else [(path, effective(path, body)) for path, body in files]
    scanned: list[dict[str, Any]] = []
    for item in scan_manual_tokens(scanned_files):
        if item["placeholder"] in names:
            continue
        names.add(item["placeholder"])
        scanned.append({
            "placeholder": item["placeholder"],
            "agent_file": item["agent_file"],
            "context": f"Written in the template (line {item['line']}): {item['context']}",
        })
    manifest[SCANNED_KEY] = scanned
    if not scanned:
        return files
    content = render.render_setup_required({**manifest, "manual_required_placeholders": [*listed, *scanned]})
    return [(path, content if Path(path).name == SETUP_REQUIRED_NAME else body) for path, body in files]
