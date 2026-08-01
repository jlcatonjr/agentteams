"""A file an agent-file scanner will parse must not begin with a fence marker.

**The failure.** Goose's ACP agent-file scanner refused
``.claude/agents/team-builder.md`` with ``could not find expected ':' at line 5 column 1, while
scanning a simple key at line 4 column 1``. The file began with
``<!-- AGENTTEAMS:BEGIN content v=1 -->``, so the scanner read the HTML comment as the opening of
a YAML mapping and hit the template's horizontal rule four lines later.

**It was logged as fence corruption. It was not.**
:func:`agentteams.emit._normalize_generated_content` already wraps *only the body* when a file has
YAML front matter, precisely so framework parsers keep seeing front matter first. The wrapper
lands on line 1 for exactly one reason: the file has no front matter for it to land after.

**The actual cause was a single outlier template.** Three of the four builder templates
(`copilot-vscode`, `copilot-cli`, `goose`) open with a `---` block. The Claude one did not, and
``render_builder_file`` is identity for Claude — nothing downstream injects front matter the way
``render_agent_file`` does for ordinary agents. So it shipped bare.

``SETUP-REQUIRED.md`` also begins with the wrapper and is deliberately left alone: it is a status
report, not a persona, and ``emit._is_agent_doc`` and ``bridge_sources`` both exclude it from
agent-file enumeration. Nothing scans it. Requiring front matter there would be inventing a
persona for a build report.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.emit import _normalize_generated_content

_BUILDER_DIR = Path(__file__).resolve().parents[1] / "agentteams" / "templates" / "builder"
_FENCE = "<!-- AGENTTEAMS:BEGIN"


def _builder_templates() -> list[Path]:
    return sorted(_BUILDER_DIR.glob("team-builder-*.template.md"))


def test_the_builder_templates_are_all_present():
    """Guards the parametrisation below against silently covering nothing."""
    names = {p.name for p in _builder_templates()}
    assert len(names) >= 4, f"expected one builder template per framework, found {names}"


@pytest.mark.parametrize("template", _builder_templates(), ids=lambda p: p.stem)
def test_every_builder_template_opens_with_front_matter(template: Path):
    """The Claude one was the outlier; nothing should be able to become the next one."""
    text = template.read_text(encoding="utf-8")
    assert text.startswith("---\n"), (
        f"{template.name} has no YAML front matter. The whole-body fence will land on line 1, "
        "and an agent-file scanner reading it as YAML will fail on the first bare line."
    )


@pytest.mark.parametrize("template", _builder_templates(), ids=lambda p: p.stem)
def test_the_emitted_builder_file_never_starts_with_a_fence(template: Path):
    """The property that actually broke: what a scanner sees on line 1."""
    emitted = _normalize_generated_content("team-builder.md", template.read_text(encoding="utf-8"))
    assert not emitted.startswith(_FENCE), (
        f"{template.name} emits a file whose first line is a fence marker — this is the exact "
        "shape Goose's ACP scanner rejected."
    )


def test_the_builder_file_is_still_fenced_and_therefore_still_updatable():
    """Front matter must fix the scanner without costing the file its update path."""
    claude = _BUILDER_DIR / "team-builder-claude.template.md"
    emitted = _normalize_generated_content("team-builder.md", claude.read_text(encoding="utf-8"))
    assert emitted.startswith("---\n")
    assert _FENCE in emitted, "losing the fence would trade an ACP failure for a stale file"


# --- the mechanism, so the diagnosis cannot silently rot --------------------

def test_front_matter_is_what_moves_the_wrapper_off_line_one():
    """Documents the real cause: the wrapper is front-matter-aware and always was."""
    bare = "# Title\n\nbody\n"
    with_fm = "---\nname: X\n---\n\n# Title\n\nbody\n"

    assert _normalize_generated_content("a.md", bare).startswith(_FENCE)
    assert not _normalize_generated_content("a.md", with_fm).startswith(_FENCE)


def test_setup_required_is_deliberately_exempt():
    """It is a build report, not a persona — nothing enumerates it as an agent file."""
    from agentteams.emit import _is_agent_doc

    assert _is_agent_doc("SETUP-REQUIRED.md", "# SETUP-REQUIRED.md\n") is False
