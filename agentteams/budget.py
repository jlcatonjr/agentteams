"""budget.py — Agent-file efficiency lint (token-budget + cache-prefix shape).

Read-only audit over the live `.agent.md` files. Two checks:

1. **Token budget.** Estimates per-file token count (chars/4 heuristic; coarse
   but stable across files). Warns when a file exceeds `BUDGET_WARN_LINES`
   and fails when it exceeds `BUDGET_FAIL_LINES`. Orchestrator-class agents
   get a higher budget (`ORCHESTRATOR_FAIL_LINES`) because they enumerate
   the team. Tunable constants — change deliberately.

2. **Prefix-cache friendliness.** Anthropic prompt caching keys on the
   stable prefix of the prompt. Volatile content (timestamps, hashes,
   per-run values) within the first `PREFIX_WINDOW_LINES` causes the
   cache to miss on every refresh. Flags ISO-date patterns outside HTML
   comments in the prefix window.

Both checks are advisory — they emit findings; remediation goes to
`@agent-refactor` per the constitutional gate.

Plan: 3.1 + 3.4 from the 2026-05-27 efficiency review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# Tunable thresholds. Adjust deliberately; doc the rationale in CHANGELOG.
# --------------------------------------------------------------------------

#: Warn when a non-orchestrator agent file exceeds this many lines.
#: Derived from current p75 size (~165 lines) + headroom.
BUDGET_WARN_LINES = 300

#: Fail when a non-orchestrator agent file exceeds this many lines.
#: Picked to flag the current orchestrator (466 lines) as a one-off
#: exception requiring the orchestrator carve-out below.
BUDGET_FAIL_LINES = 600

#: Orchestrator-class fail threshold (the orchestrator enumerates the team).
ORCHESTRATOR_FAIL_LINES = 1000

#: Lines inspected for prefix-cache friendliness. The first 60 lines
#: typically contain front matter + identity + role description — the
#: portion that benefits most from caching.
PREFIX_WINDOW_LINES = 60

#: Coarse char-to-token ratio used for the budget estimate.
CHARS_PER_TOKEN = 4

_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


@dataclass
class BudgetFinding:
    """One advisory finding about an agent file."""
    file: str
    category: str  # "budget-warn" | "budget-fail" | "prefix-volatile"
    severity: str  # "warn" | "fail"
    message: str


@dataclass
class BudgetReport:
    """Aggregate of all budget findings."""
    scanned_files: int = 0
    findings: list[BudgetFinding] = field(default_factory=list)

    @property
    def has_failures(self) -> bool:
        return any(f.severity == "fail" for f in self.findings)


def _is_orchestrator(rel_path: str) -> bool:
    return rel_path.endswith("orchestrator.agent.md")


def _strip_html_comments(text: str) -> str:
    return _HTML_COMMENT_RE.sub("", text)


def _check_budget(rel_path: str, lines: list[str], chars: int, findings: list[BudgetFinding]) -> None:
    """3.1 — file-size budget."""
    n = len(lines)
    fail_thresh = ORCHESTRATOR_FAIL_LINES if _is_orchestrator(rel_path) else BUDGET_FAIL_LINES
    approx_tokens = chars // CHARS_PER_TOKEN
    if n > fail_thresh:
        findings.append(BudgetFinding(
            file=rel_path,
            category="budget-fail",
            severity="fail",
            message=f"{n} lines (~{approx_tokens} tokens) exceeds fail threshold {fail_thresh}",
        ))
    elif n > BUDGET_WARN_LINES and not _is_orchestrator(rel_path):
        findings.append(BudgetFinding(
            file=rel_path,
            category="budget-warn",
            severity="warn",
            message=f"{n} lines (~{approx_tokens} tokens) exceeds warn threshold {BUDGET_WARN_LINES}",
        ))


def _check_prefix_cache(rel_path: str, lines: list[str], findings: list[BudgetFinding]) -> None:
    """3.4 — prefix-cache friendliness: no volatile dates in first PREFIX_WINDOW_LINES."""
    prefix = "\n".join(lines[:PREFIX_WINDOW_LINES])
    cleaned = _strip_html_comments(prefix)
    matches = _ISO_DATE_RE.findall(cleaned)
    if matches:
        sample = matches[:3]
        more = f" (+{len(matches) - 3} more)" if len(matches) > 3 else ""
        findings.append(BudgetFinding(
            file=rel_path,
            category="prefix-volatile",
            severity="warn",
            message=(
                f"ISO-date(s) within first {PREFIX_WINDOW_LINES} lines outside HTML comments: "
                f"{', '.join(sample)}{more}. Move to a fenced section lower in the file "
                f"or wrap in an HTML comment to preserve prompt-cache hits."
            ),
        ))


def scan_directory(agents_dir: Path) -> BudgetReport:
    """Audit every `*.agent.md` file directly under `agents_dir` (not recursive).

    Skips `.agentteams-backups/` by virtue of being non-recursive.
    """
    report = BudgetReport()
    if not agents_dir.is_dir():
        return report

    for agent_file in sorted(agents_dir.glob("*.agent.md")):
        try:
            content = agent_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = content.splitlines()
        rel = agent_file.name
        report.scanned_files += 1
        _check_budget(rel, lines, len(content), report.findings)
        _check_prefix_cache(rel, lines, report.findings)
    return report


def print_report(report: BudgetReport) -> None:
    """Human-readable summary for CLI / daily-pipeline log."""
    print(f"Agent-budget scan: {report.scanned_files} file(s)")
    if not report.findings:
        print("  ✓ No findings.")
        return
    warns = sum(1 for f in report.findings if f.severity == "warn")
    fails = sum(1 for f in report.findings if f.severity == "fail")
    print(f"  {fails} fail, {warns} warn")
    for f in report.findings:
        marker = "✗" if f.severity == "fail" else "⚠"
        print(f"  {marker} {f.file} [{f.category}] {f.message}")
