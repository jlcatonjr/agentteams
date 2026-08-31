"""Turning an agent file's front matter into graph inputs.

Carved out of ``graph.py`` on 2026-08-03 under CH-07 — the sixth such carve, and the **first
chosen rather than forced**. The previous five each happened when an ordinary edit pushed a
module over the 1000-line ceiling mid-change; `graph.py` sat at 992 with eight lines of runway,
so the next edit would have done the same.

`graph.py` owns the graph model — ``TeamGraph``, ``AgentNode``, ``GraphEdge`` — and the
rendering built on it. This module owns the step before that: read an agent file's YAML front
matter and produce the values a node is built from. Its name, whether a user may invoke it,
which tools it declares, which agents it references, its handoff blocks, and which tier it
belongs to.

Verified independent before the move: no function here references ``TeamGraph``, ``AgentNode``,
``GraphEdge``, ``AGENT_TYPES`` or the SVG palette. The dependency runs one way, and `graph.py`
re-exports every name, so ``from agentteams.graph import _extract_tools`` still resolves —
which is what `tests/test_graph.py` does for all nine.
"""

from __future__ import annotations

import re

from agentteams.yaml_frontmatter import parse_yaml_front_matter  # shared YAML splitter (MAP-17)

#: Matches the 'handoffs:' top-level key on its own line (block YAML sequence)
_HANDOFFS_SECTION_RE = re.compile(r"^handoffs:\s*$", re.MULTILINE)
#: Marks the start of each YAML sequence item within a handoffs block.
#: Uses \s{2,} rather than exactly two spaces so that 3- or 4-space-indented
#: files (not currently in the corpus but valid YAML) are handled correctly.
_HANDOFF_ITEM_BOUNDARY_RE = re.compile(r"^\s{2,}- ", re.MULTILINE)
#: Matches 'agent: slug' at the START of a line within a handoff item chunk.
#: The ^ anchor (with re.MULTILINE) prevents a false match when 'agent:' appears
#: embedded inside a prompt or send value on the same line as another key.
_HANDOFF_AGENT_KEY_RE = re.compile(r"^\s*(?:-\s+)?agent:\s*['\"]?([\w][\w-]*)['\"]?", re.MULTILINE)
#: Matches 'label: text' at the START of a line within a handoff item chunk.
#: The ^ anchor prevents a false match when 'label:' appears embedded inside a
#: prompt or send value on the same line as another key.
_HANDOFF_LABEL_KEY_RE = re.compile(r"^\s*(?:-\s+)?label:\s*['\"]?([^'\"\n]+?)['\"]?\s*$", re.MULTILINE)
#: 'name: "<role> — <project>"'
_NAME_RE = re.compile(r"^name:\s*['\"]?(.+?)['\"]?\s*$", re.MULTILINE)
#: 'user-invocable: true/false'
_INVOKABLE_RE = re.compile(r"^user-invocable:\s*(true|false)", re.MULTILINE | re.IGNORECASE)
def _split_yaml(content: str) -> tuple[str | None, str]:
    """Split file content into YAML front matter and body.

    Delegates to ``yaml_frontmatter.parse_yaml_front_matter`` — the single
    shared line-anchored, block-scalar-aware scanner also used by interop.py
    (MAP-06) — for boundary detection (MAP-17; the duplicate scanner that used
    to live in ``_utils._split_yaml_front_matter`` was retired by the
    durable-canonical-agent-format plan, step B.1). The yaml block is returned
    without a trailing newline to preserve the historical return shape this
    module's regex extractors were written against.

    Args:
        content: Full file text.

    Returns:
        Tuple of (yaml_block, body). yaml_block is None if no front matter.
    """
    yaml_block, body = parse_yaml_front_matter(content)
    if yaml_block is None:
        return None, body
    if yaml_block.endswith("\r\n"):
        yaml_block = yaml_block[:-2]
    elif yaml_block.endswith("\n"):
        yaml_block = yaml_block[:-1]
    return yaml_block, body
