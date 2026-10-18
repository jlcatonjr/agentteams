"""test_graph_after_merge.py — a merge-mode --update redraws the pipeline graph from the files it wrote (1.0.x).

Found by baseAgent: the graph rendered before the write overlaid freshly rendered agents on the disk copies, but the
merge kept the on-disk front matter (adopted agents in the orchestrator's ``agents:``). The written graph therefore
described files that were never written: 13 adopted agents' edges dropped. ``--refresh-graph`` (which reads disk) was
right. ``git_hooks.redraw_graph_after_merge`` now redraws from disk after the write, before the build log.
"""

from __future__ import annotations

from pathlib import Path

from agentteams import git_hooks

_ORCH = "---\nname: orchestrator\ndescription: o\nagents:\n  - producer\n  - adopted-one\n---\n# Orchestrator\n"
_AGENT = "---\nname: {n}\ndescription: {n}\n---\n# {n}\n"


def _team(tmp_path: Path) -> Path:
    agents = tmp_path / ".github" / "agents"
    (agents / "references").mkdir(parents=True)
    (agents / "orchestrator.agent.md").write_text(_ORCH, encoding="utf-8")
    for n in ("producer", "adopted-one"):
        (agents / f"{n}.agent.md").write_text(_AGENT.format(n=n), encoding="utf-8")
    # A stale graph, as the pre-write render would have left it: no edge to the adopted agent.
    (agents / "references" / "pipeline-graph.md").write_text("# stale\n", encoding="utf-8")
    return agents


def test_graph_is_redrawn_from_the_written_files(tmp_path):
    agents = _team(tmp_path)
    git_hooks.redraw_graph_after_merge(agents)
    graph = (agents / "references" / "pipeline-graph.md").read_text(encoding="utf-8")
    assert "adopted-one" in graph and "stale" not in graph
    assert (agents / "references" / "pipeline-graph.svg").exists()


def test_redraw_is_idempotent(tmp_path):
    agents = _team(tmp_path)
    git_hooks.redraw_graph_after_merge(agents)
    first = (agents / "references" / "pipeline-graph.md").read_bytes()
    git_hooks.redraw_graph_after_merge(agents)
    assert (agents / "references" / "pipeline-graph.md").read_bytes() == first


def test_generate_calls_the_redraw_only_for_a_real_merge():
    src = (Path(git_hooks.__file__).parent / "cli" / "generate.py").read_text(encoding="utf-8")
    line = next(l for l in src.splitlines() if "redraw_graph_after_merge(output_dir)" in l)
    assert "not args.dry_run" in line and "not args.overwrite" in line and "result.success" in line
