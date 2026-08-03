"""test_front_matter_reconcile.py — the divergence report, and the apply path it gates.

Front matter cannot be fenced, so the merge keeps a deployed value when the file has been
edited and reports drift. That is the right default — an edit may be a project's choice — but
it means a capability fix stops silently at every edited file, with the only signal buried in a
long merge run.

`front_matter_reconcile` gives that a standing answer and an apply path that fires only on
explicit instruction.

**Measured before building:** this repository's own team has **zero** front-matter divergences,
so the apply path is exercised here by fixtures rather than by real data. Stated plainly rather
than left implied — machinery that has never run against a real instance should say so.
"""

from __future__ import annotations

from pathlib import Path

from agentteams.front_matter_reconcile import (
    CAPABILITY_KEYS,
    apply_divergences,
    find_divergences,
    format_report,
)

RENDERED = (
    "---\n"
    "name: Navigator\n"
    "description: d\n"
    "allowed-tools: Read, Grep, Glob, Bash(python -m agentteams.research:*)\n"
    "---\n\n"
    "## Body\n"
)
DEPLOYED = (
    "---\n"
    "name: Navigator\n"
    "description: d\n"
    "allowed-tools: Read, Grep, Glob\n"
    "---\n\n"
    "## Body\n\n"
    "## Project-Specific Notes\n\nMine.\n"
)


def _tree(tmp_path: Path, deployed: str = DEPLOYED) -> Path:
    out = tmp_path / "agents"
    out.mkdir()
    (out / "navigator.md").write_text(deployed, encoding="utf-8")
    return out


def test_a_diverging_capability_grant_is_reported(tmp_path: Path) -> None:
    out = _tree(tmp_path)
    found = find_divergences([("navigator.md", RENDERED)], out)
    assert len(found) == 1
    d = found[0]
    assert d.key == "allowed-tools"
    assert d.is_capability is True
    assert "Bash(python -m agentteams.research:*)" in d.template


def test_reporting_changes_nothing(tmp_path: Path) -> None:
    """The report is read-only. This is the property that makes it safe to run by habit."""
    out = _tree(tmp_path)
    before = (out / "navigator.md").read_text(encoding="utf-8")
    find_divergences([("navigator.md", RENDERED)], out)
    format_report(find_divergences([("navigator.md", RENDERED)], out))
    assert (out / "navigator.md").read_text(encoding="utf-8") == before


def test_apply_changes_only_the_diverging_key(tmp_path: Path) -> None:
    out = _tree(tmp_path)
    applied = apply_divergences(find_divergences([("navigator.md", RENDERED)], out), out)

    text = (out / "navigator.md").read_text(encoding="utf-8")
    assert "allowed-tools: Read, Grep, Glob, Bash(python -m agentteams.research:*)" in text
    assert "name: Navigator" in text and "description: d" in text
    assert "## Project-Specific Notes" in text and "Mine." in text, "body was touched"
    assert any("CAPABILITY GRANT CHANGED" in line for line in applied), (
        "a widened capability grant must be called out in the applied output, not merely counted"
    )


def test_a_key_the_template_does_not_declare_is_not_a_divergence(tmp_path: Path) -> None:
    """A project addition is not drift. Reporting it would train operators to ignore the report."""
    deployed = DEPLOYED.replace("description: d\n", "description: d\nproject-note: keep me\n")
    out = _tree(tmp_path, deployed)
    found = find_divergences([("navigator.md", RENDERED)], out)
    assert [d.key for d in found] == ["allowed-tools"]


def test_a_deployed_file_with_no_front_matter_is_skipped(tmp_path: Path) -> None:
    """The distinct sub-case from the ledger: a file cannot receive a block it never had.

    Adding one unasked would mean inventing a capability grant, which is precisely the decision
    this module refuses to make automatically.
    """
    out = _tree(tmp_path, "## Body only, no front matter\n")
    assert find_divergences([("navigator.md", RENDERED)], out) == []


def test_a_reconciled_team_says_so(tmp_path: Path) -> None:
    out = _tree(tmp_path, RENDERED)
    found = find_divergences([("navigator.md", RENDERED)], out)
    assert found == []
    assert "0 divergences" in format_report(found)


