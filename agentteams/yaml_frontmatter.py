"""yaml_frontmatter.py — Shared YAML front-matter scanner for agentteams.

Replaces the regex-based front-matter parsers in interop.py (_YAML_FRONT_MATTER_RE)
and graph.py (_split_yaml) and all five framework adapter files with a line-by-line
scanner that correctly handles YAML block-scalar values containing bare '---' lines.

Public API
----------
parse_yaml_front_matter(content) -> (yaml_text | None, body)
"""

from __future__ import annotations
import re

# Matches a YAML mapping key that opens a block scalar:
#   <optional-indent><word-chars>: | or >  (with optional chomping/indent indicators)
# Group 1 captures the leading whitespace to determine the key's indentation level.
#
# Known limitation: lines of the form  key: | # comment  (block scalar indicator
# followed by a YAML comment on the same line) are NOT matched by this regex.
# In that case the scanner does not enter in_block_scalar mode, so if the block
# scalar content contains an unindented bare '---' line the closing-delimiter
# check will fire prematurely. This edge case is uncommon in agent front matter
# but callers should be aware of it.
_BLOCK_SCALAR_START_RE = re.compile(
    r"^(\s*)[\w][\w-]*\s*:\s*[|>][|>0-9+-]*\s*$"
)


def parse_yaml_front_matter(content: str) -> tuple[str | None, str]:
    """Split YAML front matter from body using a line-by-line scanner.

    The opening delimiter must be '---' (with optional trailing whitespace) on
    the very first line of *content*.  The closing delimiter is the next line
    that is exactly '---' (with optional trailing whitespace) at column 0,
    provided it is NOT inside a YAML block scalar.

    A block scalar is detected when a line matches:
        <indent>key: |   or   <indent>key: >   (with optional modifiers)
    All subsequent lines that are blank or more-indented than that key are
    considered scalar content.  The scanner exits the block scalar on the
    first non-blank line whose indentation is <= the key's indentation.

    Note: block scalar indicator lines that carry a YAML comment
    (e.g. ``key: | # comment``) are not recognised as block-scalar openers;
    see the module-level note on ``_BLOCK_SCALAR_START_RE``.

    Returns:
        (yaml_text, body)  — yaml_text is the raw text between the two '---'
        lines (not including the delimiter lines themselves); body is the text
        after the closing delimiter.
        Returns (None, content) when no valid front matter is found.

    Edge case: an empty front matter block ('---\\n---\\n') returns
    yaml_text='', which is falsy. Callers that gate on ``if yaml_text:``
    will skip the block even though front matter was technically present.
    Use ``if yaml_text is not None:`` to distinguish empty-but-present from
    absent front matter.
    """
    lines = content.splitlines(keepends=True)
    if not lines:
        return None, content

    # Opening delimiter: first line must be '---' (plus optional trailing whitespace)
    if lines[0].rstrip() != "---":
        return None, content

    in_block_scalar = False
    block_key_indent = 0  # indentation of the key that started the current block scalar

    for i, line in enumerate(lines[1:], start=1):
        # Preserve leading whitespace; strip only the line ending.
        stripped = line.rstrip("\r\n")
        indent = len(stripped) - len(stripped.lstrip())

        if in_block_scalar:
            # Stay in the block scalar while lines are blank or more-indented.
            if stripped.strip() == "" or indent > block_key_indent:
                continue
            # A non-blank line at indent <= key indent exits the block scalar.
            in_block_scalar = False
            # Fall through: this line may be the closing delimiter or a new key.

        # Check whether this line opens a block scalar.
        m = _BLOCK_SCALAR_START_RE.match(stripped)
        if m:
            in_block_scalar = True
            block_key_indent = len(m.group(1))
            continue

        # Check for the closing delimiter: '---' at column 0, no indent.
        if indent == 0 and stripped.rstrip() == "---":
            yaml_text = "".join(lines[1:i])
            body = "".join(lines[i + 1:])
            return yaml_text, body

    # No closing delimiter found.
    return None, content
