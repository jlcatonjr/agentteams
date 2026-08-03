"""test_unfenced_constraint_ratchet.py — constraint debt must not grow.

A constraint-bearing line outside every fence is a rule a project can delete and no
`--update --merge` will ever restore. `security.template.md` already forbids this
outright (test_security_authority_fenced.py); the rest of the template library carries
159 such lines across 43 files, which is too many to fence safely in one pass.

So: a ratchet, in the same shape as `BROAD_EXCEPT_BASELINE` and `LENGTH_ALLOWLIST`.
The count may fall, never rise. Lowering an entry as debt is paid is required — a stale
baseline silently re-opens the room it was meant to close.

**What a passing run does NOT establish.** It says no NEW unfenced constraint was added.
It says nothing about the 159 that remain: each is still deletable and unrestorable. The
security review's S4.5 stays open until they are fenced or reclassified, and this file is
the measurement of that debt, not its discharge.
"""

from __future__ import annotations

import pathlib

import pytest

from agentteams.fences import (
    CONSTRAINT_BEARING_RE,
    is_trackable_constraint_line,
    unfenced_lines,
)

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "agentteams/templates"

#: Constraint-bearing lines outside every fence, per template. Verified 2026-08-02.
#: LOWER these as fencing lands; never raise one to make a failure go away.
_BASELINE: dict[str, int] = {
    "AUTHORING-GUIDE.md": 13,
    "FENCE-CONVENTIONS.md": 8,
    "PLACEHOLDER-CONVENTIONS.md": 2,
    "builder/team-builder-claude.template.md": 3,
    "builder/team-builder-copilot-cli.template.md": 1,
    "builder/team-builder-copilot-vscode.template.md": 2,
    "builder/team-builder-goose.template.md": 3,
    "copilot-instructions.template.md": 2,
    "domain/agentteams-updater.template.md": 1,
    "domain/code-hygiene-rules-reference.template.md": 15,
    "domain/cohesion-repairer.template.md": 1,
    "domain/content-enricher.template.md": 2,
    "domain/format-converter.template.md": 2,
    "domain/module-doc-author.template.md": 3,
    "domain/module-doc-validator.template.md": 4,
    "domain/output-compiler.template.md": 3,
    "domain/post-production-auditor.template.md": 1,
    "domain/primary-producer.template.md": 2,
    "domain/quality-auditor.template.md": 1,
    "domain/reference-manager.template.md": 3,
    "domain/research-analyst.template.md": 1,
    "domain/style-guardian.template.md": 1,
    "domain/technical-validator.template.md": 1,
    "domain/visual-designer.template.md": 1,
    "domain/work-summarizer.template.md": 8,
    "universal/adversarial.template.md": 4,
    "universal/agent-refactor.template.md": 1,
    "universal/agent-updater.template.md": 2,
    "universal/cleanup.template.md": 2,
    "universal/cli-tool-discovery.reference.template.md": 2,
    "universal/code-hygiene.template.md": 13,
    "universal/conflict-auditor.template.md": 1,
    "universal/external-retrieval-quality-gate.reference.template.md": 8,
    "universal/git-operations.template.md": 2,
    "universal/github-workflows-merge.reference.template.md": 2,
    "universal/instruction-authority.reference.template.md": 7,
    "universal/navigator.template.md": 1,
    "universal/orchestrator.template.md": 11,
    "universal/parallelization.reference.template.md": 3,
    "universal/retrospective-remediation.reference.template.md": 2,
    "universal/skill-generation.reference.template.md": 3,
    "universal/work-summary-backfill.reference.template.md": 9,
    "universal/work-summary-tooling.reference.template.md": 2,
}


def _stray_count(path: pathlib.Path) -> int:
    """Constraint-bearing lines outside every fence.

    Eligibility (table rows out, fragments out) comes from
    `fences.is_trackable_constraint_line`, not from a copy here. This function used to carry
    its own `startswith("|")` and length floor, which is how it and
    `_detect_deleted_constraints` ended up disagreeing about what a rule is while both cited
    `CONSTRAINT_BEARING_RE` — the drift that constant's docstring exists to forbid.

    The matcher stays local: this counts `CONSTRAINT_BEARING_RE` only, where the detector also
    counts `NUMBERED_RULE_RE`. That divergence is real and tracked separately (130 lines
    across 24 templates); it is not a copy of a shared rule.
    """
    return sum(
        1
        for ln in unfenced_lines(path.read_text(encoding="utf-8"))
        if CONSTRAINT_BEARING_RE.search(ln) and is_trackable_constraint_line(ln)
    )


def _current() -> dict[str, int]:
    out: dict[str, int] = {}
    for f in sorted(TEMPLATES.rglob("*.md")):
        n = _stray_count(f)
        if n:
            out[str(f.relative_to(TEMPLATES))] = n
    return out


def test_no_template_gains_an_unfenced_constraint() -> None:
    """The ratchet. A rise means a new rule was written outside a fence."""
    current = _current()
    regressions = {
        k: (v, _BASELINE.get(k, 0)) for k, v in current.items() if v > _BASELINE.get(k, 0)
    }
    assert not regressions, (
        "template(s) gained constraint-bearing line(s) outside every fence "
        f"(now, baseline): {regressions}. A constraint outside a fence is one a project "
        "can delete and no --update --merge will restore. Put it inside a fence."
    )


def test_baseline_has_no_stale_entries() -> None:
    """Keep the ratchet honest: an entry that improved must be lowered."""
    current = _current()
    stale = {
        k: (current.get(k, 0), v) for k, v in _BASELINE.items() if current.get(k, 0) < v
    }
    assert not stale, (
        f"baseline entries are higher than reality (now, baseline): {stale}. Lower them, "
        "or the ratchet leaves room for a regression it was meant to catch."
    )


def test_the_measurement_is_not_vacuous() -> None:
    """A parser regression would zero the counts and pass forever."""
    current = _current()
    assert sum(current.values()) >= 100, (
        f"only {sum(current.values())} constraint lines found across the library; the "
        "matcher or the fence walk regressed"
    )


@pytest.mark.parametrize("rel", ["universal/security.template.md"])
def test_the_already_clean_templates_stay_clean(rel: str) -> None:
    """security.template.md is fenced outright and must not regress into the baseline."""
    assert rel not in _BASELINE, f"{rel} should carry zero unfenced constraints"
    assert _stray_count(TEMPLATES / rel) == 0
