"""advisory.py — Aggregate daily-pipeline advisory findings into a
single PR-ready markdown body.

The five in-repo advisory detectors (shrink, orphan, budget,
prefix-cache, operational-JSON) each write to a separate location.
This module reads those locations and produces a consolidated
report. Empty input → empty output (caller's signal to skip
opening a PR).

The dual-descriptor detector is intentionally excluded — its
findings come from consumer repositories and the remediation
belongs there, not in an agentteams-side PR.

Plan: references/plans/rc6-advisory-pr-pattern-2026-05-27.plan.md
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DAILY = ROOT / "tmp" / "daily-pipeline"  # gitignored — Operator-local logs.


def _read_today(subdir: str, today: str) -> str:
    path = DAILY / subdir / f"{today}.md"
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def shrink_section(today: str) -> str:
    raw = _read_today("shrink-events", today)
    if not raw.strip():
        return ""
    return "\n".join([
        "### Fenced-region shrink events",
        "",
        "Mergeable into the current code only if a human verifies the "
        "shrink is intentional. The pre-shrink content remains in the "
        "backup directory listed in each section. To restore, copy the "
        "fence body back from the backup and re-run `--update --merge`.",
        "",
        "```",
        raw.rstrip(),
        "```",
        "",
    ])


def orphan_section(today: str) -> str:
    raw = _read_today("orphan-events", today)
    if not raw.strip():
        return ""
    return "\n".join([
        "### Orphan agent files",
        "",
        "Files present on disk under `.github/agents/` that are not part "
        "of the current team configuration. Routing: `@cleanup` (delete "
        "if obsolete) or `@code-hygiene` (review). The daily pipeline "
        "never auto-deletes — destructive action requires orchestrator "
        "approval.",
        "",
        "```",
        raw.rstrip(),
        "```",
        "",
    ])


def budget_section() -> str:
    """Token-budget + prefix-cache findings, captured by invoking the
    same audit the daily pipeline already runs. Single call covers both
    detectors (3.1 and 3.4 from the efficiency review)."""
    agents_dir = ROOT / ".github" / "agents"
    if not agents_dir.is_dir():
        return ""
    try:
        result = subprocess.run(
            ["python", "build_team.py", "--self", "--yes", "--check-budget",
             "--security-offline", "--security-no-nvd"],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    body = (result.stdout + result.stderr).strip()
    findings = [ln for ln in body.splitlines() if ln.lstrip().startswith(("✗", "⚠"))]
    if not findings:
        return ""
    return "\n".join([
        "### Token-budget and prompt-cache findings",
        "",
        "Files exceeding `agentteams.budget` thresholds or carrying "
        "volatile content in the cache-prefix window. Routing: "
        "`@agent-refactor` for token-budget overruns (semantic refactor); "
        "manual HTML-comment wrap for prefix-volatile dates.",
        "",
        "```",
        "\n".join(findings),
        "```",
        "",
    ])


def operational_json_section(today: str) -> str:
    """Pull the operational-JSON audit section from the daily digest,
    if it ran and produced findings today."""
    digest = DAILY / "digest" / f"{today}.md"
    if not digest.is_file():
        return ""
    try:
        text = digest.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(
        r"^## Operational-JSON allow-list audit\n(.*?)(?=^## |\Z)",
        text, re.MULTILINE | re.DOTALL,
    )
    if not match:
        return ""
    return "\n".join([
        "### Operational-JSON allow-list audit",
        "",
        "Files matching the operational-metadata shape but not in "
        "`agentteams.scan._OPERATIONAL_JSON_NAMES`. Review and add to "
        "the allow-list if legitimate; scrub otherwise.",
        "",
        match.group(1).strip(),
        "",
    ])


def aggregate(today: str | None = None) -> str:
    """Return the full advisory markdown body, or empty if no findings."""
    today = today or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    sections = [
        shrink_section(today),
        orphan_section(today),
        budget_section(),
        operational_json_section(today),
    ]
    sections = [s for s in sections if s]
    if not sections:
        return ""
    header = "\n".join([
        f"# Daily-Pipeline Advisory — {today}",
        "",
        "Findings from in-repo advisory detectors that require operator "
        "decision. The daily pipeline does NOT auto-fix these — each is "
        "either semantic (requires judgment), destructive (requires "
        "approval), or policy-driven (requires sign-off).",
        "",
        "**How to respond:** review the sections below. Then:",
        "",
        "- **Merge this PR** to commit the dated finding to "
        "`references/advisories/` as a durable audit record.",
        "- **Close without merge** to dismiss the finding for today.",
        "- **Comment** with notes for the next operator pass (no "
        "automated grammar yet; freeform notes are read manually).",
        "",
        "---",
        "",
    ])
    return header + "\n".join(sections).rstrip() + "\n"


def hash_body(body: str) -> str:
    """12-hex SHA-256 prefix used as the dedup key."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
