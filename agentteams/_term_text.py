"""Text machinery behind :mod:`agentteams.term_index`: form keys, scope globs, masking and the matcher.

Ported from researchteam's ``scripts/term_index_engine.py`` (researchteam 5beca9f, #193/#195/#196). The
logic is kept identical so a register built by either produces the same occurrences. The one change is
that the reference-section heading is a parameter (``skip_heading``) instead of a constant.

CEILING (do not soften): matching finds forms, not meanings. An occurrence is a place where a registered
form appears, not evidence that the term is used in its defined sense. Standard library only.
"""
from __future__ import annotations

import bisect
import re

FENCE = re.compile(r"[ \t]{0,3}(`{3,}|~{3,})")
HEADING = re.compile(r"^#{1,6}[ \t]+")
FRONT_MATTER = re.compile(r"﻿?---[ \t]*\n(?:(?!\n[ \t]*\n).)*?\n(?:---|\.\.\.)[ \t]*(?:\n|\Z)", re.S)
# Not prose: link targets, reference-link definitions, bare URLs. Each pattern stops at a line break or
# a delimiter, so none can rescan a long run. Code spans are found by a linear pass (_code_spans).
NOT_PROSE = [re.compile(p, re.M) for p in (
    r"(?<=\])\((?:<[^<>\n]*>|[^()\s]*)\)", r"^[ \t]{0,3}\[[^\]\n]+\]:[^\n]*$", r"<?https?://[^\s<>)]+>?")]
BACKTICKS = re.compile(r"`+")
WORD = re.compile(r"[^\W_]+")
SEP = r"[*_]{0,3}(?:[ \t]+|[ \t]*\n(?:[ \t]*>)*[ \t]*|-)[*_]{0,3}"  # between the words of a form


# ----------------------------------------------------------------------------- forms
def form_key(text: str, folds: bool = True) -> str:
    """The one test of whether two forms, or a form and matched text, are the same.

    Whatever the matcher lets stand between words (spaces, a line break, hyphens, emphasis, a blockquote
    prefix) is one space.

    Args:
        text: A form or a matched span.
        folds: Whether case is folded (a lower-case form matches without regard to case).

    Returns:
        The normalised key.
    """
    flat = " ".join(w for w in re.split(r"[\s>*_-]+", text) if w)
    return flat.casefold() if folds else flat


def form_keys(form: str, plural: bool, folds: bool = True) -> set[str]:
    """Every key a form answers to: itself and, with the automatic plural, its ``s`` and ``es`` forms.

    Args:
        form: A registered form.
        plural: Whether the term takes the automatic plural.
        folds: Whether case is folded.

    Returns:
        The set of keys.
    """
    return {form_key(form + suffix, folds) for suffix in (("", "s", "es") if plural else ("",))}


def match_words(form: str) -> list[str]:
    """The words a pattern is built from: the form split at spaces and hyphens, and nowhere else.

    Args:
        form: A registered form.

    Returns:
        Its words.
    """
    return [w for w in re.split(r"[\s-]+", form) if w]


def lead_key(form: str) -> tuple[bool, str, int]:
    """How the matcher files a form: case mode, the first letter-or-digit run of its first word, and its offset.

    ".NET runtime" is filed under "NET" with offset 1: in text, the form starts ``offset`` characters
    before the word the scan is visiting. Every word of a valid form has such a run (checked at load).

    Args:
        form: A registered form.

    Returns:
        ``(folds, lead, offset)``.
    """
    folds, run = form == form.lower(), WORD.search(match_words(form)[0])
    return folds, (run.group().casefold() if folds else run.group()), run.start()


def term_forms(term: dict) -> list[tuple[str, str]]:
    """``(form, kind)`` for a term: the label, its variants and its discouraged forms.

    Args:
        term: A validated term record.

    Returns:
        The forms, label first.
    """
    return ([(term["label"], "label")] + [(v, "variant") for v in term.get("variants", [])]
            + [(v, "discouraged") for v in term.get("discouraged", [])])


# ----------------------------------------------------------------------------- scope globs
def glob_match(pattern: str, path: str) -> bool:
    """Match a scope glob (``**/``, ``**``, ``*``, ``?``) against a POSIX relative path.

    Own matcher: pathlib's differs across Python versions, and a compiled glob could backtrack. This keeps
    the positions reachable in ``path`` after each token, with at most one pass over ``path`` per token.

    Args:
        pattern: The glob.
        path: A POSIX path relative to the scope root.

    Returns:
        Whether the whole path matches.
    """
    size = len(path)
    reach = {0}
    for tok in re.findall(r"\*\*/|\*\*|\*|\?|.", pattern, re.S):
        if not reach:
            return False
        if tok == "**":  # anything: every position from the earliest reachable one
            reach = set(range(min(reach), size + 1))
        elif tok == "**/":  # nothing, or any number of whole directories
            first = min(reach)
            reach = reach | {j + 1 for j in range(first, size) if path[j] == "/"}
        elif tok == "*":  # anything but a slash: extend each position to the end of its segment, once
            nxt, covered = set(), -1
            for i in sorted(reach):
                if i > covered:
                    end = path.find("/", i)
                    covered = size if end < 0 else end
                    nxt.update(range(i, covered + 1))
            reach = nxt
        else:
            reach = {i + 1 for i in reach if i < size and (path[i] == tok or (tok == "?" and path[i] != "/"))}
    return size in reach


