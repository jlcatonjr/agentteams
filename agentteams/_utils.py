"""
_utils.py — Shared internal utilities for the agentteams pipeline.

Private module. Not part of the public API.
"""

from __future__ import annotations

import re


def _slugify(text: str) -> str:
    """Convert a string to a lowercase hyphen-separated slug."""
    slug = re.sub(r"[^a-zA-Z0-9\s\-]", "", text)
    slug = re.sub(r"\s+", "-", slug.strip())
    return slug.lower()


def _slugify_tool_name(name: str) -> str:
    """Slugify a tool name, treating `@`/`/` as word separators rather than
    deleting them — plain `_slugify` silently concatenates adjacent words for
    inputs like npm-scoped packages (`@scope/name` -> `scopename`), which can
    collide with an unrelated, differently-named package. Produces identical
    output to `_slugify` for any name that doesn't contain `@` or `/`.
    """
    normalized = re.sub(r"[@/]+", "-", name)
    slug = _slugify(normalized)
    return re.sub(r"-+", "-", slug).strip("-")
