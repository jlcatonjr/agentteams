"""test_book_figure_refs.py — every figure the book embeds must exist and render on the web.

The book (`docs_src/book/`) became canonical in agentteams on 2026-11-14 (see
`references/book-source-of-truth.md`). Until then, two guards ran only in the former working project's
`scripts/sync-book.sh`, as a pre-flight to each sync. This test is their replacement:

* no ``<img>`` may point at a ``figures/*.pdf`` (browsers do not render a PDF in ``<img>``, so the graph
  silently disappears from the web book);
* every ``<img>``/``<source>``/``<object>``/``<embed>`` that points into ``figures/`` must name a file
  that exists under ``docs_src/book/figures/``, with exact letter case, without escaping that directory.

Chapters are authored with ``../figures/`` paths; ``index.html`` rewrites them to ``figures/`` at load
time, so both forms resolve against the same directory.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BOOK = REPO_ROOT / "docs_src" / "book"
FIGURES = BOOK / "figures"

#: The HTML the book SPA loads: chapters, chapter notes and the bibliography.
SOURCES = sorted(
    [*(BOOK / "chapters").glob("*.html"), *(BOOK / "notes").glob("*.html"), BOOK / "bibliography.html"]
)

#: ``src``/``data`` of the embedding elements, pointing into ``figures/`` (``../figures/``, ``figures/`` or
#: ``/book/figures/``). The book uses no ``srcset``; ``test_no_srcset_into_figures`` keeps it that way.
_FIGURE_REF = re.compile(
    r"""<(?:img|source|object|embed)\b[^>]*?\b(?:src|data)\s*=\s*["']"""
    r"""(?:\.\./|/book/)?figures/([^"'#?]+)["']""",
    re.IGNORECASE,
)
_SRCSET_INTO_FIGURES = re.compile(r"""\bsrcset\s*=\s*["'][^"']*figures/""", re.IGNORECASE)


def _figure_refs() -> list[tuple[str, str]]:
    """Collect ``(source file, figure path)`` for every ``<img>`` into ``figures/``.

    Returns:
        Pairs of the referencing file (repo-relative) and the path below ``figures/``.
    """
    refs: list[tuple[str, str]] = []
    for src in SOURCES:
        for match in _FIGURE_REF.finditer(src.read_text(encoding="utf-8")):
            refs.append((str(src.relative_to(REPO_ROOT)), match.group(1)))
    return refs


REFS = _figure_refs()


def test_the_book_embeds_figures_at_all() -> None:
    """Guard the guard: a regex that matches nothing would pass every check below vacuously."""
    assert SOURCES, "no book HTML found under docs_src/book/"
    assert REFS, "no <img src=…figures/…> found in the book; the pattern or the layout changed"


@pytest.mark.parametrize(("source", "figure"), REFS, ids=[f"{s}:{f}" for s, f in REFS])
def test_book_figure_is_not_a_pdf(source: str, figure: str) -> None:
    """A PDF in ``<img>`` does not render in a browser; book figures must be SVG (or raster)."""
    assert not figure.lower().endswith(".pdf"), f"{source} embeds a PDF figure: figures/{figure}"


@pytest.mark.parametrize(("source", "figure"), REFS, ids=[f"{s}:{f}" for s, f in REFS])
def test_book_figure_exists(source: str, figure: str) -> None:
    """Every embedded figure must exist under ``docs_src/book/figures/``, with exactly that spelling.

    The name is compared against the directory listing rather than with ``is_file()`` alone, because
    macOS's case-insensitive filesystem would accept a wrong-case name that 404s on the deployed site.
    """
    target = (FIGURES / figure).resolve()
    assert target.is_relative_to(FIGURES.resolve()), f"{source} escapes figures/: {figure}"
    assert target.is_file(), f"{source} references missing figure: figures/{figure}"
    assert target.name in {p.name for p in target.parent.iterdir()}, (
        f"{source} references figures/{figure} with the wrong letter case"
    )


@pytest.mark.parametrize("source", SOURCES, ids=lambda p: p.name)
def test_no_srcset_into_figures(source: Path) -> None:
    """``srcset`` is not scanned by the checks above, so the book must not use it for figures."""
    assert not _SRCSET_INTO_FIGURES.search(source.read_text(encoding="utf-8")), source
