"""The feature-audit workflow must not write state it later reads back.

Mirrors tests/test_redteam_audit_workflow.py. A scheduled job that re-baselines itself
silently absorbs the very drift it exists to detect, and the absorption is invisible
because the job stays green while doing it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WF = REPO / ".github/workflows/feature-audit.yml"


@pytest.fixture(scope="module")
def text() -> str:
    assert WF.is_file(), f"missing workflow: {WF}"
    return WF.read_text(encoding="utf-8")


def test_workflow_is_parseable_yaml(text):
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(text)
    assert doc, "workflow parsed to nothing"
    # `on:` is parsed by PyYAML as the boolean True (YAML 1.1); accept either spelling.
    assert ("on" in doc) or (True in doc), "workflow declares no triggers"
    assert "jobs" in doc and doc["jobs"], "workflow declares no jobs"


def test_no_step_writes_under_references(text):
    """references/ holds committed, reviewed artifacts. The audit only reads them."""
    offenders = [
        line.strip() for line in text.splitlines()
        if re.search(r"(>|>>|tee|cp|mv|sed -i|rm)\s+\S*references/", line)
    ]
    assert not offenders, "workflow writes under references/:\n" + "\n".join(offenders)


def test_no_cancel_in_progress(text):
    """A cancelled run leaves no verdict, and on a busy day that would be every run."""
    assert "cancel-in-progress: true" not in text


def test_report_step_runs_even_on_failure(text):
    assert "if: always()" in text, "a crashed audit must still report"


def test_harness_broken_is_distinguished_from_findings(text):
    """Indeterminate is not a pass, and code 2 must not be read as code 1."""
    assert "HARNESS BROKEN" in text
    assert "rc == '2'" in text or 'rc == "2"' in text


def test_schedule_does_not_collide_with_an_existing_workflow(text):
    """Two crons in the same minute queue against each other on shared runners."""
    mine = re.search(r'cron:\s*"([^"]+)"', text)
    assert mine, "no cron declared"
    others = set()
    for wf in (REPO / ".github/workflows").glob("*.yml"):
        if wf.name == WF.name:
            continue
        for m in re.finditer(r"cron:\s*['\"]([^'\"]+)['\"]", wf.read_text(encoding="utf-8")):
            others.add(m.group(1).strip())
    assert mine.group(1).strip() not in others, (
        f"cron {mine.group(1)!r} collides with an existing workflow"
    )