def test_the_report_never_implies_it_applied_anything(tmp_path: Path) -> None:
    out = _tree(tmp_path)
    report = format_report(find_divergences([("navigator.md", RENDERED)], out))
    assert "Nothing has been changed" in report
    assert "--reconcile-apply" in report, "the report must name the flag that would apply it"


def test_capability_keys_cover_both_framework_spellings() -> None:
    """copilot-vscode writes `tools:`, claude writes `allowed-tools:`. Both are grants."""
    assert {"tools", "allowed-tools"} <= CAPABILITY_KEYS


def test_this_repos_own_team_is_reconciled() -> None:
    """The measurement that shaped the plan: 0 divergences here, so apply is fixture-tested.

    If this ever fails, the apply path has real data to run against and the report should be
    read before anything else is done.
    """
    import sys
    import tempfile

    import pytest

    repo = Path(__file__).resolve().parents[1]
    brief = repo / ".github/agents/_build-description.json"
    agents = repo / ".claude/agents"
    if not brief.exists() or not agents.is_dir():
        pytest.skip("self brief or deployed team absent")

    sys.path.insert(0, str(repo / "tests"))
    from test_integration import _run_pipeline

    with tempfile.TemporaryDirectory() as td:
        rendered_dir = Path(td) / "r"
        _run_pipeline(brief, rendered_dir, framework="claude")
        rendered = [
            (p.relative_to(rendered_dir).as_posix(), p.read_text(encoding="utf-8"))
            for p in sorted(rendered_dir.rglob("*.md"))
        ]
    found = find_divergences(rendered, agents)
    assert found == [], "front matter has diverged:\n" + format_report(found)


# --------------------------------------------------------------------------------------
# End-to-end through the CLI: the gate is the point, so exercise the gate
# --------------------------------------------------------------------------------------


def _generate(tmp_path: Path):
    import sys

    import pytest

    repo = Path(__file__).resolve().parents[1]
    brief = repo / ".github/agents/_build-description.json"
    if not brief.exists():
        pytest.skip("self brief absent")
    sys.path.insert(0, str(repo / "tests"))
    from test_integration import _run_pipeline

    out = tmp_path / "agents"
    _run_pipeline(brief, out, framework="claude")
    return brief, out


def test_cli_report_never_writes_even_with_divergence(tmp_path: Path) -> None:
    """The property the whole design rests on: reporting is not applying.

    A capability grant is perturbed on disk, so there is something real to apply. The report
    must find it and still leave every byte alone.
    """
    import hashlib
    import subprocess
    import sys

    brief, out = _generate(tmp_path)
    target = out / "navigator.md"
    text = target.read_text(encoding="utf-8")
    perturbed = text.replace("allowed-tools: ", "allowed-tools: Bash, ", 1)
    assert perturbed != text, "fixture did not perturb anything"
    target.write_text(perturbed, encoding="utf-8")

    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob("*.md"))}

    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, "build_team.py", "--description", str(brief), "--framework", "claude",
         "--output", str(out), "--reconcile-front-matter", "--yes"],
        cwd=repo, capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "divergence" in proc.stdout
    assert "Nothing has been changed" in proc.stdout

    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob("*.md"))}
    assert after == before, "the report wrote to disk"


def test_cli_apply_changes_only_the_diverging_key(tmp_path: Path) -> None:
    """And with the explicit flag, it does apply — reporting the capability change."""
    import subprocess
    import sys

    brief, out = _generate(tmp_path)
    target = out / "navigator.md"
    original = target.read_text(encoding="utf-8")
    target.write_text(original.replace("allowed-tools: ", "allowed-tools: Bash, ", 1), encoding="utf-8")
    other = out / "security.md"
    other_before = other.read_text(encoding="utf-8")

    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, "build_team.py", "--description", str(brief), "--framework", "claude",
         "--output", str(out), "--reconcile-front-matter", "--reconcile-apply", "--yes"],
        cwd=repo, capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "CAPABILITY GRANT CHANGED" in proc.stdout, proc.stdout[-1500:]
    assert target.read_text(encoding="utf-8") == original, "did not restore the template value"
    assert other.read_text(encoding="utf-8") == other_before, "an unrelated file changed"
