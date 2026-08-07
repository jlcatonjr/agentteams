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
DRIVER = REPO_ROOT / "scripts" / "run_redteam_audit.sh"
CATCHUP = REPO_ROOT / ".github" / "workflows" / "redteam-audit-catchup.yml"


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


def _cron_fields(text: str) -> list[str]:
    match = re.search(r'-\s*cron:\s*"([^"]+)"', text)
    assert match, "no cron schedule found"
    fields = match.group(1).split()
    assert len(fields) == 5, f"malformed cron: {match.group(1)!r}"
    return fields


def test_the_audit_is_scheduled_weekly(workflow_text: str) -> None:
    """Weekly on a fixed weekday, not daily and not monthly.

    The cadence moved from daily on 2026-08-07 at the operator's instruction. The coverage
    cost is near zero and it is worth recording why, because "we made the security audit less
    frequent" reads badly without it: tests/test_constitutional_redteam.py runs the full
    38-probe battery on EVERY CI run, so the fast regression net for the 21 closed exploits is
    CI. The cron uniquely provides the phase-6 self-audit and the dated artifact trail.
    """
    fields = _cron_fields(workflow_text)
    assert fields[2] == "*" and fields[3] == "*", (
        f"cron {' '.join(fields)!r} restricts day-of-month or month; that is not weekly"
    )
    assert fields[4] != "*", (
        f"cron {' '.join(fields)!r} has no weekday restriction — it is still daily"
    )
    assert fields[4].isdigit(), (
        f"cron {' '.join(fields)!r} should name ONE weekday so the catch-up window is a "
        f"single known day"
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


# ===========================================================================
# the catch-up guard — one test per risk decision
# ===========================================================================
#
# The guard re-fires the weekly audit when GitHub drops or delays its scheduled run. Every
# assertion below corresponds to a way the guard could fail *silently*, which is the only way
# it can fail dangerously: a guard that wrongly concludes "the audit already ran" suppresses a
# security audit and looks exactly like one that worked.

@pytest.fixture(scope="module")
def catchup_text() -> str:
    assert CATCHUP.exists(), (
        f"{CATCHUP} is missing — a dropped weekly run would go unnoticed for seven days"
    )
    return _strip_comments(CATCHUP.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def catchup_raw() -> str:
    return CATCHUP.read_text(encoding="utf-8")


def test_the_guard_runs_hourly_on_the_audits_scheduled_day(
    catchup_text: str, workflow_text: str
) -> None:
    """Hourly, and on the SAME weekday the audit is scheduled for.

    A guard watching a different day than the audit fires is a guard that never sees the thing
    it is watching for — and it would report nothing, indefinitely, exactly like a working one.
    """
    guard = _cron_fields(catchup_text)
    audit = _cron_fields(workflow_text)

    minute, hour = guard[0], guard[1]
    assert minute.isdigit(), (
        f"guard minute field {minute!r} is not a single minute; the guard should fire exactly "
        f"once per hour, not several times"
    )
    hours = (
        24 if hour == "*"
        else len(range(int(hour.split("-")[0]), int(hour.split("-")[1]) + 1)) if "-" in hour
        else len(hour.split(","))
    )
    assert hours >= 2, (
        f"guard hour field {hour!r} covers {hours} hour(s), so it fires once — the request was "
        f"an HOURLY retry until the audit runs or the next cycle opens"
    )
    assert guard[4] == audit[4], (
        f"the guard watches weekday {guard[4]} but the audit runs on weekday {audit[4]}. "
        f"A guard watching the wrong day never fires and never says so."
    )


def test_the_guard_keys_on_completion_not_on_success(catchup_text: str) -> None:
    """A run that found something HAS run. Retrying it would spam.

    The audit exits 1 on findings and 2 on a broken harness — both `conclusion: failure` to
    GitHub, and both mean the audit executed. A guard keyed on conclusion would re-fire it
    every hour for the rest of the day on any Monday with a real finding: 17 runs and 17 issue
    comments, turning a working alarm into noise.
    """
    assert "status === 'completed'" in catchup_text, (
        "the guard must decide on run STATUS (did it run), not on conclusion (was it clean)"
    )
    assert "r.conclusion ===" not in catchup_text and "conclusion ==" not in catchup_text, (
        "the guard branches on `conclusion` somewhere — a run with findings would be treated "
        "as not having run, and re-fired hourly"
    )


def test_the_guard_fails_open(catchup_text: str, catchup_raw: str) -> None:
    """An unusable answer must RUN the audit, never suppress it.

    'I could not tell whether it ran' resolving to 'it must have run' is indeterminate read as
    a pass — the inversion the audit's own exit-code policy exists to prevent, relocated into
    the thing that decides whether the audit happens at all. A spurious extra audit costs a
    runner-minute; a wrongly suppressed one is silent.
    """
    assert "continue-on-error: true" in catchup_text, (
        "a crashed decision step must not fail the job, or the dispatch step never evaluates"
    )
    assert "steps.decide.outputs.missing != 'false'" in catchup_text, (
        "the dispatch condition must be `!= 'false'`, not `== 'true'`. With `== 'true'` an "
        "unset output (a crashed step) suppresses the audit, which is failing CLOSED."
    )
    assert "fail" in catchup_raw.lower() and "open" in catchup_raw.lower(), (
        "the fail-open decision must be stated in the file, not only implied by an operator"
    )


def test_the_guard_writes_no_state(catchup_text: str) -> None:
    """No cursor. The verdict is run history plus the wall clock, and nothing else.

    A 'last successful run' marker is a file that, once stale or corrupted, silently suppresses
    every future audit — the same prohibition redteam-audit.yml already carries.
    """
    for token in (
        "references/", "git commit", "git push", "upload-artifact",
        "cache@", "GITHUB_ENV",
    ):
        assert token not in catchup_text, (
            f"{token!r} appears in the catch-up guard. Its verdict must derive from the GitHub "
            f"API and the clock; anything persisted becomes a cursor that can suppress the audit."
        )


def test_the_guard_dispatches_the_audit_and_the_audit_accepts_dispatch(
    catchup_text: str, workflow_text: str
) -> None:
    """The dispatch target must exist and must be dispatchable, or the guard 404s in silence."""
    assert "createWorkflowDispatch" in catchup_text
    assert "workflow_id: 'redteam-audit.yml'" in catchup_text
    assert "workflow_dispatch:" in workflow_text, (
        "redteam-audit.yml has no workflow_dispatch trigger, so the guard cannot fire it"
    )
    assert "actions: write" in catchup_text, (
        "dispatching a workflow needs `actions: write`; `read` alone fails with 403"
    )


def test_the_guard_has_no_cancel_in_progress(catchup_text: str) -> None:
    """Same reason as the audit: a cancelled guard leaves no decision."""
    assert "cancel-in-progress" not in catchup_text
    assert not re.search(r"^\s*concurrency:", catchup_text, re.MULTILINE)


def test_nothing_still_calls_the_audit_daily() -> None:
    """Rule 7: a name or doc asserting a cadence the system no longer has is stale content.

    Scoped to the files that DESCRIBE the current system. The CHANGELOG and the plan archives
    are history and correctly say "daily" about the period when it was daily.
    """
    live_surfaces = [
        REPO_ROOT / ".github" / "workflows" / "redteam-audit.yml",
        CATCHUP,
        DRIVER,
        REPO_ROOT / "references" / "redteam-audit.procedure.md",
    ]
    assert not (REPO_ROOT / "scripts" / "run_daily_redteam_audit.sh").exists(), (
        "the old driver name is still on disk"
    )
    # Precision matters more than reach. "daily" appears legitimately in these files for
    # three reasons that are NOT stale claims about this audit: the daily
    # security-maintenance job it deconflicts from, the note recording the rename, and the
    # one-line instruction for reverting to daily. A rule that flagged all of them would be
    # satisfied by deleting the explanations — which is worse than the drift it hunts.
    _NOT_A_CADENCE_CLAIM = (
        "security-maintenance", "security_maintenance", "run_daily_security",
        "not daily", "was daily", "reverting", "renamed from",
    )
    offenders = {}
    for path in live_surfaces:
        if not path.exists():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            low = line.lower()
            if "daily" not in low:
                continue
            if any(marker in low for marker in _NOT_A_CADENCE_CLAIM):
                continue
            offenders.setdefault(str(path.relative_to(REPO_ROOT)), []).append(number)
    assert not offenders, (
        f"these live surfaces still describe the audit as daily: {offenders}. The cadence is "
        f"weekly; only history (CHANGELOG, plan archives) may say otherwise."
    )
