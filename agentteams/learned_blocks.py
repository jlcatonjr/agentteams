"""learned_blocks.py — parse, compose and verify an agent file's AGENTTEAMS-LEARNED block.

An agent records what it learns about its project in one explicit, agent-owned region of its
own agent file::

    <!-- AGENTTEAMS-LEARNED:BEGIN -->
    - notes the agent wrote
    <!-- AGENTTEAMS-LEARNED:END -->

That region is the only thing ``agentteams --sync-agent-docs`` (:mod:`agentteams.agent_doc_sync`)
moves between the copies of one agent across frameworks. This module is the pure-text half of
that sync: it finds the block, renders a replacement, and proves that a composed file differs
from the original **only inside the block**. It never touches the filesystem.

Two host formats:

* **markdown** — ``.github/agents/*.agent.md`` and ``.claude/agents/*.md``. The markers are whole
  lines at column 0, in the body (never in YAML front matter) and never inside an
  ``AGENTTEAMS:BEGIN/END`` template fence (``--update`` owns those). A file without a block gets
  one appended at its end.
* **recipe** — ``.goose/recipes/*.yaml``. The block lives inside the ``instructions: |`` literal
  block scalar, indented to that scalar's indent. Its canonical content is the block with that
  indent removed, so the same notes digest identically in every framework. A file without a block
  gets one appended after the scalar's last non-blank line, still inside the scalar.

The safety argument does not rest on the parser alone. :func:`verify_composed` re-parses the
composed text and checks that everything outside the block is byte-identical to the original,
that the template fence set is unchanged, and — for recipes — that the structural recipe check
and every top-level key other than ``instructions`` are unchanged. Content that could escape the
block (a fence or learned marker, a line break other than ``\\n``) is refused by
:func:`content_problems` before anything is composed.

Stdlib only; no YAML dependency (the codebase hand-parses recipes, see
:mod:`agentteams.frameworks.goose_recipe_read`).
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from agentteams.fences import _extract_fenced_regions
from agentteams.frameworks.goose_recipe_validate import _validate_recipe_yaml
from agentteams.unfenced import _FENCE_BEGIN_RE, _FENCE_END_RE

#: The opening marker line (without indent or newline).
BEGIN_MARKER = "<!-- AGENTTEAMS-LEARNED:BEGIN -->"
#: The closing marker line (without indent or newline).
END_MARKER = "<!-- AGENTTEAMS-LEARNED:END -->"

#: Host formats this module understands.
MARKDOWN = "markdown"
RECIPE = "recipe"

#: Any agentteams fence-family token (``AGENTTEAMS:``, ``AGENTTEAMS-LEARNED:``,
#: ``AGENTTEAMS-BRIDGE:`` …). A learned block may contain none of them: a fence marker inside it
#: would let propagated text open or close a template fence in the target.
_FENCE_TOKEN_RE = re.compile(r"AGENTTEAMS[:\-]")

#: Characters that some reader treats as a line break (``str.splitlines`` does, and YAML 1.1
#: treats NEL/LS/PS as breaks) or that have no business in prose. A block is split on ``\n``
#: only; any of these could start a "line" the re-indent never saw — the dedent escape.
_FORBIDDEN_CHARS = frozenset("\r\x00\x0b\x0c\x1c\x1d\x1e\x85  ")

_INSTRUCTIONS_LINE_RE = re.compile(r"^instructions:[ \t]*\|[ \t]*$")
_TOP_LEVEL_KEY_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*:")
_RECIPE_SHAPE_RE = re.compile(r'^version:\s*"', re.MULTILINE)


class LearnedBlockError(ValueError):
    """Raised when a file's learned block (or its host structure) cannot be handled safely."""


