"""doc_overlap.py — find, reduce and hold repeated passages across Markdown files.

Three functions for documentation that is authored by hand:

* **Detect** (:func:`scan`): groups passages that occur in more than one place. An *exact*
  cluster is the same passage after normalisation; a *near* cluster is passages at least
  ``near`` alike by word-shingle Jaccard. Near clusters are for a person to review: a variant
  is not assumed to be drift. Derived paths (rendered example output, backups) are not
  scanned, and overlap an operator has declared as intended is listed but not counted.
* **Reduce** (:func:`plan_extract`, :func:`apply_extract`): moves a run of lines that is
  identical in several documentation-site pages into one snippet file, included with the
  site's ``pymdownx.snippets`` ``--8<--`` line. The write is refused unless every rewritten
  page expands to exactly its original text.
* **Hold** (:func:`write_baseline`, :func:`check_baseline`): records the exact clusters
  present, keyed by passage content, and reports any that is new.

Usage::

    python -m agentteams.doc_overlap docs_src [--json]
    python -m agentteams.doc_overlap docs_src --check --baseline references/doc-overlap-baseline.json
    python -m agentteams.doc_overlap --extract part-ii-sources A.md B.md [--yes]

It detects copied text only. A passage reworded past the threshold is not found, and nothing
here shortens what an agent loads. Standard library only; read-only unless ``--yes`` or
``--write-baseline`` is given.
"""
from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

#: Shortest passage compared, in words (Juergens et al. 2010 used 20 for specifications).
DEFAULT_MIN_WORDS = 20
#: Jaccard similarity over word shingles at or above which two passages are "near".
DEFAULT_NEAR = 0.8
#: Words per shingle.
SHINGLE = 4
#: Paths never scanned: rendered or snapshot copies of text whose source is elsewhere.
DEFAULT_DERIVED: tuple[str, ...] = ("*/expected/*", "*/.agentteams-backups/*")
#: Where :func:`plan_extract` puts snippet files, relative to the repository root.
DEFAULT_SNIPPET_DIR = "docs_src/assets/snippets"
#: Fewest non-blank lines a run must have to be extracted.
MIN_EXTRACT_LINES = 4
#: A shingle found in more blocks than this is boilerplate and proposes no candidate pairs.
_COMMON_SHINGLE = 400
_MAX_SNIPPET_DEPTH = 5

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_'-]*")
_FENCE_RE = re.compile(r"^[ \t]*(```|~~~)")
#: A whole-file ``pymdownx.snippets`` line. A path holding ``:`` (line range or section) is not matched.
_SNIPPET_RE = re.compile(r'^(?P<indent>[ \t]*)-{1,}8<-{1,}[ \t]+(?P<q>["\'])(?P<path>[^"\':\n]+)(?P=q)$')
#: Any snippet marker at all, including the block form this module does not expand.
_ANY_SNIPPET_RE = re.compile(r"^[ \t]*;*-{1,}8<-{1,}")
#: A thematic break: a section boundary an extracted run must not cross.
_RULE_RE = re.compile(r"^[ \t]{0,3}([-*_])[ \t]*(?:\1[ \t]*){2,}\r?$")
_HEADING_RE = re.compile(r"^#{1,6}[ \t]")
_REL_LINK_RE = re.compile(r"\]\((?!https?://|mailto:|#)[^)]*\)")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class DocOverlapError(ValueError):
    """A request this module refuses: a bad name, an unsafe path, or an extract that would change a page."""


@dataclasses.dataclass(frozen=True)
class Block:
    """One passage of a scanned file.

    Attributes:
        file: Path of the file, relative to the scan root, with forward slashes.
        line: 1-based line of the passage's first line.
        words: Word count after normalisation.
        key: Content hash of the normalised passage.
        preview: The first words of the passage.
    """

    file: str
    line: int
    words: int
    key: str
    preview: str


