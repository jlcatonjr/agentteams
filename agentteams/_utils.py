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
