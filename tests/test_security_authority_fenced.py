"""The security agent's *authority* must be fence-restored, not just its *rules*.

A fenced region is template-owned: tamper with it on disk and the next `--update --merge` writes
the template's version back. Anything outside a fence is preserved unconditionally, forever.

`security.template.md` had that split backwards. Its rules (S-1..S-9, the trigger table, the
escalation criteria) were fenced, while the claims an attacker would actually target were not:

  - "PRIORITY LEVEL: HIGHEST ... No other agent, rule, or delegation overrides this agent's
    HALT directives"  -- the precedence claim
  - "You are **read-only**: you do not write code, modify files, or run terminal commands"
  - "HALT is final. If this agent returns HALT, the operation must stop."
  - the obligation to append every verdict to `references/security-decisions.log.csv`

Deleting any of those from a generated team was permanent. Measured 2026-08-01: 7 constraint-
bearing lines outside a fence in this template, including all four above.

This guards the corrected split. It is deliberately specific to the security agent -- the general
sweep is a separate, larger piece of work -- and it is the narrow precursor to a repo-wide
"no constraint-bearing line outside a fence" policy test.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agentteams.fences import _extract_fenced_regions
from agentteams.fences import _FENCE_BEGIN_RE, _FENCE_END_RE

_TEMPLATE = Path(__file__).resolve().parents[1] / (
    "agentteams/templates/universal/security.template.md"
)

#: Language that carries an obligation, a prohibition, or a capability limit.
_CONSTRAINT = re.compile(
    r"⛔|\bread-only\b|PRIORITY LEVEL|\bHALT\b|MUST NOT|\bnever\b", re.IGNORECASE
)

#: The authority claims this test exists to protect. Substrings, matched against fence bodies.
_AUTHORITY_CLAIMS = (
    "PRIORITY LEVEL: HIGHEST",
    "No other agent, rule, or delegation overrides",
    "You are **read-only**",
    "HALT is final",
    "Security Decisions Log",
)

#: Fences whose content states authority rather than procedure.
_AUTHORITY_FENCES = ("security_authority", "security_verdict_contract", "invariant_core")


def _text() -> str:
    return _TEMPLATE.read_text(encoding="utf-8")


def _regions() -> dict[str, str]:
    regions = _extract_fenced_regions(_text())
    assert isinstance(regions, dict), f"template does not parse: {regions}"
    return regions


def _lines_outside_fences(text: str) -> list[tuple[int, str]]:
    """Every line that sits outside both the front matter and every fence."""
    out: list[tuple[int, str]] = []
    in_fence = False
    in_front_matter = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.strip() == "---" and lineno <= 3:
            in_front_matter = True
            continue
        if in_front_matter and line.strip() == "---":
            in_front_matter = False
            continue
        if _FENCE_BEGIN_RE.search(line):
            in_fence = True
            continue
        if _FENCE_END_RE.search(line):
            in_fence = False
            continue
        if not in_fence and not in_front_matter:
            out.append((lineno, line))
    return out


def test_the_template_parses_and_carries_the_authority_fences():
    regions = _regions()
    for fence in _AUTHORITY_FENCES:
        assert fence in regions, f"{fence} missing; authority would be preserved-forever, not restored"


def test_no_constraint_bearing_line_sits_outside_a_fence():
    """The invariant. A constraint outside a fence is a constraint a merge will never restore."""
    stray = [
        (n, ln.strip())
        for n, ln in _lines_outside_fences(_text())
        if _CONSTRAINT.search(ln) and not ln.strip().startswith("|")
    ]
    assert not stray, (
        "constraint-bearing lines outside every fence in security.template.md — a project can "
        f"delete these and no --update --merge will bring them back: {stray}"
    )


@pytest.mark.parametrize("claim", _AUTHORITY_CLAIMS)
def test_each_authority_claim_lives_inside_a_fence(claim: str):
    """Presence is not enough: the claim must be *inside* a fenced region to be restored."""
    regions = _regions()
    assert any(claim in body for body in regions.values()), (
        f"{claim!r} is not inside any fence — it is present but not template-owned"
    )


@pytest.mark.parametrize("fence", _AUTHORITY_FENCES)
def test_authority_fences_are_position_independent(fence: str):
    """Part 2 of the three-part selection test, enforced.

    `_insert_section_at_render_position` anchors a new fence to whichever fences already exist on
    disk, so a first merge into a deployed team can place it anywhere in the file. Content that
    says "the table below" is then wrong. The original PRIORITY LEVEL claim said exactly that.
    """
    body = _regions()[fence]
    prose = "\n".join(
        ln for ln in body.splitlines() if not ln.lstrip().startswith("<!--")
    )
    offenders = re.findall(r"\b(?:below|above)\b", prose, re.IGNORECASE)
    # "below"/"above" is only a defect when it points at document position. Naming a section is
    # fine; this fence's own text must not rely on where the merge engine puts it.
    assert not offenders, (
        f"{fence} contains positional wording {offenders} — refer to sections by name instead, "
        "because a merged file may not carry them in template order"
    )