def _extract_name(yaml_block: str, fallback_slug: str) -> str:
    """Extract the display name from YAML, stripping the ' — Project' suffix.

    Args:
        yaml_block:    YAML front matter text.
        fallback_slug: Used when no name field is found.

    Returns:
        Display name string (role part only, without project suffix).
    """
    m = _NAME_RE.search(yaml_block)
    if not m:
        return fallback_slug
    raw = m.group(1).strip().strip("'\"")
    # Strip ' — ProjectName' or ' - ProjectName' suffix
    if " — " in raw:
        return raw.split(" — ")[0].strip()
    if " - " in raw:
        return raw.split(" - ")[0].strip()
    return raw
def _extract_user_invokable(yaml_block: str) -> bool:
    """Extract the user-invocable boolean from YAML.

    Args:
        yaml_block: YAML front matter text.

    Returns:
        True if user-invocable is 'true', False otherwise.
    """
    m = _INVOKABLE_RE.search(yaml_block)
    if not m:
        return False
    return m.group(1).lower() == "true"
def _extract_yaml_sequence(yaml_block: str, key: str) -> list[str]:
    """Return items from a YAML sequence key, handling inline and block forms.

    Inline form::

        key: [item1, 'item2', "item3"]

    Block form::

        key:
          - item1
          - 'item2'
          - "item3"

    Block termination: scanning stops at the first newline that is followed by
    a non-whitespace, non-carriage-return, non-dash character — i.e., the next
    top-level YAML key.  This prevents capturing sub-keys (``label:``,
    ``agent:``) from subsequent YAML blocks.  The explicit ``\\r`` exclusion
    handles CRLF files: ``\\r`` falls inside ``\\s``, so without it the
    terminator would stall on ``\\r\\nkey:`` sequences and allow block_text to
    grow to the entire remaining tail.

    Args:
        yaml_block: YAML front-matter text (content between the ``---`` fences).
        key:        YAML key name to look up (e.g. ``"tools"`` or ``"agents"``).

    Returns:
        List of item strings with surrounding quotes stripped.  Empty list if
        the key is absent or the sequence contains no items.
    """
    # -- Inline form: key: [item1, item2] ------------------------------------
    inline_re = re.compile(
        r"^" + re.escape(key) + r"\s*:\s*\[([^\]]*)\]",
        re.MULTILINE,
    )
    m = inline_re.search(yaml_block)
    if m:
        return [t.strip().strip("'\"") for t in m.group(1).split(",") if t.strip()]

    # -- Block form: key:\n  - item1\n  - item2 ------------------------------
    key_re = re.compile(
        r"^" + re.escape(key) + r"\s*:\s*$",
        re.MULTILINE,
    )
    m = key_re.search(yaml_block)
    if not m:
        return []

    # tail begins at the character immediately after the key line (the \n).
    tail = yaml_block[m.end():]

    # Terminate at the next top-level YAML line: a newline followed by any
    # character that is neither whitespace nor a dash.  This correctly skips
    # sequence items ("  - …") and sub-key lines ("    agent: …") while
    # stopping at the next root key ("handoffs:", "name:", etc.).
    end_match = re.search(r"\n(?=[^\s\r-])", tail)
    block_text = tail[: end_match.start()] if end_match else tail

    # Match only bare dash-prefixed items (no sub-key content after the name).
    # Pattern: optional whitespace, dash, whitespace, optional quote,
    #          word-and-hyphen slug, optional closing quote, end-of-line.
    # The \s*$ ensures we do NOT match "  - label: foo" (colon after slug).
    item_re = re.compile(r"^\s+-\s+['\"]?([\w][\w-]*)['\"]?\s*$", re.MULTILINE)
    return item_re.findall(block_text)
def _extract_tools(yaml_block: str) -> list[str]:
    """Extract the tools list from YAML (inline bracket or block dash form).

    Args:
        yaml_block: YAML front matter text.

    Returns:
        List of tool name strings.
    """
    return _extract_yaml_sequence(yaml_block, "tools")
