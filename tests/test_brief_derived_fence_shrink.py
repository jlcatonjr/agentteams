"""_BRIEF_DERIVED_FENCES — a brief-driven rename must reach authority_hierarchy
and source_repositories through --update --merge, without losing the shrink
notice or the .lost.<sid>.md sidecar recovery mechanism.

Bug: shrink_policy=preserve (the default) read a legitimate brief-driven path
rename (e.g. src/ -> agentteams/) as suspected content loss via
_detect_fence_shrink rule (c), "lost concrete refs", and kept the stale body
forever — a brief-level rename could never reach these two fences through the
sanctioned --update --merge path. Confirmed live 2026-08-15
(agent-doc-optimal-structure plan): .github/copilot-instructions.md's
authority_hierarchy/source_repositories fences stayed on `src/` across a real
--update --merge run.

First fix attempt exempted these two fence ids inside _detect_fence_shrink
itself, the same way _LIVE_DATA_FENCES is exempted — which also silently
disabled the shrink notice and the sidecar for a real, unexpected shrink (a
bad brief edit, a hand-edit, tampering), and weakened --shrink-policy=halt
for them (scripts/run_daily_security_maintenance.sh runs with halt on
purpose). Caught by adversarial audit before shipping (remediation-log row
178) — the fix instead routes these two fence ids through
_is_template_authoritative (same mechanism already used for security fences):
the new content always wins, but detection/notice/sidecar keep running.
"""

from __future__ import annotations

from agentteams import emit


def _fenced(sid: str, body: str) -> str:
    return (
        f"<!-- AGENTTEAMS:BEGIN {sid} v=1 -->\n"
        f"{body}"
        + ("" if body.endswith("\n") else "\n")
        + f"<!-- AGENTTEAMS:END {sid} -->\n"
    )


_OLD_AUTHORITY_BODY = (
    "1. **Template library** (`src/templates/`) — agent file structure\n"
    "2. **JSON schemas** (`schemas/`) — input/output contract accuracy\n"
    "3. **Python source pipeline** (`src/`) — pipeline logic\n"
)
_NEW_AUTHORITY_BODY = (
    "1. **Template library** (`agentteams/templates/`) — agent file structure\n"
    "2. **JSON schemas** (`schemas/`) — input/output contract accuracy\n"
    "3. **Python source pipeline** (`agentteams/`) — pipeline logic\n"
)


def test_authority_hierarchy_rename_is_never_preserved():
    """The bug fix: a brief-driven rename always reaches the fence."""
    existing = _fenced("authority_hierarchy", _OLD_AUTHORITY_BODY)
    new = _fenced("authority_hierarchy", _NEW_AUTHORITY_BODY)
    mr = emit._merge_fenced_content(new, existing, preserve_on_shrink=True)
    assert "authority_hierarchy" in mr.sections_replaced
    assert mr.sections_preserved == []
    assert "src/" not in mr.merged_content
    assert "agentteams/" in mr.merged_content


def test_source_repositories_rename_is_never_preserved():
    existing = _fenced("source_repositories", "- `src/` — pipeline logic\n")
    new = _fenced("source_repositories", "- `agentteams/` — pipeline logic\n")
    mr = emit._merge_fenced_content(new, existing, preserve_on_shrink=True)
    assert "source_repositories" in mr.sections_replaced
    assert "src/" not in mr.merged_content


def test_shrink_notice_still_fires_for_brief_derived_fences():
    """The adversarial-audit correction: never-preserved must not mean silent."""
    existing = _fenced("authority_hierarchy", _OLD_AUTHORITY_BODY)
    new = _fenced("authority_hierarchy", _NEW_AUTHORITY_BODY)
    mr = emit._merge_fenced_content(new, existing, preserve_on_shrink=True)
    assert mr.shrink_notices, (
        "a real shrink (lost concrete refs: src/) must still produce a notice "
        "even though the fence is never preserved — silence here would also "
        "weaken --shrink-policy=halt for these fences"
    )
    assert any("authority_hierarchy" in n for n in mr.shrink_notices)


def test_lost_fence_sidecar_still_populated_for_brief_derived_fences():
    existing = _fenced("authority_hierarchy", _OLD_AUTHORITY_BODY)
    new = _fenced("authority_hierarchy", _NEW_AUTHORITY_BODY)
    mr = emit._merge_fenced_content(new, existing, preserve_on_shrink=True)
    assert "authority_hierarchy" in mr.lost_fence_bodies
    assert "src/" in mr.lost_fence_bodies["authority_hierarchy"]


def test_ordinary_fence_still_preserves_on_shrink_by_default():
    """Contrast case: the exemption is scoped to these two sids, not global."""
    existing_body = (
        "- rule a covering `example-collector`\n"
        "- rule b covering `example-research`\n"
        "- rule c covering `example-services-local`\n"
        "- rule d covering `example_data_collection`\n"
    )
    existing = _fenced("content", existing_body)
    new = _fenced("content", "- generic placeholder\n")
    mr = emit._merge_fenced_content(new, existing, preserve_on_shrink=True)
    assert "content" in mr.sections_preserved
    assert "example-collector" in mr.merged_content


def test_live_data_fences_still_suppress_notice_and_sidecar():
    """Contrast case: _LIVE_DATA_FENCES keeps its stronger, silent exemption."""
    existing = _fenced(
        "threat_intelligence",
        "- CVE-2026-0001 affects `foo`\n- CVE-2026-0002 affects `bar`\n"
        "- CVE-2026-0003 affects `baz`\n",
    )
    new = _fenced("threat_intelligence", "- no current threats\n")
    mr = emit._merge_fenced_content(new, existing, preserve_on_shrink=True)
    assert "threat_intelligence" in mr.sections_replaced
    assert mr.shrink_notices == []
    assert mr.lost_fence_bodies == {}
