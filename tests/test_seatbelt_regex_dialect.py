"""No Seatbelt regex agentteams emits may use a bounded count such as ``{32}`` or ``{2,4}``.

Measured 2026-11-23 (Darwin 25.5, ``sandbox-exec``): in a ``#"..."`` regex a bounded count silently matches nothing,
so a deny rule that uses one fails open, and an allow rule that uses one never allows. Backslash escapes and ``[x]``
classes work. This scans every ``(regex`` literal in every text file under the package (Python source, templates,
data), so a new rule can't add one unnoticed. A regex assembled from variables at run time is not visible here.
"""

from __future__ import annotations

import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "agentteams"
#: A Seatbelt regex literal as it appears in Python source: ``(regex #"...")`` or ``(regex "...")``.
_REGEX_LITERAL = re.compile(r'\(regex #?\\?"(?P<body>(?:\\.|[^"\\])*)')
_BOUNDED_COUNT = re.compile(r"\{[0-9]+(,[0-9]*)?\}")


def _seatbelt_regexes() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in sorted(p for p in PACKAGE.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        text = path.read_bytes().decode("utf-8", "replace")   # a binary file just yields no match
        for number, line in enumerate(text.splitlines(), 1):
            for m in _REGEX_LITERAL.finditer(line):
                found.append((f"{path.relative_to(PACKAGE.parent)}:{number}", m.group("body")))
    return found


def test_the_scan_finds_the_known_rules() -> None:
    assert any(where.startswith("agentteams/_sandbox_profiles.py") for where, _ in _seatbelt_regexes())


def test_no_emitted_seatbelt_regex_uses_a_bounded_count() -> None:
    bad = [f"{where}: {body}" for where, body in _seatbelt_regexes() if _BOUNDED_COUNT.search(body)]
    assert not bad, "Seatbelt ignores {n} counts (a deny then fails open); repeat a [class] instead:\n" + "\n".join(bad)


def test_the_count_pattern_catches_the_measured_failure() -> None:
    assert _BOUNDED_COUNT.search(r"^/.*/inb/[0-9a-f]{32}[.]msg$")
    assert _BOUNDED_COUNT.search("[a-z]{2,4}") and _BOUNDED_COUNT.search("x{3,}")
    assert not _BOUNDED_COUNT.search("{w}") and not _BOUNDED_COUNT.search(r"/\.[gG][iI][tT](/|$)")


def test_an_escaped_quote_does_not_end_the_body() -> None:
    line = r'(deny file-write* (regex #"/a\"b[0-9]{4}"))'
    body = _REGEX_LITERAL.search(line).group("body")
    assert _BOUNDED_COUNT.search(body)
