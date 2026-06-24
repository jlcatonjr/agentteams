"""Guard: no stray plan/report/investigation documents at the repository root.

Root cause this prevents
------------------------
Multiple concurrent autonomous sessions run against this repo, each following the
standing rule "every multi-step request must generate a plan." That rule targets
``tmp/by-week/…`` but lives only in the *generated-team* instructions; a direct
in-repo session that does not read them defaults the plan file to the current
working directory — the repo root. The result was an accumulation of stray
``*-plan.md`` / ``*-report.md`` files (and an ignore-in-place ``.gitignore``
band-aid). See ``references/filing-conventions.md`` for the full policy and the
canonical homes (``tmp/by-week/`` for active plans, ``references/plans/`` for
retained ones).

This guard fails when any ``*.md`` appears at the repo root that is not on the
canonical allowlist below — catching strays in CI and local test runs.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# The only Markdown files permitted at the repository root: canonical project
# docs plus two deliberately-maintained artifacts (see references/filing-conventions.md).
ALLOWED_ROOT_MD = {
    "README.md",
    "CHANGELOG.md",
    "CLAUDE.md",
    "SECURITY.md",
    "STABILITY.md",
    # build-team-plan.md is part of the memory-index source set
    # (MEMORY_INDEX_EXTRA_DOC_NAMES in agentteams/cli/artifacts.py).
    "build-team-plan.md",
    # bridge-offline-investigation.md kept at root by maintainer decision (commit 9716b47).
    "bridge-offline-investigation.md",
    # AGENTS.md is the SHARED, multi-tool standard entry file written to the repo
    # root by design when this project is bridged/generated for Goose (and the
    # agents-md framework) — Goose reads it via CONTEXT_FILE_NAMES. It is a
    # bridge-owned, fenced canonical root doc, not a stray plan/report.
    # See references/bridge-refresh-safety.md and references/filing-conventions.md.
    "AGENTS.md",
}

# Temporary allowances for root *.md owned by an in-flight session (relocate to
# references/plans/ once the session is done). Empty: the 2026-06-15 filing
# remediation's security-waiver-remediation-plan.md has been relocated to
# references/plans/security-waiver-remediation.plan.md.
TEMP_ALLOWED_ROOT_MD: set[str] = set()


def test_no_stray_plan_docs_at_repo_root() -> None:
    allowed = ALLOWED_ROOT_MD | TEMP_ALLOWED_ROOT_MD
    present = {p.name for p in REPO_ROOT.glob("*.md")}
    strays = sorted(present - allowed)
    assert not strays, (
        "Stray document(s) at the repository root: "
        + ", ".join(strays)
        + ".\nPlan/investigation/report docs must NOT live at the root. Move them to "
        "references/plans/<slug>.plan.md (retained) or tmp/by-week/YYYY-Www/ (active). "
        "If a file is genuinely a canonical root doc, add it to ALLOWED_ROOT_MD. "
        "Policy: references/filing-conventions.md"
    )