# ----------------------------------------------------------------------------- masking what is not prose
def _blank(chars: list, start: int, end: int) -> None:
    for i in range(start, end):
        if chars[i] != "\n":
            chars[i] = "\x00"


def _code_spans(masked: str) -> list[tuple[int, int]]:
    """Inline code: a run of backticks closes at the next run of the same length on the same line.

    Linear: right to left each run learns the next run of its length; left to right openers are paired.
    """
    runs = [(m.start(), m.end()) for m in BACKTICKS.finditer(masked)]
    breaks = [i for i, c in enumerate(masked) if c == "\n"]
    lines = [bisect.bisect_left(breaks, start) for start, _ in runs]
    next_same: list[int | None] = [None] * len(runs)
    nearest: dict[tuple[int, int], int] = {}
    for i in range(len(runs) - 1, -1, -1):
        key = (lines[i], runs[i][1] - runs[i][0])
        next_same[i] = nearest.get(key)
        nearest[key] = i
    spans, i = [], 0
    while i < len(runs):
        j = next_same[i]
        if j is None:
            i += 1
        else:
            spans.append((runs[i][0], runs[j][1]))
            i = j + 1
    return spans


def mask(text: str, skip_heading: re.Pattern | None = None) -> tuple[str, list[int]]:
    """Blank out what is not prose, keeping offsets and line breaks.

    Front matter, fenced code, HTML comments, inline code, link targets and URLs are blanked, and so is
    every section whose heading matches ``skip_heading`` (researchteam passes its reference-list headings).

    Args:
        text: The document, with ``\\n`` line breaks.
        skip_heading: A pattern for the heading lines whose sections are not prose, or ``None``.

    Returns:
        ``(masked, heading_ends)``: the masked text and the offsets of the line breaks that end a heading
        (a match must not run out of a heading, and the caller needs the breaks intact to find paragraphs).
    """
    chars, heading_ends = list(text), []
    front = FRONT_MATTER.match(text)
    pos, fence, in_skip = (front.end() if front else 0), "", False
    if front:
        _blank(chars, 0, front.end())
    for line in text[pos:].split("\n"):
        end = pos + len(line)
        opened = FENCE.match(line)
        if fence:
            _blank(chars, pos, end)
            if opened and opened.group(1)[0] == fence[0] and len(opened.group(1)) >= len(fence) \
                    and not line[opened.end():].strip():
                fence = ""  # a closing fence: same character, at least as long, nothing after it
        elif opened:
            fence = opened.group(1)
            _blank(chars, pos, end)
        elif HEADING.match(line):
            in_skip = skip_heading is not None and skip_heading.match(line) is not None
            if in_skip:
                _blank(chars, pos, end)
            heading_ends.append(end)
        elif in_skip:
            _blank(chars, pos, end)
        pos = end + 1
    masked = "".join(chars)
    start = masked.find("<!--")
    while start >= 0:  # HTML comments, by search and not by a pattern that could rescan
        close = masked.find("-->", start + 4)
        stop = len(masked) if close < 0 else close + 3
        _blank(chars, start, stop)
        start = masked.find("<!--", stop)
    for start, end in _code_spans("".join(chars)):
        _blank(chars, start, end)
    for pattern in NOT_PROSE:
        for m in pattern.finditer("".join(chars)):
            _blank(chars, m.start(), m.end())
    return "".join(chars), [e for e in heading_ends if e < len(chars)]


def quoted_spans(masked: str, heading_ends: list[int]) -> list[tuple[int, int]]:
    """Sorted, merged spans that count as quotation: blockquote lines and balanced double quotation marks.

    Args:
        masked: Output of :func:`mask`.
        heading_ends: The heading-end offsets :func:`mask` returned.

    Returns:
        ``(start, end)`` spans.
    """
    spans, pos, breaks = [], 0, set(heading_ends)
    for line in masked.split("\n"):
        if line.lstrip(" \t").startswith(">"):
            spans.append((pos, pos + len(line)))
        pos += len(line) + 1
    start = 0
    for m in re.finditer(r"\n[ \t]*\n|\n", masked):  # a paragraph ends at a blank line or after a heading
        if m.group() == "\n" and m.start() not in breaks:
            continue
        spans += _paragraph_quotes(masked, start, m.start())
        start = m.end()
    spans += _paragraph_quotes(masked, start, len(masked))
    merged: list[tuple[int, int]] = []
    for s, e in sorted(spans):
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(e, merged[-1][1]))
        else:
            merged.append((s, e))
    return merged


