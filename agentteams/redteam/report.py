"""report.py — render the audit artifacts: discoveries, remediation skeleton, self-audit.

Three documents, because they answer three different questions and get read by three different
readers at three different times:

* ``discoveries.md`` — what the attack phase found, with every count stated over its
  population. This is the document F-4 exists to keep honest, and the checker reads it back.
* ``remediation.plan.md`` — a skeleton, one row per open finding, each naming the verifier it
  needs and the rehearsal target it must be proven against. **Deliberately a skeleton**: the
  daily job does not decide fixes, and a plan that arrives pre-decided invites approval rather
  than review.
* ``selfaudit.md`` — phase 6. Lists every check, including the ones that could not run and
  why, because five clean checks and a silent sixth reads as six clean checks.

The Counts table is the only path by which this renderer emits a number. That is not a style
preference: :class:`~agentteams.redteam.runner.Count` refuses to construct without a
denominator and a canonical population source, so "state the denominator" is enforced at the
type rather than remembered at the keyboard.
"""

from __future__ import annotations

from pathlib import Path

from agentteams.redteam.registry import DEFENDED
from agentteams.redteam.runner import Findings
from agentteams.redteam.selfaudit import CHECK_IDS, CHECK_TITLES, SelfAuditResult

DISCOVERIES_NAME = "discoveries.md"
REMEDIATION_NAME = "remediation.plan.md"
SELFAUDIT_NAME = "selfaudit.md"
FINDINGS_NAME = "findings.json"


def _counts_table(findings: Findings) -> list[str]:
    """Render the Counts table — the only path by which a number reaches a report."""
    lines = [
        "| claim | numerator | denominator | population_source |",
        "|---|---|---|---|",
    ]
    for count in findings.counts:
        lines.append(
            f"| {count.claim} | {count.numerator} | {count.denominator} | "
            f"`{count.population_source}` |"
        )
    notes = [c for c in findings.counts if c.note]
    if notes:
        lines.append("")
        for count in notes:
            lines.append(f"- **{count.claim}** — {count.note}")
    return lines


def render_discoveries(findings: Findings) -> str:
    """Render ``discoveries.md`` from the attack phase.

    Args:
        findings: Phase 1 + 2 results.

    Returns:
        The document text.
    """
    lines = [
        "# Red-team discoveries",
        "",
        f"**Generated:** {findings.generated_at}",
        f"**Probe module:** {findings.probe_module or 'none registered'}",
        "",
        "## Counts",
        "",
        "Every number below is stated with the population it was computed over. A numerator "
        "without its denominator is the failure that hid 719 exposed agents in 34 untouched "
        "teams behind the sentence *\"0 agents on the ignored key across the fleet\"*.",
        "",
    ]
    lines.extend(_counts_table(findings))
    lines.extend(["", "## Harness integrity", ""])
    if findings.harness_is_broken:
        lines.append(
            "**The harness is broken. These results measure the instrument, not the target, "
            "and must not be read as a clean run.**"
        )
        lines.append("")
        for label, items in (
            ("control probes that failed", findings.control_failures),
            ("registration defects", findings.registration_problems),
            ("corpus claims that no longer match the scanner", findings.corpus_mismatches),
        ):
            if items:
                lines.append(f"- **{label}:**")
                lines.extend(f"  - {item}" for item in items)
    else:
        lines.append(
            "Controls all held, registration is well-formed, and every corpus payload's "
            "`scanner_matches` claim still matches what the scanner does."
        )

    lines.extend(["", "## Probe outcomes", ""])
    if not findings.probes:
        lines.append(
            "No probe module was registered, so the attack phase did not run. This is an "
            "unmeasured population, not a clean result."
        )
    else:
        lines.append("| probe | article | tier | outcome | control | name |")
        lines.append("|---|---|---|---|---|---|")
        for probe in findings.probes:
            marker = "" if probe.outcome == DEFENDED else " **"
            lines.append(
                f"| {probe.pid} | {probe.article} | {probe.tier} |{marker}{probe.outcome}"
                f"{marker} | {probe.control or '—'} | {probe.name} |"
            )
    return "\n".join(lines) + "\n"


