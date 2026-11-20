"""Commit-bound workflow-conformance report, verifiable by a release check (operator ruling 2026-11-20).

An x.y.0 release needs a passing live conformance run on its release candidate before the final tag. This module
fixes the report's shape so a tag check can verify it without trusting the run:

* the report names the **RC commit**, the **threshold**, the **model and provider pins** of every draw's model, the
  **coverage** classes at that commit, and **per-workflow, per-model** draw counts;
* ``report_sha256`` is the SHA-256 of the canonical JSON of everything else, so an edited report no longer verifies;
* :func:`check_conformance_report` re-derives the live-class workflows from the template at the RC commit and fails
  unless every one is present for every panel model and meets the threshold.

The digest catches accidental and casual edits; it is not a signature. Who may write a passing report, and an
operator's signed waiver for a bypass, belong to the release-policy check that calls this. stdlib only.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from agentteams.redteam import workflow_spec

REPORT_TYPE = "workflow-conformance-report"
REPORT_VERSION = "1"


def _body_sha256(body: dict[str, Any]) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def build_conformance_report(rc_commit: str, threshold: dict[str, int], panel: list[dict[str, str]],
                             results: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble a report and stamp its digest.

    Args:
        rc_commit: Full 40-hex SHA of the release candidate the run used.
        threshold: ``{"draws": N, "min_conforming": k}``: each panel model needs at least k of N conforming draws
            on every live-class workflow.
        panel: One ``{"model", "provider"}`` per panel model (provider routing pinned, AC1).
        results: One ``{"workflow", "model", "draws", "conforming", "first_deviations": [...]}`` per pair.

    Returns:
        The report, including ``report_sha256``.

    Raises:
        ValueError: For a commit that is not a full SHA or a threshold outside ``0 < k <= N``.
    """
    if len(rc_commit) != 40 or any(c not in "0123456789abcdef" for c in rc_commit):
        raise ValueError("rc_commit must be a full lower-case 40-hex SHA")
    n, k = int(threshold.get("draws", 0)), int(threshold.get("min_conforming", 0))
    if not 0 < k <= n:
        raise ValueError("threshold needs 0 < min_conforming <= draws")
    body = {
        "artifact_type": REPORT_TYPE,
        "report_version": REPORT_VERSION,
        "rc_commit": rc_commit,
        "threshold": {"draws": n, "min_conforming": k},
        "panel": sorted(panel, key=lambda p: (p["model"], p["provider"])),
        "coverage": {w: c for w, (c, _r) in sorted(workflow_spec.COVERAGE.items())},
        "results": sorted(results, key=lambda r: (r["workflow"], r["model"])),
    }
    return {**body, "report_sha256": _body_sha256(body)}


def check_conformance_report(report: dict[str, Any], rc_commit: str, template_text: str) -> list[str]:
    """Return why ``report`` does not clear the release gate for ``rc_commit`` (empty when it does).

    Args:
        report: A report from :func:`build_conformance_report`, as read back from disk.
        rc_commit: The commit about to be tagged.
        template_text: ``orchestrator.template.md`` at ``rc_commit``.

    Returns:
        Problems: a digest mismatch, another commit, a wrong type, a coverage table that no longer matches the
        template, or a live workflow that is missing or under threshold for a panel model.
    """
    body = {k: v for k, v in report.items() if k != "report_sha256"}
    problems: list[str] = []
    if report.get("report_sha256") != _body_sha256(body):
        problems.append("report_sha256 does not match the report body (edited or truncated)")
    if report.get("artifact_type") != REPORT_TYPE:
        problems.append(f"artifact_type is {report.get('artifact_type')!r}, not {REPORT_TYPE!r}")
    if report.get("rc_commit") != rc_commit:
        problems.append(f"report is bound to {report.get('rc_commit')!r}, not {rc_commit!r}")
    problems += [f"coverage at the RC: {p}" for p in workflow_spec.check_coverage_is_complete(template_text)]
    threshold = report.get("threshold") or {}
    n, k = threshold.get("draws", 0), threshold.get("min_conforming", 0)
    if not (isinstance(n, int) and isinstance(k, int) and 0 < k <= n):
        problems.append("threshold is missing or invalid")
        return problems
    models = sorted({p.get("model", "") for p in report.get("panel", [])})
    if not models:
        problems.append("panel is empty")
    got = {(r.get("workflow"), r.get("model")): r for r in report.get("results", [])}
    live = [w for w in workflow_spec.workflow_headings(template_text)
            if workflow_spec.COVERAGE.get(w, ("", ""))[0] == workflow_spec.LIVE]
    for wid in live:
        for model in models:
            r = got.get((wid, model))
            draws, ok = (r or {}).get("draws"), (r or {}).get("conforming")
            if r is None:
                problems.append(f"Workflow {wid}: no result for {model}")
            elif not (isinstance(draws, int) and isinstance(ok, int) and 0 <= ok <= draws):
                problems.append(f"Workflow {wid}: {model} has invalid counts {ok!r}/{draws!r}")
            elif draws < n or ok < k:
                problems.append(f"Workflow {wid}: {model} conformed {r.get('conforming', 0)}/{r.get('draws', 0)}, "
                                f"needs {k}/{n}")
    return problems


__all__ = ["REPORT_TYPE", "REPORT_VERSION", "build_conformance_report", "check_conformance_report"]
