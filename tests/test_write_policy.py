"""P2 of the orchestrator-only-writes pilot: the ``write_policy`` switch and the ``AR_WRITE_POLICY`` check.

* The switch reaches the manifest only as ``"orchestrator-only"``; without it, generation is byte-identical.
* Under the switch, every non-orchestrator agent file is checked per framework shape: front-matter tools
  (copilot, claude), Goose recipe grants, Codex ``sandbox_mode``. agents-md cannot be checked at all.
* Until P3 changes the templates, a generated team under the switch fails this check (expected).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams import analyze
from agentteams.audit import run_post_audit
from agentteams.audit_agent_contract import _check_write_policy

REPO = Path(__file__).resolve().parent.parent
BRIEF = REPO / "examples" / "software-project" / "brief.json"
_RECIPE = 'version: "1.0.0"\ntitle: "t"\ninstructions: |\n  hi\n'


def _md(tools_line: str | None) -> str:
    return "---\nname: X\ndescription: d\n" + (f"{tools_line}\n" if tools_line is not None else "") + "---\n# X\n"


def _codes(file_map, agent_ext, framework):
    found = _check_write_policy(file_map, agent_ext=agent_ext, framework=framework, enabled=True)
    return sorted((f.file, f.severity) for f in found)


# --- the switch reaches the manifest only when set ----------------------------------------------


def test_manifest_byte_identical_without_the_switch():
    desc = {"project_goal": "x" * 20, "project_name": "P", "components": [{"slug": "alpha", "name": "A"}]}
    base = analyze.build_manifest(dict(desc), framework="claude")
    default = analyze.build_manifest({**desc, "write_policy": "default"}, framework="claude")
    assert json.dumps(base, sort_keys=True) == json.dumps(default, sort_keys=True)
    assert "write_policy" not in base
    assert analyze.build_manifest({**desc, "write_policy": "orchestrator-only"},
                                  framework="claude")["write_policy"] == "orchestrator-only"


@pytest.mark.parametrize("framework", ["claude", "copilot-vscode", "goose", "codex"])
def test_rendered_team_byte_identical_without_the_switch(framework):
    from agentteams import render

    brief = json.loads(BRIEF.read_text(encoding="utf-8"))
    templates = REPO / "agentteams" / "templates"
    base = render.render_all(analyze.build_manifest(dict(brief), framework=framework), templates_dir=templates)
    default = render.render_all(analyze.build_manifest({**brief, "write_policy": "default"}, framework=framework),
                                templates_dir=templates)
    assert base == default


def test_check_is_inert_without_the_switch():
    assert _check_write_policy({"a.md": _md("tools: Edit")}, agent_ext=".md", framework="claude",
                               enabled=False) == []


# --- markdown agents (copilot-vscode, copilot-cli, claude) --------------------------------------


@pytest.mark.parametrize("tools, framework, expected", [
    ("tools: Read, Grep", "claude", []),
    ("tools: Read, Edit", "claude", [("a.md", "error")]),
    ("tools: Read, Bash", "claude", [("a.md", "warning")]),
    ("tools: Bash(python -m x:*)", "claude", [("a.md", "warning")]),
    (None, "claude", [("a.md", "error")]),                      # no tools key: inherits everything
    ("tools:", "claude", [("a.md", "error")]),                  # empty value, no block list
    ("tools: ['read', 'search']", "copilot-vscode", []),
    ("tools: ['read', 'edit']", "copilot-vscode", [("a.md", "error")]),
    ("tools: ['read', 'execute']", "copilot-vscode", [("a.md", "error")]),  # no per-agent sandbox
    ("tools: ['read', 'execute']", "copilot-cli", [("a.md", "error")]),
    ("tools:\n  - read\n  - write", "claude", [("a.md", "error")]),         # block list
    ("tools: ~", "claude", [("a.md", "error")]),                  # null: inherits everything
    ("tools: null", "claude", [("a.md", "error")]),
    ('tools: ""', "claude", [("a.md", "error")]),
    ("tools: []", "copilot-vscode", [("a.agent.md", "error")]),  # empty list: host semantics unverified
    ("tools: Read\ntools: Edit", "claude", [("a.md", "error")]),  # duplicate key
    ("tools: Read, Task", "claude", [("a.md", "error")]),         # dispatch can start a writer
    ("tools: ['read', 'agent']", "copilot-vscode", [("a.md", "error")]),
    ("tools: ['read', 'editFiles']", "copilot-vscode", [("a.md", "error")]),  # unknown = unclassifiable
    ("tools: Read, mcp__fs__write", "claude", [("a.md", "error")]),
    ("tools: ['*']", "copilot-vscode", [("a.md", "error")]),
    ("tools: Read, Grep, Glob, WebFetch, TodoWrite", "claude", []),
    ("tools: ['read',\n  'edit']", "copilot-vscode", [("a.md", "error")]),    # flow list over lines
    ("tools: >-\n  Read, Edit", "claude", [("a.md", "error")]),               # block scalar
    ("tools:\n  - read\n  # c\n  - edit", "claude", [("a.md", "error")]),   # comment inside a block list
    ("tools: ['read', 'powershell']", "copilot-vscode", [("a.md", "error")]),
    ("tools: Read, Bash", "unknown-fw", [("a.md", "error")]),                  # unknown framework: fail closed
    ("tools: Read,\n  Edit", "claude", [("a.md", "error")]),                  # continued plain scalar
    ("tools: 'Read,\n  Edit'", "claude", [("a.md", "error")]),                # continued quoted scalar
    ("tools:\n  - Read\n\n  - Edit", "claude", [("a.md", "error")]),        # blank line inside a block list
    ("tools:\n  - Read\n  - Grep\n", "claude", []),                         # clean block list
    ("tools:\n  - Read\n# c\n  - Edit", "claude", [("a.md", "error")]),     # column-0 comment inside the list
])
def test_markdown_agents(tools, framework, expected):
    ext = ".agent.md" if framework.startswith("copilot") else ".md"
    name = "a" + ext
    got = _codes({name: _md(tools)}, ext, framework)
    assert got == [(name, sev) for _, sev in expected]


def test_only_the_shallowest_orchestrator_is_exempt():
    files = {"orchestrator.md": _md("tools: Edit, Write, Task"), "nested/orchestrator.md": _md("tools: Edit")}
    assert _codes(files, ".md", "claude") == [("nested/orchestrator.md", "error")]
    twins = {"a/orchestrator.md": _md("tools: Edit"), "b/orchestrator.md": _md("tools: Edit")}
    assert _codes(twins, ".md", "claude") == [("a/orchestrator.md", "error"), ("b/orchestrator.md", "error")]


# --- Goose recipes ------------------------------------------------------------------------------


@pytest.mark.parametrize("body, expected", [
    ("# agentteams-declared-tools: read, search\nextensions: []\n", []),
    ("# agentteams-declared-tools: read, edit\nextensions: []\n", ["error"]),
    ("# agentteams-declared-tools: read, execute\nextensions: []\n", ["warning"]),
    ("extensions: []\n", []),                                                           # unmarked, nothing granted
    ("extensions:\n  - type: builtin\n    name: developer\n", ["error", "warning"]),     # unmarked, full developer
    ("", ["error"]),                                                                    # fail-open extensions
    # the marker states its own authority: the real grant is judged too
    ("# agentteams-declared-tools: read\nextensions:\n  - type: builtin\n    name: developer\n"
     "    available_tools: [write, tree]\n", ["error"]),
    ("extensions:\n  - type: stdio\n    name: some_mcp\n    cmd: x\n", ["error"]),       # unclassifiable extension
    ("extensions:\n  - type: builtin\n    name: summon\n", ["error"]),                  # dispatch
    ("extensions:\n  - type: stdio\n    name: agentteams_readfs\n    cmd: x\n", []),
])
def test_goose_recipes(body, expected):
    assert [sev for _, sev in _codes({"a.yaml": _RECIPE + body}, ".yaml", "goose")] == expected


def test_recipe_without_an_exact_version_line_is_still_checked():
    recipe = 'version: 1.0.0\ntitle: t\nextensions:\n  - type: builtin\n    name: developer\n'
    assert [sev for _, sev in _codes({"a.yaml": recipe}, ".yaml", "goose")] == ["error", "warning"]


def test_bridge_orchestrator_exempt_only_on_goose():
    assert _codes({"bridge-orchestrator.md": _md("tools: Edit")}, ".md", "claude") == [
        ("bridge-orchestrator.md", "error")]


def test_goose_bridge_orchestrator_exempt():
    assert _codes({"bridge-orchestrator.yaml": _RECIPE + "extensions:\n  - type: builtin\n    name: developer\n"},
                  ".yaml", "goose") == []


# --- Codex --------------------------------------------------------------------------------------


@pytest.mark.parametrize("body, expected", [
    ('name = "a"\nsandbox_mode = "read-only"\n', []),
    ('name = "a"\nsandbox_mode = "workspace-write"\n', ["error"]),
    ('name = "a"\n', ["error"]),                     # missing: inherits the parent's mode
    # a decoy line inside a multi-line string must not satisfy the check
    ('name = "a"\ndeveloper_instructions = """\nsandbox_mode = "read-only"\n"""\nsandbox_mode = "danger-full-access"\n',
     ["error"]),
    ('name = "a"\nsandbox_mode = ', ["error"]),          # unparseable
])
def test_codex_agents(body, expected):
    assert [sev for _, sev in _codes({"a.toml": body}, ".toml", "codex")] == expected


def test_codex_references_dir_is_checked_and_exemption_needs_the_name():
    files = {"references/x.toml": 'name = "x"\n', "orchestrator.toml": 'name = "lean-prover"\n'}
    assert _codes(files, ".toml", "codex") == [("orchestrator.toml", "error"), ("references/x.toml", "error")]
    assert _codes({"orchestrator.toml": 'name = "orchestrator"\n'}, ".toml", "codex") == []


def test_goose_summon_is_dispatch():
    recipe = _RECIPE + "# agentteams-declared-tools: read, agent\nextensions: []\n"
    assert [sev for _, sev in _codes({"a.yaml": recipe}, ".yaml", "goose")] == ["error"]


def test_agents_md_cannot_be_enforced():
    assert _codes({"AGENTS.md": "# team\n"}, ".md", "agents-md") == [("AGENTS.md", "error")]


# --- end to end: a generated team, audited from disk --------------------------------------------


@pytest.fixture(scope="module", params=[("goose", ".yaml"), ("codex", ".toml")], ids=["goose", "codex"])
def generated(request, tmp_path_factory):
    """One team per framework whose agent files the disk loader used to skip (claude/copilot are unit-covered)."""
    framework, ext = request.param
    tmp_path = tmp_path_factory.mktemp(framework)
    brief = json.loads(BRIEF.read_text(encoding="utf-8"))
    brief["write_policy"] = "orchestrator-only"
    brief_path = tmp_path / "brief.json"
    brief_path.write_text(json.dumps(brief), encoding="utf-8")
    out = tmp_path / "agents"
    proc = subprocess.run(
        [sys.executable, str(REPO / "build_team.py"), "--description", str(brief_path), "--project", str(tmp_path),
         "--framework", framework, "--output", str(out), "--no-scan", "--yes"],
        cwd=tmp_path, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-1500:]
    return out, analyze.build_manifest(brief, framework=framework), ext


def test_generated_team_under_the_switch_is_flagged_until_p3(generated):
    out, manifest, ext = generated
    assert any(p.name.endswith(ext) for p in out.rglob("*")), f"no {ext} agent files generated"
    result = run_post_audit(out, manifest, ai_audit=False)
    flagged = {f.file for f in result.agent_refactor_findings if f.code == "AR_WRITE_POLICY"}
    assert flagged, "the disk audit saw no agent files to check"
    assert not any(Path(f).name.startswith("orchestrator.") for f in flagged)


def test_same_team_without_the_switch_has_no_findings(generated):
    out, manifest, _ = generated
    result = run_post_audit(out, {k: v for k, v in manifest.items() if k != "write_policy"}, ai_audit=False)
    assert not [f for f in result.agent_refactor_findings if f.code == "AR_WRITE_POLICY"]


def test_disk_loader_reports_symlinks_and_undecodable_files(tmp_path):
    from agentteams.audit import _load_files_from_disk

    team = tmp_path / "team"
    team.mkdir()
    (team / "a.yaml").write_text("x: 1\n", encoding="utf-8")
    (team / "bad.yaml").write_bytes(b"\xff\xfe\x00bad")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.yaml").write_text("secret\n", encoding="utf-8")
    (team / "link.yaml").symlink_to(outside / "secret.yaml")
    (team / "linkdir").symlink_to(outside, target_is_directory=True)
    unreadable: list[str] = []
    assert sorted(_load_files_from_disk(team, agent_ext=".yaml", unreadable=unreadable)) == ["a.yaml"]
    assert sorted(unreadable) == ["bad.yaml", "link.yaml", "linkdir"]
    found = _check_write_policy({}, agent_ext=".yaml", framework="goose", enabled=True, unreadable=unreadable)
    assert sorted((f.file, f.severity) for f in found) == [
        ("bad.yaml", "error"), ("link.yaml", "error"), ("linkdir", "error")]


def test_goose_sub_recipes_and_codex_mcp_servers_refused():
    recipe = _RECIPE + "extensions: []\nsub_recipes:\n  - name: x\n    path: x.yaml\n"
    assert [sev for _, sev in _codes({"a.yaml": recipe}, ".yaml", "goose")] == ["error"]
    quoted = _RECIPE + 'extensions: []\n"sub_recipes":\n  - name: x\n'
    assert [sev for _, sev in _codes({"a.yaml": quoted}, ".yaml", "goose")] == ["error"]
    agent = 'name = "a"\nsandbox_mode = "read-only"\n[mcp_servers.fs]\ncommand = "x"\n'
    assert [sev for _, sev in _codes({"a.toml": agent}, ".toml", "codex")] == ["error"]


def test_default_loader_still_follows_links(tmp_path):
    """Without the switch, a linked agent file is still loaded, so the other per-agent checks see it."""
    from agentteams.audit import _load_files_from_disk

    team = tmp_path / "team"
    team.mkdir()
    real = tmp_path / "real.md"
    real.write_text("---\nname: x\ntools: Edit\n---\n", encoding="utf-8")
    (team / "linked.md").symlink_to(real)
    assert "linked.md" in _load_files_from_disk(team, agent_ext=".md")
