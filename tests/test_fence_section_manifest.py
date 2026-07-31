"""Every fenced template must carry a SECTION MANIFEST, and it must match its real fences.

`agentteams/templates/FENCE-CONVENTIONS.md` requires that "every template that uses fence markers
**must** include a section manifest in a comment block at the top of the file". Nothing enforced
it, and measured 2026-07-30, **11 of 26** fenced templates had none.

The manifest is what tells a downstream editor which regions `--merge` will overwrite (FENCED)
and which are theirs to keep (USER-EDITABLE). Getting that wrong loses authored content, so a
manifest that is *absent* is a gap and a manifest that *misdescribes* the file is worse — it is
consulted and believed. Both tests below therefore run: presence, and agreement with the fence
IDs actually in the file.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_TEMPLATES = Path(__file__).resolve().parents[1] / "agentteams" / "templates"

_FENCE_BEGIN = re.compile(r"<!--\s*AGENTTEAMS:BEGIN\s+([A-Za-z0-9_]+)", re.MULTILINE)
_MANIFEST_BLOCK = re.compile(r"SECTION MANIFEST(.*?)-->", re.DOTALL)
_MANIFEST_ROW = re.compile(r"^\|\s*([A-Za-z0-9_]+)\s*\|\s*([A-Z-]+)\s*\|", re.MULTILINE)

_VALID_DESIGNATIONS = {"FENCED", "USER-EDITABLE"}


def _fenced_templates() -> list[Path]:
    return sorted(
        p for p in _TEMPLATES.rglob("*.template.md")
        if _FENCE_BEGIN.search(p.read_text(encoding="utf-8"))
    )


def _manifest_rows(text: str) -> dict[str, str]:
    block = _MANIFEST_BLOCK.search(text)
    if not block:
        return {}
    return {
        sid: designation
        for sid, designation in _MANIFEST_ROW.findall(block.group(1))
        if sid != "section_id"
    }


def test_there_are_fenced_templates_to_check():
    """Guards the guard: a path typo would make every assertion below vacuously pass."""
    assert len(_fenced_templates()) >= 20


@pytest.mark.parametrize(
    "template", _fenced_templates(), ids=lambda p: p.name.replace(".template.md", "")
)
def test_fenced_template_has_a_section_manifest(template: Path):
    text = template.read_text(encoding="utf-8")
    assert "SECTION MANIFEST" in text, (
        f"{template.name} uses AGENTTEAMS fences but has no SECTION MANIFEST block. "
        f"FENCE-CONVENTIONS.md requires one immediately after the front matter, listing each "
        f"section_id as FENCED or USER-EDITABLE."
    )


@pytest.mark.parametrize(
    "template", _fenced_templates(), ids=lambda p: p.name.replace(".template.md", "")
)
def test_section_manifest_lists_every_fence_in_the_file(template: Path):
    """A manifest that omits a fence is worse than none — it is read as complete."""
    text = template.read_text(encoding="utf-8")
    if "SECTION MANIFEST" not in text:
        pytest.skip("presence is asserted by the sibling test")
    declared = set(_manifest_rows(text))
    actual = set(_FENCE_BEGIN.findall(text))
    missing = sorted(actual - declared)
    assert not missing, (
        f"{template.name}: fences present in the file but absent from its SECTION MANIFEST: "
        f"{missing}"
    )


@pytest.mark.parametrize(
    "template", _fenced_templates(), ids=lambda p: p.name.replace(".template.md", "")
)
def test_section_manifest_designations_are_valid(template: Path):
    text = template.read_text(encoding="utf-8")
    if "SECTION MANIFEST" not in text:
        pytest.skip("presence is asserted by the sibling test")
    invalid = {
        sid: d for sid, d in _manifest_rows(text).items() if d not in _VALID_DESIGNATIONS
    }
    assert not invalid, (
        f"{template.name}: invalid designations {invalid}; expected one of "
        f"{sorted(_VALID_DESIGNATIONS)}"
    )
