"""`template-chapter-audit.csv` must keep describing the template tree it ships beside.

`docs_src/template-authoring.md` instructs authors to register every new template in the ledger
with a unique `TA-` ID. Nothing enforced that, and the ledger drifted: rows understated a rule
count by five and ranked four already-implemented functions as high-severity gaps.

`scripts/verify_audit_ledger.py` was written to catch exactly that, and it works — but it is
**report-only and manual**, and CI runs four steps, none of which is this script. A detector
nobody runs is the same as no detector; that is the half of the problem this file closes.

**Why a ratchet and not a backfill.** 28 of 60 templates have no row. Writing 28 rows now would
mean inventing dispositions, severities and chapter mappings for templates nobody audited —
fabricating exactly the judgment `verify_audit_ledger.py` says it cannot make. The baseline
records what is unregistered today; anything new must be registered.

**Why a generator was rejected.** The obvious reading of "this artifact has no generator" is to
write one. But a generator would overwrite the file, and the ledger's contents are *judgments*
(disposition, severity, chapter relationship) that no code can derive from the tree. Registration
is a deliberate authoring act by design. The gap was never a missing generator — it was that
nothing checked the authoring rule was followed.
"""

from __future__ import annotations

import csv
from pathlib import Path

_TEMPLATES = Path(__file__).resolve().parents[1] / "agentteams" / "templates"
_LEDGER = _TEMPLATES / "template-chapter-audit.csv"

#: Placeholder used by rows that document a gap with no template behind it.
_NO_TEMPLATE = {"", "-", "—"}

#: Templates carrying no ledger row on 2026-07-31, listed individually so the debt stays legible:
#: registering one means deleting its line, and nothing may be added.
_UNREGISTERED_BASELINE: frozenset[str] = frozenset({
    "builder/team-builder-goose.template.md",
    "domain/content-enricher.template.md",
    "domain/module-doc-author.template.md",
    "domain/module-doc-validator.template.md",
    "domain/research-analyst.template.md",
    "domain/retrieval-integrator.template.md",
    "domain/unix-philosophy-mapping-reference.template.md",
    "domain/work-summarizer.template.md",
    "universal/adjacent-repos.reference.template.md",
    "universal/ai-bad-habits-watch.reference.template.md",
    "universal/cli-tool-discovery.reference.template.md",
    "universal/external-retrieval-quality-gate.reference.template.md",
    "universal/framework-watch.reference.template.md",
    "universal/git-operations.template.md",
    "universal/github-workflows-merge.reference.template.md",
    "universal/parallelization.reference.template.md",
    "universal/repo-liaison.template.md",
    "universal/retrieval-integration.reference.template.md",
    "universal/retrieval-trigger-contract.reference.template.md",
    "universal/retrospective-remediation.reference.template.md",
    "universal/security-linux-hardening.reference.template.md",
    "universal/security-macos-hardening.reference.template.md",
    "universal/security-vulnerability-watch.reference.template.md",
    "universal/security-windows-hardening.reference.template.md",
    "universal/skill-generation.reference.template.md",
    "universal/work-summary-backfill.reference.template.md",
    "universal/work-summary-spec.reference.template.md",
    "universal/work-summary-tooling.reference.template.md",
})


def _templates_on_disk() -> set[str]:
    return {str(p.relative_to(_TEMPLATES)) for p in _TEMPLATES.rglob("*.template.md")}


def _registered() -> set[str]:
    with _LEDGER.open(encoding="utf-8") as fh:
        return {
            row["template_file"].strip()
            for row in csv.DictReader(fh)
            if row.get("template_file", "").strip() not in _NO_TEMPLATE
        }


def test_no_new_template_ships_unregistered():
    """The ratchet. A template added from here on must be registered in the ledger."""
    unregistered = sorted(_templates_on_disk() - _registered() - _UNREGISTERED_BASELINE)
    assert not unregistered, (
        "template(s) with no row in template-chapter-audit.csv:\n  "
        + "\n  ".join(unregistered)
        + "\n\nRegister each with a unique TA- ID per docs_src/template-authoring.md, or add it "
          "to _UNREGISTERED_BASELINE with a reason if it genuinely does not belong in the ledger."
    )


def test_the_baseline_does_not_keep_registered_templates():
    """A registered template must leave the baseline, so the debt cannot be padded."""
    stale = sorted(_UNREGISTERED_BASELINE & _registered())
    assert not stale, (
        "template(s) listed as unregistered but present in the ledger:\n  " + "\n  ".join(stale)
        + "\n\nDelete them from _UNREGISTERED_BASELINE — a baseline that keeps resolved entries "
          "hides the fact that the debt shrank."
    )


def test_the_baseline_does_not_name_deleted_templates():
    """A baseline entry for a template that no longer exists is bookkeeping, not debt."""
    ghosts = sorted(_UNREGISTERED_BASELINE - _templates_on_disk())
    assert not ghosts, (
        "_UNREGISTERED_BASELINE names template(s) not on disk:\n  " + "\n  ".join(ghosts)
    )


def test_every_ledger_row_names_a_template_that_exists():
    """Gated at zero, not ratcheted — this direction is already clean and must stay so."""
    phantom = sorted(_registered() - _templates_on_disk())
    assert not phantom, (
        "ledger row(s) naming a template that is not on disk:\n  " + "\n  ".join(phantom)
        + "\n\nA row pointing at a deleted template describes a tree that no longer exists."
    )


def test_the_reconciliation_is_actually_covering_something():
    """Guards every assertion above against a glob or column rename silently emptying it."""
    assert len(_templates_on_disk()) > 40, "template discovery found almost nothing"
    assert len(_registered()) > 20, "ledger parsing found almost no template_file values"
