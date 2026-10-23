"""test_docs_api_nav_groups.py — the API Reference nav is derived from the API index, never a second list.

`docs_src/api-reference/index.md` sorts every documented module into H2 sections, each holding a
module table. The site nav groups the API pages the same way (docs-site restructure, phase 1). Two
hand-maintained groupings would drift, so this test makes the index the single source of truth:

* a **group** is an H2 section of the index that contains a module table — derived from the content,
  not from a list of names, so a table added under any H2 later becomes a group the nav must show;
* a page belongs to the group whose table lists it in the **first column** (links in the Role column,
  such as `../cli-reference.md`, are not memberships);
* the nav's API groups must equal the index's groups (titles verbatim), each API page must sit in its
  own group, every API nav page must have a table placement (stricter than the any-link check in
  test_docs_nav_completeness.py), and no nav target may appear twice.

`mkdocs.yml` is parsed as text because its `!!python/name` tag defeats `yaml.safe_load`; titles may be
quoted (a title containing ": " must be), so quotes are stripped.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MKDOCS = REPO_ROOT / "mkdocs.yml"
API_INDEX = REPO_ROOT / "docs_src" / "api-reference" / "index.md"
API_OVERVIEW = "api-reference/index.md"

_ENTRY = re.compile(r'^(?P<indent> *)- (?P<title>"[^"]*"|[^:\n]+):(?: (?P<path>\S+\.md))?\s*$')


def _index_groups() -> dict[str, list[str]]:
    """Return {H2 title: [page basenames listed in the first column of its tables]} for table-bearing H2s."""
    groups: dict[str, list[str]] = {}
    current: str | None = None
    for line in API_INDEX.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^## (.+?)\s*$", line)
        if heading:
            current = heading.group(1)
            continue
        if current and line.startswith("|"):
            first_cell = line.strip().strip("|").split("|")[0]
            link = re.search(r"\]\(([^)#/]+\.md)\)", first_cell)
            if link:
                groups.setdefault(current, []).append(link.group(1))
    return groups


def _nav_entries() -> list[tuple[int, str, str | None]]:
    """Return (indent, title, path) for every nav entry, in order.

    The nav block runs from the top-level ``nav:`` key to the next line that starts in column 0;
    blank and comment lines inside it are skipped, and any other unparseable line fails loudly.
    """
    lines = MKDOCS.read_text(encoding="utf-8").splitlines()
    start = lines.index("nav:") + 1
    out: list[tuple[int, str, str | None]] = []
    for line in lines[start:]:
        if line and not line[0].isspace():
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _ENTRY.match(line)
        assert m, f"unparseable nav line: {line!r}"
        out.append((len(m.group("indent")), m.group("title").strip('"'), m.group("path")))
    return out


def _api_section() -> tuple[int, list[tuple[int, str, str | None]]]:
    """Return (overview indent, entries inside the API section) — the section is the one holding the overview page."""
    entries = _nav_entries()
    pos = [i for i, (_, _, path) in enumerate(entries) if path == API_OVERVIEW]
    assert len(pos) == 1, f"expected exactly one nav entry for {API_OVERVIEW}, found {len(pos)}"
    child_indent = entries[pos[0]][0]
    start = pos[0]
    while start > 0 and entries[start - 1][0] >= child_indent:  # rewind to the section's first child
        start -= 1
    end = pos[0] + 1
    while end < len(entries) and entries[end][0] >= child_indent:
        end += 1
    return child_indent, entries[start:end]


def _nav_api_groups() -> dict[str, list[str]]:
    """Return {group title: [page basenames]} for the groups inside the nav's API reference section."""
    child_indent, section = _api_section()
    groups: dict[str, list[str]] = {}
    group: str | None = None
    for indent, title, path in section:
        if indent == child_indent:
            group = title if path is None else None
            if group is not None:
                groups[group] = []
        elif group is not None:
            groups[group].append((path or "").removeprefix("api-reference/"))
    return groups


def test_api_section_holds_only_the_overview_and_groups() -> None:
    """Nothing may sit loose in the API section or in a group without being an API page (Rule 12)."""
    child_indent, section = _api_section()
    loose = [p for indent, _, p in section if indent == child_indent and p not in (None, API_OVERVIEW)]
    assert not loose, f"pages placed directly in the API reference section, outside any group: {loose}"
    stray = [p for indent, _, p in section if indent > child_indent and not (p or "").startswith("api-reference/")]
    assert not stray, f"non-API entries inside API groups: {stray}"


def test_nav_api_groups_equal_the_index_groups() -> None:
    """The nav's API groups are exactly the index's table-bearing H2s, in order, spelled identically."""
    index, nav = _index_groups(), _nav_api_groups()
    assert index, "found no module tables in api-reference/index.md"
    assert list(nav) == list(index), (
        "mkdocs.yml's API reference groups must be the table-bearing H2s of api-reference/index.md, "
        f"in the same order and spelled identically.\nnav:   {list(nav)}\nindex: {list(index)}"
    )


def test_each_api_page_sits_in_the_group_whose_table_lists_it() -> None:
    """Each group holds exactly the pages its index table lists."""
    index, nav = _index_groups(), _nav_api_groups()
    wrong = {g: sorted(set(nav.get(g, [])) ^ set(pages)) for g, pages in index.items() if set(nav.get(g, [])) != set(pages)}
    assert not wrong, f"API pages placed in the wrong nav group (symmetric difference per group): {wrong}"


def test_every_api_nav_page_has_a_table_placement() -> None:
    """A mention elsewhere in the index is not a placement; a table row is."""
    placed = {p for pages in _index_groups().values() for p in pages}
    in_nav = {
        path.removeprefix("api-reference/")
        for _, _, path in _nav_entries()
        if path and path.startswith("api-reference/") and path != "api-reference/index.md"
    }
    assert not in_nav - placed, (
        f"API pages in the nav with no row in an api-reference/index.md table: {sorted(in_nav - placed)}. "
        "A mention elsewhere in the index (e.g. 'Coverage gaps') is not a placement."
    )


def test_no_page_is_listed_twice() -> None:
    """No page appears in two index tables or twice in the nav."""
    index_dupes = [p for p, n in Counter(p for ps in _index_groups().values() for p in ps).items() if n > 1]
    assert not index_dupes, f"pages listed in more than one api-reference/index.md table: {index_dupes}"
    nav_dupes = [p for p, n in Counter(path for _, _, path in _nav_entries() if path).items() if n > 1]
    assert not nav_dupes, f"pages listed more than once in mkdocs.yml's nav: {nav_dupes}"