@dataclasses.dataclass
class Cluster:
    """Passages that repeat one another.

    Attributes:
        id: Stable id derived from content, so it survives renames and reordering.
        kind: ``"exact"`` or ``"near"``.
        members: The passages, in file then line order.
        declared: The reason from the declared-overlap file when every member falls in one
            declared group, else ``""``.
    """

    id: str
    kind: str
    members: list[Block]
    declared: str = ""

    @property
    def redundant_words(self) -> int:
        """Words in every member after the first."""
        return sum(b.words for b in self.members[1:])


@dataclasses.dataclass
class Report:
    """The result of :func:`scan`.

    Attributes:
        files: Number of files scanned.
        words: Words in all scanned files.
        min_words: The passage-length threshold used.
        clusters: Exact clusters, then near clusters, largest first within each kind.
    """

    files: int
    words: int
    min_words: int
    clusters: list[Cluster]

    def counted(self, kind: str = "exact") -> list[Cluster]:
        """Clusters of *kind* that are not declared overlap."""
        return [c for c in self.clusters if c.kind == kind and not c.declared]

    @property
    def redundant_words(self) -> int:
        """Redundant words in exact clusters that are not declared."""
        return sum(c.redundant_words for c in self.counted("exact"))

    def to_json(self) -> dict[str, Any]:
        """The report as plain data."""
        return {
            "files": self.files, "words": self.words, "min_words": self.min_words,
            "redundant_words_exact": self.redundant_words,
            "clusters": [
                {"id": c.id, "kind": c.kind, "declared": c.declared, "redundant_words": c.redundant_words,
                 "members": [dataclasses.asdict(b) for b in c.members]}
                for c in self.clusters
            ],
        }


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _is_derived(rel: str, derived: Sequence[str]) -> bool:
    probe = "/" + rel
    return any(fnmatch.fnmatch(probe, pat) or fnmatch.fnmatch(rel, pat) for pat in derived)


def collect_files(paths: Iterable[Path | str], *, root: Path, derived: Sequence[str] = DEFAULT_DERIVED) -> list[Path]:
    """Markdown files under *paths*, sorted, without derived or hidden-directory files.

    Args:
        paths: Files or directories.
        root: Directory that relative paths and derived patterns are measured from.
        derived: ``fnmatch`` patterns of paths to leave out.

    Returns:
        The files, sorted by relative path.

    Raises:
        DocOverlapError: A path does not exist.
    """
    found: dict[str, Path] = {}
    for raw in paths:
        p = Path(raw)
        if not p.exists():
            raise DocOverlapError(f"no such file or directory: {raw}")
        candidates = [p] if p.is_file() else sorted(p.rglob("*.md"))
        for c in candidates:
            if c.suffix != ".md" or not c.is_file():
                continue
            rel = _rel(c, root)
            if p.is_dir() and any(part.startswith(".") for part in Path(_rel(c, p)).parts[:-1]):
                continue
            if not _is_derived(rel, derived):
                found[rel] = c
    return [found[k] for k in sorted(found)]


def _read(path: Path) -> str:
    """The file's text with its line endings untouched; an unreadable file is a refused request."""
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise DocOverlapError(f"cannot read {path}: {exc}") from exc


