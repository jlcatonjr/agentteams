"""baseAgent P5–P7: memory-index retrieval is read-only, finds its brief, and keeps every surface's copy in step.

* ``--query-index`` never writes: a stale index is reported, not rebuilt (it used to rewrite the tracked
  ``memory-index.json`` with or without ``--no-scan``), and the validation sidecar isn't stored.
* It finds the brief itself, so the recall skill's bare command and the fenced
  ``--description .agentteams/brief.json`` both work, and falls back to another surface's index.
* ``--refresh-index`` rebuilds every surface's copy from the same sources.
* Staleness notices a source added with an old mtime; ``memory_index_extra_files`` names single files.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams.memory_index_surfaces import coverage_notices, extra_files, index_surfaces, resolve_brief
from agentteams.memory_index import build_memory_index, index_staleness
from tests._fresh_intel import run_build

REPO = Path(__file__).resolve().parent.parent
BRIEF = REPO / "examples" / "software-project" / "brief.json"


def _tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()}


def _cli(project: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(REPO / "build_team.py"), *args], cwd=project,
                          capture_output=True, text=True, timeout=300)


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    """A project with a copilot and a claude team, each with its own index, and one work summary."""
    root = tmp_path_factory.mktemp("proj")
    (root / "brief.json").write_text(BRIEF.read_text(encoding="utf-8"), encoding="utf-8")
    (root / "workSummaries" / "daily").mkdir(parents=True)
    (root / "workSummaries" / "daily" / "2026-11-01.md").write_text(
        "# Daily\n\nThe security gate overwrite guidance was updated.\n", encoding="utf-8")
    for fw, out in (("copilot-vscode", ".github/agents"), ("claude", ".claude/agents")):
        proc = run_build(root / "brief.json", root, fw, root / out)
        assert proc.returncode == 0, proc.stdout[-800:] + proc.stderr[-800:]
    assert len(index_surfaces(root)) == 2
    return root


def test_bare_query_finds_the_brief_and_writes_nothing_on_a_stale_index(project: Path):
    note = project / "workSummaries" / "daily" / "2026-11-02.md"
    note.write_text("# Daily\n\nA new note about overwrite guidance.\n", encoding="utf-8")
    try:
        before = _tree(project)
        proc = _cli(project, "--query-index", "overwrite guidance", "--project", ".", "--yes")
        assert proc.returncode == 0, proc.stdout[-800:] + proc.stderr[-800:]
        assert "stale" in proc.stderr and "--refresh-index" in proc.stderr
        assert "Auto-refreshed" not in proc.stdout + proc.stderr
        after = _tree(project)
        changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
        assert not changed, f"--query-index wrote: {changed}"
    finally:
        note.unlink()


def test_fenced_query_with_a_missing_brief_path_falls_back(project: Path):
    proc = _cli(project, "--query-index", "overwrite guidance", "--query-k", "3", "--description",
                ".agentteams/brief.json", "--project", ".", "--output", ".github/agents", "--no-scan", "--yes")
    assert proc.returncode == 0, proc.stderr[-800:]
    assert ".agentteams/brief.json does not exist; using" in proc.stderr


def test_query_falls_back_to_another_surfaces_index(project: Path, tmp_path: Path):
    lone = tmp_path / "lone"
    (lone / ".claude" / "agents" / "references").mkdir(parents=True)
    (lone / "brief.json").write_text((project / "brief.json").read_text(encoding="utf-8"), encoding="utf-8")
    src = lone / "references" / "notes.md"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("# Notes\n\nOverwrite guidance lives here.\n", encoding="utf-8")
    index = build_memory_index([src], project_name="P", framework="claude", root=lone)
    (lone / ".claude" / "agents" / "references" / "memory-index.json").write_text(json.dumps(index), encoding="utf-8")
    proc = _cli(lone, "--query-index", "overwrite guidance", "--project", ".", "--no-scan", "--yes")
    assert proc.returncode == 0, proc.stderr[-800:]
    assert "No memory index at" in proc.stderr and ".claude/agents" in proc.stderr


def test_refresh_index_rebuilds_every_surface(project: Path):
    proc = _cli(project, "--refresh-index", "--project", ".", "--no-scan", "--yes")
    assert proc.returncode == 0, proc.stdout[-800:] + proc.stderr[-800:]
    indexes = [json.loads((d / "references" / "memory-index.json").read_text(encoding="utf-8"))
               for d in index_surfaces(project)]
    assert len({i["source_fingerprint"] for i in indexes}) == 1
    assert {i["framework"] for i in indexes} == {"copilot-vscode", "claude"}
    assert proc.stdout.count("Refreshed memory index") == 2


def test_a_source_added_with_an_old_mtime_is_stale(tmp_path: Path):
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text("# A\n\nalpha words here\n", encoding="utf-8")
    index = build_memory_index([a], root=tmp_path)
    b.write_text("# B\n\nbeta words here\n", encoding="utf-8")
    os.utime(b, (1_000_000_000, 1_000_000_000))  # older than the index: the mtime gate misses it
    assert index_staleness(index, [a, b], root=tmp_path) is None
    assert "1 source file(s); there are 2" in index_staleness(index, [a, b], root=tmp_path, complete_sources=True)


def test_extra_files_names_single_files_inside_the_project(tmp_path: Path):
    (tmp_path / "Projects" / "X").mkdir(parents=True)
    keep = tmp_path / "Projects" / "X" / "scope.md"
    keep.write_text("# Scope\n", encoding="utf-8")
    (tmp_path / "Projects" / "X" / "data.csv").write_text("a,b\n", encoding="utf-8")
    manifest = {"memory_index_extra_files": ["Projects/X/scope.md", "Projects/X/data.csv", "Projects/*/x.md",
                                             "/etc/hosts", "Projects/X", "missing.md"]}
    assert extra_files(manifest, tmp_path) == [keep]


def test_coverage_notices(tmp_path: Path):
    (tmp_path / "Projects" / "X").mkdir(parents=True)
    (tmp_path / "Projects" / "X" / "scope.md").write_text("# Scope\n", encoding="utf-8")
    (tmp_path / "notes").mkdir()
    (tmp_path / "READ.md").write_text("# r\n", encoding="utf-8")
    manifest = {"memory_index_extra_dirs": ["READ.md", "notes", "gone"]}
    notices = coverage_notices(manifest, tmp_path, [])
    assert any("'READ.md' is a file" in n and "memory_index_extra_files" in n for n in notices)
    assert any("'notes' indexes every Markdown file added there later" in n for n in notices)
    assert any("'gone' matches nothing" in n for n in notices)
    assert any("under Projects/ are not in the memory index" in n for n in notices)
    covered = coverage_notices({}, tmp_path, [tmp_path / "Projects" / "X" / "scope.md"])
    assert not any("Projects/" in n for n in covered)


def test_resolve_brief_prefers_the_rich_brief_and_rejects_foreign_ones(tmp_path: Path):
    assert resolve_brief(tmp_path) is None
    (tmp_path / ".github" / "agents").mkdir(parents=True)
    (tmp_path / ".github" / "agents" / "_build-description.json").write_text("{}", encoding="utf-8")
    (tmp_path / "brief.json").write_text("{}", encoding="utf-8")
    assert resolve_brief(tmp_path) == tmp_path / "brief.json"
    (tmp_path / "brief.json").write_text(json.dumps({"existing_project_path": "/elsewhere"}), encoding="utf-8")
    assert resolve_brief(tmp_path) is None  # it describes another project
    (tmp_path / "brief.json").unlink()
    (tmp_path / "brief.json").symlink_to(tmp_path / ".github" / "agents" / "_build-description.json")
    assert resolve_brief(tmp_path) is None


def test_refresh_never_swaps_in_an_unnamed_brief(project: Path):
    proc = _cli(project, "--refresh-index", "--description", "brieff.json", "--project", ".", "--no-scan", "--yes")
    assert proc.returncode != 0 and "does not exist; using" not in proc.stderr


def test_refresh_leaves_a_frozen_surface_alone(project: Path, tmp_path: Path):
    frozen = json.loads((project / "brief.json").read_text(encoding="utf-8"))
    frozen["frozen_surfaces"] = ["claude"]
    brief = tmp_path / "frozen.json"
    brief.write_text(json.dumps(frozen), encoding="utf-8")
    claude_index = project / ".claude" / "agents" / "references" / "memory-index.json"
    before = claude_index.read_bytes()
    proc = _cli(project, "--refresh-index", "--description", str(brief), "--project", ".", "--no-scan", "--yes")
    assert proc.returncode == 0, proc.stderr[-800:]
    assert claude_index.read_bytes() == before and proc.stdout.count("Refreshed memory index") == 1


def test_bare_refresh_creates_no_index_for_an_unused_provider(project: Path, tmp_path: Path):
    lone = tmp_path / "claude-only"
    (lone / ".claude" / "agents" / "references").mkdir(parents=True)
    (lone / "brief.json").write_text((project / "brief.json").read_text(encoding="utf-8"), encoding="utf-8")
    (lone / ".claude" / "agents" / "references" / "memory-index.json").write_text(
        json.dumps(build_memory_index([], framework="claude", root=lone)), encoding="utf-8")
    proc = _cli(lone, "--refresh-index", "--project", ".", "--no-scan", "--yes")
    assert proc.returncode == 0, proc.stderr[-800:]
    assert not (lone / ".github").exists()
