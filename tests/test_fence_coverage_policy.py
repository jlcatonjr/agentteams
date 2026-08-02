"""A constraint outside a fence is a constraint no `--update --merge` will ever restore.

Fenced regions are template-owned: tamper with one on disk and the next merge writes the
template's version back. Everything outside a fence is preserved unconditionally, forever. So
where a rule sits decides whether it survives an edit, and the split was made by accident rather
than by decision — procedural content tended to get fenced, authority content tended not to.

**Fenceless templates are exempt, and that is not an oversight.** `emit._normalize_generated_content`
wraps a rendered file's whole body in a single `content` fence *only if it contains no fence at
all*. So a template with zero fences is 100% template-owned, and a template with one fence owns
only that one region. Partial fencing is strictly weaker than none. This test therefore applies
only to templates that already have a fence — the ones where an unfenced line is genuinely
unreachable by a merge.

The ratchet mirrors `test_code_hygiene.py`'s `CEILING_MARGIN_BASELINE`: existing offenders are
recorded and may shrink but never grow, and a new offender fails outright. Baselining is not
forgiveness — every line below is a rule a project can delete permanently.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentteams.fences import CONSTRAINT_BEARING_RE as _CONSTRAINT
from agentteams.fences import _FENCE_BEGIN_RE as _BEGIN
from agentteams.fences import unfenced_lines

TEMPLATES = Path(__file__).resolve().parents[1] / "agentteams/templates"

# Classification uses the ENGINE's marker regexes, not a substring. A template that documents
# fence syntax in prose or a code span (instruction-authority.reference.template.md does) would
# otherwise be misread as fenced, and then wrongly policed for unfenced constraints.


#: Templates that already have a fence and still carry constraint-bearing lines outside one.
#: Measured 2026-08-01 after security.template.md was brought to zero (7 -> 0): **19 lines across
#: 8 templates**.
#:
#: The number moved twice while this test was being written, both times because the *classifier*
#: was wrong rather than the templates. 103/32 counted fenceless templates, which are exempt.
#: 21/9 used a substring match for `AGENTTEAMS:BEGIN`, which misread `agent-updater.template.md`
#: — a fenceless template that *documents* fence syntax in prose — as fenced, and then policed it.
#: Both are why classification now uses the engine's own marker regexes: a test and a merge engine
#: that disagree about what a fence is will disagree about what is protected.
#:
#: `orchestrator.template.md` holds 11 of the 19 and is the known, deliberate case: its
#: constitutional-rules list is designated USER-EDITABLE because projects extend it, and a fence
#: spanning the list would delete their additions on merge. The fix is to split a fenced
#: security-critical core from the unfenced extension region, not to fence the list as it stands.
FENCE_COVERAGE_BASELINE: dict[str, int] = {
    "universal/orchestrator.template.md": 11,
    "copilot-instructions.template.md": 2,
    "universal/navigator.template.md": 1,
    "universal/conflict-auditor.template.md": 1,
    "domain/technical-validator.template.md": 1,
    "domain/research-analyst.template.md": 1,
    "domain/quality-auditor.template.md": 1,
    "domain/agentteams-updater.template.md": 1,
}


def _unfenced_constraint_lines(text: str) -> list[tuple[int, str]]:
    """Constraint-bearing lines outside both the front matter and every fence.

    Walk and constraint definition both come from `fences`: a ratchet counting "rules outside a
    fence" and a merge notice claiming "a rule was deleted" must mean the same thing by the same
    definition, or neither number means anything. This module previously had its own copy of both.
    """
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(unfenced_lines(text), 1):
        if line.strip().startswith("|"):
            continue
        if _CONSTRAINT.search(line):
            out.append((lineno, line.strip()))
    return out


def _offenders() -> dict[str, int]:
    """Templates that already carry a fence, mapped to their unfenced-constraint-line count."""
    out: dict[str, int] = {}
    for path in sorted(TEMPLATES.rglob("*.template.md")):
        text = path.read_text(encoding="utf-8")
        if not _BEGIN.search(text):
            continue  # fenceless -> whole-body wrapped -> fully owned -> exempt
        count = len(_unfenced_constraint_lines(text))
        if count:
            out[str(path.relative_to(TEMPLATES))] = count
    return out


def test_no_new_template_leaves_a_constraint_outside_a_fence() -> None:
    """A fenced template gaining an unfenced rule is a rule that can be deleted permanently."""
    newly = {k: v for k, v in _offenders().items() if k not in FENCE_COVERAGE_BASELINE}
    assert not newly, (
        f"Template(s) with a constraint-bearing line outside every fence: {newly}.\n"
        "That line is preserved-forever, not restored — a project can delete it and no "
        "--update --merge will bring it back. Move it inside a fence, or add it to "
        "FENCE_COVERAGE_BASELINE with a reason if it is genuinely project-owned."
    )


def test_existing_unfenced_constraints_do_not_grow() -> None:
    """The ratchet: a baselined template may shrink, never grow."""
    current = _offenders()
    grown = {
        rel: (baseline, current[rel])
        for rel, baseline in FENCE_COVERAGE_BASELINE.items()
        if rel in current and current[rel] > baseline
    }
    assert not grown, (
        f"Template(s) already carrying unfenced constraints grew more (baseline -> now): {grown}.\n"
        "Fence the new one rather than widening the exposure."
    )


def test_the_coverage_baseline_is_current() -> None:
    """A baseline entry for a template since brought to zero is stale bookkeeping.

    Without this, `security.template.md` would still be listed at 7 and the ratchet would silently
    permit a regression back up to 7.
    """
    current = _offenders()
    stale = {
        rel: baseline
        for rel, baseline in FENCE_COVERAGE_BASELINE.items()
        if current.get(rel, 0) < baseline
    }
    assert not stale, (
        f"FENCE_COVERAGE_BASELINE is behind reality (baseline -> actual is lower): {stale}.\n"
        "Lower the numbers so the ratchet holds the ground that was actually gained."
    )


#: Templates carrying the Tier 1 `constitutional_core` fence.
_CORE_TEMPLATES = (
    "universal/orchestrator.template.md",
    "copilot-instructions.template.md",
)


def test_the_constitutional_core_is_fenced_everywhere_it_appears() -> None:
    """The core is the whole point: unfenced, it is deletable and never restored."""
    from agentteams.fences import _extract_fenced_regions

    for rel in _CORE_TEMPLATES:
        regions = _extract_fenced_regions((TEMPLATES / rel).read_text(encoding="utf-8"))
        assert isinstance(regions, dict), f"{rel} does not parse: {regions}"
        assert "constitutional_core" in regions, (
            f"{rel} has no constitutional_core fence — C-1..C-5 would be preserved-forever "
            "rather than restored, which is the exact failure this fence exists to prevent."
        )


def test_the_constitutional_core_is_position_independent() -> None:
    """Part 2 of the selection test, on the fence most likely to land somewhere unexpected.

    Measured: merging the core into `.claude/agents/orchestrator.md` places it at char 7641,
    *after* the Constitutional Rules at char 1545, because those rules are unfenced and the
    nearest anchor is the fence that follows them. So the core must name the section it relates
    to, never point at it by position. "No lower tier" is a tier reference and fine; "nothing
    below" reads as document order and is not.
    """
    from agentteams.fences import _extract_fenced_regions

    for rel in _CORE_TEMPLATES:
        body = _extract_fenced_regions((TEMPLATES / rel).read_text(encoding="utf-8"))["constitutional_core"]
        prose = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("<!--"))
        offenders = re.findall(r"\b(?:below|above|following|preceding)\b", prose, re.IGNORECASE)
        assert not offenders, (
            f"{rel}: constitutional_core uses positional wording {offenders}. A merge can place "
            "this fence anywhere in a deployed file; refer to sections by name."
        )


def test_the_c_rules_do_not_drift_between_their_three_homes() -> None:
    """C-1..C-5 are stated in three files; three copies of a rule is three chances to disagree.

    The reference is the definition; the two `constitutional_core` fences are the enforceable
    restatement an agent actually reads. A silent divergence would leave a team obeying one
    ordering while its own reference documents another — the conflict class this whole ordering
    exists to remove.
    """
    from agentteams.fences import _extract_fenced_regions

    def c_rules(text: str) -> dict[str, str]:
        return {m.group(1): m.group(2).strip()
                for m in re.finditer(r"\*\*(C-[1-5]) ([^.]+)\.\*\*", text)}

    ref = c_rules((TEMPLATES / "universal/instruction-authority.reference.template.md").read_text(encoding="utf-8"))
    assert len(ref) == 5, f"the reference should define C-1..C-5, found {sorted(ref)}"

    for rel in _CORE_TEMPLATES:
        core = _extract_fenced_regions((TEMPLATES / rel).read_text(encoding="utf-8"))["constitutional_core"]
        assert c_rules(core) == ref, (
            f"{rel}: constitutional_core has drifted from "
            f"instruction-authority.reference.template.md — the reference is the definition."
        )


def test_fenceless_templates_are_not_counted() -> None:
    """Guards the exemption itself — if this inverts, the ratchet starts policing the safe case.

    A fenceless template is whole-body wrapped at emit time and therefore fully template-owned.
    Counting it as an offender would push someone to add a partial fence, which would make it
    *less* protected, not more.
    """
    fenceless = [
        p for p in TEMPLATES.rglob("*.template.md")
        if not _BEGIN.search(p.read_text(encoding="utf-8"))
    ]
    assert fenceless, "no fenceless templates found — the exemption path is untested"
    names = set(_offenders())
    assert not any(str(p.relative_to(TEMPLATES)) in names for p in fenceless)
