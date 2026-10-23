# Working on the docs site

This page explains how to change this website without breaking its links, its API reference or its
checks. It is for anyone editing `docs_src/` or `mkdocs.yml`.

## How the site is built

The site is built with MkDocs and the Material theme from `docs_src/`, using the navigation in
`mkdocs.yml`. The versions are pinned in `.github/docs-requirements.txt`. Build it exactly as CI does:

```bash
pip install -r .github/docs-requirements.txt
mkdocs build --strict --site-dir _site
python scripts/check_docs_site.py pages _site
```

## Rules that keep the site intact

1. **Never move or rename a published page.** A page's URL comes from its file path, and pages are
   linked from outside this repository. To retire a page, list its URL in
   `.github/docs-removed-urls.txt` with a reason; the docs-build check fails otherwise.
2. **Put new pages in a section folder**, such as `docs_src/why/` or `docs_src/contribute/`, not at
   the top of `docs_src/`.
3. **Add every new page to the nav** in `mkdocs.yml`, as `Title: path.md` on one line. Quote the title
   if it contains a colon. A page missing from the nav fails `tests/test_docs_nav_completeness.py`.
4. **Give every page a unique title.** The nav title becomes the browser-tab title, and the docs-build
   check fails on duplicates.
5. **Write each page with one `#` heading** and no skipped heading levels, and use link text that says
   where the link goes ("see the CLI reference", not "click here").

## API reference pages

The API reference has extra rules, because tests tie it to the code:

- Each page in `docs_src/api-reference/` documents one module and is named after it. Do not add pages
  there that are not module pages.
- Every module page is listed in a table in `docs_src/api-reference/index.md`. The heading above that
  table is the page's nav group.
- In `mkdocs.yml`, the page goes in the nav group whose name matches that heading.
  `tests/test_docs_api_nav_groups.py` checks this.
- Documented signatures must match the code. `tests/test_api_doc_signatures.py` checks this.

## Including another file

To show a file that lives outside `docs_src/`, such as `CHANGELOG.md`, include it instead of copying
it, so there is only one copy to keep current:

```markdown
;--8<-- "CHANGELOG.md"
```

The path is relative to the repository root.
