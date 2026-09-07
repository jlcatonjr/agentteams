"""Tests for T2.D5 shrink_policy behaviour in agentteams.emit.emit_all."""

from __future__ import annotations

from pathlib import Path

from agentteams import emit


# A minimal fenced file whose existing body contains backtick identifiers
# that the new render drops — triggers _detect_fence_shrink rule (c).
EXISTING = """# Demo

Outside-fence content.

<!-- AGENTTEAMS:BEGIN demo v=1 -->
- item one referencing `CVE-2024-AAAA`
- item two referencing `CVE-2024-BBBB`
- item three referencing `CVE-2024-CCCC`
- item four referencing `CVE-2024-DDDD`
<!-- AGENTTEAMS:END demo -->

More outside content.
"""

NEW_RENDER = """# Demo

Outside-fence content.

<!-- AGENTTEAMS:BEGIN demo v=1 -->
- item one
<!-- AGENTTEAMS:END demo -->

More outside content.
"""


def _setup(tmp_path: Path) -> Path:
    target = tmp_path / "demo.agent.md"
    target.write_text(EXISTING, encoding="utf-8")
    return target


def test_warn_writes_smaller_content(tmp_path):
    target = _setup(tmp_path)
    result = emit.emit_all(
        [("demo.agent.md", NEW_RENDER)],
        output_dir=tmp_path,
        merge=True,
        yes=True,
        shrink_policy="warn",
    )
    assert result.notices, "warn should still surface notices"
    assert not result.shrink_blocked
    body = target.read_text(encoding="utf-8")
    assert "CVE-2024-AAAA" not in body  # shrink occurred
    assert "item one" in body


def test_halt_skips_write_and_records_blocked(tmp_path):
    target = _setup(tmp_path)
    result = emit.emit_all(
        [("demo.agent.md", NEW_RENDER)],
        output_dir=tmp_path,
        merge=True,
        yes=True,
        shrink_policy="halt",
    )
    assert result.notices, "halt should still surface notices for visibility"
    assert result.shrink_blocked == [str(target)]
    body = target.read_text(encoding="utf-8")
    # Original CVE refs preserved because the write was refused.
    assert "CVE-2024-AAAA" in body
    assert "CVE-2024-DDDD" in body


def test_dry_run_halt_preflight_lists_blocked_without_writing(tmp_path):
    """T5.1 / IV.1: dry-run + halt populates shrink_blocked with the file
    that a real run would refuse to write, without touching the file.
    """
    target = _setup(tmp_path)
    original_bytes = target.read_bytes()
    result = emit.emit_all(
        [("demo.agent.md", NEW_RENDER)],
        output_dir=tmp_path,
        merge=True,
        yes=True,
        dry_run=True,
        shrink_policy="halt",
    )
    assert result.notices
    assert str(target) in result.shrink_blocked
    # File must be byte-identical to before — dry-run never writes.
    assert target.read_bytes() == original_bytes


def test_preserve_keeps_enriched_body_and_is_default(tmp_path):
    """Respectful update: the default 'preserve' policy keeps the richer
    existing fence body instead of overwriting it with the thinner render,
    surfaces a notice, writes no sidecar, and blocks nothing."""
    target = _setup(tmp_path)
    result = emit.emit_all(
        [("demo.agent.md", NEW_RENDER)],
        output_dir=tmp_path,
        merge=True,
        yes=True,
        # shrink_policy omitted on purpose — 'preserve' is the default.
    )
    body = target.read_text(encoding="utf-8")
    # Enriched concrete refs survive — nothing lost.
    assert "CVE-2024-AAAA" in body
    assert "CVE-2024-DDDD" in body
    # The thin template body was NOT applied to the shrinking fence.
    assert body.count("item one") == 1 and "item two" in body
    # Visible, non-blocking notice with the preserve wording.
    assert result.notices
    assert any("retained existing enriched body" in n for n in result.notices)
    assert not result.shrink_blocked