@dataclass(frozen=True)
class LearnedBlock:
    """One located learned block.

    Attributes:
        content: Canonical content: the lines between the markers, each ending in ``\\n``, with
            the recipe indent removed. ``""`` for an empty block.
        start: Offset of the first character of the BEGIN marker line.
        end: Offset just past the END marker line (including its newline, when it has one).
    """

    content: str
    start: int
    end: int

    @property
    def digest(self) -> str:
        """Return the sha256 hex digest of :attr:`content`."""
        return content_digest(self.content)


@dataclass(frozen=True)
class ParsedDoc:
    """A host file as far as the learned block is concerned.

    Attributes:
        kind: :data:`MARKDOWN` or :data:`RECIPE`.
        block: The located block, or ``None`` when the file has none.
        insert_at: Where a block is inserted when the file has none.
        indent: The block indent (``""`` for markdown).
        fm_end: Offset just past the YAML front matter (``0`` when there is none).
    """

    kind: str
    block: LearnedBlock | None
    insert_at: int
    indent: str
    fm_end: int


def content_digest(content: str) -> str:
    """Return the sha256 hex digest of canonical block content.

    Args:
        content: Canonical block content.

    Returns:
        The 64-character hex digest of the UTF-8 encoding.
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _split_lines(text: str) -> list[str]:
    """Split on ``\\n`` only, keeping each line's newline (unlike ``str.splitlines``)."""
    parts = text.split("\n")
    lines = [p + "\n" for p in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _offsets(lines: list[str]) -> list[int]:
    """Return the start offset of each line, plus the total length as a final entry."""
    out = [0]
    for line in lines:
        out.append(out[-1] + len(line))
    return out


def _fence_spans(lines: list[str]) -> list[tuple[int, int]]:
    """Return ``(begin, end)`` line-index pairs of each ``AGENTTEAMS:BEGIN/END`` template fence."""
    spans: list[tuple[int, int]] = []
    open_at: int | None = None
    for idx, line in enumerate(lines):
        if open_at is None and _FENCE_BEGIN_RE.search(line):
            open_at = idx
        elif open_at is not None and _FENCE_END_RE.search(line):
            spans.append((open_at, idx))
            open_at = None
    if open_at is not None:
        spans.append((open_at, len(lines)))
    return spans


def _marker_indices(lines: list[str], marker: str) -> list[int]:
    """Return the indices of lines whose stripped text is exactly *marker*."""
    return [i for i, line in enumerate(lines) if line.strip() == marker]


def _locate(lines: list[str], lo: int, hi: int) -> tuple[int, int] | None:
    """Return ``(begin, end)`` marker line indices, or None; raise on an ambiguous layout."""
    begins = _marker_indices(lines, BEGIN_MARKER)
    ends = _marker_indices(lines, END_MARKER)
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1:
        raise LearnedBlockError(
            f"expected exactly one {BEGIN_MARKER} and one {END_MARKER} line, found "
            f"{len(begins)} and {len(ends)}"
        )
    b, e = begins[0], ends[0]
    if not lo <= b < e < hi:
        raise LearnedBlockError("the learned block is out of order or outside the allowed region")
    for f_begin, f_end in _fence_spans(lines):
        if f_begin <= b <= f_end or f_begin <= e <= f_end:
            raise LearnedBlockError("the learned block sits inside an AGENTTEAMS template fence")
    return b, e


def _front_matter_end(lines: list[str]) -> int:
    """Return the index of the first body line (0 when there is no YAML front matter)."""
    if not lines or lines[0].rstrip("\n") != "---":
        return 0
    for idx in range(1, len(lines)):
        if lines[idx].rstrip("\n") == "---":
            return idx + 1
    raise LearnedBlockError("unterminated YAML front matter")


def parse_markdown(text: str) -> ParsedDoc:
    """Locate the learned block of a markdown agent file.

    Args:
        text: The whole file.

    Returns:
        The parsed document (``block`` is None when the file has no block; ``insert_at`` is then
        the end of the file).

    Raises:
        LearnedBlockError: Unterminated front matter, more than one block, a marker that is not a
            whole column-0 line, a block in the front matter, or a block inside a template fence.
    """
    lines = _split_lines(text)
    offs = _offsets(lines)
    body = _front_matter_end(lines)
    loc = _locate(lines, body, len(lines))
    if loc is None:
        return ParsedDoc(MARKDOWN, None, len(text), "", offs[body])
    b, e = loc
    for idx in (b, e):
        if lines[idx].rstrip("\n") not in (BEGIN_MARKER, END_MARKER):
            raise LearnedBlockError("learned markers must be whole lines starting at column 0")
    block = LearnedBlock("".join(lines[b + 1:e]), offs[b], offs[e + 1])
    return ParsedDoc(MARKDOWN, block, len(text), "", offs[body])


def _instructions_scalar(lines: list[str]) -> tuple[int, int, str]:
    """Return ``(first content line, end line exclusive, indent)`` of ``instructions: |``."""
    heads = [i for i, line in enumerate(lines) if line.startswith("instructions:")]
    if len(heads) != 1 or not _INSTRUCTIONS_LINE_RE.match(lines[heads[0]].rstrip("\n")):
        raise LearnedBlockError("expected exactly one plain `instructions: |` block scalar")
    first = heads[0] + 1
    end = len(lines)
    indent: str | None = None
    for idx in range(first, len(lines)):
        line = lines[idx]
        if line.strip() == "":
            continue
        if line[0] not in " \t":
            end = idx
            break
        if line[0] == "\t":
            raise LearnedBlockError("tab indentation in the instructions block")
        if indent is None:
            indent = line[: len(line) - len(line.lstrip(" "))]
    if not indent:
        raise LearnedBlockError("the instructions block scalar is empty")
    return first, end, indent


def parse_recipe(text: str) -> ParsedDoc:
    """Locate the learned block inside a goose recipe's ``instructions: |`` scalar.

    Args:
        text: The whole recipe YAML.

    Returns:
        The parsed document. ``content`` has the scalar indent removed; ``insert_at`` is just past
        the scalar's last non-blank line.

    Raises:
        LearnedBlockError: No (or more than one) plain ``instructions: |`` scalar, a marker outside
            the scalar or not at the scalar indent, a block line indented less than the scalar
            (the dedent case: it would end the scalar), or a block inside a template fence.
    """
    lines = _split_lines(text)
    offs = _offsets(lines)
    first, end, indent = _instructions_scalar(lines)
    last = max((i for i in range(first, end) if lines[i].strip()), default=first - 1)
    loc = _locate(lines, first, end)
    if loc is None:
        return ParsedDoc(RECIPE, None, offs[last + 1], indent, 0)
    b, e = loc
    for idx, marker in ((b, BEGIN_MARKER), (e, END_MARKER)):
        if lines[idx].rstrip("\n") != indent + marker:
            raise LearnedBlockError("learned markers must sit exactly at the instructions indent")
    content: list[str] = []
    for line in lines[b + 1:e]:
        if line.startswith(indent):
            content.append(line[len(indent):])
        elif line.strip() == "":
            content.append("\n")
        else:
            raise LearnedBlockError(
                "a learned-block line is indented less than the instructions scalar (dedent)"
            )
    return ParsedDoc(RECIPE, LearnedBlock("".join(content), offs[b], offs[e + 1]),
                     offs[last + 1], indent, 0)


def parse(text: str, kind: str) -> ParsedDoc:
    """Dispatch to :func:`parse_markdown` or :func:`parse_recipe`.

    Args:
        text: The whole file.
        kind: :data:`MARKDOWN` or :data:`RECIPE`.

    Returns:
        The parsed document.

    Raises:
        LearnedBlockError: Propagated from the format parser, or an unknown *kind*.
    """
    if kind == MARKDOWN:
        return parse_markdown(text)
    if kind == RECIPE:
        return parse_recipe(text)
    raise LearnedBlockError(f"unknown host format {kind!r}")


def content_problems(content: str) -> list[str]:
    """Return why *content* may not be propagated (empty list when it may).

    Args:
        content: Canonical block content.

    Returns:
        Human-readable problems: a fence-family token, a non-``\\n`` line break or control
        character, or a final line without a newline.
    """
    problems: list[str] = []
    if _FENCE_TOKEN_RE.search(content):
        problems.append("contains an AGENTTEAMS fence/learned marker token")
    bad = sorted({f"U+{ord(c):04X}" for c in content if c in _FORBIDDEN_CHARS})
    if bad:
        problems.append(f"contains a line-break or control character ({', '.join(bad)})")
    if content and not content.endswith("\n"):
        problems.append("does not end with a newline")
    return problems


#: Learned-block policy denylist (on top of :func:`agentteams.scan.scan_content`). A learned
#: block is agent-written text that an unsandboxed process copies into other agents' files, so
#: anything that reads as an instruction override, a constitutional-tier claim, a governance
#: bypass or a capability/permission change is quarantined rather than propagated. Matched
#: case-insensitively on NFKC-normalised text with format characters removed; ``_GAP`` tolerates
#: whitespace and punctuation between words.
_GAP = r"[\W_]*"
_NEAR = r"[\W\w]{0,60}?"
_POLICY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(rx, re.IGNORECASE)) for label, rx in (
        ("instruction override", rf"\bignore\b{_NEAR}\b(?:previous|prior|all|above|earlier)\b"
                                 rf"{_NEAR}\binstructions?\b"),
        ("instruction override", r"\bdisregard"),
        ("instruction override", rf"\byou{_GAP}are{_GAP}now\b"),
        ("constitutional-tier claim", rf"\btier{_GAP}(?:1|one|i)\b"),
        ("constitutional-tier claim", r"\bC\s*-\s*[1-5]\b"),
        ("constitutional-tier claim", rf"\bconstitutional{_GAP}core\b"),
        ("constitutional-tier claim", r"\boverrid(?:e|es|ing|den)\b"),
        ("governance bypass", rf"\b(?:skip|skipping|bypass|bypassing|disable|disabling|ignore|"
                              rf"ignoring)\b{_NEAR}@?\b(?:security|adversarial|"
                              rf"conflict{_GAP}auditor)\b"),
        ("governance bypass", rf"\bwithout{_GAP}(?:any{_GAP})?(?:clearance|@?security)\b"),
        ("governance bypass", rf"\bno{_GAP}need{_GAP}(?:for|of){_GAP}(?:a{_GAP})?clearance\b"),
        ("permission/tool widening", rf"\bpermission{_GAP}mode"),
        ("permission/tool widening", rf"\bbypass{_GAP}permissions"),
        ("permission/tool widening", r"\bdangerously"),
        ("permission/tool widening", rf"\ballowed{_GAP}tools"),
        ("permission/tool widening", r"\btools\s*:"),
        ("permission/tool widening", r"\bhooks\s*:"),
        ("permission/tool widening", rf"\bmcp{_GAP}servers"),
    )
)


