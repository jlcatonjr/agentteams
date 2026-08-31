"""Both Goose emitters must derive their capability guidance from one source.

Two emitters produce Goose-facing operating guidance: ``frameworks/goose.py`` for a generated
Goose team, and ``bridge.py`` for a *bridged* one. Measured 2026-07-24, the bridged path advertised
none of it — ``grep -c 'agentteams.research' AGENTS.md .goosehints`` returned ``0`` and ``0`` while
the adapter carried a full section.

**The first fix was worse than the gap.** Hand-copying the prose into ``bridge.py`` produced a
second, differently-worded copy of the same capability claim — "None of the builtin extensions
include a general web-search tool" against "No builtin Goose extension does web *search*" — which
is a divergence waiting to happen rather than a fix. ``agentteams/capability_hints.py`` exists to
be the one source.

**And it half-rotted anyway.** ``goose.py`` imported ``RESEARCH_CAPABILITY_BULLET`` and then never
used it, keeping its own hand-written restatement — the exact defect, reintroduced under an import
that made it look resolved. Nothing failed, because nothing checked. That is what these tests are
for.

Note what is *not* asserted: that the two outputs read identically. They have different shapes — a
sectioned reference document and a bullet list in a shared ``AGENTS.md`` — and forcing identical
prose would be the wrong constraint. The requirement is that the *facts* come from one place.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentteams.capability_hints import RESEARCH_CAPABILITY_BULLET
from agentteams.frameworks.goose import _goose_capabilities_content

_SRC = Path(__file__).resolve().parents[1] / "agentteams"


def test_the_adapter_embeds_the_shared_text_verbatim():
    """Not "mentions the module" — the shared block itself, so drift is impossible."""
    assert RESEARCH_CAPABILITY_BULLET in _goose_capabilities_content("DemoProject")


#: The two modules that actually build Goose-facing guidance text. ``frameworks/goose.py`` is not
#: one of them any more: its document-content generators were carved to ``goose_docs.py`` on
#: 2026-07-31 when single-sourcing this text pushed it past the CH-07 ceiling.
_EMITTERS = ("frameworks/goose_docs.py", "bridge.py")


def test_both_emitters_import_the_shared_constant():
    for rel in _EMITTERS:
        source = (_SRC / rel).read_text(encoding="utf-8")
        assert "from agentteams.capability_hints import RESEARCH_CAPABILITY_BULLET" in source, rel


def test_neither_emitter_merely_imports_it():
    """An unused import is how this last regressed: resolved-looking, still duplicated."""
    for rel in _EMITTERS:
        tree = ast.parse((_SRC / rel).read_text(encoding="utf-8"))
        uses = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == "RESEARCH_CAPABILITY_BULLET"
        ]
        assert uses, f"{rel} imports RESEARCH_CAPABILITY_BULLET but never references it"


def test_the_adapter_does_not_restate_the_search_gap_in_its_own_words():
    """The specific sentence that was duplicated. Its return is the regression to catch."""
    body = _goose_capabilities_content("DemoProject")
    restatement = "None of the builtin extensions include a general web-search tool"
    assert restatement not in body or restatement in RESEARCH_CAPABILITY_BULLET, (
        "the adapter is restating the search-capability gap in prose of its own again — "
        "put the claim in capability_hints.py and embed it"
    )


def test_goose_only_framing_may_still_live_in_the_adapter():
    """Single-sourcing the facts must not flatten genuinely framework-specific content."""
    body = _goose_capabilities_content("DemoProject")
    assert "computercontroller" in body, "the extension contrast belongs to this document"
    assert "web_scrape` is a plain fetch, not a renderer" in body
