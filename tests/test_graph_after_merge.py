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


def _run(argv: list[str]) -> int:
    import build_team

    return build_team.main(argv)


def test_real_update_merge_draws_the_adopted_agent_and_logs_the_drawn_graph(tmp_path, monkeypatch):
    """Behavioural (code-hygiene condition 5): a copilot team whose orchestrator roster lists a bespoke agent, as a
    merge adoption leaves it; a real --update --merge must write a graph that includes it, and the build log must
    record the hash of the graph actually on disk."""
    import hashlib
    import json
    import re

    brief = Path(git_hooks.__file__).parent.parent / "examples" / "data-pipeline" / "brief.json"
    if not brief.exists():
        import pytest
        pytest.skip("data-pipeline brief not found")
    from tests.test_adopt_only import _WAIVER_KEY, _seed_overwrite_pass, _seed_waiver

    monkeypatch.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", _WAIVER_KEY)   # offline intel: a signed freshness waiver
    out = tmp_path / "proj" / ".github" / "agents"
    _seed_waiver(out)
    base = ["--description", str(brief), "--framework", "copilot-vscode", "--output", str(out),
            "--yes", "--no-scan", "--security-offline"]
    assert _run(base) == 0
    _seed_overwrite_pass(out, monkeypatch)                              # an --update needs an overwrite clearance
    (out / "zeta-bespoke.agent.md").write_text(
        "---\nname: zeta-bespoke\ndescription: \"Bespoke.\"\n---\n\nBody.\n", encoding="utf-8")
    orch = out / "orchestrator.agent.md"
    text = orch.read_text(encoding="utf-8")
    text, n = re.subn(r"(?m)^(agents:\n)", r"\1  - zeta-bespoke\n", text, count=1)
    assert n == 1, "orchestrator has no agents: block list"
    orch.write_text(text, encoding="utf-8")
    rc = _run([*base, "--update", "--merge"])
    assert rc == 0
    graph = (out / "references" / "pipeline-graph.md").read_text(encoding="utf-8")
    # The edge, not just the node: the file alone makes a node; only the written roster makes the edge.
    assert "orchestrator -.-> zeta_bespoke" in graph and '"orchestrator" -> "zeta-bespoke"' in graph
    log = json.loads((out / "references" / "build-log.json").read_text(encoding="utf-8"))
    recorded = log["file_hashes"].get("references/pipeline-graph.md")
    disk = hashlib.sha256((out / "references" / "pipeline-graph.md").read_bytes()).hexdigest()[:16]
    assert recorded == disk


def test_a_claude_team_redraw_is_a_no_op(tmp_path):
    agents = tmp_path / ".claude" / "agents"
    (agents / "references").mkdir(parents=True)
    (agents / "orchestrator.md").write_text(_ORCH, encoding="utf-8")
    (agents / "references" / "pipeline-graph.md").write_text("# kept\n", encoding="utf-8")
    git_hooks.redraw_graph_after_merge(agents)   # load_from_disk reads *.agent.md only: nothing to draw
    assert (agents / "references" / "pipeline-graph.md").read_text(encoding="utf-8") == "# kept\n"


def test_generate_calls_the_redraw_only_for_a_real_merge():
    src = (Path(git_hooks.__file__).parent / "cli" / "generate.py").read_text(encoding="utf-8")
    lines = src.splitlines()
    i = next(k for k, l in enumerate(lines) if "redraw_graph_after_merge(output_dir)" in l)
    guard = lines[i - 1]
    assert "not args.dry_run" in guard and "not args.overwrite" in guard and "result.success" in guard
