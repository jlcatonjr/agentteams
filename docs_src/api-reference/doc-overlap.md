# `doc_overlap` — AgentTeamsModule

Finds passages that repeat across Markdown files, moves a passage that is identical in several
documentation-site pages into one snippet file, and holds a baseline so that new repetition is
noticed.

> *Source: `agentteams/doc_overlap.py`*

---

**What it does.**

- **Detect.** A passage is the text between blank lines; a fenced code block is one passage and
  YAML front matter is skipped. Passages under 20 words are ignored. An *exact* cluster is the same
  passage after lower-casing and stripping punctuation. A *near* cluster is passages at least 0.8
  alike by Jaccard similarity over 4-word shingles.
- **Reduce.** `--extract` moves the longest run of lines shared by the named pages into
  `docs_src/assets/snippets/NAME.md` and replaces it in each page with a `pymdownx.snippets`
  include line naming that file. The rendered pages do not change.
- **Hold.** The baseline lists the exact clusters present, keyed by a hash of the passage, so file
  order and renames do not affect it.

**What it does not do.**

- It finds copied text only. A passage reworded past the threshold is not found.
- A near cluster is a candidate for review. A variant is often deliberate, so it is never counted
  and never changes the exit code.
- It does not shorten what an agent loads, and it does not touch templates. The template renderer
  has no include, so text shared between templates stays copied.

**Left out of the count.**

- *Derived paths* are not scanned: `*/expected/*` and `*/.agentteams-backups/*`, plus any
  `--derived` pattern. Hidden directories under a scanned directory are skipped.
- *Declared overlap* is listed and not counted. The declared file is JSON:
  `{"groups": [{"reason": "...", "paths": ["pattern", ...]}]}`. A cluster is declared when every
  member's path matches a pattern of one group. Patterns are `fnmatch` patterns, in which `*` also
  matches `/`. This repository's file is `references/doc-overlap-declared.json`.

## Key surface

### scan(paths, *, root, min_words, near, derived, declared)

Returns a `Report`. `near=1.0` turns near matching off.

### render_report(report, *, limit)

The plain-text summary the command prints.

### collect_files(paths, *, root, derived)

The Markdown files a scan would read, sorted.

### split_blocks(text)

`(line, passage)` pairs for one file's text.

### load_declared(path)

Reads a declared-overlap file. Every group needs a non-empty `reason`.

### baseline_from(report)

The baseline data for a report: its exact clusters that are not declared.

### write_baseline(report, path)

Writes that data as JSON.

### check_baseline(report, baseline)

Returns `(problems, stale)`. A problem is an exact, undeclared cluster the baseline does not hold,
or one with more copies than recorded. A baseline entry that no longer occurs is stale. The
baseline and the scan must use the same `min_words`.

### expand_snippets(text, base)

Expands whole-file snippet include lines relative to `base`. Lines that name a line range or a
section, escaped lines, and lines naming a missing file are left as written.

### plan_extract(name, files, *, root, snippet_dir, min_lines)

Returns an `ExtractPlan` and writes nothing.

### apply_extract(plan, *, root)

Writes the plan after checking that every rewritten page expands to its original text. If one
would not, it raises and leaves every file as it was.

### main(argv)

The command line.

## When an extract is refused

A run of lines qualifies only when all of these hold:

- it lies after any front matter;
- it begins and ends on a blank line or a file edge in every page, so it is one or more whole
  passages;
- it does not begin or end inside a fenced code block;
- it holds no heading, horizontal rule, snippet line or relative link. A shared run is split at each
  of these and the largest piece that qualifies is taken, so a run never crosses a section boundary.
  (A snippet file is also built as a page of its own, where a relative link would point somewhere
  else.);
- it has at least four non-blank lines;
- it occurs exactly once in each page.

A snippet name is lower-case letters, digits and hyphens. If the snippet file already exists with
the same text, the run is reused, so `--extract NAME one-more-page.md` points a further page at it.
If it exists with different text, the extract is refused.

Included passages are harder to read on GitHub, which does not resolve includes. Extract reference
matter such as tables and source lists, and leave narrative in the page.

## CLI

```
python -m agentteams.doc_overlap PATH [PATH ...] [--json] [--min-words N] [--near F]
                                 [--derived PATTERN] [--declared FILE]
                                 [--write-baseline FILE] [--check --baseline FILE]
python -m agentteams.doc_overlap --extract NAME PAGE [PAGE ...] [--snippet-dir DIR] [--yes]
```

- `--root DIR` sets the directory that reported paths, patterns and snippet paths are relative to. Page paths given to `--extract` are resolved against the current directory.
  It defaults to the current directory; run from the repository root.
- `--extract` is a dry run until `--yes` is given.
- Exit status: 0 when clean; 1 when `--check` finds a repeated passage that is not in the baseline;
  2 on a refused or invalid request.

The check this repository runs (`tests/test_doc_overlap.py`):

```
python -m agentteams.doc_overlap docs_src --near 1.0 \
    --declared references/doc-overlap-declared.json \
    --check --baseline references/doc-overlap-baseline.json
```

After removing a repeated passage, or accepting a new one, refresh the baseline with the same
arguments and `--write-baseline references/doc-overlap-baseline.json` in place of `--check --baseline …`.
