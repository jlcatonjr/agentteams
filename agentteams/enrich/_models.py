"""_models.py — Data structures and shared regex patterns for enrich."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class DefaultFinding:
    """A single default-value (underdeveloped template) finding."""

    file: str
    """Relative path to the agent file."""

    category: str
    """MANUAL_PLACEHOLDER | GENERIC_SECTION | TOOL_METADATA | MISSING_TOOL_REF"""

    token: str
    """The placeholder name or section label, e.g. COMPONENT_SPEC or STYLE_REFERENCE_PATH."""

    line_no: int
    """1-based line number of the finding in the agent file."""

    section: str
    """Nearest ## section heading above the finding (or '' if in front matter)."""

    context_snippet: str
    """1-2 lines of surrounding context."""

    auto_suggestion: str
    """Value inferred from project context (empty string if unknown)."""

    status: str = "pending"
    """pending | auto_filled | ai_filled | skipped"""


#: Matches any {MANUAL:TOKEN} token
_MANUAL_RE = re.compile(r"\{MANUAL:([A-Z][A-Z0-9_]*)\}")

#: Matches a markdown ## section heading
_SECTION_RE = re.compile(r"^#{1,3}\s+(.+)", re.MULTILINE)

#: Heading line detector (h1-h3)
_HEADING_LINE_RE = re.compile(r"^#{1,3}\s+")
