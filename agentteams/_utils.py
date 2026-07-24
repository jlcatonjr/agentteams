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


def _split_yaml_front_matter(content: str) -> tuple[str | None, str]:
    """Split file content into YAML front matter and body using a line-by-line scan.

    Recognises the closing ``---`` delimiter only when it occupies an entire
    line at column 0 (after stripping a trailing carriage return). This prevents
    a false-positive split when ``---`` appears inside a YAML scalar value such as
    ``description: 'foo---bar'``.

    Shared implementation consumed by ``graph._split_yaml`` (MAP-17) and intended
    for use by ``interop._strip_framework_wrappers`` / ``_frontmatter_value``
    (MAP-06) once that fix is applied.

    Args:
        content: Full file text (any line endings).

    Returns:
        Tuple of (yaml_block, body).  ``yaml_block`` is ``None`` when the file has
        no ``---`` front-matter block.  When front matter is present, ``yaml_block``
        contains the lines between the opening and closing ``---`` delimiters joined
        by ``\\n`` (no leading or trailing ``\\n``); ``body`` contains every line
        after the closing ``---``, also joined by ``\\n``.
    """
    if not content.startswith("---"):
        return None, content
    lines = content.split("\n")
    # lines[0] is the opening "---" delimiter.
    # Scan from lines[1] onward for a line that is exactly "---" at column 0.
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r") == "---":
            yaml_block = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1:])
            return yaml_block, body
    return None, content
