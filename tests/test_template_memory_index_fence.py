"""Template-author lint: memory-index consultation fence presence.

Scans every ``agentteams/templates/**/*.template.md`` body for trigger phrases
that indicate the template reasons over prior decisions / temporal-causal
claims / historical content (i.e. exactly the queries the memory-index is
designed to serve). When a trigger phrase is present, the template must
either:

  - carry the canonical fence
    ``<!-- AGENTTEAMS:BEGIN memory_index_consultation ... -->``
    ``<!-- AGENTTEAMS:END memory_index_consultation -->``
    so ``--update --merge`` can propagate protocol revisions, OR
  - carry an inline escape marker
    ``<!-- agentteams-lint: no-memory-index OK -->``
    when the template legitimately uses one of those phrases without needing
    the consultation protocol.

The trigger-phrase list is intentionally short and conservative; broadening
it requires updating this test and re-auditing the template library.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "agentteams" / "templates"

TRIGGER_PHRASES = (
    "prior decision",
    "when did",
    "history of",
    "previously",
    "what did we decide",
)

FENCE_BEGIN_RE = re.compile(
    r"<!--\s*AGENTTEAMS:BEGIN\s+memory_index_consultation\b",
    re.IGNORECASE,
)
FENCE_END_RE = re.compile(
    r"<!--\s*AGENTTEAMS:END\s+memory_index_consultation\b",
    re.IGNORECASE,
)
ESCAPE_RE = re.compile(
    r"<!--\s*agentteams-lint:\s*no-memory-index\b",
    re.IGNORECASE,
)


def _template_paths() -> list[Path]:
    if not TEMPLATES_DIR.exists():
        return []
    return sorted(TEMPLATES_DIR.rglob("*.template.md"))


def _trigger_hits(body: str) -> list[str]:
    lower = body.lower()
    return [phrase for phrase in TRIGGER_PHRASES if phrase in lower]


@pytest.mark.parametrize("template_path", _template_paths(), ids=lambda p: p.name)
def test_memory_index_fence_present_when_required(template_path: Path) -> None:
    body = template_path.read_text(encoding="utf-8")
    hits = _trigger_hits(body)
    if not hits:
        return

    has_fence = bool(FENCE_BEGIN_RE.search(body) and FENCE_END_RE.search(body))
    has_escape = bool(ESCAPE_RE.search(body))

    if has_fence or has_escape:
        return

    rel = template_path.relative_to(TEMPLATES_DIR.parent.parent)
    pytest.fail(
        f"{rel} contains memory-index trigger phrases {hits!r} "
        "but lacks the canonical "
        "<!-- AGENTTEAMS:BEGIN memory_index_consultation --> fence. "
        "Either add the fence (see "
        "agentteams/templates/domain/technical-validator.template.md for "
        "the canonical form) or add the inline escape marker "
        "'<!-- agentteams-lint: no-memory-index OK -->' "
        "if the phrase is used without intent to follow the consultation "
        "protocol. See docs_src/template-authoring.md."
    )


def test_fence_markers_are_balanced() -> None:
    """Every BEGIN fence must have a matching END fence in the same template."""
    for path in _template_paths():
        body = path.read_text(encoding="utf-8")
        begins = len(FENCE_BEGIN_RE.findall(body))
        ends = len(FENCE_END_RE.findall(body))
        assert begins == ends, (
            f"{path.relative_to(TEMPLATES_DIR.parent.parent)}: "
            f"unbalanced memory_index_consultation fences "
            f"(BEGIN={begins}, END={ends})"
        )


def test_trigger_phrase_list_is_documented() -> None:
    """The escape marker is documented in template-authoring.md so authors
    can find it. This guards against silent drift between the lint and the
    public docs."""
    doc = (
        TEMPLATES_DIR.parent.parent
        / "docs_src"
        / "template-authoring.md"
    )
    text = doc.read_text(encoding="utf-8")
    assert "agentteams-lint: no-memory-index" in text, (
        "Escape marker is undocumented in docs_src/template-authoring.md"
    )
    for phrase in TRIGGER_PHRASES:
        assert phrase in text.lower(), (
            f"Trigger phrase {phrase!r} should be mentioned in "
            "docs_src/template-authoring.md so template authors know what "
            "the lint scans for"
        )
