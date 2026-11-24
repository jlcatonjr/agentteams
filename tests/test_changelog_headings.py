"""CHANGELOG.md must not contain a line that the docs site renders as an unintended heading.

``docs_src/changelog.md`` includes ``CHANGELOG.md``, and docs-build's heading ratchet
(``scripts/check_docs_site.py pages``) fails a page whose heading levels jump. Python-Markdown turns two line shapes
into an ``<h1>`` (probed with markdown 3.10.3 and the site's extensions):

- a list item whose text starts with ``#``, such as ``- #574's ...`` (this broke docs-build on #591);
- a column-0 line starting with ``#`` that continues a paragraph, such as ``#546 ...``.

A continuation line indented under a list item (``  #367, #381 ...``) stays text, and so does ``#`` inside a fenced
code block or after the first character. The two rules below are stdlib-only, so they run in every test job, well
before docs-build. The last test checks them against the real renderer where ``markdown`` is installed; it is also
the only check for a setext heading (a text line underlined with ``---`` or ``===``), so that shape is caught only
where ``markdown`` is installed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"

#: A list item (bulleted or numbered, at any depth) whose text starts with "#".
_LIST_ITEM_HASH = re.compile(r"[ \t]*(?:[-*+]|[0-9]+[.)])[ \t]+#")
#: An intended heading: one to four "#", a space, then text. CHANGELOG uses levels 1 to 4.
_HEADING = re.compile(r"#{1,4} \S.*")
#: A fence line: its marker run, then an info string on an opener (a closer has none).
_FENCE = re.compile(r"[ \t]*(`{3,}|~{3,})(.*)")


def _lines_outside_fences(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    fence: str | None = None
    for number, line in enumerate(text.splitlines(), 1):
        m = _FENCE.match(line)
        if m and fence is None:
            fence = m.group(1)
            continue
        if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not m.group(2).strip():
            fence = None   # a closer: the same character, at least as long, nothing after it
            continue
        if fence is None:
            out.append((number, line))
    return out


def heading_problems(text: str) -> list[str]:
    """Return one ``line N: ...`` message per line that would render as an unintended heading.

    Args:
        text: The Markdown source.

    Returns:
        The problems, in line order; empty when there are none.
    """
    problems: list[str] = []
    for number, line in _lines_outside_fences(text):
        if _LIST_ITEM_HASH.match(line):
            problems.append(f"line {number}: a list item starts with '#' (renders as <h1>): {line.strip()[:60]}")
        elif line.startswith("#####"):
            problems.append(f"line {number}: a heading deeper than level 4 (CHANGELOG uses 1 to 4): {line[:60]}")
        elif line.startswith("#") and not _HEADING.fullmatch(line):
            problems.append(f"line {number}: starts with '#' but is not a heading (renders as one): {line[:60]}")
    return problems


def test_the_changelog_has_no_line_that_renders_as_an_unintended_heading() -> None:
    problems = heading_problems(CHANGELOG.read_text(encoding="utf-8"))
    assert not problems, (
        "reword so the line does not start with '#' (for example 'Issue #574' or 'For #574'):\n" + "\n".join(problems)
    )


@pytest.mark.parametrize("source", [
    "- #574's thing\n",
    "- a\n  - #574's thing\n",
    "1. #574 first\n",
    "first line\n#574 continues\n",
    "#574\n",
    "##[1.3.0]\n",
    "##### too deep\n",
])
def test_each_unintended_heading_shape_is_caught(source: str) -> None:
    assert heading_problems(source)


@pytest.mark.parametrize("source", [
    "# Changelog\n## [Unreleased]\n### security (x)\n#### sub\n",
    "- first line of text\n  #367, #381 continue it\n",
    "- see #574 here\n",
    "```\n#574\n```\n",
    "~~~\n- #574\n~~~\n",
    "````\n```\n#574\n````\n",
    "```python\n#ok\n```python\n#574\n```\n",   # a line with an info string never closes a fence
])
def test_intended_headings_and_safe_hashes_pass(source: str) -> None:
    assert heading_problems(source) == []


def test_the_rules_match_the_real_renderer() -> None:
    """Every heading the docs renderer makes from CHANGELOG.md comes from an intended heading line."""
    markdown = pytest.importorskip("markdown")
    pytest.importorskip("pymdownx")
    # From mkdocs.yml markdown_extensions; those left out (highlight, inlinehilite, tilde, snippets) make no headings.
    md = markdown.Markdown(extensions=["attr_list", "md_in_html", "pymdownx.superfences", "admonition", "tables",
                                       "toc", "pymdownx.tabbed"])
    text = CHANGELOG.read_text(encoding="utf-8")
    rendered = len(re.findall(r"<h[1-6][ >]", md.convert(text)))
    intended = sum(1 for _, line in _lines_outside_fences(text) if _HEADING.fullmatch(line))
    assert rendered == intended
    for source in ("- #574's thing\n", "first line\n#574 continues\n"):
        md.reset()
        assert "<h1" in md.convert(source), source