def test_preserve_still_updates_non_shrinking_fences(tmp_path):
    """preserve must not freeze the whole file: fences that don't shrink still
    receive their template updates."""
    existing = (
        "# Demo\n\n"
        "<!-- AGENTTEAMS:BEGIN rich v=1 -->\n"
        "- keep `CVE-2024-AAAA`\n- keep `CVE-2024-BBBB`\n"
        "- keep `CVE-2024-CCCC`\n- keep `CVE-2024-DDDD`\n"
        "<!-- AGENTTEAMS:END rich -->\n\n"
        "<!-- AGENTTEAMS:BEGIN plain v=1 -->\n"
        "old plain body\n"
        "<!-- AGENTTEAMS:END plain -->\n"
    )
    new_render = (
        "# Demo\n\n"
        "<!-- AGENTTEAMS:BEGIN rich v=1 -->\n"
        "- only one\n"
        "<!-- AGENTTEAMS:END rich -->\n\n"
        "<!-- AGENTTEAMS:BEGIN plain v=1 -->\n"
        "new improved plain body with more detail and guidance\n"
        "<!-- AGENTTEAMS:END plain -->\n"
    )
    target = tmp_path / "demo.agent.md"
    target.write_text(existing, encoding="utf-8")
    result = emit.emit_all(
        [("demo.agent.md", new_render)],
        output_dir=tmp_path,
        merge=True,
        yes=True,
        shrink_policy="preserve",
    )
    body = target.read_text(encoding="utf-8")
    # Shrinking fence preserved...
    assert "CVE-2024-AAAA" in body and "CVE-2024-DDDD" in body
    # ...while the non-shrinking fence was updated to the new template body.
    assert "new improved plain body" in body
    assert "old plain body" not in body
    assert not result.shrink_blocked


def test_allow_writes_silently(tmp_path):
    target = _setup(tmp_path)
    result = emit.emit_all(
        [("demo.agent.md", NEW_RENDER)],
        output_dir=tmp_path,
        merge=True,
        yes=True,
        shrink_policy="allow",
    )
    assert not result.notices, "allow should suppress notices"
    assert not result.shrink_blocked
    body = target.read_text(encoding="utf-8")
    assert "CVE-2024-AAAA" not in body  # shrink occurred silently


# ---------------------------------------------------------------------------
# Security-owned fences: the template wins, even under shrink-policy=preserve
#
# `preserve` resolves in favour of on-disk content whenever the template's body looks
# materially smaller. For most fences that is right — it protects operator enrichment.
# For a security contract it inverts the trust: the mechanism cannot distinguish
# enrichment from tampering, because they are the same operation, and appending a
# backticked token to a fenced security region is enough to suppress the update
# indefinitely while a notice scrolls past each run.
#
# Deliberately a SEPARATE set from `_LIVE_DATA_FENCES`. That one means "upstream feed
# rotation is expected", which is a different claim; conflating them would obscure both.
# ---------------------------------------------------------------------------


def test_security_owned_fence_is_not_preserved_on_shrink():
    """A security fence takes the template's body even when preserve is on."""
    from agentteams.fences import _TEMPLATE_AUTHORITATIVE_FENCES, _merge_fenced_content

    sid = sorted(_TEMPLATE_AUTHORITATIVE_FENCES)[0]
    template = (
        f"<!-- AGENTTEAMS:BEGIN {sid} v=1 -->\n"
        "S-1 the authoritative rule.\n"
        f"<!-- AGENTTEAMS:END {sid} -->\n"
    )
    # On disk: same fence, but padded so the template looks like a material shrink.
    on_disk = (
        f"<!-- AGENTTEAMS:BEGIN {sid} v=1 -->\n"
        "S-1 the authoritative rule.\n"
        + "".join(f"- `extra/path/{i}.md` appended content\n" for i in range(12))
        + f"<!-- AGENTTEAMS:END {sid} -->\n"
    )
    result = _merge_fenced_content(template, on_disk, preserve_on_shrink=True)
    assert sid not in result.sections_preserved, (
        f"{sid} was preserved from disk; a security-owned fence must take the template's "
        "body, or padding it is enough to suppress the update forever"
    )
    assert "extra/path/0.md" not in result.merged_content, "the padded on-disk body survived"


