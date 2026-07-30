"""Tests for agentteams.research.scholarly — OpenAlex / Crossref / arXiv retrieval.

Fixture payloads are trimmed but structurally faithful to each API's real response shape.
No test performs live network I/O.

The emphasis is on the two properties that make this module safe to cite from: nothing is
fabricated when a source omits a field, and a failing source never takes the others down with
it. A bibliography assembled from silently-invented metadata is the exact failure
Constitutional Rule 5 exists to prevent.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from agentteams.research.scholarly import (
    CONTACT_EMAIL_ENV,
    ScholarlyWork,
    _dedupe,
    _host_allowed,
    _normalise_title,
    _parse_arxiv,
    _parse_crossref,
    _parse_openalex,
    _reconstruct_openalex_abstract,
    format_citation,
    scholarly_search,
    search_arxiv,
    search_crossref,
    search_openalex,
)


class _FakeResp:
    def __init__(self, status_code=200, payload=None, text="", url="https://api.openalex.org/works"):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.url = url
        self.content = (text or "").encode() or b"{}"

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


_OPENALEX_PAYLOAD = {
    "results": [
        {
            "title": "Attention Is All You Need",
            "publication_year": 2017,
            "doi": "https://doi.org/10.5555/3295222",
            "id": "https://openalex.org/W1",
            "authorships": [
                {"author": {"display_name": "A Vaswani"}},
                {"author": {"display_name": "N Shazeer"}},
            ],
            "primary_location": {"source": {"display_name": "NeurIPS"}},
            "abstract_inverted_index": {"The": [0], "model": [1], "works": [2]},
        }
    ]
}

_CROSSREF_PAYLOAD = {
    "message": {
        "items": [
            {
                "title": ["A Crossref Paper"],
                "DOI": "10.1234/abcd",
                "URL": "https://doi.org/10.1234/abcd",
                "issued": {"date-parts": [[2020, 3]]},
                "author": [{"given": "Jane", "family": "Doe"}],
                "container-title": ["Journal of Things"],
                "abstract": "<jats:p>An abstract.</jats:p>",
            }
        ]
    }
}

_ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2101.00001v1</id>
    <published>2021-01-01T00:00:00Z</published>
    <title>An   arXiv
    Preprint</title>
    <summary>A summary
    with newlines.</summary>
    <author><name>R Searcher</name></author>
  </entry>
</feed>"""


# --- parsing ---------------------------------------------------------------

def test_parse_openalex_extracts_every_field():
    works = _parse_openalex(_OPENALEX_PAYLOAD, 5)
    assert len(works) == 1
    w = works[0]
    assert w.title == "Attention Is All You Need"
    assert w.year == 2017
    assert w.doi == "10.5555/3295222", "the https://doi.org/ prefix must be stripped"
    assert w.authors == ["A Vaswani", "N Shazeer"]
    assert w.venue == "NeurIPS"
    assert w.abstract == "The model works"
    assert w.source == "openalex"


def test_parse_crossref_extracts_every_field():
    works = _parse_crossref(_CROSSREF_PAYLOAD, 5)
    w = works[0]
    assert w.title == "A Crossref Paper"
    assert w.doi == "10.1234/abcd"
    assert w.year == 2020
    assert w.authors == ["Jane Doe"]
    assert w.venue == "Journal of Things"
    assert w.abstract == "An abstract.", "JATS markup must be stripped"
    assert w.source == "crossref"


def test_parse_arxiv_extracts_and_collapses_whitespace():
    works = _parse_arxiv(_ARXIV_XML, 5)
    w = works[0]
    assert w.title == "An arXiv Preprint"
    assert w.abstract == "A summary with newlines."
    assert w.year == 2021
    assert w.authors == ["R Searcher"]
    assert w.venue == "arXiv"
    assert w.doi is None, "no DOI in the feed means None, never a guess"


def test_reconstruct_openalex_abstract_orders_by_position():
    inverted = {"world": [1], "hello": [0], "again": [2]}
    assert _reconstruct_openalex_abstract(inverted) == "hello world again"


