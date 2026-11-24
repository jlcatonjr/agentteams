"""test_doc_overlap.py — the repeated-passage detector, the snippet extractor and the baseline check.

Each rule the module states is pinned here: what counts as a passage, what is left out of the
count, when an extract is refused, and that a written extract always expands back to the
original page.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams import doc_overlap as do

PASSAGE = (
    "The gate refuses a destructive write unless a clearance row names the exact path, "
    "the operator who approved it, the date it expires and the number of uses that remain."
)
OTHER = (
    "A waiver is scoped to one action, bounded in time, counted on every use and verified "
    "against the operator key before any agent is allowed to rely on what it says."
)
TABLE = "\n".join([
    "| Layer | What it buys |",
    "|---|---|",
    "| Constitution | principles that outrank every other instruction in the team |",
    "| Gates | a refusal before the write and not a report after it |",
    "| Confinement | the operating system refusing what the agent was never granted |",
])


def _write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# --- passages ---------------------------------------------------------------

def test_split_blocks_skips_front_matter_and_keeps_code_whole() -> None:
    text = "---\ntitle: x\n---\n\nfirst para\n\n```\ncode a\n\ncode b\n```\n\nlast\n"
    blocks = do.split_blocks(text)
    assert [b for _, b in blocks] == ["first para", "```\ncode a\n\ncode b\n```", "last"]
    assert blocks[0][0] == 5


def test_short_passages_are_ignored(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", "Too short to count.\n")
    _write(tmp_path, "b.md", "Too short to count.\n")
    assert do.scan([tmp_path], root=tmp_path).clusters == []


# --- detect -----------------------------------------------------------------

def test_exact_repeat_is_found_with_a_content_id(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", f"# A\n\n{PASSAGE}\n")
    _write(tmp_path, "sub/b.md", f"# B\n\nIntro.\n\n{PASSAGE.upper()}\n")
    report = do.scan([tmp_path], root=tmp_path)
    (cluster,) = report.clusters
    assert cluster.kind == "exact"
    assert [(m.file, m.line) for m in cluster.members] == [("a.md", 3), ("sub/b.md", 5)]
    assert report.redundant_words == cluster.members[0].words
    # The id is a content hash: the same passage in differently named files gives the same id.
    other = tmp_path / "elsewhere"
    _write(other, "z.md", PASSAGE + "\n")
    _write(other, "y.md", PASSAGE + "\n")
    assert do.scan([other], root=other).clusters[0].id == cluster.id


def test_near_repeat_is_reported_separately_and_never_counted(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", PASSAGE + "\n")
    _write(tmp_path, "b.md", PASSAGE.replace("the number of uses", "the count of uses") + "\n")
    report = do.scan([tmp_path], root=tmp_path, near=0.5)
    assert [c.kind for c in report.clusters] == ["near"]
    assert report.redundant_words == 0
    assert do.scan([tmp_path], root=tmp_path, near=1.0).clusters == []


def test_derived_paths_are_not_scanned(tmp_path: Path) -> None:
    _write(tmp_path, "docs/a.md", PASSAGE + "\n")
    _write(tmp_path, "examples/x/expected/a.md", PASSAGE + "\n")
    _write(tmp_path, "docs/.agentteams-backups/1/a.md", PASSAGE + "\n")
    report = do.scan([tmp_path], root=tmp_path)
    assert report.files == 1 and report.clusters == []
    assert do.scan([tmp_path], root=tmp_path, derived=()).files == 2  # the hidden directory stays out


def test_declared_overlap_is_listed_but_not_counted(tmp_path: Path) -> None:
    _write(tmp_path, "guide/dev/p.md", PASSAGE + "\n")
    _write(tmp_path, "guide/ref/p.md", PASSAGE + "\n")
    _write(tmp_path, "other.md", OTHER + "\n")
    _write(tmp_path, "other2.md", OTHER + "\n")
    decl = _write(tmp_path, "declared.json", json.dumps(
        {"groups": [{"reason": "audience editions", "paths": ["guide/*"]}]}))
    report = do.scan([tmp_path], root=tmp_path, declared=do.load_declared(decl))
    assert len(report.clusters) == 2
    assert [c.declared for c in report.clusters if c.members[0].file.startswith("guide")] == ["audience editions"]
    assert len(report.counted("exact")) == 1
    assert "Declared (listed, not counted)" in do.render_report(report)


def test_declared_file_must_give_a_reason(tmp_path: Path) -> None:
    bad = _write(tmp_path, "d.json", json.dumps({"groups": [{"paths": ["a/*"]}]}))
    with pytest.raises(do.DocOverlapError):
        do.load_declared(bad)


# --- hold -------------------------------------------------------------------

def test_baseline_passes_as_written_and_fails_on_a_new_passage(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", PASSAGE + "\n")
    _write(tmp_path, "b.md", PASSAGE + "\n")
    baseline = do.baseline_from(do.scan([tmp_path], root=tmp_path))
    assert do.check_baseline(do.scan([tmp_path], root=tmp_path), baseline) == ([], [])

    _write(tmp_path, "c.md", OTHER + "\n")
    _write(tmp_path, "d.md", OTHER + "\n")
    problems, stale = do.check_baseline(do.scan([tmp_path], root=tmp_path), baseline)
    assert len(problems) == 1 and "new repeated passage" in problems[0] and stale == []


def test_baseline_fails_on_one_more_copy_and_ignores_renames(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", PASSAGE + "\n")
    _write(tmp_path, "b.md", PASSAGE + "\n")
    baseline = do.baseline_from(do.scan([tmp_path], root=tmp_path))
    (tmp_path / "a.md").rename(tmp_path / "zz-renamed.md")
    assert do.check_baseline(do.scan([tmp_path], root=tmp_path), baseline) == ([], [])
    _write(tmp_path, "c.md", PASSAGE + "\n")
    problems, _ = do.check_baseline(do.scan([tmp_path], root=tmp_path), baseline)
    assert len(problems) == 1 and "3 copies" in problems[0]


def test_removed_repetition_is_stale_not_a_failure(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", PASSAGE + "\n")
    b = _write(tmp_path, "b.md", PASSAGE + "\n")
    baseline = do.baseline_from(do.scan([tmp_path], root=tmp_path))
    b.write_text("Gone.\n", encoding="utf-8")
    problems, stale = do.check_baseline(do.scan([tmp_path], root=tmp_path), baseline)
    assert problems == [] and len(stale) == 1


def test_baseline_made_with_other_settings_is_refused(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", PASSAGE + "\n")
    baseline = do.baseline_from(do.scan([tmp_path], root=tmp_path, min_words=5))
    with pytest.raises(do.DocOverlapError):
        do.check_baseline(do.scan([tmp_path], root=tmp_path), baseline)


# --- snippet expansion --------------------------------------------------------

def test_expand_snippets_whole_file_indent_and_nesting(tmp_path: Path) -> None:
    _write(tmp_path, "s/inner.md", "inner\n")
    _write(tmp_path, "s/outer.md", 'one\n--8<-- "s/inner.md"\n')
    text = 'top\n--8<-- "s/outer.md"\n  --8<-- "s/inner.md"\nend\n'
    assert do.expand_snippets(text, tmp_path) == "top\none\ninner\n  inner\nend\n"


def test_expand_snippets_leaves_ranges_and_missing_files(tmp_path: Path) -> None:
    _write(tmp_path, "s.md", "x\n")
    text = '--8<-- "s.md:2:3"\n--8<-- "nope.md"\n;--8<-- "s.md"\n'
    assert do.expand_snippets(text, tmp_path) == text


def test_expand_snippets_stops_on_a_cycle(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", '--8<-- "b.md"\n')
    _write(tmp_path, "b.md", '--8<-- "a.md"\n')
    with pytest.raises(do.DocOverlapError):
        do.expand_snippets('--8<-- "a.md"\n', tmp_path)


# --- reduce -------------------------------------------------------------------

def _pages(root: Path, shared: str = TABLE) -> tuple[Path, Path]:
    a = _write(root, "docs_src/g/dev.md", f"---\ntitle: dev\n---\n\n# Dev\n\nIntro for developers.\n\n{shared}\n\nTail A.\n")
    b = _write(root, "docs_src/g/ref.md", f"# Ref\n\n{shared}\n\nTail B.\n")
    return a, b


def test_extract_is_a_dry_run_until_applied_and_expands_back(tmp_path: Path) -> None:
    a, b = _pages(tmp_path)
    before = {p: p.read_text(encoding="utf-8") for p in (a, b)}
    plan = do.plan_extract("layers", [a, b], root=tmp_path)
    assert plan.body == TABLE + "\n" and plan.lines == 5
    assert not plan.snippet.exists() and a.read_text(encoding="utf-8") == before[a]

    do.apply_extract(plan, root=tmp_path)
    assert plan.snippet.read_text(encoding="utf-8") == TABLE + "\n"
    for p in (a, b):
        text = p.read_text(encoding="utf-8")
        assert '--8<-- "docs_src/assets/snippets/layers.md"' in text and "Constitution" not in text
        assert do.expand_snippets(text, tmp_path) == before[p]
    # The pages no longer repeat one another; the passage lives once, in the snippet.
    assert do.scan([tmp_path / "docs_src"], root=tmp_path, min_words=10).clusters == []


def test_extract_takes_the_run_common_to_all_named_files(tmp_path: Path) -> None:
    both = f"{TABLE}\n\n{PASSAGE}\nsecond line\nthird line\nfourth line"
    a, b = _pages(tmp_path, both)
    c = _write(tmp_path, "docs_src/g/res.md", f"# Res\n\n{TABLE}\n\nSomething else.\n")
    assert do.plan_extract("pair", [a, b], root=tmp_path).body == both + "\n"
    plan = do.plan_extract("layers", [a, b, c], root=tmp_path)
    assert plan.body == TABLE + "\n" and len(plan.targets) == 3


def test_a_second_extract_works_on_pages_that_already_include_a_snippet(tmp_path: Path) -> None:
    second = "\n".join(f"- source file number {n} and the lines it supports" for n in range(1, 6))
    a, b = _pages(tmp_path, f"{TABLE}\n\nOnly here: PAGE.\n\n{second}")
    b.write_text(b.read_text(encoding="utf-8").replace("PAGE", "ref"), encoding="utf-8")
    before = {p: p.read_text(encoding="utf-8") for p in (a, b)}
    do.apply_extract(do.plan_extract("first", [a, b], root=tmp_path), root=tmp_path)
    do.apply_extract(do.plan_extract("second", [a, b], root=tmp_path), root=tmp_path)
    for p in (a, b):
        text = p.read_text(encoding="utf-8")
        assert text.count("--8<--") == 2
        assert do.expand_snippets(text, tmp_path) == before[p]


def test_a_further_page_can_reuse_an_existing_snippet(tmp_path: Path) -> None:
    a, b = _pages(tmp_path)
    do.apply_extract(do.plan_extract("layers", [a, b], root=tmp_path), root=tmp_path)
    c = _write(tmp_path, "docs_src/g/res.md", f"# Res\n\n{TABLE}\n")
    do.apply_extract(do.plan_extract("layers", [c], root=tmp_path), root=tmp_path)
    assert "Constitution" not in c.read_text(encoding="utf-8")


@pytest.mark.parametrize("shared, why", [
    ("```\nline one\nline two\nline three\nline four\n", "begins a code block it does not close"),
    ("a\nb\nc\n[see the gate](../ref/gates.md)", "holds a relative link"),
    ("only\ntwo", "too short"),
])
def test_extract_refuses_runs_that_break_structure(tmp_path: Path, shared: str, why: str) -> None:
    a = _write(tmp_path, "docs_src/a.md", f"# A\n\n{shared}\n\nalpha\n")
    b = _write(tmp_path, "docs_src/b.md", f"# B\n\n{shared}\n\nbeta\n")
    if shared.startswith("```"):
        a.write_text(a.read_text(encoding="utf-8") + "```\n", encoding="utf-8")
        b.write_text(b.read_text(encoding="utf-8") + "```\n", encoding="utf-8")
    with pytest.raises(do.DocOverlapError):
        do.plan_extract("x", [a, b], root=tmp_path)


def test_extract_never_takes_front_matter_or_a_heading(tmp_path: Path) -> None:
    fm = "---\ntitle: same\nowner: same\ntags: same\nkind: same\n---\n"
    a = _write(tmp_path, "docs_src/a.md", f"{fm}\n## Shared heading\n\n{TABLE}\n\nalpha\n")
    b = _write(tmp_path, "docs_src/b.md", f"{fm}\n## Shared heading\n\n{TABLE}\n\nbeta\n")
    plan = do.plan_extract("t", [a, b], root=tmp_path)
    assert plan.body == TABLE + "\n"


def test_extract_takes_whole_passages_only(tmp_path: Path) -> None:
    items = ["- item one of the list", "- item two of the list", "- item three of the list",
             "- item four of the list", "- item five of the list"]
    a = _write(tmp_path, "docs_src/a.md", "- only in a\n" + "\n".join(items) + f"\n\n{TABLE}\n\nalpha\n")
    b = _write(tmp_path, "docs_src/b.md", "- only in b\n" + "\n".join(items) + f"\n\n{TABLE}\n\nbeta\n")
    # The shared run starts part-way through each list, so only the table is a whole passage.
    assert do.plan_extract("t", [a, b], root=tmp_path).body == TABLE + "\n"
    c = _write(tmp_path, "docs_src/c.md", "- only in c\n" + "\n".join(items) + "\n")
    d = _write(tmp_path, "docs_src/d.md", "- only in d\n" + "\n".join(items) + "\n")
    with pytest.raises(do.DocOverlapError):
        do.plan_extract("u", [c, d], root=tmp_path)


def test_extract_refuses_a_run_that_occurs_twice_in_a_page(tmp_path: Path) -> None:
    a = _write(tmp_path, "docs_src/a.md", f"{TABLE}\n\nmiddle\n\n{TABLE}\n")
    b = _write(tmp_path, "docs_src/b.md", f"{TABLE}\n")
    with pytest.raises(do.DocOverlapError):
        do.plan_extract("t", [a, b], root=tmp_path)


def test_extract_refuses_bad_names_outside_paths_and_changed_snippets(tmp_path: Path) -> None:
    a, b = _pages(tmp_path)
    with pytest.raises(do.DocOverlapError):
        do.plan_extract("../escape", [a, b], root=tmp_path)
    with pytest.raises(do.DocOverlapError):
        do.plan_extract("ok", [a, b], root=tmp_path, snippet_dir="../outside")
    _write(tmp_path, "docs_src/assets/snippets/layers.md", "something else\n")
    with pytest.raises(do.DocOverlapError):
        do.plan_extract("layers", [a, b], root=tmp_path)


def test_apply_refuses_and_restores_when_a_page_would_change(tmp_path: Path) -> None:
    a, b = _pages(tmp_path)
    before = {p: p.read_text(encoding="utf-8") for p in (a, b)}
    plan = do.plan_extract("layers", [a, b], root=tmp_path)
    path, line, text = plan.targets[1]
    plan.targets[1] = (path, line, text.replace("Tail B.", "Tail changed."))
    with pytest.raises(do.DocOverlapError):
        do.apply_extract(plan, root=tmp_path)
    assert {p: p.read_text(encoding="utf-8") for p in (a, b)} == before
    assert not plan.snippet.exists()


def test_a_shorter_passage_is_found_when_the_longest_run_is_refused(tmp_path: Path) -> None:
    items = "\n".join(f"- reference item number {n} in the shared list" for n in range(1, 6))
    shared = f"{TABLE}\n\nSee [the gate](../ref/gates.md) for more.\n\n---\n\n{items}"
    a, b = _pages(tmp_path, shared)
    # The whole run holds a relative link and crosses a rule, so the largest clean passage is taken.
    body = do.plan_extract("t", [a, b], root=tmp_path).body
    assert body in (TABLE + "\n", items + "\n") and "](" not in body and "---\n" not in body.replace("|---", "")


def test_shared_front_matter_does_not_block_the_passage_after_it(tmp_path: Path) -> None:
    fm = "---\ntitle: same\nowner: same\n---\n"
    a = _write(tmp_path, "docs_src/a.md", f"{fm}{TABLE}\n\nalpha\n")
    b = _write(tmp_path, "docs_src/b.md", f"{fm}{TABLE}\n\nbeta\n")
    assert do.plan_extract("t", [a, b], root=tmp_path).body == TABLE + "\n"


def test_extract_keeps_crlf_line_endings(tmp_path: Path) -> None:
    a, b = _pages(tmp_path)
    for page in (a, b):
        page.write_bytes(page.read_bytes().replace(b"\n", b"\r\n"))
    before = {page: page.read_bytes() for page in (a, b)}
    do.apply_extract(do.plan_extract("layers", [a, b], root=tmp_path), root=tmp_path)
    for page in (a, b):
        raw = page.read_bytes()
        assert b"--8<--" in raw and raw.count(b"\n") == raw.count(b"\r\n")
        assert do.expand_snippets(raw.decode("utf-8"), tmp_path).encode("utf-8") == before[page]


def test_a_page_already_written_is_put_back_when_a_later_write_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = _pages(tmp_path)
    before = {page: page.read_text(encoding="utf-8") for page in (a, b)}
    plan = do.plan_extract("layers", [a, b], root=tmp_path)
    real_write, calls = do._write, []

    def failing_write(path: Path, text: str) -> None:
        calls.append(path)
        if len(calls) == 3:  # the snippet, the first page, then fail on the second page
            raise OSError("disk full")
        real_write(path, text)

    monkeypatch.setattr(do, "_write", failing_write)
    with pytest.raises(OSError):
        do.apply_extract(plan, root=tmp_path)
    monkeypatch.setattr(do, "_write", real_write)
    assert {page: page.read_text(encoding="utf-8") for page in (a, b)} == before
    assert not plan.snippet.exists()


def test_backup_copies_are_derived_wherever_they_sit(tmp_path: Path) -> None:
    _write(tmp_path, "docs/a.md", PASSAGE + "\n")
    backup = _write(tmp_path, "docs/.agentteams-backups/1/a.md", PASSAGE + "\n")
    assert do.collect_files([backup], root=tmp_path) == []
    assert do.collect_files([backup], root=tmp_path, derived=()) == [backup]
    assert do.scan([tmp_path], root=tmp_path, derived=("docs/*",)).files == 0


def test_a_malformed_baseline_is_refused(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", PASSAGE + "\n")
    _write(tmp_path, "b.md", PASSAGE + "\n")
    report = do.scan([tmp_path], root=tmp_path)
    with pytest.raises(do.DocOverlapError):
        do.check_baseline(report, {"min_words": 20, "clusters": {report.clusters[0].id: 3}})
    with pytest.raises(do.DocOverlapError):
        do.check_baseline(report, {"min_words": 20})


# --- this repository ------------------------------------------------------------

REPO = Path(__file__).resolve().parents[1]
DECLARED = REPO / "references" / "doc-overlap-declared.json"
BASELINE = REPO / "references" / "doc-overlap-baseline.json"
_REFRESH = ("python -m agentteams.doc_overlap docs_src --near 1.0 --declared references/doc-overlap-declared.json "
            "--write-baseline references/doc-overlap-baseline.json")


def _docs_report() -> do.Report:
    return do.scan([REPO / "docs_src"], root=REPO, near=1.0, declared=do.load_declared(DECLARED))


def test_docs_src_gains_no_repeated_passage() -> None:
    problems, _ = do.check_baseline(_docs_report(), json.loads(BASELINE.read_text(encoding="utf-8")))
    assert not problems, (
        "docs_src repeats a passage that is not in the baseline. Include it from one snippet "
        "(--extract), or accept it by refreshing the baseline:\n  " + _REFRESH + "\n" + "\n".join(problems))


def test_docs_baseline_lists_nothing_that_is_gone() -> None:
    _, stale = do.check_baseline(_docs_report(), json.loads(BASELINE.read_text(encoding="utf-8")))
    assert not stale, f"baseline entries no longer occur: {stale}. Refresh it:\n  {_REFRESH}"


def test_every_snippet_include_under_docs_src_names_a_file() -> None:
    missing = []
    for page in sorted((REPO / "docs_src").rglob("*.md")):
        for line in page.read_text(encoding="utf-8").split("\n"):
            m = do._SNIPPET_RE.match(line)
            if m and not (REPO / m.group("path")).is_file():
                missing.append(f"{page.relative_to(REPO)}: {m.group('path')}")
    assert not missing, missing


# --- command line ---------------------------------------------------------------

def test_main_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(tmp_path, "a.md", PASSAGE + "\n")
    _write(tmp_path, "b.md", PASSAGE + "\n")
    base = tmp_path / "base.json"
    root = ["--root", str(tmp_path)]
    assert do.main([str(tmp_path), *root, "--write-baseline", str(base)]) == 0
    assert do.main([str(tmp_path), *root, "--check", "--baseline", str(base)]) == 0
    _write(tmp_path, "c.md", OTHER + "\n")
    _write(tmp_path, "d.md", OTHER + "\n")
    assert do.main([str(tmp_path), *root, "--check", "--baseline", str(base)]) == 1
    assert "FAIL: new repeated passage" in capsys.readouterr().err
    assert do.main([str(tmp_path), *root, "--check"]) == 2
    # Writing and checking the same baseline in one run would always pass, so it is refused.
    assert do.main([str(tmp_path), *root, "--check", "--baseline", str(base), "--write-baseline", str(base)]) == 2
    assert do.main([str(tmp_path), *root, "--check", "--baseline", str(tmp_path / "none.json")]) == 2
    capsys.readouterr()
    assert do.main([str(tmp_path), *root, "--json", "--derived", "c.md", "--derived", "d.md"]) == 0
    assert len(json.loads(capsys.readouterr().out)["clusters"]) == 1
    assert do.main([str(tmp_path / "missing"), *root]) == 2
    assert do.main([]) == 2


def test_main_extract_dry_run_then_yes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a, b = _pages(tmp_path)
    args = ["--extract", "layers", str(a), str(b), "--root", str(tmp_path)]
    assert do.main(args) == 0
    assert "Dry run" in capsys.readouterr().out and "Constitution" in a.read_text(encoding="utf-8")
    assert do.main([*args, "--yes"]) == 0
    assert "Constitution" not in a.read_text(encoding="utf-8")
    # An existing snippet and no page named: refused, not a crash.
    assert do.main(["--extract", "layers", "--root", str(tmp_path)]) == 2
