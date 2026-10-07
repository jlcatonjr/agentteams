"""OI-21: build-log.json never records an absolute path (build logs are committed; mathAgents request 2026-09-30)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from agentteams.cli.artifacts import _baseline_key, _compute_front_matter_baseline

REPO = Path(__file__).resolve().parent.parent


def test_files_inside_and_outside_the_agents_dir_get_relative_keys(tmp_path):
    agents = tmp_path / ".claude" / "agents"
    assert _baseline_key(agents / "orchestrator.md", agents) == "orchestrator.md"
    assert _baseline_key(tmp_path / ".claude" / "skills" / "tool-x" / "SKILL.md", agents) == \
        "../skills/tool-x/SKILL.md"


def test_the_baseline_has_no_absolute_key(tmp_path):
    agents = tmp_path / ".claude" / "agents"
    skill = tmp_path / ".claude" / "skills" / "tool-x" / "SKILL.md"
    for path in (agents / "a.md", skill):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nname: x\n---\n# X\n")
    baseline = _compute_front_matter_baseline([str(agents / "a.md"), str(skill)], agents)
    assert set(baseline) == {"a.md", "../skills/tool-x/SKILL.md"}


def _absolute_strings(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k.startswith("/"):
                yield k
            yield from _absolute_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _absolute_strings(v)
    elif isinstance(obj, str) and obj.startswith("/"):
        yield obj


def test_a_claude_render_with_skills_writes_no_absolute_path(tmp_path):
    out = tmp_path / ".claude" / "agents"
    proc = subprocess.run([sys.executable, str(REPO / "build_team.py"), "--description",
                           str(REPO / "examples" / "data-pipeline" / "brief.json"), "--project", str(tmp_path),
                           "--framework", "claude", "--output", str(out), "--no-scan", "--yes"],
                          cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout[-800:] + proc.stderr[-800:]
    assert (tmp_path / ".claude" / "skills").is_dir()            # the case that leaked: skills exist
    log = json.loads((out / "references" / "build-log.json").read_text())
    assert any(k.startswith("../skills/") for k in log["front_matter_baseline"])
    assert list(_absolute_strings(log)) == []
    assert str(tmp_path) not in json.dumps(log)


def test_files_written_is_relative_for_a_non_default_output_depth(tmp_path):
    out = tmp_path / "agents-out"                 # one level deep: project_root guess is tmp_path.parent
    proc = subprocess.run([sys.executable, str(REPO / "build_team.py"), "--description",
                           str(REPO / "examples" / "data-pipeline" / "brief.json"), "--project", str(tmp_path),
                           "--framework", "claude", "--output", str(out), "--no-scan", "--yes"],
                          cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout[-800:] + proc.stderr[-800:]
    log = json.loads((out / "references" / "build-log.json").read_text())
    assert log["files_written"] and list(_absolute_strings(log)) == []


def test_a_skill_capability_key_stays_proposal_only_with_a_baseline():
    # The skill key now matches emit's rel_path, so skills have a baseline. A template widening a capability
    # key on an unedited skill must still be a proposal, never applied.
    from agentteams.front_matter_merge import _merge_front_matter
    on_disk = "---\nname: tool-x\nargument-hint: old\nallowed-tools: Read\n---\n# X\n"
    rendered = "---\nname: tool-x\nargument-hint: new\nallowed-tools: Read, Bash\n---\n# X\n"
    baseline = {"name": "tool-x", "argument-hint": "old", "allowed-tools": "Read"}
    keys, applied, proposals = _merge_front_matter(rendered, on_disk, baseline)
    assert keys["allowed-tools"] == "Read" and proposals
    assert keys["argument-hint"] == "new" and applied   # unedited metadata follows the template
    # (name/description are drift-exempt and never auto-apply, for skills as for agents)
