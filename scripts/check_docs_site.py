#!/usr/bin/env python3
"""check_docs_site.py — checks over a BUILT MkDocs site (`mkdocs build --site-dir _site`).

Three subcommands, all stdlib-only, all run by the `docs-build` job in `.github/workflows/ci.yml`:

``pages SITE``
    Accessibility/usability checks on every page built from a `mkdocs.yml` nav target:
    (a) unique ``<title>``, (b) Material's skip link present, (c) every ``<img>`` has alt text,
    (d) exactly one h1 and no skipped heading level, (e) no non-descriptive link text.
    **Scope is derived, never declared:** a page is in scope iff its source ``.md`` is a nav target
    (the same regex `tests/test_docs_nav_completeness.py` uses). Everything else in the site — the
    404 page, the book SPA copied in as static HTML, assets — is out of scope by construction, and
    the count of skipped HTML files is printed so the boundary stays visible.
    Pages under ``ENFORCED_PREFIXES`` must have zero violations, and each prefix must match at least
    one nav page (so renaming a section cannot silently drop it from enforcement). Elsewhere each
    check is a **per-page ratchet** against ``BASELINE``, which records the exact set of pages that
    violate it: a page not in the set that starts violating fails, and a page in the set that no
    longer violates also fails until it is removed, so fixes are locked in and one page's fix cannot
    offset another page's regression.

``urls BASE_SITE HEAD_SITE --allowlist FILE``
    Every HTML file in the base build (pages, the 404 page, the static book SPA) must still exist in
    the head build, unless the allowlist names it (one URL path per line, ``# reason`` comment required). An allowlisted path that *does* exist
    in the head build is a stale entry and fails, so a restored page cannot later be re-deleted
    unnoticed; so does a duplicate entry. Every honoured removal is printed. Non-HTML assets
    (images, CSS, JS) are out of scope: they are not cited from outside the site.

``inbound SITE``
    Every ``jlcatonjr.github.io/agentteams/<path>`` URL hard-coded in the files listed in
    ``INBOUND_SOURCES`` must resolve to a page in the built site. A listed source file that does not
    exist fails, so renaming one cannot silently switch the check off.
"""
from __future__ import annotations

import argparse
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MKDOCS_YML = REPO_ROOT / "mkdocs.yml"

#: Same nav-target regex as tests/test_docs_nav_completeness.py:43 — keep the two in step.
_NAV_TARGET_RE = re.compile(r":\s*([A-Za-z0-9_\-/]+\.md)\s*$", re.M)

#: Source-page prefixes (docs_src-relative) whose pages must have zero violations of any check.
#: Grows as sections are brought up to the rubric; never shrinks. Every prefix must match a nav page.
ENFORCED_PREFIXES: tuple[str, ...] = ("index.md", "why/", "contribute/")

#: Per-page ratchet: for each check, the exact set of nav pages (outside ENFORCED_PREFIXES) known to
#: violate it, measured on the build. Remove a page when you fix it; the check fails until you do.
BASELINE: dict[str, frozenset[str]] = {
    "duplicate_title": frozenset(),
    "missing_skip_link": frozenset(),
    "img_without_alt": frozenset(),
    "heading_structure": frozenset({
        "agentteams-sandboxing-guide/for-developers/part-i-what-it-is-and-why.md",
        "api-reference/fence-twins.md",
        "goose-cheat-sheet.md",
    }),
    "vague_link_text": frozenset(),
}

#: Link texts that do not describe their destination (Google developer style: link text).
VAGUE_LINK_TEXT = frozenset({"here", "click here", "this page", "this link", "link", "more", "learn more", "read more"})

#: Files whose hard-coded site URLs must keep resolving (README, man page and its generator, package metadata).
INBOUND_SOURCES: tuple[str, ...] = ("README.md", "agentteams.1", "agentteams/man.py", "pyproject.toml")
#: HTML void elements: they never get an end tag, so they must not change the parser's depth.
_VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"})

_SITE_URL_RE = re.compile(r"https?://jlcatonjr\.github\.io/agentteams/([A-Za-z0-9_\-/.]*)")