def render_remediation_skeleton(
    findings: Findings,
    selfaudit: SelfAuditResult,
    kev_terms: frozenset[str] | None = None,
) -> str:
    """Render ``remediation.plan.md`` — one row per open item, no decisions made.

    Args:
        findings: Phase 1 + 2 results.
        selfaudit: Phase 6 results.
        kev_terms: Product/vendor/package names from the @security agent's already-fetched
            threat-intel cache (improvement item I3), or ``None`` when unavailable. Purely
            data — this function does no filesystem access itself; the caller (``cycle.py``)
            loads the cache, this only does a best-effort case-insensitive substring match
            against each row's own text and appends an ADVISORY note on a hit. Never changes
            a row's content, ordering, or implies a decision — the skeleton stays a skeleton.

    Returns:
        The document text.
    """
    lines = [
        "# Remediation skeleton",
        "",
        f"**Generated:** {findings.generated_at}",
        "",
        "The daily audit measures and reports; it does not remediate. This is a skeleton for "
        "a human or agent to take through phases 3–5 and 7: audit the plan, rehearse each fix "
        "**against an isolated copy of a real target**, and assert the complete expected end "
        "state rather than the absence of an error.",
        "",
        "Each row must be completed with the verifier that will prove the fix, and the "
        "rehearsal target it will be proven against. A fix with no named verifier repeats F-1; "
        "a fix with no named rehearsal target repeats F-2.",
        "",
        "| # | source | item | verifier (fill in) | rehearsal target (fill in) |",
        "|---|---|---|---|---|",
    ]
    def _row(row_num: int, source: str, item_text: str) -> str:
        note = _kev_correlation_note(item_text, kev_terms)
        return f"| {row_num} | {source} | {item_text}{note} | | |"

    row = 0
    for pid in findings.exploited:
        row += 1
        lines.append(_row(row, "probe", f"{pid} returned EXPLOITED — a measured attack is live"))
    for item in findings.control_failures:
        row += 1
        lines.append(_row(row, "harness", f"control probe {item} failed"))
    for item in findings.registration_problems:
        row += 1
        lines.append(_row(row, "harness", item))
    for item in findings.corpus_mismatches:
        row += 1
        lines.append(_row(row, "harness", item))
    for finding in selfaudit.findings:
        row += 1
        lines.append(
            _row(row, finding.check, f"{finding.subject}: {finding.detail} (→ {finding.remedy})")
        )
    if row == 0:
        lines.append("| — | — | nothing open | — | — |")
    return "\n".join(lines) + "\n"


def _kev_correlation_note(item_text: str, kev_terms: frozenset[str] | None) -> str:
    """Best-effort, advisory-only substring correlation (improvement item I3).

    Never a claim of certainty — a matched term means the finding's own text happens to
    contain a product/vendor/package name the @security agent's cache also flags, nothing
    more. No match, or no cache available, renders no note at all.
    """
    if not kev_terms:
        return ""
    lowered = item_text.lower()
    # A short/common term (e.g. a 2-3 char vendor abbreviation) would match almost any
    # sentence and turn an advisory signal into noise — require a minimum length.
    hit = next((t for t in kev_terms if t and len(t) >= 4 and t.lower() in lowered), None)
    if hit is None:
        return ""
    return f" *(advisory: '{hit}' also appears in the cached threat-intel watch — not a claim of relevance, worth a look)*"