def policy_problems(content: str) -> list[str]:
    """Return the learned-block policy denylist matches in *content* (empty when clean).

    Args:
        content: Canonical block content.

    Returns:
        One ``"policy: <category> (<matched text>)"`` entry per matching pattern.
    """
    text = "".join(c for c in unicodedata.normalize("NFKC", content)
                   if unicodedata.category(c) != "Cf")
    out: list[str] = []
    for label, rx in _POLICY_PATTERNS:
        m = rx.search(text)
        if m:
            snippet = " ".join(m.group(0).split())[:60]
            out.append(f"policy: {label} ({snippet!r})")
    return out


def render_block(content: str, indent: str) -> str:
    """Render the marker lines and *content* at *indent*.

    Args:
        content: Canonical block content (each line ending in ``\\n``, or empty).
        indent: The host indent (``""`` for markdown).

    Returns:
        The block text, ending in ``\\n``. Empty content lines stay empty (no trailing spaces).
    """
    body = "".join(line if line == "\n" else indent + line for line in _split_lines(content))
    return f"{indent}{BEGIN_MARKER}\n{body}{indent}{END_MARKER}\n"


def _insert_separator(prefix: str) -> str:
    """Return the blank-line separator placed before an inserted block."""
    return "\n" if prefix.endswith("\n") else "\n\n"