def _extract_agents_list(yaml_block: str) -> list[str]:
    """Extract agent slugs from the agents: field (inline or block YAML).

    Handles both forms::

        agents: ['slug1', 'slug2']        (quoted inline)
        agents: [slug1, slug2]            (unquoted inline)
        agents:                           (block YAML)
          - slug1
          - slug2

    This is a named convenience wrapper around :func:`_extract_yaml_sequence`
    for callers that only need the ``agents:`` field.

    Args:
        yaml_block: YAML front matter text.

    Returns:
        List of agent slugs in declaration order.
    """
    return _extract_yaml_sequence(yaml_block, "agents")
def _parse_handoff_blocks(yaml_block: str) -> list[tuple[str, str | None]]:
    """Parse the ``handoffs:`` YAML block into ``(agent_slug, label)`` pairs.

    Handles ``label:`` appearing before OR after ``agent:`` within the same
    item block.  An item with no ``label:`` key yields ``None`` for the label
    without affecting the labels of subsequent items.

    Args:
        yaml_block: YAML front matter text.

    Returns:
        List of ``(agent_slug, label_or_None)`` tuples, one per handoff item
        that contains a valid ``agent:`` key.  Items with no ``agent:`` key are
        skipped.
    """
    # Isolate the handoffs: section (everything after "handoffs:\n" until the
    # next top-level YAML key or end of block).
    section_match = _HANDOFFS_SECTION_RE.search(yaml_block)
    if not section_match:
        return []
    section_text = yaml_block[section_match.end():]
    next_toplevel = re.search(r"\n[A-Za-z]", section_text)
    if next_toplevel:
        section_text = section_text[: next_toplevel.start()]

    # Find the start position of every "  - " item within the section.
    item_starts = [m.start() for m in _HANDOFF_ITEM_BOUNDARY_RE.finditer(section_text)]
    if not item_starts:
        return []

    results: list[tuple[str, str | None]] = []
    for idx, start in enumerate(item_starts):
        end = item_starts[idx + 1] if idx + 1 < len(item_starts) else len(section_text)
        chunk = section_text[start:end]

        agent_m = _HANDOFF_AGENT_KEY_RE.search(chunk)
        if not agent_m:
            continue  # skip items that have no agent: key
        label_m = _HANDOFF_LABEL_KEY_RE.search(chunk)
        label = label_m.group(1).strip() if label_m else None
        results.append((agent_m.group(1), label))

    return results
def _classify_agent_type(slug: str) -> str:
    """Classify an agent slug into its taxonomy tier.

    Args:
        slug: Machine-readable agent identifier.

    Returns:
        One of: ``"governance"``, ``"domain"``, ``"workstream_expert"``,
        ``"tool_specialist"``, ``"unknown"``.
    """
    _GOVERNANCE_SLUGS = frozenset({
        "orchestrator", "navigator", "security", "code-hygiene", "adversarial",
        "conflict-auditor", "conflict-resolution", "cleanup", "agent-updater",
        "agent-refactor", "repo-liaison", "git-operations", "team-builder",
    })
    _DOMAIN_SLUGS = frozenset({
        "primary-producer", "quality-auditor", "style-guardian", "technical-validator",
        "format-converter", "output-compiler", "reference-manager", "visual-designer",
        "cohesion-repairer", "work-summarizer", "content-enricher",
        "module-doc-author", "module-doc-validator",
    })
    if slug in _GOVERNANCE_SLUGS:
        return "governance"
    if slug in _DOMAIN_SLUGS:
        return "domain"
    if slug.endswith("-expert"):
        return "workstream_expert"
    if slug.startswith("tool-"):
        return "tool_specialist"
    return "unknown"
def _mermaid_id(slug: str) -> str:
    """Convert a slug to a valid Mermaid node identifier.

    Args:
        slug: Agent slug, possibly containing hyphens.

    Returns:
        Mermaid-safe identifier (hyphens replaced with underscores).
    """
    return slug.replace("-", "_")