def test_ordinary_fence_is_still_preserved_on_shrink():
    """The protection for operator enrichment is unchanged."""
    from agentteams.fences import _TEMPLATE_AUTHORITATIVE_FENCES, _merge_fenced_content

    sid = "routing_table_rows"
    assert sid not in _TEMPLATE_AUTHORITATIVE_FENCES
    template = f"<!-- AGENTTEAMS:BEGIN {sid} v=1 -->\nshort.\n<!-- AGENTTEAMS:END {sid} -->\n"
    on_disk = (
        f"<!-- AGENTTEAMS:BEGIN {sid} v=1 -->\nshort.\n"
        + "".join(f"- `enriched/{i}.md` operator content\n" for i in range(12))
        + f"<!-- AGENTTEAMS:END {sid} -->\n"
    )
    result = _merge_fenced_content(template, on_disk, preserve_on_shrink=True)
    assert sid in result.sections_preserved, "operator enrichment must still be protected"


# ---------------------------------------------------------------------------
# additive shrink-policy: splice NEW heading-delimited sub-sections into an
# enriched fence, preserving the enriched body verbatim (strict superset).
# ---------------------------------------------------------------------------

# Enriched on-disk `available_workflows`: only through Workflow 11, and Workflow 11
# carries a project-specific concrete ref (`reports/`) that the fresh template lacks —
# so a plain re-render both shrinks (loses the ref) AND fails to add the new workflows.
_WF_EXISTING = (
    "# Orchestrator\n\n"
    "<!-- AGENTTEAMS:BEGIN available_workflows v=2 -->\n"
    "### Workflow 10: Plan\nDraft the plan.\n\n"
    "### Workflow 11: Final Check\nVerify output in `reports/` before closeout.\n"
    "<!-- AGENTTEAMS:END available_workflows -->\n\n"
    "Outside-fence project notes.\n"
)
# Fresh render: generic Workflow 11 (no `reports/`) + three brand-new workflows.
_WF_NEW = (
    "# Orchestrator\n\n"
    "<!-- AGENTTEAMS:BEGIN available_workflows v=2 -->\n"
    "### Workflow 10: Plan\nDraft the plan.\n\n"
    "### Workflow 11: Final Check\nVerify output before closeout.\n\n"
    "### Workflow 12: Spawn-Authority Query Funnel\nFunnel spawn requests.\n\n"
    "### Workflow 13: Scoped Child Orchestrator\nSpawn a scoped child.\n\n"
    "### Workflow 14: Management Directives\nStatus/identity check-in at every pause.\n"
    "<!-- AGENTTEAMS:END available_workflows -->\n\n"
    "Outside-fence project notes.\n"
)


def test_additive_splices_new_subsections_preserving_enrichment(tmp_path):
    """additive delivers new heading-delimited sub-sections into an enriched fence
    while keeping the enriched body verbatim — the exact fleet-propagation case."""
    target = tmp_path / "orchestrator.agent.md"
    target.write_text(_WF_EXISTING, encoding="utf-8")
    result = emit.emit_all(
        [("orchestrator.agent.md", _WF_NEW)],
        output_dir=tmp_path,
        merge=True,
        yes=True,
        shrink_policy="additive",
    )
    body = target.read_text(encoding="utf-8")
    # Enrichment preserved (nothing dropped) ...
    assert "reports/" in body
    assert "Verify output in `reports/` before closeout." in body
    # ... AND the new sub-sections were delivered ...
    assert "Workflow 12" in body and "Workflow 13" in body and "Workflow 14" in body
    assert "Status/identity check-in at every pause." in body
    # ... in render order, after the enriched Workflow 11.
    assert (body.index("Workflow 11") < body.index("Workflow 12")
            < body.index("Workflow 13") < body.index("Workflow 14"))
    # Outside-fence content untouched; nothing blocked.
    assert "Outside-fence project notes." in body
    assert not result.shrink_blocked