def compose(text: str, parsed: ParsedDoc, content: str) -> str:
    """Return *text* with its learned block replaced by (or extended with) *content*.

    Args:
        text: The original file.
        parsed: ``parse(text, kind)``.
        content: The new canonical block content.

    Returns:
        The composed file. Nothing outside the block (or, for an insertion, outside the inserted
        separator + block) differs from *text*; :func:`verify_composed` proves it.
    """
    rendered = render_block(content, parsed.indent)
    if parsed.block is not None:
        if not text[: parsed.block.end].endswith("\n"):
            rendered = rendered[:-1]
        return text[: parsed.block.start] + rendered + text[parsed.block.end:]
    prefix = text[: parsed.insert_at]
    return prefix + _insert_separator(prefix) + rendered + text[parsed.insert_at:]


def top_level_sections(text: str) -> dict[str, str]:
    """Return each top-level YAML key of a recipe mapped to its raw text.

    Column-0 lines that are not keys (comments) are kept with the section they sit in. A
    duplicated key gets a ``#n`` suffix so it cannot hide behind the first occurrence.

    Args:
        text: The recipe YAML.

    Returns:
        ``{key: raw text from the key line up to the next column-0 key}``; text before the first
        key is under ``""``.
    """
    sections: dict[str, list[str]] = {"": []}
    current = ""
    for line in _split_lines(text):
        m = _TOP_LEVEL_KEY_RE.match(line)
        if m and line[0] not in " \t#":
            key = m.group(1)
            n = 1
            while (key if n == 1 else f"{key}#{n}") in sections:
                n += 1
            current = key if n == 1 else f"{key}#{n}"
            sections[current] = []
        sections[current].append(line)
    return {k: "".join(v) for k, v in sections.items()}


