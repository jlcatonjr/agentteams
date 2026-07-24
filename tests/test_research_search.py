"""Tests for agentteams.research.search — ported from LingoFriend's own test suite for the
module this was ported from."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agentteams.research.search import (
    _extract_pdf_text,
    extract_published_date,
    fetch_text_and_date,
    is_public_https,
)


def test_is_public_https_accepts_ordinary_public_url() -> None:
    assert is_public_https("https://example.com/page") is True


def test_is_public_https_rejects_non_https_scheme() -> None:
    assert is_public_https("http://example.com/page") is False


def test_is_public_https_rejects_loopback() -> None:
    assert is_public_https("https://127.0.0.1/internal") is False
    assert is_public_https("https://localhost/internal") is False


def test_is_public_https_rejects_link_local_metadata_endpoint() -> None:
    """The canonical SSRF target this guard exists to block."""
    assert is_public_https("https://169.254.169.254/latest/meta-data/") is False


def test_is_public_https_rejects_private_range() -> None:
    assert is_public_https("https://10.0.0.5/internal") is False
    assert is_public_https("https://192.168.1.1/router") is False


def test_is_public_https_rejects_malformed_or_hostless_url() -> None:
    assert is_public_https("https://") is False
    assert is_public_https("not a url at all") is False
    assert is_public_https("") is False


def test_is_public_https_rejects_unresolvable_hostname() -> None:
    assert is_public_https("https://this-host-does-not-exist.invalid/") is False


def _build_minimal_pdf(text: str) -> bytes:
    """Hand-build a minimal, genuinely valid single-page PDF with an embedded text stream —
    computes real byte offsets for the xref table rather than guessing them, so this is a real
    round-trip test of pypdf's parser, not a hand-waved fixture."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 300 300] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = f"BT /F1 12 Tf 20 150 Td ({text}) Tj ET".encode("latin-1")
    objects.append(
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
    )

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_offset = len(out)
    n = len(objects) + 1
    out += f"xref\n0 {n}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += b"trailer\n<< /Size " + str(n).encode() + b" /Root 1 0 R >>\n"
    out += b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF"
    return bytes(out)


def test_extract_pdf_text_round_trips_real_content() -> None:
    pdf_bytes = _build_minimal_pdf("Hello agentteams research test")
    assert "Hello agentteams research test" in _extract_pdf_text(pdf_bytes)


def test_extract_pdf_text_degrades_to_empty_on_malformed_bytes() -> None:
    assert _extract_pdf_text(b"not a real pdf at all, just garbage bytes") == ""