def test_additive_falls_back_to_preserve_when_no_new_subsection(tmp_path):
    """When a shrink has no new heading-delimited sub-section to add, additive keeps
    the enriched body (like preserve) rather than dropping it."""
    existing = (
        "<!-- AGENTTEAMS:BEGIN demo v=1 -->\n"
        "### Section A\nKeep `enriched/a.md` and `enriched/b.md` and `enriched/c.md`.\n"
        "<!-- AGENTTEAMS:END demo -->\n"
    )
    new_render = (
        "<!-- AGENTTEAMS:BEGIN demo v=1 -->\n"
        "### Section A\nThinner.\n"
        "<!-- AGENTTEAMS:END demo -->\n"
    )
    target = tmp_path / "demo.agent.md"
    target.write_text(existing, encoding="utf-8")
    result = emit.emit_all(
        [("demo.agent.md", new_render)],
        output_dir=tmp_path,
        merge=True,
        yes=True,
        shrink_policy="additive",
    )
    body = target.read_text(encoding="utf-8")
    assert "enriched/a.md" in body and "enriched/c.md" in body  # nothing lost
    assert "Thinner." not in body                                # thin body not applied
    assert not result.shrink_blocked


def test_additive_respects_template_authoritative_fence():
    """A template-authoritative (security-owned) fence still takes the template body
    under additive — padding it must not suppress the update, same as preserve."""
    from agentteams.fences import _TEMPLATE_AUTHORITATIVE_FENCES, _merge_fenced_content

    sid = sorted(_TEMPLATE_AUTHORITATIVE_FENCES)[0]
    template = f"<!-- AGENTTEAMS:BEGIN {sid} v=1 -->\n### New\ncanonical body.\n<!-- AGENTTEAMS:END {sid} -->\n"
    on_disk = (
        f"<!-- AGENTTEAMS:BEGIN {sid} v=1 -->\n### Old\n"
        + "".join(f"- `pad/{i}.md` padding\n" for i in range(12))
        + f"<!-- AGENTTEAMS:END {sid} -->\n"
    )
    result = _merge_fenced_content(template, on_disk, additive_on_shrink=True)
    assert sid not in result.sections_preserved
    assert "pad/0.md" not in result.merged_content
    assert "canonical body." in result.merged_content


def test_additive_merge_block_is_strict_superset():
    """The splice never drops a concrete ref or list item from the enriched body."""
    from agentteams.fences import _additive_merge_block, _detect_fence_shrink

    existing = (
        "<!-- AGENTTEAMS:BEGIN wf v=1 -->\n"
        "### Workflow 11\nRefs `a/x.md`, `b/y.md`, `c/z.md`.\n"
        "<!-- AGENTTEAMS:END wf -->\n"
    )
    new = (
        "<!-- AGENTTEAMS:BEGIN wf v=1 -->\n"
        "### Workflow 11\nRefs changed.\n\n### Workflow 12\nNew.\n"
        "<!-- AGENTTEAMS:END wf -->\n"
    )
    merged, n, _notices = _additive_merge_block("wf", existing, new)
    assert n == 1
    for ref in ("a/x.md", "b/y.md", "c/z.md"):
        assert ref in merged
    assert "Workflow 12" in merged
    # No shrink of the merged result versus the enriched original.
    assert _detect_fence_shrink("wf", existing, merged) is None


def test_additive_does_not_duplicate_on_post_colon_title_drift():
    """A parenthetical/detail drift after the colon must NOT make the template
    heading look brand new (which would silently duplicate the sub-section)."""
    from agentteams.fences import _additive_merge_block

    existing = (
        "<!-- AGENTTEAMS:BEGIN wf v=1 -->\n"
        "### Workflow 14: Management Directives (issue / honor)\n"
        "Enriched body referencing `reports/local.md`.\n"
        "<!-- AGENTTEAMS:END wf -->\n"
    )
    new = (
        "<!-- AGENTTEAMS:BEGIN wf v=1 -->\n"
        "### Workflow 14: Management Directives\nGeneric template body.\n"
        "<!-- AGENTTEAMS:END wf -->\n"
    )
    merged, n, _notices = _additive_merge_block("wf", existing, new)
    # Same Workflow 14 → NOT treated as new; no duplicate spliced.
    assert n == 0
    assert merged.count("Workflow 14") == 1
    assert "reports/local.md" in merged  # enrichment intact