@pytest.mark.parametrize("bad", [None, {}, "string", [], {"w": "not-a-list"}])
def test_reconstruct_openalex_abstract_degrades_on_bad_shapes(bad):
    assert _reconstruct_openalex_abstract(bad) == ""


@pytest.mark.parametrize("parser,bad", [
    (_parse_openalex, {"results": "not a list"}),
    (_parse_openalex, {}),
    (_parse_crossref, {"message": {}}),
    (_parse_crossref, {}),
])
def test_parsers_degrade_on_shape_mismatch(parser, bad):
    assert parser(bad, 5) == []


def test_parse_arxiv_on_malformed_xml_is_empty_not_an_exception():
    assert _parse_arxiv("<not valid xml", 5) == []


# --- no fabrication --------------------------------------------------------

def test_absent_fields_stay_absent_rather_than_being_invented():
    """The anti-fabrication property, asserted directly."""
    sparse = {"results": [{"title": "Bare Record", "id": "https://openalex.org/W2"}]}
    w = _parse_openalex(sparse, 5)[0]
    assert w.title == "Bare Record"
    assert w.year is None
    assert w.doi is None
    assert w.authors == []
    assert w.abstract == ""
    assert w.venue == ""


# --- host allowlist --------------------------------------------------------

@pytest.mark.parametrize("url,ok", [
    ("https://api.openalex.org/works", True),
    ("https://api.crossref.org/works", True),
    ("https://export.arxiv.org/api/query", True),
    ("https://evil.example.com/works", False),
    ("https://api.openalex.org.evil.com/works", False),
    ("https://sub.api.openalex.org/works", False),
    ("not a url at all", False),
])
def test_host_allowlist_is_exact_match_only(url, ok):
    assert _host_allowed(url) is ok


def test_a_disallowed_host_is_never_requested():
    with (
        patch("agentteams.research.scholarly.httpx.get") as mock_get,
        patch("agentteams.research.scholarly._OPENALEX_URL", "https://evil.example.com/x"),
    ):
        assert search_openalex("q") == []
    mock_get.assert_not_called()


# --- contact email ---------------------------------------------------------

def test_contact_email_is_not_sent_unless_opted_in(monkeypatch):
    monkeypatch.delenv(CONTACT_EMAIL_ENV, raising=False)
    with patch("agentteams.research.scholarly.httpx.get",
               return_value=_FakeResp(payload=_OPENALEX_PAYLOAD)) as mock_get:
        search_openalex("q")
    _, kwargs = mock_get.call_args
    assert "mailto" not in kwargs["params"]
    assert "mailto" not in kwargs["headers"]["User-Agent"]


def test_contact_email_is_sent_when_opted_in(monkeypatch):
    monkeypatch.setenv(CONTACT_EMAIL_ENV, "me@example.org")
    with patch("agentteams.research.scholarly.httpx.get",
               return_value=_FakeResp(payload=_OPENALEX_PAYLOAD)) as mock_get:
        search_openalex("q")
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["mailto"] == "me@example.org"


# --- failure degradation ---------------------------------------------------

def test_transport_failure_yields_empty_not_an_exception():
    with patch("agentteams.research.scholarly.httpx.get",
               side_effect=httpx.ConnectError("down")):
        assert search_openalex("q") == []
        assert search_crossref("q") == []
        assert search_arxiv("q") == []


def test_non_200_yields_empty():
    with patch("agentteams.research.scholarly.httpx.get",
               return_value=_FakeResp(status_code=429)):
        assert search_crossref("q") == []


def test_oversized_response_is_refused():
    huge = _FakeResp(payload=_OPENALEX_PAYLOAD)
    huge.content = b"x" * (9 * 1024 * 1024)
    with patch("agentteams.research.scholarly.httpx.get", return_value=huge):
        assert search_openalex("q") == []


# --- dedup -----------------------------------------------------------------

def test_dedupe_prefers_doi_and_preserves_source_order():
    works = [
        ScholarlyWork(title="Same Paper", doi="10.1/x", source="openalex"),
        ScholarlyWork(title="Same Paper (different casing)", doi="10.1/X", source="crossref"),
    ]
    out = _dedupe(works)
    assert len(out) == 1 and out[0].source == "openalex"