def _write(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _front_matter_end(lines: Sequence[str]) -> int:
    """Index of the first line after YAML front matter, or 0 when there is none."""
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return i + 1
    return 0


def _inside_before(lines: Sequence[str]) -> list[bool]:
    """For each line, whether a fenced code block is open just before it. One extra entry for the end."""
    state: list[bool] = []
    inside = False
    for line in lines:
        state.append(inside)
        if _FENCE_RE.match(line):
            inside = not inside
    state.append(inside)
    return state


def _fence_state(lines: Sequence[str]) -> list[bool]:
    """For each line, whether it lies inside a fenced code block (fence lines count as inside)."""
    before = _inside_before(lines)
    return [before[i] or bool(_FENCE_RE.match(line)) for i, line in enumerate(lines)]


def split_blocks(text: str) -> list[tuple[int, str]]:
    """Split Markdown into passages: text between blank lines, a fenced code block kept whole.

    YAML front matter is skipped.

    Args:
        text: The file's text.

    Returns:
        ``(line, passage)`` pairs; *line* is 1-based.

    Raises:
        Nothing.
    """
    lines = text.split("\n")
    fenced = _fence_state(lines)
    out: list[tuple[int, str]] = []
    cur: list[str] = []
    start = 0
    for i in range(_front_matter_end(lines), len(lines)):
        if not lines[i].strip() and not fenced[i]:
            if cur:
                out.append((start + 1, "\n".join(cur)))
            cur = []
        else:
            if not cur:
                start = i
            cur.append(lines[i])
    if cur:
        out.append((start + 1, "\n".join(cur)))
    return out


def _digest(words: Sequence[str]) -> str:
    return hashlib.sha256(" ".join(words).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Detect
# ---------------------------------------------------------------------------

def load_declared(path: Path | str | None) -> list[dict[str, Any]]:
    """Read a declared-overlap file: ``{"groups": [{"reason": str, "paths": [pattern, ...]}]}``.

    Args:
        path: The file, or ``None`` for no declarations.

    Returns:
        The groups.

    Raises:
        DocOverlapError: The file is not valid or a group lacks a reason or patterns.
    """
    if path is None:
        return []
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DocOverlapError(f"cannot read declared-overlap file {path}: {exc}") from exc
    groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(groups, list):
        raise DocOverlapError(f"{path}: expected an object with a 'groups' list")
    for g in groups:
        if not (isinstance(g, dict) and isinstance(g.get("reason"), str) and g["reason"].strip()
                and isinstance(g.get("paths"), list) and g["paths"] and all(isinstance(x, str) for x in g["paths"])):
            raise DocOverlapError(f"{path}: every group needs a non-empty 'reason' and a 'paths' list")
    return groups


def _declared_reason(files: set[str], groups: Sequence[dict[str, Any]]) -> str:
    for g in groups:
        if all(any(fnmatch.fnmatch(f, pat) for pat in g["paths"]) for f in files):
            return str(g["reason"])
    return ""


def scan(
    paths: Iterable[Path | str],
    *,
    root: Path | str = ".",
    min_words: int = DEFAULT_MIN_WORDS,
    near: float = DEFAULT_NEAR,
    derived: Sequence[str] = DEFAULT_DERIVED,
    declared: Sequence[dict[str, Any]] = (),
) -> Report:
    """Find passages that repeat across (or within) the Markdown files under *paths*.

    Args:
        paths: Files or directories to scan.
        root: Directory that reported paths and patterns are relative to.
        min_words: Passages shorter than this are ignored.
        near: Jaccard similarity at or above which two different passages are grouped as near
            duplicates. ``1.0`` disables near matching.
        derived: ``fnmatch`` patterns of paths not to scan.
        declared: Groups from :func:`load_declared`.

    Returns:
        The report.

    Raises:
        DocOverlapError: A path does not exist.
    """
    root_path = Path(root)
    files = collect_files(paths, root=root_path, derived=derived)
    blocks: list[Block] = []
    shingles: dict[str, frozenset[str]] = {}
    total_words = 0
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise DocOverlapError(f"cannot read {f}: {exc}") from exc
        rel = _rel(f, root_path)
        for line, passage in split_blocks(text):
            words = _WORD_RE.findall(passage.lower())
            total_words += len(words)
            if len(words) < min_words:
                continue
            key = _digest(words)
            blocks.append(Block(rel, line, len(words), key, " ".join(words[:14])))
            if key not in shingles:
                shingles[key] = frozenset(
                    hashlib.blake2b(" ".join(words[i:i + SHINGLE]).encode("utf-8"), digest_size=8).hexdigest()
                    for i in range(len(words) - SHINGLE + 1)
                )
    by_key: dict[str, list[Block]] = defaultdict(list)
    for b in blocks:
        by_key[b.key].append(b)

    clusters: list[Cluster] = []
    for key, members in by_key.items():
        if len(members) > 1:
            clusters.append(Cluster(key[:12], "exact", members, _declared_reason({m.file for m in members}, declared)))

    if near < 1.0:
        keys = sorted(by_key)
        parent = {k: k for k in keys}

        def find(k: str) -> str:
            while parent[k] != k:
                parent[k] = parent[parent[k]]
                k = parent[k]
            return k

        inverted: dict[str, list[str]] = defaultdict(list)
        for k in keys:
            for s in shingles[k]:
                inverted[s].append(k)
        for k in keys:
            shared: Counter[str] = Counter()
            for s in shingles[k]:
                holders = inverted[s]
                if len(holders) > _COMMON_SHINGLE:
                    continue
                for other in holders:
                    if other > k:
                        shared[other] += 1
            size = len(shingles[k])
            for other, n in shared.items():
                if n / (size + len(shingles[other]) - n) >= near:
                    parent[find(other)] = find(k)
        groups: dict[str, list[str]] = defaultdict(list)
        for k in keys:
            groups[find(k)].append(k)
        for member_keys in groups.values():
            if len(member_keys) > 1:
                members = sorted((b for k in member_keys for b in by_key[k]), key=lambda b: (b.file, b.line))
                cid = _digest(sorted(member_keys))[:12]
                clusters.append(Cluster(cid, "near", members, _declared_reason({m.file for m in members}, declared)))

    clusters.sort(key=lambda c: (c.kind != "exact", -c.redundant_words, c.id))
    return Report(len(files), total_words, min_words, clusters)


def render_report(report: Report, *, limit: int = 40) -> str:
    """A plain-text summary of *report*.

    Args:
        report: The scan result.
        limit: Most clusters to list per kind.

    Returns:
        The text.

    Raises:
        Nothing.
    """
    exact, near = report.counted("exact"), report.counted("near")
    declared = [c for c in report.clusters if c.declared]
    share = (report.redundant_words / report.words) if report.words else 0.0
    out = [
        f"Scanned {report.files} files, {report.words} words (passages of {report.min_words}+ words).",
        f"Exact repeats: {len(exact)} passages, {report.redundant_words} redundant words ({share:.1%}).",
        f"Near repeats to review: {len(near)}. Declared as intended: {len(declared)}.",
    ]
    for title, items in (("Exact", exact), ("Near (review; a variant is not necessarily drift)", near),
                         ("Declared (listed, not counted)", declared)):
        if not items:
            continue
        out.append("")
        out.append(f"{title}:")
        for c in items[:limit]:
            first = c.members[0]
            out.append(f"  {c.id}  {len(c.members)} copies, {first.words} words: {first.preview}")
            out.extend(f"      {m.file}:{m.line}" for m in c.members)
        if len(items) > limit:
            out.append(f"  ... and {len(items) - limit} more (use --json for all)")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Hold
# ---------------------------------------------------------------------------

def baseline_from(report: Report) -> dict[str, Any]:
    """The baseline for *report*: its counted exact clusters, keyed by content id.

    Args:
        report: A scan result.

    Returns:
        Plain data suitable for :func:`write_baseline`.

    Raises:
        Nothing.
    """
    return {
        "version": 1,
        "min_words": report.min_words,
        "clusters": {c.id: {"copies": len(c.members), "words": c.members[0].words, "first": c.members[0].file}
                     for c in sorted(report.counted("exact"), key=lambda c: c.id)},
    }


def write_baseline(report: Report, path: Path | str) -> None:
    """Write the baseline for *report* to *path*.

    Args:
        report: A scan result.
        path: The JSON file to write.

    Raises:
        OSError: The file cannot be written.
    """
    Path(path).write_text(json.dumps(baseline_from(report), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def check_baseline(report: Report, baseline: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Compare *report* with a recorded baseline.

    A problem is an exact, undeclared cluster the baseline does not hold, or one that now has
    more copies than recorded. A baseline entry that no longer occurs is stale: it is returned
    separately so a caller can ask for a refresh (this repository's test does) without calling
    it new repetition.

    Args:
        report: A scan result made with the baseline's ``min_words``.
        baseline: Data read from a baseline file.

    Returns:
        ``(problems, stale)``: messages for new repetition, and ids to drop on the next refresh.

    Raises:
        DocOverlapError: The baseline is malformed or was made with a different ``min_words``.
    """
    known = baseline.get("clusters") if isinstance(baseline, dict) else None
    if not isinstance(known, dict):
        raise DocOverlapError("baseline has no 'clusters' object")
    if baseline.get("min_words") != report.min_words:
        raise DocOverlapError(
            f"baseline was made with min_words={baseline.get('min_words')}, scan used {report.min_words}")
    problems: list[str] = []
    seen: set[str] = set()
    for c in report.counted("exact"):
        seen.add(c.id)
        where = ", ".join(f"{m.file}:{m.line}" for m in c.members)
        entry = known.get(c.id)
        if entry is not None and not isinstance(entry, dict):
            raise DocOverlapError(f"baseline entry {c.id} is not an object")
        if entry is None:
            problems.append(f"new repeated passage {c.id} ({c.members[0].words} words): {where}")
        elif len(c.members) > int(entry.get("copies", 0)):
            problems.append(f"passage {c.id} now has {len(c.members)} copies (baseline {entry.get('copies')}): {where}")
    return problems, sorted(k for k in known if k not in seen)


# ---------------------------------------------------------------------------
# Reduce
# ---------------------------------------------------------------------------

def expand_snippets(text: str, base: Path | str) -> str:
    """Expand whole-file ``--8<-- "path"`` lines, the one form :func:`plan_extract` writes.

    This is not a full implementation of ``pymdownx.snippets``. A line that names a line range
    or section, an escaped line, the block form, a URL, or a file that does not exist is left
    as written. The site build is the authority on how a page renders.

    Args:
        text: Markdown source.
        base: Directory the snippet paths are relative to (the site's ``base_path``).

    Returns:
        The text with each matching line replaced by the named file's lines.

    Raises:
        DocOverlapError: Includes nest deeper than the supported depth.
    """
    return _expand(text, Path(base), 0)


def _expand(text: str, base_path: Path, depth: int) -> str:
    if depth > _MAX_SNIPPET_DEPTH:
        raise DocOverlapError("snippet includes nest too deeply (a cycle?)")
    out: list[str] = []
    for line in text.split("\n"):
        m = _SNIPPET_RE.match(line[:-1] if line.endswith("\r") else line)
        target = (base_path / m.group("path")) if m else None
        if target is None or not target.is_file():
            out.append(line)
            continue
        body = _expand(_read(target), base_path, depth + 1)
        if not body:
            continue  # an empty snippet contributes no lines
        if body.endswith("\n"):
            body = body[:-1]
        out.extend(m.group("indent") + part for part in body.split("\n"))
    return "\n".join(out)


@dataclasses.dataclass
class ExtractPlan:
    """A proposed move of one shared run of lines into a snippet file.

    Attributes:
        snippet: Path of the snippet file.
        directive: The line that replaces the run in each page.
        body: The snippet file's text.
        lines: Number of lines in the run.
        words: Words in the run.
        targets: For each page, ``(path, 1-based first line, new text)``.
    """

    snippet: Path
    directive: str
    body: str
    lines: int
    words: int
    targets: list[tuple[Path, int, str]]


def _common_runs(a: Sequence[str], b: Sequence[str]) -> list[tuple[int, int]]:
    """Maximal runs of consecutive lines of *a* that also occur consecutively in *b*: ``(start, length)``."""
    positions: dict[str, list[int]] = defaultdict(list)
    for j, line in enumerate(b):
        positions[line].append(j)
    best: dict[int, int] = {}
    prev: dict[int, int] = {}
    for i, line in enumerate(a):
        cur: dict[int, int] = {}
        for j in positions.get(line, ()):
            cur[j] = prev.get(j - 1, 0) + 1
            start = i - cur[j] + 1
            if cur[j] > best.get(start, 0):
                best[start] = cur[j]
        prev = cur
    runs = sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))
    kept: list[tuple[int, int]] = []
    for start, length in runs:
        if not any(s <= start and start + length <= s + n for s, n in kept):
            kept.append((start, length))
    return kept


def _find(lines: Sequence[str], run: Sequence[str]) -> list[int]:
    """Indexes at which *run* occurs in *lines*."""
    n = len(run)
    return [i for i in range(len(lines) - n + 1) if lines[i] == run[0] and list(lines[i:i + n]) == list(run)]


def _segments(run: Sequence[str], fenced: Sequence[bool]) -> list[tuple[int, int]]:
    """Split a run at snippet lines and, outside code, at headings, rules and lines with a relative link.

    Blank edges are trimmed. Returns ``(offset, length)`` pairs.
    """
    out: list[tuple[int, int]] = []
    start = 0
    for k in range(len(run) + 1):
        barrier = k < len(run) and bool(_ANY_SNIPPET_RE.match(run[k]) or (not fenced[k] and (
            _HEADING_RE.match(run[k]) or _RULE_RE.match(run[k]) or _REL_LINK_RE.search(run[k]))))
        if k == len(run) or barrier:
            lo, hi = start, k
            while lo < hi and not run[lo].strip():
                lo += 1
            while hi > lo and not run[hi - 1].strip():
                hi -= 1
            if hi > lo:
                out.append((lo, hi - lo))
            start = k + 1
    return out


def _whole_passages(first: Sequence[str], start: int, length: int, all_lines: Sequence[Sequence[str]],
                    fm_ends: Sequence[int]) -> tuple[int, int]:
    """Shrink a run so it begins and ends on a passage boundary in every file.

    A boundary is a blank line, a file edge, or the end of front matter.

    A run that starts part-way through a list or paragraph in any file loses its first passage;
    one that stops part-way loses its last. Returns ``(start, length)``; length 0 when nothing is left.
    """
    def edges_ok(run: Sequence[str]) -> tuple[bool, bool]:
        head = tail = True
        for ls, fm_end in zip(all_lines, fm_ends):
            at = _find(ls, run)
            if len(at) != 1:
                continue  # the caller rejects runs that are not unique
            i, j = at[0], at[0] + len(run)
            head = head and (i == 0 or i == fm_end or not ls[i - 1].strip())
            tail = tail and (j >= len(ls) or not ls[j].strip())
        return head, tail

    while length > 0:
        run = first[start:start + length]
        head, tail = edges_ok(run)
        if head and tail:
            return start, length
        blanks = [k for k, ln in enumerate(run) if not ln.strip()]
        if not blanks:
            return start, 0
        if not head:
            cut = blanks[0] + 1
            while cut < length and not run[cut].strip():
                cut += 1
            start, length = start + cut, length - cut
        else:
            cut = blanks[-1]
            while cut > 0 and not run[cut - 1].strip():
                cut -= 1
            length = cut
    return start, 0


def plan_extract(
    name: str,
    files: Sequence[Path | str],
    *,
    root: Path | str = ".",
    snippet_dir: str = DEFAULT_SNIPPET_DIR,
    min_lines: int = MIN_EXTRACT_LINES,
) -> ExtractPlan:
    """Plan moving the longest run of lines shared by every file in *files* into one snippet.

    A run qualifies when it lies after any front matter, begins and ends on a passage boundary
    (a blank line or a file edge) in every file, does not begin or end inside a code block,
    holds no heading, snippet line or relative link, has at least *min_lines* non-blank lines,
    and occurs exactly once in each file. If the snippet file already exists with the
    same text, the plan reuses it, so a further page can be pointed at an existing snippet.

    Args:
        name: Snippet name: lower-case letters, digits and hyphens.
        files: Two or more pages (one is enough when the snippet already exists). A relative
            path is resolved against the current directory, not *root*.
        root: Repository root; snippet paths in the directive are relative to it.
        snippet_dir: Directory for snippet files, relative to *root*.
        min_lines: Fewest non-blank lines a run may have.

    Returns:
        The plan. Nothing is written.

    Raises:
        DocOverlapError: The name or a path is unacceptable, no run qualifies, or the snippet
            file exists with different text.
    """
    if not _NAME_RE.match(name):
        raise DocOverlapError(f"snippet name {name!r} must be lower-case letters, digits and hyphens")
    root_path = Path(root).resolve()
    pages = [Path(f) for f in files]
    if len({p.resolve() for p in pages}) != len(pages):
        raise DocOverlapError("the same file was named twice")
    snippet = (root_path / snippet_dir / f"{name}.md").resolve()
    if root_path not in snippet.parents:
        raise DocOverlapError(f"snippet directory {snippet_dir!r} is outside {root_path}")
    existing = _read(snippet) if snippet.is_file() else None
    if not pages:
        raise DocOverlapError("name the pages that share the passage")
    if len(pages) < 2 and existing is None:
        raise DocOverlapError("name at least two files that share the passage")
    texts = []
    for p in pages:
        if not p.is_file() or root_path not in p.resolve().parents:
            raise DocOverlapError(f"{p} is not a file under {root_path}")
        if p.resolve() == snippet:
            raise DocOverlapError(f"{p} is the snippet file itself")
        texts.append(_read(p))
    all_lines = [t.split("\n") for t in texts]
    fences = [_fence_state(ls) for ls in all_lines]
    fm_ends = [_front_matter_end(ls) for ls in all_lines]

    first = all_lines[0]
    if existing is not None:
        want = existing[:-1].split("\n") if existing.endswith("\n") else existing.split("\n")
        candidates = [(i, len(want)) for i in _find(first, want)]
    else:
        candidates = _common_runs(first, all_lines[1])
        for other in all_lines[2:]:
            candidates = [(start + s2, n2) for start, length in candidates
                          for s2, n2 in _common_runs(first[start:start + length], other)]

    best: tuple[int, int, int] | None = None  # (words, start, length) within the first file
    for start, length in candidates:
        if start < fm_ends[0]:
            start, length = fm_ends[0], length - (fm_ends[0] - start)
        if length <= 0:
            continue
        for off, seg_len in _segments(first[start:start + length], fences[0][start:start + length]):
            s, seg_len = _whole_passages(first, start + off, seg_len, all_lines, fm_ends)
            run = first[s:s + seg_len]
            if sum(1 for ln in run if ln.strip()) < min_lines:
                continue
            if any(_REL_LINK_RE.search(ln) for ln in run):
                continue
            ok = True
            for ls, fm_end in zip(all_lines, fm_ends):
                at = _find(ls, run)
                if len(at) != 1 or at[0] < fm_end:
                    ok = False
                    break
                before = _inside_before(ls)
                if before[at[0]] or before[at[0] + seg_len]:
                    ok = False  # the run would begin or end inside a code block
                    break
            if not ok:
                continue
            words = sum(len(_WORD_RE.findall(ln.lower())) for ln in run)
            if best is None or words > best[0]:
                best = (words, s, seg_len)
    if best is None:
        raise DocOverlapError("no shared run of lines qualifies for extraction in these files")
    words, s, seg_len = best
    run = first[s:s + seg_len]
    body = "\n".join(run) + "\n"
    if existing is not None and existing != body:
        raise DocOverlapError(f"{snippet} already exists with different text")
    directive = f'--8<-- "{snippet.relative_to(root_path).as_posix()}"'
    targets: list[tuple[Path, int, str]] = []
    for p, ls in zip(pages, all_lines):
        at = _find(ls, run)[0]
        line = directive + ("\r" if run[-1].endswith("\r") else "")  # keep the page's line ending
        targets.append((p, at + 1, "\n".join([*ls[:at], line, *ls[at + seg_len:]])))
    return ExtractPlan(snippet, directive, body, seg_len, words, targets)


def apply_extract(plan: ExtractPlan, *, root: Path | str = ".") -> None:
    """Write *plan*, after proving every rewritten page expands to its original text.

    Args:
        plan: A plan from :func:`plan_extract`.
        root: Repository root (the snippets ``base_path``).

    Raises:
        DocOverlapError: A page would not expand to its original text. Nothing is left changed.
        OSError: A file cannot be written.
    """
    root_path = Path(root).resolve()
    originals = {p: _read(p) for p, _, _ in plan.targets}
    created = not plan.snippet.exists()
    plan.snippet.parent.mkdir(parents=True, exist_ok=True)
    _write(plan.snippet, plan.body)
    done = False
    try:
        for p, _, new_text in plan.targets:
            # Compare expanded with expanded: a page may already include other snippets.
            if expand_snippets(new_text, root_path) != expand_snippets(originals[p], root_path):
                raise DocOverlapError(f"{p} would not expand to its original text; nothing was changed")
        for p, _, new_text in plan.targets:
            _write(p, new_text)
        done = True
    finally:
        if not done:  # put back whatever was written before the failure
            for p, text in originals.items():
                if _read(p) != text:
                    _write(p, text)
            if created:
                plan.snippet.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m agentteams.doc_overlap",
        description="Find, reduce and hold repeated passages across Markdown files.")
    p.add_argument("paths", nargs="*", help="files or directories to scan (or, with --extract, the pages)")
    p.add_argument("--root", default=".", help="repository root that paths are reported relative to (default: .)")
    p.add_argument("--min-words", type=int, default=DEFAULT_MIN_WORDS, help="shortest passage compared")
    p.add_argument("--near", type=float, default=DEFAULT_NEAR, help="similarity for near repeats; 1.0 turns them off")
    p.add_argument("--derived", action="append", default=[], metavar="PATTERN", help="extra path pattern not to scan")
    p.add_argument("--declared", metavar="FILE", help="JSON file of overlap declared as intended")
    p.add_argument("--json", action="store_true", help="print the report as JSON")
    p.add_argument("--baseline", metavar="FILE", help="baseline file for --check")
    p.add_argument("--check", action="store_true", help="exit 1 if a repeated passage is not in the baseline")
    p.add_argument("--write-baseline", metavar="FILE", help="record the exact repeats now present")
    p.add_argument("--extract", metavar="NAME", help="move the longest run shared by the named pages into a snippet")
    p.add_argument("--snippet-dir", default=DEFAULT_SNIPPET_DIR, help="where --extract puts snippet files")
    p.add_argument("--yes", action="store_true", help="with --extract, write the change (default: show it only)")
    return p


def main(argv: list[str] | None = None) -> int:
    """Run the command line.

    Args:
        argv: Arguments, or ``None`` for ``sys.argv[1:]``.

    Returns:
        0 when clean; 1 when ``--check`` finds new repetition; 2 on a refused or invalid request.

    Raises:
        Nothing. Refusals and file errors are printed and returned as exit status 2.
    """
    args = _parser().parse_args(argv)
    try:
        if args.extract:
            plan = plan_extract(args.extract, args.paths, root=args.root, snippet_dir=args.snippet_dir)
            print(f"Run of {plan.lines} lines, {plan.words} words -> {plan.snippet}")
            for path, line, _ in plan.targets:
                print(f"  {path}:{line}  replaced by  {plan.directive}")
            if not args.yes:
                print("Dry run. Pass --yes to write.")
                return 0
            apply_extract(plan, root=args.root)
            print("Written. Every page expands to its original text.")
            return 0
        if not args.paths:
            print("Error: name at least one file or directory", file=sys.stderr)
            return 2
        report = scan(args.paths, root=args.root, min_words=args.min_words, near=args.near,
                      derived=(*DEFAULT_DERIVED, *args.derived), declared=load_declared(args.declared))
        if args.write_baseline and args.check:
            print("Error: --write-baseline and --check cannot be used together", file=sys.stderr)
            return 2
        if args.write_baseline:
            write_baseline(report, args.write_baseline)
        print(json.dumps(report.to_json(), indent=2) if args.json else render_report(report), end="")
        if args.check:
            if not args.baseline:
                print("Error: --check needs --baseline FILE", file=sys.stderr)
                return 2
            try:
                baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
            except ValueError as exc:
                raise DocOverlapError(f"cannot read baseline {args.baseline}: {exc}") from exc
            problems, stale = check_baseline(report, baseline)
            for s in stale:
                print(f"note: baseline entry {s} no longer occurs; refresh with --write-baseline", file=sys.stderr)
            for msg in problems:
                print(f"FAIL: {msg}", file=sys.stderr)
            return 1 if problems else 0
        return 0
    except (DocOverlapError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


__all__ = [
    "Block", "Cluster", "DocOverlapError", "ExtractPlan", "Report", "apply_extract", "baseline_from",
    "check_baseline", "collect_files", "expand_snippets", "load_declared", "main", "plan_extract",
    "render_report", "scan", "split_blocks", "write_baseline",
]

if __name__ == "__main__":
    sys.exit(main())
