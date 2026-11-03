"""baseAgent P4: operator notes must not sit past Copilot's 30,000-character agent-body limit.

Fresh Copilot renders place ``## Project-Specific Notes`` before the first fence; ``--check`` fails when a notes
region holding operator text starts past the limit, and only reports a template-only overrun.
"""

from __future__ import annotations

from pathlib import Path

from agentteams.copilot_body_limit import body_limit_findings
from agentteams.project_notes import _ensure_project_notes_section
from agentteams.user_regions import find_region
from tests._fresh_intel import run_build

BRIEF = Path(__file__).resolve().parent.parent / "examples" / "software-project" / "brief.json"
_FM = "---\nname: O\ndescription: d\n---\n"
_FENCE = "<!-- AGENTTEAMS:BEGIN invariant_core v=1 -->\n{body}\n<!-- AGENTTEAMS:END invariant_core -->\n"
_NOTES = "## Project-Specific Notes\n\n> ⚙️ **USER-EDITABLE** — banner\n"


def _agent(fence_body: str, notes_first: bool, operator_text: str = "") -> str:
    notes = _NOTES + operator_text
    fence = _FENCE.format(body=fence_body)
    return _FM + "# O\n\n" + (notes + "\n" + fence if notes_first else fence + "\n" + notes)


def test_fresh_copilot_render_puts_notes_before_the_first_fence():
    fresh = _FM + "# O\n\nintro\n\n" + _FENCE.format(body="x" * 40_000)
    out = _ensure_project_notes_section("orchestrator.agent.md", fresh)
    notes_at, fence_at = out.index("## Project-Specific Notes"), out.index("<!-- AGENTTEAMS:BEGIN")
    assert out.index("intro") < notes_at < fence_at
    start, end, _ = find_region(out, "## Project-Specific Notes")
    assert end == fence_at and "intro" not in out[start:end]  # no template text absorbed
    assert _ensure_project_notes_section("orchestrator.agent.md", out) == out


def test_a_whole_body_content_fence_keeps_the_notes_last():
    # The legacy whole-body wrapper holds the title too: notes before it would precede the title.
    fresh = _FM + "<!-- AGENTTEAMS:BEGIN content v=1 -->\n# O\n\nbody\n<!-- AGENTTEAMS:END content -->\n"
    out = _ensure_project_notes_section("a.agent.md", fresh)
    assert out.index("<!-- AGENTTEAMS:END content -->") < out.index("## Project-Specific Notes")


def test_other_frameworks_still_append_the_notes():
    fresh = _FM + "# O\n" + _FENCE.format(body="x")
    assert _ensure_project_notes_section("orchestrator.md", fresh).rstrip().endswith("preserved verbatim across "
                                                                                  "`agentteams --update --merge`.")


def test_check_fails_only_on_operator_notes_past_the_limit(tmp_path):
    big = "x" * 31_000
    (tmp_path / "late.agent.md").write_text(_agent(big, notes_first=False, operator_text="\n- my rule\n"))
    (tmp_path / "late-empty.agent.md").write_text(_agent(big, notes_first=False))
    (tmp_path / "early.agent.md").write_text(_agent(big, notes_first=True, operator_text="\n- my rule\n"))
    (tmp_path / "small.agent.md").write_text(_agent("x", notes_first=False, operator_text="\n- my rule\n"))
    failures, advisories = body_limit_findings(tmp_path)
    assert [f.split(":")[0] for f in failures] == ["late.agent.md"]
    assert sorted(a.split(":")[0] for a in advisories) == ["early.agent.md", "late-empty.agent.md"]


def test_operator_blockquote_counts_as_operator_text(tmp_path):
    big = "x" * 31_000
    (tmp_path / "quote.agent.md").write_text(_agent(big, notes_first=False, operator_text="\n> my quoted rule\n"))
    failures, _ = body_limit_findings(tmp_path)
    assert [f.split(":")[0] for f in failures] == ["quote.agent.md"]


def test_notes_go_first_when_the_body_opens_with_a_fence():
    fresh = _FM + _FENCE.format(body="x")
    out = _ensure_project_notes_section("a.agent.md", fresh)
    assert out.startswith(_FM + "## Project-Specific Notes") and out.endswith(_FENCE.format(body="x"))


def test_check_wiring_end_to_end(tmp_path):
    brief_path = tmp_path / "brief.json"
    brief_path.write_text(BRIEF.read_text(encoding="utf-8"))
    out = tmp_path / ".github" / "agents"

    def run(*extra: str):
        return run_build(brief_path, tmp_path, "copilot-vscode", out, *extra)

    assert run().returncode == 0
    orch = out / "orchestrator.agent.md"
    text = orch.read_text(encoding="utf-8")
    assert len(text) > 30_000 and text.index("## Project-Specific Notes") < 30_000
    proc = run("--check")
    assert "Copilot body limit FAILURES" not in proc.stderr, proc.stderr[-2000:]
    # Simulate a team rendered before P4: its notes, with operator text, at the end of the body.
    start, end, _ = find_region(text, "## Project-Specific Notes")
    moved = text[:start] + text[end:].rstrip("\n") + "\n\n" + text[start:end].rstrip("\n") + "\n- my rule\n"
    orch.write_text(moved, encoding="utf-8")
    proc = run("--check")
    assert proc.returncode == 1 and "orchestrator.agent.md: its Project-Specific Notes start" in proc.stderr