class _PageParser(HTMLParser):
    """Collect the facts the page checks need from one Material page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.has_skip_link = False
        self.headings: list[int] = []
        self.imgs_without_alt = 0
        self.link_texts: list[str] = []
        self._in_title = False
        self._article_depth = 0
        self._depth = 0
        self._link_text: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag not in _VOID_TAGS:
            self._depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "a" and "md-skip" in classes:
            self.has_skip_link = True
        if tag == "article" and "md-content__inner" in classes:
            self._article_depth = self._depth
        if not self._article_depth:
            return
        if re.fullmatch(r"h[1-6]", tag):
            self.headings.append(int(tag[1]))
        elif tag == "img" and not (a.get("alt") or "").strip():
            self.imgs_without_alt += 1
        elif tag == "a" and "headerlink" not in classes:
            self._link_text = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS:
            self._depth -= 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._link_text is not None:
            self.link_texts.append(" ".join("".join(self._link_text).split()).lower())
            self._link_text = None
        if self._article_depth and self._depth == self._article_depth and tag == "article":
            self._article_depth = 0
        self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._link_text is not None:
            self._link_text.append(data)


def nav_pages() -> list[str]:
    """Return every docs_src-relative ``.md`` path that `mkdocs.yml`'s nav references.

    Returns:
        Sorted nav target paths.

    Raises:
        OSError: If `mkdocs.yml` cannot be read.
    """
    return sorted(set(_NAV_TARGET_RE.findall(MKDOCS_YML.read_text(encoding="utf-8"))))


def page_html(md_path: str) -> str:
    """Map a source page to its built HTML path under ``use_directory_urls: true``.

    Args:
        md_path: docs_src-relative source path, e.g. ``guides/x.md``, ``index.md``, ``a/README.md``.

    Returns:
        The site-relative HTML path, e.g. ``guides/x/index.html``.

    Raises:
        Nothing.
    """
    stem = md_path[:-3]
    parent, _, name = stem.rpartition("/")
    if name in {"index", "README"}:
        return f"{parent}/index.html" if parent else "index.html"
    return f"{stem}/index.html"


def _heading_problem(levels: list[int]) -> bool:
    if levels.count(1) != 1 or (levels and levels[0] != 1):
        return True
    return any(b > a + 1 for a, b in zip(levels, levels[1:]))


def check_pages(site: Path) -> int:
    """Run checks (a)-(e) over every nav-target page in a built site.

    Args:
        site: The built site directory.

    Returns:
        Process exit code: 0 when every enforced page is clean and every ratchet count equals BASELINE.

    Raises:
        OSError: If a page cannot be read.
    """
    pages = nav_pages()
    in_scope = {page_html(p): p for p in pages}
    all_html = {str(f.relative_to(site)) for f in site.rglob("*.html")}
    missing = sorted(h for h in in_scope if h not in all_html)
    print(f"[pages] {len(in_scope)} nav pages in scope; {len(all_html - set(in_scope))} other HTML files out of scope by derivation")
    if missing:
        print(f"[pages] FAIL: nav pages missing from the build: {missing}")
        return 1

    def enforced(md: str) -> bool:
        return md.startswith(ENFORCED_PREFIXES)

    rc = 0
    for prefix in ENFORCED_PREFIXES:
        if not any(md.startswith(prefix) for md in pages):
            print(f"[pages] FAIL: ENFORCED_PREFIXES entry {prefix!r} matches no nav page")
            rc = 1

    violators: dict[str, set[str]] = {name: set() for name in BASELINE}
    titles: dict[str, list[str]] = {}
    for html_path, md_path in sorted(in_scope.items()):
        parser = _PageParser()
        parser.feed((site / html_path).read_text(encoding="utf-8"))
        titles.setdefault(parser.title.strip(), []).append(md_path)
        if not parser.has_skip_link:
            violators["missing_skip_link"].add(md_path)
        if parser.imgs_without_alt:
            violators["img_without_alt"].add(md_path)
        if _heading_problem(parser.headings):
            violators["heading_structure"].add(md_path)
        if any(text in VAGUE_LINK_TEXT for text in parser.link_texts):
            violators["vague_link_text"].add(md_path)
    for srcs in titles.values():
        if len(srcs) > 1:
            violators["duplicate_title"].update(srcs)

    for name, pages_bad in violators.items():
        for md in sorted(p for p in pages_bad if enforced(p)):
            print(f"[pages] FAIL (enforced page) {name}: {md}")
            rc = 1
        ratcheted = {p for p in pages_bad if not enforced(p)}
        for md in sorted(ratcheted - BASELINE[name]):
            print(f"[pages] FAIL ratchet {name}: {md} newly violates")
            rc = 1
        for md in sorted(BASELINE[name] - ratcheted):
            print(f"[pages] FAIL ratchet {name}: {md} no longer violates; remove it from BASELINE[{name!r}]")
            rc = 1
        print(f"[pages] {name}: {len(ratcheted)} baselined page(s), {len(BASELINE[name])} in BASELINE")
    return rc


def _site_urls(site: Path) -> set[str]:
    return {str(f.relative_to(site)) for f in site.rglob("*.html")}


def _url_to_html(url_path: str) -> str:
    url_path = url_path.split("#", 1)[0].split("?", 1)[0].strip("/")
    if not url_path:
        return "index.html"
    return url_path if url_path.endswith(".html") else f"{url_path}/index.html"


def check_urls(base_site: Path, head_site: Path, allowlist: Path) -> int:
    """Fail if a page URL in the base build is missing from the head build and not allowlisted.

    Args:
        base_site: The built site of the base ref.
        head_site: The built site of the change under test.
        allowlist: File of deliberately removed URL paths, one per line with a ``# reason``.

    Returns:
        Process exit code.

    Raises:
        OSError: If the allowlist exists but cannot be read.
    """
    allowed: dict[str, str] = {}
    rc = 0
    if allowlist.exists():
        for n, line in enumerate(allowlist.read_text(encoding="utf-8").splitlines(), 1):
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            path, _, reason = raw.partition("#")
            if not reason.strip():
                print(f"[urls] FAIL {allowlist}:{n}: entry has no '# reason'")
                rc = 1
            key = _url_to_html(path.strip())
            if key in allowed:
                print(f"[urls] FAIL {allowlist}:{n}: duplicate entry for {key}")
                rc = 1
            allowed[key] = reason.strip()
    base, head = _site_urls(base_site), _site_urls(head_site)
    print(f"[urls] base {len(base)} pages, head {len(head)} pages, {len(allowed)} allowlisted removals")
    for page in sorted(base - head):
        if page in allowed:
            print(f"[urls] removal honoured (allowlisted): {page} — {allowed[page]}")
        else:
            print(f"[urls] FAIL: {page} exists in the base build but not in this one (add a redirect or an allowlist entry)")
            rc = 1
    for page in sorted(set(allowed) & head):
        print(f"[urls] FAIL: stale allowlist entry {page} — the page exists again; remove it from {allowlist}")
        rc = 1
    return rc


def check_inbound(site: Path) -> int:
    """Fail if a site URL hard-coded in INBOUND_SOURCES does not resolve in the built site.

    Args:
        site: The built site directory.

    Returns:
        Process exit code.

    Raises:
        OSError: If a source file cannot be read.
    """
    urls: dict[str, set[str]] = {}
    rc = 0
    for rel in INBOUND_SOURCES:
        src = REPO_ROOT / rel
        if not src.exists():
            print(f"[inbound] FAIL: listed source {rel} does not exist; update INBOUND_SOURCES")
            rc = 1
            continue
        for path in _SITE_URL_RE.findall(src.read_text(encoding="utf-8")):
            urls.setdefault(path, set()).add(rel)
    for path, sources in sorted(urls.items()):
        if not (site / _url_to_html(path)).exists():
            print(f"[inbound] FAIL: /{path} (cited in {', '.join(sorted(sources))}) does not resolve")
            rc = 1
    print(f"[inbound] {len(urls)} distinct site URLs cited in {', '.join(INBOUND_SOURCES)}")
    return rc


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point.

    Args:
        argv: Arguments (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.

    Raises:
        SystemExit: On invalid arguments.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pages")
    p.add_argument("site", type=Path)
    u = sub.add_parser("urls")
    u.add_argument("base_site", type=Path)
    u.add_argument("head_site", type=Path)
    u.add_argument("--allowlist", type=Path, default=REPO_ROOT / ".github" / "docs-removed-urls.txt")
    i = sub.add_parser("inbound")
    i.add_argument("site", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "pages":
        return check_pages(args.site)
    if args.cmd == "urls":
        return check_urls(args.base_site, args.head_site, args.allowlist)
    return check_inbound(args.site)


if __name__ == "__main__":
    sys.exit(main())