def _paragraph_quotes(masked: str, start: int, end: int) -> list[tuple[int, int]]:
    text = masked[start:end]
    spans = [(start + m.start(), start + m.end()) for m in re.finditer(r"“[^“”]*”", text)]
    marks = [i for i, c in enumerate(text) if c == '"']
    if len(marks) % 2 == 0:  # an odd number of straight marks exempts nothing
        spans += [(start + a, start + b) for a, b in zip(marks[::2], marks[1::2])]
    return spans


# ----------------------------------------------------------------------------- matching
class Matcher:
    """Every form of a register, filed by :func:`lead_key`.

    The scan visits each word of a document once and tries the forms filed under the word and under its
    singular, in each case mode: at most ``MAX_FORMS_PER_LEAD`` per word and case mode (enforced at load),
    each at most ``MAX_FORM_WORDS`` words. Lower-case forms match without regard to case; a form with a
    capital letter matches exactly.

    Args:
        reg: A validated register.
        skip_heading: Passed to :func:`mask`.
    """

    def __init__(self, reg: dict, skip_heading: re.Pattern | None = None) -> None:
        self.skip_heading = skip_heading
        self.lookup: dict[tuple[bool, str], tuple[str, str, str]] = {}
        self.bodies: dict[tuple[bool, str, int], list[tuple[int, str]]] = {}
        self.offsets: dict[tuple[bool, str], set[int]] = {}
        self.compiled: dict[tuple[bool, str, int], re.Pattern] = {}
        for term in reg["terms"]:
            plural = term.get("plural", True)
            for form, kind in term_forms(term):
                key = lead_key(form)
                words = match_words(form)
                for form_id in form_keys(form, plural, key[0]):
                    self.lookup.setdefault((key[0], form_id), (term["id"], form, kind))
                body = SEP.join(re.escape(w) for w in words) + ("(?:e?s)?" if plural else "")
                self.bodies.setdefault(key, []).append((len(form), body))
                self.offsets.setdefault(key[:2], set()).add(key[2])

    def _pattern(self, key: tuple[bool, str, int]) -> re.Pattern:
        if key not in self.compiled:  # longest first, so the longest form wins at any one position
            bodies = sorted(self.bodies[key], key=lambda it: (-it[0], it[1]))
            self.compiled[key] = re.compile(r"(?<![^\W_])(?<!-)(?:" + "|".join(b for _, b in bodies)
                                            + r")(?![^\W_])(?!-)", re.I if key[0] else 0)
        return self.compiled[key]

    def _hits(self, searchable: str) -> list[tuple[int, int, tuple[str, str, str]]]:
        """Candidate ``(start, end, (term, form, kind))`` matches; the caller resolves overlaps."""
        hits = []
        for word in WORD.finditer(searchable):
            token, start = word.group(), word.start()
            stems = {token}  # a plural one-word form is filed under its singular, so try that too
            if token[-1:] in ("s", "S"):
                stems.add(token[:-1])
            if token[-2:].lower() == "es":
                stems.add(token[:-2])
            for folds in (True, False):
                for stem in stems:
                    lead = stem.casefold() if folds else stem
                    for offset in self.offsets.get((folds, lead), ()):
                        m = self._pattern((folds, lead, offset)).match(searchable, start - offset) \
                            if start >= offset else None
                        hit = self.lookup.get((folds, form_key(m.group(), folds))) if m else None
                        if hit:
                            hits.append((m.start(), m.end(), hit))
        return hits

    def occurrences(self, text: str) -> list[dict]:
        """Every non-overlapping occurrence of a registered form in ``text``.

        Args:
            text: The document, with ``\\n`` line breaks.

        Returns:
            Records ``{term, line, column, form, kind, quoted}``; the leftmost match wins, and at one
            position the longest.
        """
        masked, heading_ends = mask(text, self.skip_heading)
        spans = quoted_spans(masked, heading_ends)
        chars = list(masked)
        for end in heading_ends:
            chars[end] = "\x00"
        line_starts = [0] + [i + 1 for i, c in enumerate(text) if c == "\n"]
        span_starts = [s for s, _ in spans]
        out, last_end = [], 0
        by_start_then_longest = sorted(self._hits("".join(chars)), key=lambda h: (h[0], -(h[1] - h[0])))
        for start, end, (tid, form, kind) in by_start_then_longest:
            if start < last_end:
                continue  # overlapping forms: the leftmost wins, and at one position the longest
            last_end = end
            line = bisect.bisect_right(line_starts, start)
            k = bisect.bisect_right(span_starts, start) - 1
            out.append({"term": tid, "line": line, "column": start - line_starts[line - 1] + 1, "form": form,
                        "kind": kind, "quoted": k >= 0 and start < spans[k][1]})
        return out
