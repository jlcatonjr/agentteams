"""test_redteam_findings_ledger.py — the ledger is a control, not a list.

A red-team audit that produces findings nobody records is theatre, and that is measurably what
the judgment layer was doing: its reports landed in a gitignored directory, were read by no
script but the two that wrote them, and triggered no notification of any kind. The daily
cadence was justified on *trend detection* while the artifacts showing which payload moved were
discarded every night.

Recording them is necessary and not sufficient. This repository already demonstrated how a
ledger without an ageing rule ends: the remediation log reached 28 open rows, seven of which
described work that had **already shipped**. A list nobody is forced to read stops describing
the system.

So the load-bearing assertion here is not that findings are written down — it is
:func:`test_an_untriaged_finding_ages_out`. Everything else keeps the vocabulary honest; that
one makes the vocabulary get used.

Every check has a test proving it **fires** on a defective ledger and a test proving it stays
silent on a clean one, because a validator nobody has watched fail is a validator nobody has
reason to trust.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from agentteams.redteam import findings_ledger as fl

REPO_ROOT = Path(__file__).resolve().parents[1]
TODAY = datetime.date(2026, 8, 7)


def _finding(**overrides) -> fl.Finding:
    base = dict(
        layer="judgment", finding_id="auth-01", interface="goose", model="z-ai/glm-5.2",
        expected="HALT", observed="REPORT", first_seen="2026-08-07", last_seen="2026-08-07",
    )
    base.update(overrides)
    return fl.Finding(**base)


def _ledger(tmp_path: Path, findings: list[fl.Finding]) -> Path:
    (tmp_path / "references").mkdir(parents=True, exist_ok=True)
    fl.write_ledger(tmp_path, findings)
    return tmp_path


def _register(tmp_path: Path, rows: str = "") -> None:
    (tmp_path / "references").mkdir(parents=True, exist_ok=True)
    (tmp_path / fl.PROVIDER_DOCS_REL).write_text(
        "| doc_id | provider | url | governs | last_verified | window_days |\n"
        "|---|---|---|---|---|---|\n" + rows,
        encoding="utf-8",
    )


# ===========================================================================
# the load-bearing one
# ===========================================================================

def test_an_untriaged_finding_ages_out(tmp_path: Path) -> None:
    """An unclassified finding cannot sit forever. This is what makes it a control."""
    old = (TODAY - datetime.timedelta(days=fl.UNTRIAGED_MAX_AGE_DAYS + 1)).isoformat()
    _ledger(tmp_path, [_finding(first_seen=old, last_seen=old)])

    problems = fl.ledger_problems(tmp_path, today=TODAY)

    assert len(problems) == 1
    assert "UNTRIAGED" in problems[0]
    # The message has to say what to do, or it produces a red suite and no action.
    assert "OUR-DEFECT" in problems[0] and "MODEL-LIMITATION" in problems[0]


def test_a_recent_untriaged_finding_is_allowed(tmp_path: Path) -> None:
    """Negative control: triage is not required instantly, only within the window.

    Without this the check would demand classification of a finding produced an hour ago, and
    the first response would be to widen the window until it stopped firing.
    """
    _ledger(tmp_path, [_finding()])
    assert fl.ledger_problems(tmp_path, today=TODAY) == []


def test_a_closed_untriaged_finding_does_not_age_out(tmp_path: Path) -> None:
    """Ageing pressure applies to OPEN questions. A closed row is a record, not a task."""
    old = (TODAY - datetime.timedelta(days=90)).isoformat()
    _ledger(tmp_path, [_finding(first_seen=old, last_seen=old, status="closed")])
    assert fl.ledger_problems(tmp_path, today=TODAY) == []


# ===========================================================================
# each triage class must carry its evidence
# ===========================================================================

@pytest.mark.parametrize("triage, required", [
    (fl.OUR_DEFECT, "remediation_target"),
    (fl.PROVIDER_DOCUMENTED, "citation"),
    (fl.MODEL_LIMITATION, "citation"),
    (fl.HARNESS_DEFECT, "remediation_target"),
])
def test_a_triage_class_without_its_evidence_fires(
    tmp_path: Path, triage: str, required: str
) -> None:
    """A class with no evidence is a label. Labels close questions that are still open."""
    _register(tmp_path)
    _ledger(tmp_path, [_finding(triage=triage)])

    problems = fl.ledger_problems(tmp_path, today=TODAY)

    assert len(problems) == 1 and required in problems[0]


def test_an_unknown_triage_class_fires(tmp_path: Path) -> None:
    _ledger(tmp_path, [_finding(triage="PROBABLY-FINE")])
    problems = fl.ledger_problems(tmp_path, today=TODAY)
    assert len(problems) == 1 and "unknown triage" in problems[0]


def test_a_dangling_citation_fires(tmp_path: Path) -> None:
    """A citation that resolves to nothing is worse than none: it looks like evidence."""
    _register(tmp_path, "| `goose-cli-run` | Goose | https://x | flags | 2026-08-07 | 90 |\n")
    _ledger(tmp_path, [_finding(triage=fl.MODEL_LIMITATION, citation="no-such-doc")])

    problems = fl.ledger_problems(tmp_path, today=TODAY)

    assert len(problems) == 1 and "resolves to no doc_id" in problems[0]


def test_a_resolving_citation_is_silent(tmp_path: Path) -> None:
    _register(tmp_path, "| `glm-5.2-card` | Z.AI | https://x | caps | 2026-08-07 | 90 |\n")
    _ledger(tmp_path, [_finding(triage=fl.MODEL_LIMITATION, citation="glm-5.2-card")])
    assert fl.ledger_problems(tmp_path, today=TODAY) == []


# ===========================================================================
# OUR-DEFECT must point at the module, never at a generated instance
# ===========================================================================

def test_our_defect_pointing_at_a_generated_instance_fires(tmp_path: Path) -> None:
    """The audited agent file is DERIVED. A fix written there does not survive.

    `.claude/agents/security.md` is rendered from `security.template.md`. A fix applied to the
    instance is overwritten in fenced regions by the next `--update --merge`, silently diverges
    in unfenced ones, and reaches no other generated team — so a row pointing there records a
    remediation that will quietly undo itself.
    """
    _ledger(tmp_path, [_finding(
        triage=fl.OUR_DEFECT, remediation_target=".claude/agents/security.md"
    )])

    problems = fl.ledger_problems(tmp_path, today=TODAY)

    assert len(problems) == 1
    assert "GENERATED artifact" in problems[0]
    assert "--update" in problems[0]


def test_our_defect_pointing_at_the_template_is_silent(tmp_path: Path) -> None:
    _ledger(tmp_path, [_finding(
        triage=fl.OUR_DEFECT,
        remediation_target="agentteams/templates/universal/security.template.md",
    )])
    assert fl.ledger_problems(tmp_path, today=TODAY) == []


# ===========================================================================
# the register goes stale, loudly
# ===========================================================================

def test_a_stale_register_entry_fires(tmp_path: Path) -> None:
    """A URL with no fresh verification cites whatever the page says NOW, not what was read."""
    old = (TODAY - datetime.timedelta(days=200)).isoformat()
    _register(tmp_path, f"| `goose-cli-run` | Goose | https://x | flags | {old} | 90 |\n")

    stale = fl.stale_provider_docs(tmp_path, today=TODAY)

    assert len(stale) == 1 and "goose-cli-run" in stale[0]


def test_a_fresh_register_entry_is_silent(tmp_path: Path) -> None:
    _register(tmp_path, "| `goose-cli-run` | Goose | https://x | flags | 2026-08-07 | 90 |\n")
    assert fl.stale_provider_docs(tmp_path, today=TODAY) == []


# ===========================================================================
# upsert semantics — one row per finding, not per run
# ===========================================================================

def test_a_repeated_failure_advances_one_row(tmp_path: Path) -> None:
    """365 identical rows a year would bury the only signal that matters."""
    (tmp_path / "references").mkdir(parents=True)
    observed = [_finding(first_seen="", last_seen="")]
    fl.promote(tmp_path, observed, today="2026-08-07")
    fl.promote(tmp_path, [_finding(first_seen="", last_seen="")], today="2026-08-08")

    rows = fl.read_ledger(tmp_path)

    assert len(rows) == 1, "a repeated identical failure created a second row"
    assert rows[0].first_seen == "2026-08-07" and rows[0].last_seen == "2026-08-08"


def test_a_changed_verdict_is_recorded_as_a_transition(tmp_path: Path) -> None:
    """This is the trend record the daily cadence was justified on."""
    (tmp_path / "references").mkdir(parents=True)
    fl.promote(tmp_path, [_finding(observed="REPORT", first_seen="", last_seen="")],
               today="2026-08-07")
    result = fl.promote(tmp_path, [_finding(observed="MISS", first_seen="", last_seen="")],
                        today="2026-08-08")

    assert result.transitioned == ["auth-01"]
    changed = [r for r in fl.read_ledger(tmp_path) if r.observed == "MISS"][0]
    assert "REPORT -> MISS" in changed.note


def test_promotion_never_deletes(tmp_path: Path) -> None:
    """"It stopped appearing" and "we fixed it" are different claims; only a human can say which.

    Same reason a probe that starts passing needs a re-validation note rather than a silent
    re-baseline: a finding can vanish because the control improved or because the harness went
    blind, and dropping the row destroys the evidence needed to tell them apart.
    """
    (tmp_path / "references").mkdir(parents=True)
    fl.promote(tmp_path, [_finding(first_seen="", last_seen="")], today="2026-08-07")
    fl.promote(tmp_path, [], today="2026-08-08")

    assert len(fl.read_ledger(tmp_path)) == 1


# ===========================================================================
# the live repository
# ===========================================================================

def test_the_live_ledger_is_clean() -> None:
    """The real ledger, held to its own rules."""
    problems = fl.ledger_problems(REPO_ROOT, today=datetime.date.today())
    assert not problems, "findings ledger problems:\n  " + "\n  ".join(problems)


def test_the_live_register_is_fresh() -> None:
    stale = fl.stale_provider_docs(REPO_ROOT, today=datetime.date.today())
    assert not stale, "provider docs past their verification window:\n  " + "\n  ".join(stale)


def test_the_live_ledger_is_not_empty() -> None:
    """Anti-vacuity: every check above passes trivially on an empty ledger.

    The judgment audit has run and produced failures; if the ledger is empty, promotion is
    broken and every assertion in this module is measuring nothing.
    """
    rows = fl.read_ledger(REPO_ROOT)
    assert len(rows) >= 10, (
        f"only {len(rows)} findings recorded, but the judgment layer has measured runs. "
        f"Promotion is not reaching {fl.FINDINGS_LEDGER_REL}."
    )
    assert {r.layer for r in rows} == {"judgment"} or "judgment" in {r.layer for r in rows}
