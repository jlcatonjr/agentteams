"""test_handoff_strip_fence_safety.py — stripping prose must not destroy a fence.

`_HANDOFFS_HEADING_RE` removes a `## Handoff…` heading and everything under it, up to the
next `#{1,3}` heading or end of file. It treats AGENTTEAMS fence markers as ordinary prose.

`conflict-auditor.template.md` fences a heading-only section — `## Handoff Payload Conflict
Codes` — and no further `##` heading follows before EOF. So the match ran to `\\Z` and
consumed **three fence markers**: that fence's `END`, and both markers of the whole following
`handoff_payload_codes` fence. Proven by running the regex in isolation: it spans template
lines 159–172, 877 characters.

The `BEGIN` at line 158 precedes the heading and survived, giving the emitted file 8 BEGIN /
7 END. Every downstream symptom followed from that one unbalanced render:

1. `_extract_fenced_regions` returns an error string rather than a dict;
2. `_normalize_generated_content` wraps the whole body in a `content` fence;
3. the merge reports **"Nested fence not allowed: invariant_core inside content"**.

Three steps, and the reported error named none of them. This file was misdiagnosed twice —
first blaming the template for nesting (it is clean and now guarded), then blaming the merge.
The cause was a prose stripper that could not see fences.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from agentteams.frameworks.base import FrameworkAdapter

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "agentteams/templates"

_BEGIN = re.compile(r"<!--\s*AGENTTEAMS:BEGIN\s+(\S+)")
_END = re.compile(r"<!--\s*AGENTTEAMS:END\s+(\S+)")


def _balance(text: str) -> tuple[int, int]:
    return len(_BEGIN.findall(text)), len(_END.findall(text))


def test_stripping_never_unbalances_a_template() -> None:
    """The property, over the whole library. A strip may empty a fence, never break one."""
    broken = []
    for path in sorted(TEMPLATES.rglob("*.md")):
        original = path.read_text(encoding="utf-8")
        if not re.search(r"^#{1,3}\s+Handoff", original, re.MULTILINE):
            continue
        stripped = FrameworkAdapter._strip_handoffs_section(original)
        before, after = _balance(original), _balance(stripped)
        if after[0] != after[1]:
            broken.append(
                f"{path.relative_to(TEMPLATES)}: {before[0]}/{before[1]} BEGIN/END before, "
                f"{after[0]}/{after[1]} after"
            )
    assert not broken, "handoff stripping destroyed fence markers:\n  " + "\n  ".join(broken)


def test_the_specific_case_that_was_misdiagnosed_twice() -> None:
    """conflict-auditor: the strip must leave all three markers it used to consume."""
    path = TEMPLATES / "universal/conflict-auditor.template.md"
    stripped = FrameworkAdapter._strip_handoffs_section(path.read_text(encoding="utf-8"))
    for sid in ("handoff_payload_conflict_codes", "handoff_payload_codes"):
        assert f"AGENTTEAMS:END {sid}" in stripped, f"strip consumed END {sid}"
    assert "AGENTTEAMS:BEGIN handoff_payload_codes" in stripped, "strip consumed a BEGIN"


def test_a_fenced_handoff_heading_strips_only_inside_its_fence() -> None:
    """Fences survive; and since the exact-title fix (2026-W33), so does the heading.

    Contract change, made deliberately: `## Handoff Payload Codes` is a template-owned
    section merely PREFIXED by "Handoff", not a handoffs section. The original assertion
    ("the heading itself should still be stripped") codified the over-match this test's
    sibling `test_handoff_prefixed_headings_survive_a_direct_strip` now forbids."""
    text = (
        "<!-- AGENTTEAMS:BEGIN a v=1 -->\n"
        "## Handoff Payload Codes\n"
        "<!-- AGENTTEAMS:END a -->\n\n"
        "<!-- AGENTTEAMS:BEGIN b v=1 -->\n"
        "Body of b that must survive.\n"
        "<!-- AGENTTEAMS:END b -->\n"
    )
    out = FrameworkAdapter._strip_handoffs_section(text)
    assert _balance(out) == (2, 2), out
    assert "Body of b that must survive." in out
    assert "## Handoff Payload Codes" in out, "template-owned heading must survive the strip"


def test_unfenced_handoff_sections_are_still_fully_stripped() -> None:
    """Negative control. The fix must not stop the stripper doing its actual job."""
    text = (
        "## Responsibilities\n\nDo things.\n\n"
        "## Handoffs\n\n"
        "- `@navigator` -> locate\n"
        "- `@security` -> clear\n\n"
        "## Rules\n\nFollow them.\n"
    )
    out = FrameworkAdapter._strip_handoffs_section(text)
    assert "## Handoffs" not in out
    assert "@navigator" not in out, "handoff body survived a strip"
    assert "## Responsibilities" in out and "## Rules" in out


def test_a_trailing_unfenced_handoff_section_is_still_stripped_to_eof() -> None:
    """The `\\Z` branch is correct when no fence is involved — keep it working."""
    text = "## Rules\n\nFollow them.\n\n## Handoffs\n\n- `@navigator` -> locate\n"
    out = FrameworkAdapter._strip_handoffs_section(text)
    assert "## Handoffs" not in out and "@navigator" not in out
    assert "## Rules" in out


# --------------------------------------------------------------------------------------
# Audit finding B1: an emptied fence must still be valid to the merge
# --------------------------------------------------------------------------------------


def test_the_emptied_fence_round_trips_through_the_merge() -> None:
    """A fence whose only content was a stripped heading is left empty. Prove that parses.

    If an empty fence were invalid, the heading-only fence would be the wrong template shape
    and the fix would belong in the template instead of the regex.
    """
    from agentteams.fences import _extract_fenced_regions, _merge_fenced_content

    rendered = FrameworkAdapter._strip_handoffs_section(
        (TEMPLATES / "universal/conflict-auditor.template.md").read_text(encoding="utf-8")
    )
    regions = _extract_fenced_regions(rendered)
    assert isinstance(regions, dict), f"stripped render does not parse: {regions}"
    assert "handoff_payload_conflict_codes" in regions

    merged = _merge_fenced_content(rendered, rendered)
    assert isinstance(_extract_fenced_regions(merged.merged_content), dict), (
        "merging a stripped render produced an unparseable file"
    )
    assert not merged.parse_errors, merged.parse_errors


# --------------------------------------------------------------------------------------
# Audit finding B2: measure on RENDERED output, not template sources
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("framework", ["claude", "copilot-cli", "copilot-vscode"])
def test_every_rendered_agent_file_is_fence_balanced(framework: str, tmp_path) -> None:
    """The real surface. Templates are not what ships; rendered files are.

    A render that does not balance is the input that produced "Nested fence not allowed" —
    an error naming a defect two steps from its cause.

    Three frameworks, not five: `tests/test_integration._run_pipeline` wires exactly these
    adapters. goose and agents_md are not covered here — noted rather than silently omitted.
    """
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from test_integration import _run_pipeline

    repo = pathlib.Path(__file__).resolve().parents[1]
    brief = repo / ".github/agents/_build-description.json"
    if not brief.exists():
        pytest.skip("self brief absent")

    out = tmp_path / "agents"
    _run_pipeline(brief, out, framework=framework)

    unbalanced = []
    for f in sorted(out.rglob("*.md")):
        b, e = _balance(f.read_text(encoding="utf-8"))
        if b != e:
            unbalanced.append(f"{f.relative_to(out)}: {b} BEGIN / {e} END")
    assert not unbalanced, (
        f"[{framework}] rendered file(s) have unbalanced fences:\n  " + "\n  ".join(unbalanced)
    )


# --------------------------------------------------------------------------------------
# handoffs-heading-overmatch-fix (2026-W33): exact-title match, no prefix over-match
# --------------------------------------------------------------------------------------


def test_handoff_prefixed_headings_survive_a_direct_strip() -> None:
    """A heading merely PREFIXED by "Handoff" is template-owned content, not a handoffs
    section — it must survive `_strip_handoffs_section` called directly, with no
    shrink-guard in the loop. Before this fix the only thing keeping these headings on
    disk was `preserve_on_shrink` coincidentally suppressing the emptied render."""
    text = (
        "## Handoff Payload Conflict Codes\n\n"
        "| code | meaning |\n|---|---|\n| X | y |\n\n"
        "## Handoff Payload Codes\n\nBody.\n\n"
        "## Handoffs\n\n- `@navigator` -> locate\n\n"
        "## Rules\n\nFollow them.\n"
    )
    out = FrameworkAdapter._strip_handoffs_section(text)
    assert "## Handoff Payload Conflict Codes" in out, "prefix over-match: conflict-codes heading stripped"
    assert "| X | y |" in out, "prefix over-match consumed template-owned body"
    assert "## Handoff Payload Codes" in out, "prefix over-match: payload-codes heading stripped"
    assert "## Handoffs" not in out and "@navigator" not in out, "the real section must still go"
    assert "## Rules" in out


def test_handoff_instructions_section_is_still_stripped() -> None:
    """`## Handoff Instructions` is a genuine handoffs section title — keep stripping it."""
    text = "## Rules\n\nFollow.\n\n## Handoff Instructions\n\n- `@security` -> clear\n"
    out = FrameworkAdapter._strip_handoffs_section(text)
    assert "Handoff Instructions" not in out and "@security" not in out
    assert "## Rules" in out
