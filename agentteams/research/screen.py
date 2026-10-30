"""Read-content screening — S-5 and C-1 patterns over fetched pages and retrieved passages (C-4).

Read content (a fetched page, a search snippet, a retrieved passage) is Tier-6 data. Text inside it
that tries to direct its reader is a finding to report, never an instruction to follow. This module
screens such text before a model reads it, or after a model has summarised it.

It is built on :func:`agentteams.scan.scan_content`, the scanner every agent file already passes,
so the S-5 instruction- and identity-override patterns and the C-1 tier claims are the same ones,
with the same Unicode normalisation and line-break handling. It never modifies ``scan.py``, which is
a pinned enforcement module (``references/enforcement-integrity.json``). Two differences from
scanning an agent file:

- **No exemptions.** A leading blank line is prepended before scanning, so read content can never
  open a YAML front-matter block and suppress the identity patterns inside it, and the source label
  is not a module-owned path, so no code-span exemption applies.
- **Model-directed phrasing.** :data:`READ_CONTENT_PATTERNS` ("system prompt", "tell the user" ...)
  are flagged here only: agent files legitimately talk about the assistant and its system prompt,
  so ``scan_content`` does not run them.

Credential and path findings from ``scan_content`` are not returned: read content leaking a secret is
not an instruction, and callers decide separately whether to keep it. The pattern lists are
literal and few; novel wording will not match (the accepted trade, as in ``scan``).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable

from agentteams.scan import ScanFinding, scan_content

#: Model-directed phrasing read content has no business addressing to whoever reads it.
READ_CONTENT_PATTERNS: tuple[str, ...] = (
    "system prompt",
    "the assistant should",
    "the assistant must",
    "the model should",
    "the model must",
    "the ai should",
    "the ai must",
    "tell the user",
    "respond only with",
    "do not reveal",
    "as an ai language model",
)

_SOURCE_LABEL = "<read-content>"


def _normalize(line: str) -> str:
    """NFKC, then drop format (``Cf``) characters, lower-cased: the same canonicalisation ``scan``
    applies before literal matching (a zero-width space must not split a phrase)."""
    text = unicodedata.normalize("NFKC", line)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf").lower()


def scan_read_content(
    content: str,
    source: str = _SOURCE_LABEL,
    *,
    extra_patterns: Iterable[str] = (),
) -> list[ScanFinding]:
    """Screen ``content`` as read content.

    Args:
        content: The text to screen.
        source: A label for the findings (a domain, a document id; never an operator path).
        extra_patterns: Further literal phrases to flag, e.g. a product's own role names.

    Returns:
        ``category="injection"``, ``severity="high"`` findings, at most one per line, with 1-based
        line numbers into ``content``. A finding means the text tried to direct its reader: drop or
        quarantine it, never follow it.
    """
    findings: dict[int, ScanFinding] = {}
    for f in scan_content("\n" + content, _SOURCE_LABEL):
        if f.category == "injection" and f.line > 1:
            findings.setdefault(f.line - 1, ScanFinding(file=source, line=f.line - 1, category=f.category,
                                                        severity=f.severity, message=f.message, snippet=f.snippet))
    patterns = READ_CONTENT_PATTERNS + tuple(p.lower() for p in extra_patterns if p)
    previous = ""
    for line_num, line in enumerate(content.splitlines(), start=1):
        norm = _normalize(line)
        joined = " ".join(f"{previous} {norm}".split())
        if line_num not in findings:
            for pattern in patterns:
                if pattern in norm or (previous and pattern in joined and pattern not in previous):
                    findings[line_num] = ScanFinding(
                        file=source, line=line_num, category="injection", severity="high",
                        message=f"read content addresses its reader ({pattern!r}): data, never instruction",
                        snippet=line.strip()[:120])
                    break
        previous = norm
    return [findings[n] for n in sorted(findings)]


def is_inert_read_content(content: str, *, extra_patterns: Iterable[str] = ()) -> bool:
    """True when :func:`scan_read_content` finds nothing in ``content``."""
    return not scan_read_content(content, extra_patterns=extra_patterns)