def test_extract_pdf_text_degrades_to_empty_on_truncated_pdf() -> None:
    pdf_bytes = _build_minimal_pdf("Hello agentteams research test")
    truncated = pdf_bytes[: len(pdf_bytes) // 2]
    assert _extract_pdf_text(truncated) == ""


def test_extract_published_date_json_ld() -> None:
    html = (
        '<html><head><script type="application/ld+json">'
        '{"@type":"NewsArticle","datePublished":"2026-07-18T10:00:00Z"}'
        "</script></head></html>"
    )
    assert extract_published_date(html) == "2026-07-18T10:00:00Z"


def test_extract_published_date_article_meta_tag() -> None:
    html = (
        '<html><head><meta property="article:published_time" '
        'content="2026-07-19T08:30:00+00:00"></head></html>'
    )
    assert extract_published_date(html) == "2026-07-19T08:30:00+00:00"


def test_extract_published_date_generic_date_meta_tag() -> None:
    html = '<html><head><meta name="pubdate" content="2026-07-20"></head></html>'
    assert extract_published_date(html) == "2026-07-20"


def test_extract_published_date_time_tag() -> None:
    html = '<html><body><time datetime="2026-07-17">July 17</time></body></html>'
    assert extract_published_date(html) == "2026-07-17"


def test_extract_published_date_no_match_returns_none_never_fabricates() -> None:
    assert extract_published_date("<html><body>No date anywhere here.</body></html>") is None


def test_extract_published_date_never_raises_on_malformed_input() -> None:
    assert extract_published_date("<<<not even close to html>>>") is None
    assert extract_published_date("") is None


def test_fetch_text_and_date_extracts_date_from_raw_html_before_stripping() -> None:
    """The date must be pulled from the RAW body -- fetch_text's own stripping regex removes
    <script> blocks (where JSON-LD dates live) before the text is returned, so date extraction
    has to happen before that stripping runs, not after. Mocks the shared _fetch_raw seam rather
    than httpx directly, since this module has no existing httpx-mocking test to follow.

    Deliberately placed BEFORE test_import_agentteams_research_search_without_pypdf below: that
    test pops this module from sys.modules and re-imports it, which would leave this file's own
    top-level `fetch_text_and_date` binding pointing at a stale module object whose `_fetch_raw`
    this test's own patch() call could no longer reach -- confirmed live by moving these tests
    after that one and watching them fail on an unmocked real network call.
    """
    html = (
        '<html><head><script type="application/ld+json">'
        '{"datePublished":"2026-07-18"}</script></head>'
        "<body><p>Article body text.</p></body></html>"
    ).encode("utf-8")
    with patch(
        "agentteams.research.search._fetch_raw",
        return_value=(html, "text/html; charset=utf-8", "utf-8"),
    ):
        text, published_at = fetch_text_and_date("https://bbc.com/news/story")
    assert published_at == "2026-07-18"
    assert "Article body text." in text
    assert "datePublished" not in text  # the script block itself must still be stripped from text


def test_fetch_text_and_date_degrades_to_none_when_no_date_found() -> None:
    html = b"<html><body>No date in this one.</body></html>"
    with patch(
        "agentteams.research.search._fetch_raw",
        return_value=(html, "text/html; charset=utf-8", "utf-8"),
    ):
        text, published_at = fetch_text_and_date("https://bbc.com/news/story")
    assert published_at is None
    assert "No date in this one." in text


def test_fetch_text_and_date_returns_empty_and_none_on_fetch_failure() -> None:
    with patch("agentteams.research.search._fetch_raw", return_value=None):
        text, published_at = fetch_text_and_date("https://bbc.com/news/story")
    assert text == ""
    assert published_at is None


def test_fetch_text_and_date_pdf_response_has_no_date() -> None:
    """PDF structure has no HTML meta/JSON-LD to extract a date from -- confirms this is treated
    as an honest empty, not an error, for the one content-type this module's date regexes cannot
    reach."""
    pdf_bytes = _build_minimal_pdf("PDF body text")
    with patch(
        "agentteams.research.search._fetch_raw",
        return_value=(pdf_bytes, "application/pdf", "utf-8"),
    ):
        text, published_at = fetch_text_and_date("https://reuters.com/report.pdf")
    assert "PDF body text" in text
    assert published_at is None


def test_no_module_level_pypdf_import() -> None:
    """The exact regression guard this pattern's LingoFriend origin shipped: `search.py` must be
    importable without pypdf ever being touched at module scope, since agentteams' base install
    doesn't have it. Checks for the actual `import pypdf` statement specifically (not the bare
    word "pypdf", which legitimately appears in this module's own top-of-file docstring prose
    describing what's implemented below)."""
    import inspect

    import agentteams.research.search as search_module

    module_source = inspect.getsource(search_module)
    func_source = inspect.getsource(search_module._extract_pdf_text)
    module_only = module_source.replace(func_source, "")
    assert "import pypdf" not in module_only


def test_import_agentteams_research_search_without_pypdf() -> None:
    """Live-confirm the base-install decoupling claim, not just by static inspection: block pypdf
    at import time with a genuine find_spec-based meta-path finder (NOT the deprecated
    find_module/load_module protocol, which silently fails to intercept under Python 3.12+) and
    confirm this module still imports cleanly.

    Deliberately the LAST test in this file: it pops this module from sys.modules and re-imports
    it, which leaves this file's own top-level bindings pointing at a stale module object for any
    test that runs after it and needs to patch() one of this module's internal attributes.
    """
    import importlib
    import sys

    class _BlockPypdf:
        def find_spec(self, name, path, target=None):
            if name == "pypdf" or name.startswith("pypdf."):
                raise ImportError("pypdf is not installed (simulating agentteams base install)")
            return None

    for mod in ("agentteams.research.search", "pypdf"):
        sys.modules.pop(mod, None)

    blocker = _BlockPypdf()
    sys.meta_path.insert(0, blocker)
    try:
        with pytest.raises(ImportError):
            importlib.import_module("pypdf")
        importlib.import_module("agentteams.research.search")
    finally:
        sys.meta_path.remove(blocker)
        for mod in ("agentteams.research.search", "pypdf"):
            sys.modules.pop(mod, None)
        importlib.import_module("agentteams.research.search")