def test_dedupe_falls_back_to_normalised_title_when_doi_is_absent():
    works = [
        ScholarlyWork(title="A Great Paper!", source="openalex"),
        ScholarlyWork(title="a great paper", source="arxiv"),
    ]
    assert len(_dedupe(works)) == 1


def test_dedupe_keeps_genuinely_distinct_works():
    works = [
        ScholarlyWork(title="Paper One", doi="10.1/a"),
        ScholarlyWork(title="Paper Two", doi="10.1/b"),
    ]
    assert len(_dedupe(works)) == 2


def test_a_doi_bearing_work_is_not_merged_with_a_same_titled_doiless_one():
    """Two records can share a title and still be different works (preprint vs. proceedings).

    Only a DOI collision is treated as identity; a title collision merges only when neither
    record carries a DOI to distinguish them.
    """
    works = [
        ScholarlyWork(title="Shared Title", doi="10.1/a", source="crossref"),
        ScholarlyWork(title="Shared Title", doi="10.1/b", source="openalex"),
    ]
    assert len(_dedupe(works)) == 2


@pytest.mark.parametrize("title,expected", [
    ("A Great Paper!", "a great paper"),
    ("  Spaces   Everywhere  ", "spaces everywhere"),
    ("!!!", ""),
])
def test_normalise_title(title, expected):
    assert _normalise_title(title) == expected


# --- orchestration ---------------------------------------------------------

def test_scholarly_search_merges_sources_and_survives_one_failing():
    """One index being down must not lose the others' results."""
    def fake_get(url, params=None, **kwargs):
        if "openalex" in url:
            raise httpx.ConnectError("openalex down")
        if "crossref" in url:
            return _FakeResp(payload=_CROSSREF_PAYLOAD, url=url)
        return _FakeResp(text=_ARXIV_XML, url=url)

    with patch("agentteams.research.scholarly.httpx.get", side_effect=fake_get):
        works = scholarly_search("transformers", k=10)

    sources = {w.source for w in works}
    assert sources == {"crossref", "arxiv"}


def test_scholarly_search_respects_k_after_dedup():
    def fake_get(url, params=None, **kwargs):
        if "crossref" in url:
            return _FakeResp(payload=_CROSSREF_PAYLOAD, url=url)
        if "openalex" in url:
            return _FakeResp(payload=_OPENALEX_PAYLOAD, url=url)
        return _FakeResp(text=_ARXIV_XML, url=url)

    with patch("agentteams.research.scholarly.httpx.get", side_effect=fake_get):
        assert len(scholarly_search("q", k=2)) == 2


def test_blank_query_and_unknown_sources_return_empty():
    assert scholarly_search("   ") == []
    assert scholarly_search("q", sources=("not-a-source",)) == []


def test_source_subset_is_honoured():
    with patch("agentteams.research.scholarly.httpx.get",
               return_value=_FakeResp(payload=_CROSSREF_PAYLOAD,
                                      url="https://api.crossref.org/works")) as mock_get:
        scholarly_search("q", sources=("crossref",))
    assert mock_get.call_count == 1


# --- citation formatting ---------------------------------------------------

def test_format_citation_uses_only_present_fields():
    work = ScholarlyWork(
        title="A Paper", authors=["Jane Doe"], year=2020, doi="10.1/x", venue="J. Things"
    )
    line = format_citation(work)
    assert "Jane Doe" in line and "(2020)" in line and "A Paper" in line
    assert "J. Things" in line and "https://doi.org/10.1/x" in line


def test_format_citation_marks_a_missing_year_rather_than_guessing():
    assert "(n.d.)" in format_citation(ScholarlyWork(title="Undated"))


def test_format_citation_abbreviates_long_author_lists():
    work = ScholarlyWork(title="T", authors=["A A", "B B", "C C", "D D"])
    assert "et al." in format_citation(work)


def test_format_citation_falls_back_to_url_when_no_doi():
    work = ScholarlyWork(title="T", url="https://arxiv.org/abs/1")
    line = format_citation(work)
    assert "https://arxiv.org/abs/1" in line and "doi.org" not in line
