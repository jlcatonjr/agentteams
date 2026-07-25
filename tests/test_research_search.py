"""Tests for agentteams.research.search — ported from LingoFriend's own test suite for the
module this was ported from."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agentteams.research.search import (
    _broaden,
    _extract_pdf_text,
    extract_published_date,
    fetch_text_and_date,
    is_public_https,
    web_search,
    web_search_verbose,
)


# --- challenged-request handling (2026-07-24) -------------------------------
#
# DuckDuckGo answers a challenged request with HTTP 202 + an interstitial page, NOT an
# error status, so raise_for_status() never fires. Before this handling, a challenge was
# indistinguishable from "nothing matched": a live agent read the empty list as "no such
# information exists" and abandoned an answerable question. Measured that day: a long,
# specific query was challenged 4/4 attempts while a shortened form returned 10 results.

class _FakeResp:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError("test fixture should not use error statuses here")


_RESULT_HTML = (
    '<a class="result__a" href="https://example.com/a">Result A</a>'
    '<a class="result__snippet">snippet a</a>'
)


def test_broaden_preserves_the_entity_not_just_a_fixed_prefix():
    """The broadened query must keep the entity being searched for, intact.

    Regression guard for a defect caught in review: a fixed 4-term cap turned the
    motivating query into "2026 NASCAR Cup Pennzoil", severing the race name
    "Pennzoil 400" — the one term the search actually needs. Halving keeps it.
    """
    q = "2026 NASCAR Cup Pennzoil 400 Las Vegas top 10 finishers results"
    out = _broaden(q)
    assert "Pennzoil 400" in out, f"broadening severed the entity: {out!r}"
    assert len(out.split()) < len(q.split())


def test_broaden_scales_with_query_length_rather_than_truncating_to_a_constant():
    long_q = " ".join(f"t{i}" for i in range(20))
    mild_q = " ".join(f"t{i}" for i in range(6))
    assert len(_broaden(long_q).split()) == 10   # halved
    assert len(_broaden(mild_q).split()) == 3    # halved, at the floor


def test_broaden_returns_empty_when_query_is_already_short():
    # Below the floor, shortening would strip meaning rather than filler.
    assert _broaden("2026 Pennzoil 400") == ""
    assert _broaden("one two") == ""


def test_challenged_query_retries_broadened_and_reports_it():
    calls: list[str] = []

    def fake_get(url, params=None, **kwargs):
        calls.append(params["q"])
        if len(calls) == 1:
            return _FakeResp(202, "<html>anomaly challenge</html>")  # challenged
        return _FakeResp(200, _RESULT_HTML)

    with patch("agentteams.research.search.httpx.get", side_effect=fake_get):
        results, note = web_search_verbose("one two three four five six seven")

    assert len(calls) == 2 and calls[1] == "one two three"   # 7 terms -> halved
    assert [r.title for r in results] == ["Result A"]
    assert note is not None and "challenged" in note and "one two three" in note


def test_challenge_with_unshortenable_query_reports_block_not_no_results():
    # The distinction that matters: an agent must not conclude "nothing exists".
    with patch("agentteams.research.search.httpx.get",
               return_value=_FakeResp(202, "<html>challenge</html>")):
        results, note = web_search_verbose("short query")
    assert results == []
    assert note is not None and "not evidence that nothing matched" in note


def test_genuine_zero_results_is_not_reported_as_a_challenge():
    # 200 with no parseable results really does mean nothing matched.
    with patch("agentteams.research.search.httpx.get",
               return_value=_FakeResp(200, "<html>no results here</html>")):
        results, note = web_search_verbose("obscure query")
    assert results == [] and note is None


def test_successful_search_needs_no_retry_and_emits_no_note():
    calls: list[str] = []

    def fake_get(url, params=None, **kwargs):
        calls.append(params["q"])
        return _FakeResp(200, _RESULT_HTML)

    with patch("agentteams.research.search.httpx.get", side_effect=fake_get):
        results, note = web_search_verbose("a b c d e f g")
    assert len(calls) == 1 and note is None and len(results) == 1


def test_web_search_keeps_its_list_return_contract():
    # Back-compat: existing callers still get a plain list, retry included.
    def fake_get(url, params=None, **kwargs):
        return _FakeResp(200, _RESULT_HTML)

    with patch("agentteams.research.search.httpx.get", side_effect=fake_get):
        assert [r.title for r in web_search("q")] == ["Result A"]


# --- download cap vs output cap (2026-07-24) --------------------------------

def test_default_max_bytes_is_large_enough_for_a_real_article():
    """`max_bytes` bounds the DOWNLOAD; too low silently returns navigation chrome.

    Regression guard for a live failure: at the previous 40,000-byte default, an
    en.wikipedia.org article extracted to 342 chars containing ZERO body content —
    the whole budget was spent on <head> and nav before reaching the article. At
    400,000 the same page yields 17,744 chars including the full results table.
    Nothing errored in the 40 KB case, so a caller could not distinguish "the page
    doesn't contain that" from "we never downloaded the part that does".
    """
    from agentteams.research.search import _DEFAULT_MAX_BYTES

    assert _DEFAULT_MAX_BYTES >= 200_000


def test_max_bytes_and_max_chars_are_independent_knobs():
    """Raising the download cap must not enlarge what reaches the caller's context.

    `max_chars` is the context guard and stays small by default; `max_bytes` only
    governs how much is pulled before extraction. Conflating them is what would make
    a fix for one a regression for the other.
    """
    import inspect

    from agentteams.research.search import fetch_text

    params = inspect.signature(fetch_text).parameters
    assert params["max_chars"].default == 4000
    assert params["max_bytes"].default > params["max_chars"].default


def test_blank_query_short_circuits_without_network():
    with patch("agentteams.research.search.httpx.get",
               side_effect=AssertionError("must not hit the network")):
        assert web_search_verbose("   ") == ([], None)


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
