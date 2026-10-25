"""test_check_docs_site.py — the docs-site checker must itself be trustworthy.

`scripts/check_docs_site.py` gates every PR in the `docs-build` CI job. A checker that silently
passes is worse than none, so these tests pin the failure modes the Phase 0 code-hygiene audit named:
the per-page ratchet in both directions, allowlist hygiene (missing reason, duplicate, stale entry),
the derived page scope, void-element parsing, and the inbound check refusing a missing source.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_docs_site.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_docs_site", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cds():
    return _load()


def _page(title: str, body: str, skip: bool = True) -> str:
    skip_link = '<a href="#x" class="md-skip">Skip to content</a>' if skip else ""
    return (
        f"<html><head><title>{title}</title></head><body>{skip_link}"
        f'<article class="md-content__inner md-typeset">{body}</article>'
        "<nav><h2>not counted</h2><a href='/'>here</a></nav></body></html>"
    )


def _site(tmp_path: Path, pages: dict[str, str]) -> Path:
    site = tmp_path / "site"
    for rel, html in pages.items():
        f = site / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(html, encoding="utf-8")
    return site


def test_nav_regex_matches_the_nav_completeness_test(cds) -> None:
    """The two copies of the nav-target regex must not drift apart."""
    test_src = (REPO_ROOT / "tests" / "test_docs_nav_completeness.py").read_text(encoding="utf-8")
    assert cds._NAV_TARGET_RE.pattern in test_src


@pytest.mark.parametrize(
    ("md", "html"),
    [
        ("index.md", "index.html"),
        ("cli-reference.md", "cli-reference/index.html"),
        ("why/index.md", "why/index.html"),
        ("agentteams-security-guide/README.md", "agentteams-security-guide/index.html"),
        ("api-reference/emit.md", "api-reference/emit/index.html"),
    ],
)
def test_page_html_follows_directory_urls(cds, md: str, html: str) -> None:
    assert cds.page_html(md) == html


def test_parser_ignores_void_elements_and_text_outside_the_article(cds) -> None:
    p = cds._PageParser()
    p.feed(_page("T", "<h1>A</h1><p>x<br>y<img src='a.png'></p><h2>B</h2>"))
    assert p.headings == [1, 2]  # the <h2> in the trailing <nav> is outside the article
    assert p.imgs_without_alt == 1
    assert p.link_texts == []  # the vague "here" link sits outside the article
    assert p.has_skip_link


def test_heading_problems(cds) -> None:
    assert not cds._heading_problem([1, 2, 3, 2])
    assert cds._heading_problem([1, 3])  # skipped level
    assert cds._heading_problem([1, 2, 1])  # two h1
    assert cds._heading_problem([2, 3])  # no h1


def _run_pages(cds, monkeypatch, tmp_path, nav: list[str], pages: dict[str, str], baseline, enforced=("index.md",)):
    monkeypatch.setattr(cds, "nav_pages", lambda: nav)
    monkeypatch.setattr(cds, "ENFORCED_PREFIXES", enforced)
    monkeypatch.setattr(cds, "BASELINE", {k: frozenset(baseline.get(k, ())) for k in cds.BASELINE})
    return cds.check_pages(_site(tmp_path, pages))


GOOD = "<h1>A</h1><p>text</p>"


def test_ratchet_fails_when_a_new_page_starts_violating(cds, monkeypatch, tmp_path, capsys) -> None:
    rc = _run_pages(cds, monkeypatch, tmp_path, ["index.md", "a.md"],
                    {"index.html": _page("Home", GOOD), "a/index.html": _page("A", "<h1>A</h1><h3>x</h3>")}, {})
    assert rc == 1 and "a.md newly violates" in capsys.readouterr().out


def test_ratchet_fails_when_a_baselined_page_is_fixed_but_not_removed(cds, monkeypatch, tmp_path, capsys) -> None:
    rc = _run_pages(cds, monkeypatch, tmp_path, ["index.md", "a.md"],
                    {"index.html": _page("Home", GOOD), "a/index.html": _page("A", GOOD)},
                    {"heading_structure": {"a.md"}})
    assert rc == 1 and "no longer violates" in capsys.readouterr().out


def test_one_pages_fix_cannot_offset_another_pages_regression(cds, monkeypatch, tmp_path) -> None:
    rc = _run_pages(cds, monkeypatch, tmp_path, ["index.md", "a.md", "b.md"],
                    {"index.html": _page("Home", GOOD), "a/index.html": _page("A", GOOD),
                     "b/index.html": _page("B", "<h1>B</h1><h3>x</h3>")},
                    {"heading_structure": {"a.md"}})
    assert rc == 1


def test_matching_baseline_passes_and_out_of_scope_html_is_ignored(cds, monkeypatch, tmp_path) -> None:
    rc = _run_pages(cds, monkeypatch, tmp_path, ["index.md", "a.md"],
                    {"index.html": _page("Home", GOOD), "a/index.html": _page("A", "<h1>A</h1><h3>x</h3>"),
                     "404.html": "<html><title>404</title></html>", "book/x.html": "<p>no chrome</p>"},
                    {"heading_structure": {"a.md"}})
    assert rc == 0


def test_enforced_pages_tolerate_nothing(cds, monkeypatch, tmp_path, capsys) -> None:
    rc = _run_pages(cds, monkeypatch, tmp_path, ["index.md"],
                    {"index.html": _page("Home", GOOD + "<a href='x'>click here</a>")}, {})
    assert rc == 1 and "FAIL (enforced page) vague_link_text: index.md" in capsys.readouterr().out


def test_duplicate_titles_and_missing_skip_link_are_caught(cds, monkeypatch, tmp_path) -> None:
    rc = _run_pages(cds, monkeypatch, tmp_path, ["index.md", "a.md", "b.md"],
                    {"index.html": _page("Home", GOOD), "a/index.html": _page("Same", GOOD),
                     "b/index.html": _page("Same", GOOD, skip=False)},
                    {"duplicate_title": {"a.md", "b.md"}, "missing_skip_link": {"b.md"}})
    assert rc == 0


def test_an_enforced_prefix_matching_no_page_fails(cds, monkeypatch, tmp_path, capsys) -> None:
    rc = _run_pages(cds, monkeypatch, tmp_path, ["index.md"], {"index.html": _page("Home", GOOD)}, {},
                    enforced=("index.md", "gone/"))
    assert rc == 1 and "'gone/' matches no nav page" in capsys.readouterr().out


def test_a_nav_page_missing_from_the_build_fails(cds, monkeypatch, tmp_path) -> None:
    rc = _run_pages(cds, monkeypatch, tmp_path, ["index.md", "a.md"], {"index.html": _page("Home", GOOD)}, {})
    assert rc == 1


def _urls(cds, tmp_path, base_files, head_files, allow: str | None):
    base = _site(tmp_path / "b", {f: "x" for f in base_files})
    head = _site(tmp_path / "h", {f: "x" for f in head_files})
    allowlist = tmp_path / "allow.txt"
    if allow is not None:
        allowlist.write_text(allow, encoding="utf-8")
    return cds.check_urls(base, head, allowlist)


def test_removed_url_fails_unless_allowlisted(cds, tmp_path) -> None:
    assert _urls(cds, tmp_path, ["index.html", "old/index.html"], ["index.html"], None) == 1
    assert _urls(cds, tmp_path, ["index.html", "old/index.html"], ["index.html"], "old/  # module deleted in #1\n") == 0


def test_removed_non_index_html_is_caught(cds, tmp_path) -> None:
    assert _urls(cds, tmp_path, ["index.html", "book/ch1.html"], ["index.html"], None) == 1


def test_allowlist_entry_needs_a_reason(cds, tmp_path) -> None:
    assert _urls(cds, tmp_path, ["index.html", "old/index.html"], ["index.html"], "old/\n") == 1


def test_duplicate_allowlist_entry_fails(cds, tmp_path) -> None:
    allow = "old/  # a\nold/  # b\n"
    assert _urls(cds, tmp_path, ["index.html", "old/index.html"], ["index.html"], allow) == 1


def test_stale_allowlist_entry_fails(cds, tmp_path) -> None:
    allow = "back/  # removed once\n"
    assert _urls(cds, tmp_path, ["index.html"], ["index.html", "back/index.html"], allow) == 1


def test_inbound_fails_when_a_listed_source_is_missing(cds, monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(cds, "INBOUND_SOURCES", ("README.md", "no-such-file.md"))
    site = _site(tmp_path, {"index.html": "x"})
    for path in set(re.findall(cds._SITE_URL_RE, (REPO_ROOT / "README.md").read_text(encoding="utf-8"))):
        target = site / cds._url_to_html(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")
    assert cds.check_inbound(site) == 1
    assert "no-such-file.md does not exist" in capsys.readouterr().out


def test_inbound_fails_on_an_unresolved_url(cds, monkeypatch, tmp_path) -> None:
    src = tmp_path / "SRC.md"
    src.write_text("see https://jlcatonjr.github.io/agentteams/missing-page/\n", encoding="utf-8")
    monkeypatch.setattr(cds, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cds, "INBOUND_SOURCES", ("SRC.md",))
    assert cds.check_inbound(_site(tmp_path, {"index.html": "x"})) == 1


def _external(cds, monkeypatch, tmp_path, hrefs: list[str], statuses: dict):
    body = GOOD + "".join(f"<a href='{h}'>link text {i}</a>" for i, h in enumerate(hrefs))
    monkeypatch.setattr(cds, "nav_pages", lambda: ["index.md"])

    def fake(url, timeout):
        result = statuses[url]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(cds, "_fetch_status", fake)
    return cds.check_external(_site(tmp_path, {"index.html": _page("Home", body)}))


def test_external_fails_only_on_definite_breakage(cds, monkeypatch, tmp_path, capsys) -> None:
    hrefs = ["https://ok.example/", "https://gone.example/x", "https://nohost.example/"]
    rc = _external(cds, monkeypatch, tmp_path, hrefs,
                   {"https://ok.example/": 200, "https://gone.example/x": 404, "https://nohost.example/": None})
    out = capsys.readouterr().out
    assert rc == 1 and "gone.example/x -> 404" in out and "host not found" in out


def test_external_rate_limits_and_timeouts_are_unverified_not_passing(cds, monkeypatch, tmp_path, capsys) -> None:
    hrefs = ["https://busy.example/", "https://slow.example/"]
    rc = _external(cds, monkeypatch, tmp_path, hrefs,
                   {"https://busy.example/": 429, "https://slow.example/": OSError("timed out")})
    out = capsys.readouterr().out
    assert rc == 0 and "2 unverified" in out and "0 broken" in out


def test_external_client_routes_are_checked_once_and_counted(cds, monkeypatch, tmp_path, capsys) -> None:
    hrefs = ["https://blog.example/#/post-1", "https://blog.example/#/post-2"]
    rc = _external(cds, monkeypatch, tmp_path, hrefs, {"https://blog.example/": 200})
    out = capsys.readouterr().out
    assert rc == 0 and "1 distinct external URLs" in out and "2 links are client-side routes" in out
