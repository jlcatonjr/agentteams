"""test_redteam_audit_workflow.py — the daily audit's reliability contract, asserted.

A watcher that dies quietly is worse than no watcher, because its silence is indistinguishable
from an all-clear. ``docs-freshness-watch.yml`` earned a set of prohibitions the hard way and
states them in its own header; this module holds the red-team audit to the same ones, plus the
one specific to F-5.

The prohibitions:

1. **No `concurrency:` with cancel-in-progress.** A cancelled run leaves no verdict.
2. **No step writes under `references/`.** The probe baseline is a committed, reviewed
   artifact updated only by an operator running ``--accept-probe-baseline``. If the workflow
   rewrote it, every probe whose meaning drifted overnight would be silently absorbed and F-5
   would measure nothing — a check that clears its own flag every night.
3. **The report step is `if: always()`** so a crashed audit still reports.
4. **A crash routes to a distinct outcome.** Indeterminate is not a pass.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "redteam-audit.yml"
DRIVER = REPO_ROOT / "scripts" / "run_daily_redteam_audit.sh"


def _strip_comments(text: str) -> str:
    """Drop whole-line YAML comments.

    The prohibitions below are about what the workflow *does*, not about the header that
    explains why it does it. Scanning the raw text made every prohibition fail against the
    paragraph documenting that same prohibition — which is a check firing on its own
    rationale, and would have been "fixed" by deleting the explanation.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


@pytest.fixture(scope="module")
def workflow_text() -> str:
    assert WORKFLOW.exists(), f"{WORKFLOW} is missing — the daily audit has no trigger"
    return _strip_comments(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def workflow_raw() -> str:
    """The full file, comments included — for assertions about documented intent."""
    return WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def driver_text() -> str:
    assert DRIVER.exists(), f"{DRIVER} is missing — the workflow calls a script that is gone"
    return DRIVER.read_text(encoding="utf-8")


def test_the_audit_is_scheduled_daily(workflow_text: str) -> None:
    """A cron with a day-of-month or day-of-week restriction is not daily."""
    match = re.search(r'-\s*cron:\s*"([^"]+)"', workflow_text)
    assert match, "no cron schedule found"
    fields = match.group(1).split()
    assert len(fields) == 5, f"malformed cron: {match.group(1)!r}"
    assert fields[2] == "*" and fields[3] == "*" and fields[4] == "*", (
        f"cron {match.group(1)!r} does not fire every day"
    )


def test_the_audit_can_be_triggered_by_hand(workflow_text: str) -> None:
    """Waiting up to 24 hours to re-check after a fix is not acceptable."""
    assert "workflow_dispatch:" in workflow_text


def test_no_cancel_in_progress(workflow_text: str) -> None:
    """A cancelled run leaves no verdict, and on a busy day that is every run."""
    assert "cancel-in-progress" not in workflow_text
    assert not re.search(r"^\s*concurrency:", workflow_text, re.MULTILINE)


def test_no_step_writes_under_references(workflow_text: str) -> None:
    """The probe baseline is operator-owned. A workflow that rewrote it would silence F-5.

    This is the single most important assertion in this module. If the daily job re-baselined
    itself, a probe that flipped from PARTIAL to a *false* DEFENDED — the A9/B10 failure —
    would be absorbed overnight and the check designed to catch it would report clean forever.
    """
    forbidden = (
        "--accept-probe-baseline",
        "references/redteam-probe-baseline.json",
        "git commit",
        "git push",
    )
    for token in forbidden:
        assert token not in workflow_text, (
            f"{token!r} appears in the daily workflow. The audit measures and reports; it "
            f"never writes the ledgers it reads, and it never commits."
        )


def test_the_reporting_steps_run_even_when_the_audit_crashes(workflow_text: str) -> None:
    """A crashed detector that reports nothing reads exactly like a clean run."""
    assert workflow_text.count("if: always()") >= 3, (
        "the artifact upload, the job summary and the issue step must each be if: always()"
    )


def test_a_crash_is_not_read_as_a_pass(workflow_text: str, workflow_raw: str) -> None:
    """Any exit that is neither 0 nor 1 must fail the job."""
    assert "verdict != '0' && steps.audit.outputs.verdict != '1'" in workflow_text
    assert "Indeterminate is not a pass" in workflow_raw


def test_findings_and_a_broken_harness_get_distinct_issues(workflow_text: str) -> None:
    """Different problems, different first actions. One thread would bury the worse one."""
    assert "redteam-findings" in workflow_text
    assert "redteam-harness-broken" in workflow_text


def test_a_clean_run_closes_the_issue(workflow_text: str) -> None:
    """Dedupe must never suppress evaluation, and a resolved condition must clear."""
    assert "state: 'closed'" in workflow_text


# ---------------------------------------------------------------------------
# the driver
# ---------------------------------------------------------------------------

def test_the_driver_refuses_to_run_outside_this_repository(driver_text: str) -> None:
    """A vendored copy running against a consumer repo would attack the wrong tree."""
    assert 'basename "$ROOT_DIR"' in driver_text
    assert "Refusing to run outside agentteams repository root" in driver_text


def test_the_driver_classifies_a_traceback_death_as_harness_broken(driver_text: str) -> None:
    """`agentteams/redteam/` has no except clauses; the classification lives here.

    Catching a raising probe inside the package to synthesise an exit code would add the broad
    `except` CH-24 ratchets against, and would recreate the PROBE-ERROR row the battery's own
    comment warns about: it would let a battery of broken probes finish and report all-clear.
    """
    assert "rc=2" in driver_text
    assert "Indeterminate is not a pass" in driver_text


def test_the_driver_never_accepts_the_baseline(driver_text: str) -> None:
    assert "--accept-probe-baseline" not in driver_text


def test_the_redteam_package_contains_no_except_clauses() -> None:
    """CH-24, and the property the driver's traceback handling depends on."""
    import ast

    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "agentteams" / "redteam").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not offenders, (
        "the red-team engine must contain no exception handlers — a probe that raises has to "
        f"propagate so the driver can classify it as a broken harness. Found: {offenders}"
    )