def render_selfaudit(selfaudit: SelfAuditResult, generated_at: str) -> str:
    """Render ``selfaudit.md`` — phase 6, including the checks that could not run.

    Args:
        selfaudit: Phase 6 results.
        generated_at: Timestamp for the header.

    Returns:
        The document text.
    """
    grouped = selfaudit.by_check()
    lines = [
        "# Red-team self-audit (phase 6)",
        "",
        f"**Generated:** {generated_at}",
        "",
        "Phase 6 evaluates the red team, not the target. Each check below exists because a "
        "2026-08-06 remediation shipped something that looked like a control and was not.",
        "",
        "| check | what it enforces | status |",
        "|---|---|---|",
    ]
    for check_id in CHECK_IDS:
        if check_id in selfaudit.checks_skipped:
            status = "**SKIPPED**"
        elif grouped.get(check_id):
            status = f"**{len(grouped[check_id])} finding(s)**"
        else:
            status = "clean"
        lines.append(f"| {check_id} | {CHECK_TITLES[check_id]} | {status} |")

    if selfaudit.checks_skipped:
        lines.extend(["", "## Checks that did not run", "",
                      "Listed rather than omitted: a report showing five clean checks and "
                      "saying nothing about the sixth reads as six clean checks.", ""])
        for check_id, reason in sorted(selfaudit.checks_skipped.items()):
            lines.append(f"- **{check_id}** — {reason}")

    lines.extend(["", "## Findings", ""])
    if not selfaudit.findings:
        lines.append("None.")
    else:
        for check_id in CHECK_IDS:
            items = grouped.get(check_id) or []
            if not items:
                continue
            lines.extend([f"### {check_id} — {CHECK_TITLES[check_id]}", ""])
            for finding in items:
                lines.append(f"- **{finding.subject}** — {finding.detail}")
                lines.append(f"  - *Remedy:* {finding.remedy}")
            lines.append("")

    if selfaudit.advisory:
        lines.extend([
            "",
            "## Advisory — hand-written historical documents",
            "",
            "These do not gate the run. The handoff's rule is that *a finding report* must "
            "fail its own audit gate — the report being produced, not every report ever "
            "written. Gating on immutable historical documents would leave the daily job "
            "permanently red, and a permanently red job is one nobody reads. They are listed "
            "in full so they can be worked off deliberately rather than quietly dropped.",
            "",
        ])
        for finding in selfaudit.advisory:
            lines.append(f"- **{finding.subject}** — {finding.detail}")
    return "\n".join(lines).rstrip() + "\n"


def artifact_texts(
    findings: Findings,
    selfaudit: SelfAuditResult,
    kev_terms: frozenset[str] | None = None,
) -> dict[str, str]:
    """Return every artifact as ``filename -> text``, written by nothing here.

    Rendering and writing are separated so ``--dry-run`` is a property of the caller rather
    than a flag threaded through the renderers — the renderers have no filesystem access at
    all, which is the only way "a dry run writes nothing" is provable rather than promised.
    ``kev_terms`` (improvement item I3) keeps that guarantee: it is pure data the caller
    already loaded, not a path this function would read itself.

    Args:
        findings: Phase 1 + 2 results.
        selfaudit: Phase 6 results.
        kev_terms: See :func:`render_remediation_skeleton`.

    Returns:
        ``{filename: text}`` for the three markdown artifacts.
    """
    return {
        DISCOVERIES_NAME: render_discoveries(findings),
        REMEDIATION_NAME: render_remediation_skeleton(findings, selfaudit, kev_terms),
        SELFAUDIT_NAME: render_selfaudit(selfaudit, findings.generated_at),
    }


def write_artifacts(report_dir: Path, texts: dict[str, str]) -> list[Path]:
    """Write rendered artifacts into ``report_dir``.

    Args:
        report_dir: Destination, created if absent.
        texts: ``filename -> text`` from :func:`artifact_texts`.

    Returns:
        The paths written, in sorted order.
    """
    report_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name in sorted(texts):
        path = report_dir / name
        path.write_text(texts[name], encoding="utf-8")
        written.append(path)
    return written


__all__ = [
    "DISCOVERIES_NAME",
    "FINDINGS_NAME",
    "REMEDIATION_NAME",
    "SELFAUDIT_NAME",
    "artifact_texts",
    "render_discoveries",
    "render_remediation_skeleton",
    "render_selfaudit",
    "write_artifacts",
]