def verify_composed(old: str, new: str, kind: str, content: str) -> list[str]:
    """Prove *new* differs from *old* only inside the learned block.

    Args:
        old: The original file.
        new: The composed file (``compose(old, parse(old, kind), content)``).
        kind: :data:`MARKDOWN` or :data:`RECIPE`.
        content: The canonical content *new* must carry.

    Returns:
        Problems; an empty list means the write is safe to make. Checked: the re-parsed block
        carries exactly *content*; every byte outside the block is unchanged; the template fence
        set is identical (and parseable); front matter is unchanged; for recipes the structural
        check result and every top-level key other than ``instructions`` are unchanged.
    """
    try:
        before = parse(old, kind)
        after = parse(new, kind)
    except LearnedBlockError as exc:
        return [f"re-parse failed: {exc}"]
    problems: list[str] = []
    nb = after.block
    if nb is None or nb.content != content:
        return ["the composed block does not carry the propagated content"]
    if before.block is not None:
        head, tail = old[: before.block.start], old[before.block.end:]
    else:
        prefix = old[: before.insert_at]
        head, tail = prefix + _insert_separator(prefix), old[before.insert_at:]
    if new[: nb.start] != head or new[nb.end:] != tail:
        problems.append("bytes outside the learned block changed")
    fences_old, fences_new = _extract_fenced_regions(old), _extract_fenced_regions(new)
    if not isinstance(fences_old, dict) or fences_old != fences_new:
        problems.append("the template fence set changed or does not parse")
    if old[: before.fm_end] != new[: after.fm_end]:
        problems.append("front matter changed")
    if kind == RECIPE:
        if _validate_recipe_yaml(old) != _validate_recipe_yaml(new):
            problems.append("the structural recipe check result changed")
        so, sn = top_level_sections(old), top_level_sections(new)
        so.pop("instructions", None)
        sn.pop("instructions", None)
        if so != sn:
            problems.append("a top-level recipe key other than instructions changed")
    return problems


