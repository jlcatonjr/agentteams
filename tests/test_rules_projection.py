"""baseAgent P1, P2, P8–P10: one project's rules reach every provider's entry file and the key agents' notes."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams import rules_projection as R
from agentteams.user_regions import carry_regions

_BANNER = "> ⚙️ **USER-EDITABLE** — banner\n"
_RULES = "### R-1 Quiet hours\n\nNo runs after 22:00.\n\n### R-2 Cooldown\n\nThe S-10 cooldown is deprecated.\n"


def _instr(rules: str = "", extra: str = "") -> str:
    return ("# Team\n<!-- AGENTTEAMS:BEGIN overview v=1 -->\no\n<!-- AGENTTEAMS:END overview -->\n\n"
            f"## Project-Specific Rules\n\n{_BANNER}" + (f"\n{rules}" if rules else "") + extra)


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "CLAUDE.md").write_text(_instr(_RULES), encoding="utf-8")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "copilot-instructions.md").write_text(_instr(), encoding="utf-8")
    (tmp_path / ".codex").mkdir()
    (tmp_path / "AGENTS.md").write_text(_instr(), encoding="utf-8")
    (tmp_path / ".goose").mkdir()
    (tmp_path / ".goosehints").write_text(
        "<!-- AGENTTEAMS-BRIDGE:BEGIN goose-bridge-hints v=1 -->\nh\n<!-- AGENTTEAMS-BRIDGE:END goose-bridge-hints -->\n",
        encoding="utf-8")
    return tmp_path


def test_source_is_the_first_file_with_operator_rules(root: Path):
    assert R.find_source(root) == (".claude/CLAUDE.md", _RULES)
    assert R.rules_body(_instr()) is None  # the banner alone is not a rule


def test_projects_into_every_provider_and_is_idempotent(root: Path):
    report = R.project(root)
    assert report.source == ".claude/CLAUDE.md"
    assert {o.path: o.status for o in report.outcomes} == {
        ".github/copilot-instructions.md": "added", "AGENTS.md": "added", ".goosehints": "added"}
    for rel in (".github/copilot-instructions.md", "AGENTS.md", ".goosehints"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "No runs after 22:00." in text and len(R._RULES_HEADING_RE.findall(text)) == 1
    assert "goose-bridge-hints" in (root / ".goosehints").read_text(encoding="utf-8")
    again = R.project(root)
    assert {o.status for o in again.outcomes} == {"current"} and not again.drift


def test_source_change_updates_the_copies_and_keeps_target_local_rules(root: Path):
    R.project(root)
    copilot = root / ".github" / "copilot-instructions.md"
    copilot.write_text(copilot.read_text(encoding="utf-8") + "\n- a Copilot-only rule\n", encoding="utf-8")
    src = root / ".claude" / "CLAUDE.md"
    src.write_text(src.read_text(encoding="utf-8").replace("22:00", "23:00"), encoding="utf-8")
    assert {o.path for o in R.project(root, write=False).drift} == {".github/copilot-instructions.md", "AGENTS.md",
                                                                      ".goosehints"}
    R.project(root)
    text = copilot.read_text(encoding="utf-8")
    assert "23:00" in text and "22:00" not in text and "a Copilot-only rule" in text


def test_a_hand_edited_copy_is_refused_not_overwritten(root: Path):
    R.project(root)
    agents = root / "AGENTS.md"
    edited = agents.read_text(encoding="utf-8").replace("No runs after 22:00.", "No runs ever.")
    agents.write_text(edited, encoding="utf-8")
    outcome = next(o for o in R.project(root).outcomes if o.path == "AGENTS.md")
    assert outcome.status == "refused" and "by hand" in outcome.detail
    assert agents.read_text(encoding="utf-8") == edited


def test_a_duplicate_rules_region_is_refused(root: Path):
    copilot = root / ".github" / "copilot-instructions.md"
    copilot.write_text(_instr() + "\n## Project-Specific Rules\n\nagain\n", encoding="utf-8")
    outcome = next(o for o in R.project(root).outcomes if o.path == ".github/copilot-instructions.md")
    assert outcome.status == "refused" and "2" in outcome.detail


def test_codex_gets_the_override_and_a_reference_past_its_budget(root: Path):
    big = "".join(f"### R-{n} Rule {n}\n\n{'x' * 400}\n\n" for n in range(100))
    (root / ".claude" / "CLAUDE.md").write_text(_instr(big), encoding="utf-8")
    (root / "AGENTS.override.md").write_text("# Override\n", encoding="utf-8")
    report = R.project(root)
    paths = {o.path: o for o in report.outcomes}
    assert "AGENTS.md" not in paths and "AGENTS.override.md" in paths
    assert "reference" in paths["AGENTS.override.md"].detail
    text = (root / "AGENTS.override.md").read_text(encoding="utf-8")
    assert len(text.encode()) < R.CODEX_BUDGET_BYTES and "- R-99 Rule 99" in text and "x" * 400 not in text
    assert "x" * 400 in (root / ".goosehints").read_text(encoding="utf-8")  # Goose has no such budget


def test_frozen_surfaces_are_left_alone(root: Path):
    report = R.project(root, frozen=["codex", "goose"])
    assert {o.path for o in report.outcomes if o.status != "skipped"} == {".github/copilot-instructions.md"}
    assert {o.path for o in report.outcomes if o.status == "skipped"} == {"AGENTS.md", ".goosehints"}
    (root / "AGENTS.override.md").write_text("# O\n", encoding="utf-8")
    assert "AGENTS.override.md" not in {o.path for o in R.project(root, frozen=["codex"]).outcomes
                                        if o.status != "skipped"}


def test_the_projected_region_still_carries_across_overwrite(root: Path):
    R.project(root)
    projected = (root / ".github" / "copilot-instructions.md").read_text(encoding="utf-8")
    content, carried, problems = carry_regions(projected, _instr())
    assert not problems and carried and "No runs after 22:00." in content


def test_configured_source_and_no_source(root: Path, tmp_path_factory):
    assert R.project(root, configured=".github/copilot-instructions.md").source is None  # it holds no rules
    empty = tmp_path_factory.mktemp("empty")
    assert R.project(empty).source is None


def test_pointers_reach_markdown_and_goose_notes(root: Path):
    notes = "\n## Project-Specific Notes\n\n> ⚙️ **USER-EDITABLE** — notes\n"
    (root / ".claude" / "agents").mkdir()
    (root / ".claude" / "agents" / "security.md").write_text("---\nname: s\n---\n# S\n" + notes, encoding="utf-8")
    (root / ".github" / "agents").mkdir()
    (root / ".github" / "agents" / "orchestrator.agent.md").write_text("---\nname: o\n---\n# O\n" + notes +
                                                                       "\n- mine\n", encoding="utf-8")
    (root / ".goose" / "recipes").mkdir()
    (root / ".goose" / "recipes" / "security.yaml").write_text(
        'version: "1.0.0"\ntitle: s\ninstructions: |\n  # S\n\n  ## Project-Specific Notes\n\n  - mine\nprompt: go\n',
        encoding="utf-8")
    (root / ".codex" / "agents").mkdir()
    (root / ".codex" / "agents" / "security.toml").write_text('name = "s"\n', encoding="utf-8")
    outcomes = {o.path: o.status for o in R.sync_pointers(root)}
    assert outcomes == {".claude/agents/security.md": "added", ".goose/recipes/security.yaml": "added",
                        ".github/agents/orchestrator.agent.md": "added", ".codex/agents/security.toml": "skipped"}
    import yaml
    recipe = yaml.safe_load((root / ".goose" / "recipes" / "security.yaml").read_text(encoding="utf-8"))
    assert "never weaken its Invariant Core" in recipe["instructions"] and recipe["prompt"] == "go"
    assert "R-1 Quiet hours" not in recipe["instructions"]  # titles are not copied into @security's notes
    orch = (root / ".github" / "agents" / "orchestrator.agent.md").read_text(encoding="utf-8")
    assert "- mine" in orch and orch.index("PROJECT-RULES-POINTER:BEGIN") < orch.index("- mine")
    assert {o.status for o in R.sync_pointers(root)} == {"current", "skipped"}


def test_update_projects_check_gates_and_sync_repairs(tmp_path: Path):
    """End to end: a real copilot + claude project; rules written in .claude/CLAUDE.md reach Copilot."""
    import subprocess
    import sys

    from tests._fresh_intel import run_build

    repo = Path(__file__).resolve().parent.parent
    brief = tmp_path / "brief.json"
    brief.write_text((repo / "examples" / "software-project" / "brief.json").read_text(encoding="utf-8"))
    for fw, out in (("claude", ".claude/agents"), ("copilot-vscode", ".github/agents")):
        assert run_build(brief, tmp_path, fw, tmp_path / out).returncode == 0
    claude_md = next(p for p in (tmp_path / ".claude" / "CLAUDE.md", tmp_path / "CLAUDE.md") if p.is_file())
    claude_md.write_text(claude_md.read_text(encoding="utf-8").rstrip("\n") + "\n\n" + _RULES, encoding="utf-8")

    proc = run_build(brief, tmp_path, "copilot-vscode", tmp_path / ".github" / "agents", "--update", "--merge",
                     "--no-backup")
    assert proc.returncode == 0, proc.stdout[-800:] + proc.stderr[-800:]
    copilot = tmp_path / ".github" / "copilot-instructions.md"
    assert "No runs after 22:00." in copilot.read_text(encoding="utf-8")
    security = (tmp_path / ".github" / "agents" / "security.agent.md").read_text(encoding="utf-8")
    assert "PROJECT-RULES-POINTER:BEGIN" in security and "`.claude/CLAUDE.md`" in security

    check = run_build(brief, tmp_path, "copilot-vscode", tmp_path / ".github" / "agents", "--check")
    assert "Project rules from" not in check.stderr, check.stderr[-1500:]
    claude_md.write_text(claude_md.read_text(encoding="utf-8").replace("22:00", "23:00"), encoding="utf-8")
    check = run_build(brief, tmp_path, "copilot-vscode", tmp_path / ".github" / "agents", "--check")
    assert check.returncode == 1 and "copilot-instructions.md out of date" in check.stderr

    def sync(*extra: str, cwd: Path = tmp_path) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(repo / "build_team.py"), "--sync-project-rules", *extra],
                              cwd=cwd, capture_output=True, text=True, timeout=300)

    before = copilot.read_bytes()
    dry = sync("--dry-run")
    assert dry.returncode == 1 and "out of date" in dry.stderr and copilot.read_bytes() == before
    assert sync("--project", str(tmp_path), cwd=tmp_path.parent).returncode == 1  # another directory: refused
    assert copilot.read_bytes() == before

    # A rules-only edit changes no fingerprint: the converged --update still projects it, and shows the change.
    update = run_build(brief, tmp_path, "copilot-vscode", tmp_path / ".github" / "agents", "--update", "--merge",
                       "--no-backup")
    assert update.returncode == 0, update.stderr[-800:]
    assert "23:00" in copilot.read_text(encoding="utf-8") and "+No runs after 23:00." in update.stdout
    assert sync().returncode == 0


def test_check_treats_user_regions_as_operator_text_not_drift(tmp_path: Path):
    """A projected block or an operator's notes are not drift; an edit inside a fence still is.

    ``--check``'s manifest-promotion comparison was render-vs-disk, so any operator text in a user region failed
    it whenever the fingerprint moved (the live intel moves it daily); projection would have made every team with
    rules fail. The comparison now carries the disk file's user regions first, as ``--update`` keeps them.
    """
    import re

    from tests._fresh_intel import run_build

    repo = Path(__file__).resolve().parent.parent
    brief = tmp_path / "brief.json"
    brief.write_text((repo / "examples" / "software-project" / "brief.json").read_text(encoding="utf-8"))
    out = tmp_path / ".github" / "agents"
    for fw, agents in (("claude", tmp_path / ".claude" / "agents"), ("copilot-vscode", out)):
        assert run_build(brief, tmp_path, fw, agents).returncode == 0
    claude_md = tmp_path / ".claude" / "CLAUDE.md"
    claude_md.write_text(claude_md.read_text(encoding="utf-8").rstrip("\n") + "\n\n" + _RULES, encoding="utf-8")
    assert run_build(brief, tmp_path, "copilot-vscode", out, "--update", "--merge", "--no-backup").returncode == 0
    nav = out / "navigator.agent.md"
    nav.write_text(nav.read_text(encoding="utf-8").replace("## Project-Specific Notes\n",
                                                           "## Project-Specific Notes\n\n- operator note\n", 1),
                   encoding="utf-8")
    check = run_build(brief, tmp_path, "copilot-vscode", out, "--check", seed=False)
    assert check.returncode == 0, check.stdout[-1500:] + check.stderr[-1500:]
    nav.write_text(nav.read_text(encoding="utf-8").replace("<!-- AGENTTEAMS:END", "tampered\n<!-- AGENTTEAMS:END", 1),
                   encoding="utf-8")
    check = run_build(brief, tmp_path, "copilot-vscode", out, "--check", seed=False)
    assert check.returncode == 1 and "navigator.agent.md" in re.findall(r"^\s+~ (\S+)", check.stdout, re.M)



# --- adversarial review, 2026-11-02 -------------------------------------------------------------------


def test_configured_source_must_be_a_candidate(root: Path):
    for bad in ("/etc/passwd", "../other/CLAUDE.md", "docs/rules.md"):
        with pytest.raises(ValueError, match="must be one of"):
            R.project(root, configured=bad)


def test_a_second_file_with_rules_is_ambiguous_until_named(root: Path):
    (root / "AGENTS.md").write_text(_instr("- an AGENTS.md rule\n"), encoding="utf-8")
    with pytest.raises(ValueError, match="each hold rules of their own"):
        R.project(root)
    assert R.project(root, configured=".claude/CLAUDE.md").source == ".claude/CLAUDE.md"


def test_the_source_never_moves_silently(root: Path):
    R.project(root)  # copies record .claude/CLAUDE.md
    (root / ".claude" / "CLAUDE.md").write_text(_instr(), encoding="utf-8")  # its rules removed
    copilot = root / ".github" / "copilot-instructions.md"
    copilot.write_text(copilot.read_text(encoding="utf-8") + "\n- planted by a Copilot agent\n", encoding="utf-8")
    with pytest.raises(ValueError, match="holds no rules now"):
        R.project(root)


def test_crlf_and_marker_text_are_refused(root: Path):
    agents = root / "AGENTS.md"
    agents.write_bytes(agents.read_bytes().replace(b"\n", b"\r\n"))
    outcome = next(o for o in R.project(root).outcomes if o.path == "AGENTS.md")
    assert outcome.status == "refused" and "CRLF" in outcome.detail
    (root / ".claude" / "CLAUDE.md").write_text(_instr(_RULES + "<!-- PROJECTED-RULES:END -->\n"), encoding="utf-8")
    with pytest.raises(ValueError, match="projection marker"):
        R.project(root)


def test_an_updated_copy_carries_the_rules_diff(root: Path):
    R.project(root)
    src = root / ".claude" / "CLAUDE.md"
    src.write_text(src.read_text(encoding="utf-8").replace("22:00", "23:00"), encoding="utf-8")
    outcome = next(o for o in R.project(root).outcomes if o.path == "AGENTS.md")
    assert "-No runs after 22:00." in outcome.diff and "+No runs after 23:00." in outcome.diff


def test_the_source_region_stops_at_the_next_section():
    text = _instr(_RULES) + "\n## Code Style\n\n- not a rule\n"
    body = R.rules_body(text)
    assert "No runs after 22:00." in body and "not a rule" not in body


def test_titles_are_labels_not_instructions():
    titles = R.table_of_contents("### `run <!-- this --> now`\n\n### " + "x" * 300 + "\n")
    assert titles[0] == "run  this  now" and len(titles[1]) == 100


def test_a_projected_only_region_does_not_block_overwrite(root: Path):
    R.project(root)
    hints = (root / ".goosehints").read_text(encoding="utf-8")
    fresh = "<!-- AGENTTEAMS:BEGIN goose_operational_notes v=1 -->\nn\n<!-- AGENTTEAMS:END goose_operational_notes -->\n"
    _content, _carried, problems = carry_regions(hints, fresh)
    assert not problems
    _content, _carried, problems = carry_regions(hints + "\n- an operator's own goose rule\n", fresh)
    assert problems  # operator text still can't be dropped
