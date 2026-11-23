"""F10 (IM findings #13/#14): a merge names unfenced lines left by an older render that point at another surface.

The residue below is the shape reported from a downstream Codex team rendered by agentteams 1.2.1 (data): the fenced
conflict-log section is correct, while unfenced lines written by an earlier render still name the Copilot team's log.
A merge keeps unfenced text, so the notice is the only signal. It is advisory: the merged file is unchanged by it.
"""

from __future__ import annotations

from pathlib import Path

from agentteams import emit, stale_surface_paths as ssp

FENCE = """<!-- AGENTTEAMS:BEGIN conflict_log_format v=1 -->
### Conflict Log Format

Rows for `.codex/agents/references/conflict-log.csv` use the columns
`date,category,code,severity,file,description,status,resolution`. You are read-only.
<!-- AGENTTEAMS:END conflict_log_format -->
"""

EXISTING = f'''name = "conflict-auditor"
developer_instructions = \'\'\'
# Conflict Auditor

For each conflict in `.github/agents/references/conflict-log.csv` with status `open`:

{FENCE}
### Conflict Log Format

Append to `.github/agents/references/conflict-log.csv` with columns:
`date,category,code,severity,file,description,status,resolution`

CI lives in `.github/workflows/ci.yml`.

## Project-Specific Notes

We mirror decisions into `.github/agents/references/conflict-log.csv` by hand.
\'\'\'
'''

FRESH = f'''name = "conflict-auditor"
developer_instructions = \'\'\'
# Conflict Auditor

For each conflict in `.codex/agents/references/conflict-log.csv` with status `open`:

{FENCE}
CI lives in `.github/workflows/ci.yml`.

## Project-Specific Notes
\'\'\'
'''


def test_surface_dirs_come_from_the_registry():
    dirs = ssp.surface_dirs()
    assert {".github/agents", ".claude/agents", ".codex/agents", ".goose/recipes"} <= set(dirs)


def test_own_surface_matches_the_output_dir_tail(tmp_path):
    assert ssp.own_surface(tmp_path / ".codex" / "agents") == ".codex/agents"
    assert ssp.own_surface(tmp_path / ".goose" / "recipes") == ".goose/recipes"
    assert ssp.own_surface(tmp_path / "agents") is None


def test_residue_lines_are_found_and_nothing_else():
    found = ssp.stale_lines(EXISTING, FRESH, ".codex/agents")
    texts = [line for _n, _d, line in found]
    assert len(found) == 2, found
    assert texts[0].startswith("For each conflict in `.github/agents/")
    assert texts[1].startswith("Append to `.github/agents/")
    # Not flagged: the fenced text, a .github/workflows path (not an agents dir) and the user's own Notes region.
    assert all(d == ".github/agents" for _n, d, _l in found)


def test_no_notice_without_residue_or_without_a_known_surface():
    assert ssp.notice("x.toml", FRESH, FRESH, ".codex/agents") is None
    assert ssp.notice("x.toml", EXISTING, FRESH, None) is None
    # A file on the Copilot surface may name its own dir.
    assert ssp.notice("x.agent.md", EXISTING, FRESH, ".github/agents") is None


def test_a_merge_reports_the_residue_and_still_keeps_it(tmp_path):
    out = tmp_path / ".codex" / "agents"
    out.mkdir(parents=True)
    (out / "conflict-auditor.toml").write_text(EXISTING, encoding="utf-8")
    result = emit.emit_all([("conflict-auditor.toml", FRESH)], output_dir=out, merge=True, yes=True)
    stale = [n for n in result.notices if "another surface's agents directory" in n]
    assert len(stale) == 1 and "2 unfenced line(s)" in stale[0] and "--update --overwrite" in stale[0]
    # Advisory only: a merge keeps unfenced text, residue included.
    assert "Append to `.github/agents/references/conflict-log.csv`" in (out / "conflict-auditor.toml").read_text()


def test_a_dry_run_merge_reports_it_too(tmp_path):
    out = tmp_path / ".codex" / "agents"
    out.mkdir(parents=True)
    (out / "conflict-auditor.toml").write_text(EXISTING, encoding="utf-8")
    result = emit.emit_all([("conflict-auditor.toml", FRESH)], output_dir=out, merge=True, yes=True, dry_run=True)
    assert any("another surface's agents directory" in n for n in result.notices)
    assert (out / "conflict-auditor.toml").read_text() == EXISTING


def test_quoting_is_capped(tmp_path):
    many = EXISTING.replace("# Conflict Auditor\n", "# Conflict Auditor\n" + "".join(
        f"See `.claude/agents/x{i}.md`.\n" for i in range(5)))
    text = ssp.notice("x.toml", many, FRESH, ".codex/agents")
    assert text and "7 unfenced line(s)" in text and "(+4 more)" in text