# ---------------------------------------------------------------------------
# Carry across regeneration (--update / --merge / --overwrite / machine-managed full replace)
# ---------------------------------------------------------------------------

def host_kind(rel_path: str, text: str) -> str | None:
    """Return the host format a generated file would carry a learned block in, or None.

    Args:
        rel_path: The output path (relative or absolute).
        text: The file content (a ``.yaml`` must look like a recipe).

    Returns:
        :data:`MARKDOWN` for ``.md``, :data:`RECIPE` for a goose recipe, else None.
    """
    if rel_path.endswith(".md"):
        return MARKDOWN
    if rel_path.endswith(".yaml") and _RECIPE_SHAPE_RE.search(text) and re.search(
            r"^instructions:[ \t]*\|", text, re.MULTILINE):
        return RECIPE
    return None


def carry_block_text(rel_path: str, source: str, dest: str) -> tuple[str, list[str]]:
    """Carry *source*'s learned block into *dest* when *dest* has none.

    Used so that no regeneration path — a fence merge, ``--overwrite``, or the machine-managed
    full replace that unfenced goose recipes take — deletes the block an agent wrote. The same
    gates as a sync write apply: :func:`content_problems` and :func:`verify_composed` (nothing
    outside the block may differ from *dest*).

    Args:
        rel_path: The output path (selects the host format).
        source: The text holding the block (the file on disk, or a render that already carries it).
        dest: The text about to be written.

    Returns:
        ``(text to write, notices)``. *dest* unchanged (no notice) when *source* has no block or
        *dest* already carries the same one; *dest* unchanged with a notice when the block cannot
        be carried safely.
    """
    kind = host_kind(rel_path, dest) or host_kind(rel_path, source)
    if kind is None:
        return dest, []
    try:
        src = parse(source, kind)
    except LearnedBlockError:
        return dest, []
    if src.block is None:
        return dest, []
    content = src.block.content
    try:
        parsed = parse(dest, kind)
    except LearnedBlockError as exc:
        return dest, [f"AGENTTEAMS-LEARNED block NOT carried into the regenerated file ({exc}); "
                      "it survives only in the backup — re-add it, then run --sync-agent-docs"]
    if parsed.block is not None:
        if parsed.block.content == content:
            return dest, []
        return dest, ["the regenerated file already carries a different AGENTTEAMS-LEARNED block; "
                      "the on-disk block was kept only in the backup"]
    problems = content_problems(content)
    new = compose(dest, parsed, content) if not problems else dest
    problems = problems or verify_composed(dest, new, kind, content)
    if problems:
        return dest, [f"AGENTTEAMS-LEARNED block NOT carried into the regenerated file "
                      f"({'; '.join(problems)}); it survives only in the backup"]
    return new, []


def carry_learned_block(rel_path: str, fresh: str, target: Path) -> tuple[str, list[str]]:
    """Carry the learned block of the file at *target* into its fresh render.

    Args:
        rel_path: The output path relative to the agents dir.
        fresh: The fresh render about to be merged or written.
        target: The on-disk file (read only when it is a regular file, never via a symlink).

    Returns:
        ``(fresh, possibly carrying the block, notices)`` — see :func:`carry_block_text`.
    """
    if not (rel_path.endswith(".md") or rel_path.endswith(".yaml")):
        return fresh, []
    try:
        if not stat.S_ISREG(os.lstat(target).st_mode):
            return fresh, []
        existing = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return fresh, []
    if BEGIN_MARKER not in existing:
        return fresh, []
    return carry_block_text(rel_path, existing, fresh)
