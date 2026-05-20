"""Plan 3 — fenced-section shrink-notice tests (rules a/b/c)."""

from __future__ import annotations

from agentteams.emit import _detect_fence_shrink, _merge_fenced_content


def _fenced(sid: str, body: str) -> str:
    return (
        f"<!-- AGENTTEAMS:BEGIN {sid} v=1 -->\n"
        f"{body}"
        + ("" if body.endswith("\n") else "\n")
        + f"<!-- AGENTTEAMS:END {sid} -->\n"
    )


# ---------------- Rule (a): >50% byte shrink ----------------

def test_rule_a_byte_shrink_over_50pct_fires():
    existing = _fenced("auth", "This is a long, descriptive authority body. " * 8)
    new = _fenced("auth", "Generic placeholder.")
    notice = _detect_fence_shrink("auth", existing, new)
    assert notice is not None
    assert "shrank" in notice
    assert "auth" in notice


def test_rule_a_no_notice_when_body_shrinks_below_50pct_threshold():
    # New body is 60% of existing — under the 50% rule (a) trigger.
    existing_body = "x" * 100
    new_body = "y" * 60
    existing = _fenced("auth", existing_body)
    new = _fenced("auth", new_body)
    # Should not fire (a); rules (b)/(c) also clean (no list items, no paths).
    assert _detect_fence_shrink("auth", existing, new) is None


# ---------------- Rule (b): >=3 fewer markdown list items ----------------

def test_rule_b_list_item_loss_three_or_more_fires():
    existing = _fenced("sources", (
        "- alpha\n"
        "- beta\n"
        "- gamma\n"
        "- delta\n"
        "- epsilon\n"
    ))
    new = _fenced("sources", "- alpha\n- beta\n")  # lost 3 items
    notice = _detect_fence_shrink("sources", existing, new)
    assert notice is not None
    assert "list item" in notice


def test_rule_b_loss_of_two_items_does_not_fire():
    existing = _fenced("s", "- a\n- b\n- c\n- d\n")
    new = _fenced("s", "- a\n- b\n")  # lost 2 — below threshold
    # Length is 60% of existing → rule (a) also fails; rule (b) fails (2 < 3).
    assert _detect_fence_shrink("s", existing, new) is None


# ---------------- Rule (c): concrete paths / identifiers lost ----------------

def test_rule_c_lost_concrete_paths_fires():
    existing = _fenced("refs", (
        "Authoritative sources:\n"
        "- src/render.py\n"
        "- agentteams/emit.py\n"
        "- schemas/team-manifest.schema.json\n"
    ))
    new = _fenced("refs", "Authoritative sources: see project docs.\n")
    notice = _detect_fence_shrink("refs", existing, new)
    assert notice is not None
    assert "lost concrete refs" in notice
    # At least one of the original paths should appear in the notice body.
    assert any(p in notice for p in ("src/render.py", "agentteams/emit.py"))


def test_rule_c_backtick_identifier_loss_fires():
    existing = _fenced("api", "Symbols: `OrchestratorAgent`, `WorkstreamExpert`, `Reviewer`.\n")
    new = _fenced("api", "Symbols documented elsewhere.\n")
    notice = _detect_fence_shrink("api", existing, new)
    assert notice is not None
    assert "lost concrete refs" in notice


def test_no_shrink_when_content_grows():
    existing = _fenced("body", "Short.\n")
    new = _fenced("body", "Now much, much longer and richer content with more detail.\n")
    assert _detect_fence_shrink("body", existing, new) is None


# ---------------- Integration: merge_result carries shrink_notices ----------------

def test_merge_result_populates_shrink_notices():
    existing = (
        "---\nname: nav\n---\n"
        + _fenced("content", (
            "Authority sources:\n"
            "- examples/data-pipeline/brief.json\n"
            "- agentteams/render.py\n"
            "- schemas/eval-suite.schema.json\n"
        ))
    )
    # New render with the same fence id but generic body — should shrink.
    new = (
        "---\nname: nav\n---\n"
        + _fenced("content", "Authority sources: see project docs.\n")
    )
    mr = _merge_fenced_content(new, existing)
    assert mr.shrink_notices, "merge_result.shrink_notices should be populated"
    assert any("content" in n for n in mr.shrink_notices)
