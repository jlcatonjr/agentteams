"""P2 of the orchestrator-only-writes pilot: the ``write_policy`` switch and the ``AR_WRITE_POLICY`` check.

* The switch reaches the manifest only as ``"orchestrator-only"``; without it, generation is byte-identical.
* Under the switch, every non-orchestrator agent file is checked per framework shape: front-matter tools
  (copilot, claude), Goose recipe grants, Codex ``sandbox_mode``. agents-md cannot be checked at all.
* A team generated under the switch (P3) passes it on every framework.
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


def test_manifest_byte_identical_without_the_switch():  # noqa: D103
    desc = {"project_goal": "x" * 20, "project_name": "P", "components": [{"slug": "alpha", "name": "A"}]}
    base = analyze.build_manifest(dict(desc), framework="claude")
    default = analyze.build_manifest({**desc, "write_policy": "default"}, framework="claude")
    assert json.dumps(base, sort_keys=True) == json.dumps(default, sort_keys=True)
    assert "write_policy" not in base
    assert analyze.build_manifest({**desc, "write_policy": "orchestrator-only", "privilege_profile": "confined"},
                                  framework="claude")["write_policy"] == "orchestrator-only"


@pytest.mark.parametrize("framework", ["claude", "copilot-vscode", "goose", "codex"])
def test_rendered_team_byte_identical_without_the_switch(framework):  # all frameworks: the switch is off
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
    files = {"orchestrator.md": _md("tools: Edit, Write, Task") + "## Write Policy: Applying Proposals\n",
             "nested/orchestrator.md": _md("tools: Edit")}
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
    # the coordination server writes request/log files; the generator withholds it, and so does the audit
    ("extensions:\n  - type: stdio\n    name: agentteams_coordination\n    cmd: x\n", ["error"]),
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
    ok = 'name = "orchestrator"\ndeveloper_instructions = """\n## Write Policy: Applying Proposals\n"""\n'
    assert _codes({"orchestrator.toml": ok}, ".toml", "codex") == []


def test_goose_summon_is_dispatch():
    recipe = _RECIPE + "# agentteams-declared-tools: read, agent\nextensions: []\n"
    assert [sev for _, sev in _codes({"a.yaml": recipe}, ".yaml", "goose")] == ["error"]


def test_agents_md_cannot_be_enforced():
    assert _codes({"AGENTS.md": "# team\n"}, ".md", "agents-md") == [("AGENTS.md", "error")]


# --- end to end: a generated team, audited from disk --------------------------------------------


#: Frameworks the switch is allowed on (P4a key custody: their session sandbox denies the key directory).
_FRAMEWORKS = [("claude", ".md"), ("goose", ".yaml")]


@pytest.mark.parametrize("framework", ["copilot-vscode", "copilot-cli", "codex", "agents-md"])
def test_switch_refused_where_no_session_sandbox_guards_the_key(framework):
    desc = {"project_goal": "x" * 20, "project_name": "P", "components": [{"slug": "a", "name": "A"}],
            "write_policy": "orchestrator-only"}
    with pytest.raises(ValueError, match="claude and goose only"):
        analyze.build_manifest(desc, framework=framework)


@pytest.mark.parametrize("framework", ["claude", "goose"])
def test_switch_refused_with_a_cooperative_profile(framework):
    desc = {"project_goal": "x" * 20, "project_name": "P", "components": [{"slug": "a", "name": "A"}],
            "write_policy": "orchestrator-only", "privilege_profile": "cooperative"}
    with pytest.raises(ValueError, match="cooperative"):
        analyze.build_manifest(desc, framework=framework)


@pytest.fixture(scope="module")
def generated_teams(tmp_path_factory):
    """One team per framework under the switch, built in parallel (each build is ~40s of mostly waiting)."""
    root = tmp_path_factory.mktemp("teams")
    brief = json.loads(BRIEF.read_text(encoding="utf-8"))
    brief["write_policy"] = "orchestrator-only"
    brief["privilege_profile"] = "confined"
    brief_path = root / "brief.json"
    brief_path.write_text(json.dumps(brief), encoding="utf-8")
    procs = {
        fw: subprocess.Popen(
            [sys.executable, str(REPO / "build_team.py"), "--description", str(brief_path), "--project",
             str(root / fw), "--framework", fw, "--output", str(root / fw / "agents"), "--no-scan", "--yes"],
            cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        for fw, _ in _FRAMEWORKS
    }
    try:
        for fw, proc in procs.items():
            out, _ = proc.communicate(timeout=600)
            assert proc.returncode == 0, f"{fw}: {out[-1500:]}"
    finally:
        for proc in procs.values():
            if proc.poll() is None:
                proc.kill()
    return root, brief


@pytest.mark.parametrize("framework, ext", _FRAMEWORKS)
def test_generated_team_under_the_switch_passes(generated_teams, framework, ext):
    root, brief = generated_teams
    out = root / framework / "agents"
    agents = [p for p in out.rglob("*") if p.name.endswith(ext) and p.is_file()
              and "references" not in p.parts and p.name != "SETUP-REQUIRED.md"]
    assert agents, f"no {ext} agent files generated"
    result = run_post_audit(out, analyze.build_manifest(brief, framework=framework), ai_audit=False)
    found = [f for f in result.agent_refactor_findings if f.code == "AR_WRITE_POLICY"]
    assert not found, [(f.file, f.description[:120]) for f in found]
    texts = {p.name: p.read_text(encoding="utf-8") for p in agents}
    orch = next(t for n, t in texts.items() if n.startswith("orchestrator."))
    assert "Write Policy: Applying Proposals" in orch
    others = [t for n, t in texts.items() if not n.startswith(("orchestrator.", "bridge-orchestrator."))]
    assert others and all("Write Policy: Return Proposals" in t for t in others)
    assert (out / "references" / "write-policy.reference.md").is_file() or any(
        p.name == "write-policy.reference.md" for p in out.rglob("*"))


def test_same_team_without_the_switch_in_the_manifest_has_no_findings(generated_teams):
    root, brief = generated_teams
    manifest = {k: v for k, v in analyze.build_manifest(brief, framework="goose").items() if k != "write_policy"}
    result = run_post_audit(root / "goose" / "agents", manifest, ai_audit=False)
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


# --- capability keys other than `tools:` (agent-scoped-mcp report §5.1) -----------------------------


@pytest.mark.parametrize("extra, expected", [
    ("mcpServers:\n  - x:\n      type: stdio\n      command: sh", ["error"]),  # starts a process at agent start
    ("hooks:\n  PreToolUse: []", ["error"]),
    ("skills: [deploy]", ["error"]),
    ("allowed-tools: Read, Edit, Write", ["error"]),                           # legacy grant beside a narrow tools:
    ("capabilities: {}", ["error"]),
    ("permissionMode: bypassPermissions", ["error"]),
    ("permissionMode: acceptEdits", ["error"]),
    ("'permissionMode': \"dontAsk\"", ["error"]),                               # quoted key and value
    ("permissionMode: plan", []),
    ("permissionMode: default", []),
    ("disallowedTools: Bash", []),                                             # narrows only
    ("model: sonnet", []),
    ("agents: ['conflict-auditor']", []),                                      # Copilot roster, inert without dispatch
    ("memory: project", ["error"]),                                            # Claude enables Read/Write/Edit for it
    # YAML key forms the key regex can't read fail closed (@security review)
    ("? hooks\n: {PreToolUse: []}", ["error"]),                                  # explicit key
    ("<<: {hooks: {PreToolUse: []}}", ["error"]),                               # merge key
    ("&a hooks: {}", ["error"]),                                               # anchored key
    ('"perm\\u0069ssionMode": bypassPermissions', ["error"]),                  # escaped quoted key
])
def test_capability_keys_beside_tools(extra, expected):
    content = "---\nname: X\ndescription: d\ntools: ['read', 'search']\n" + extra + "\n---\n# X\n"
    assert [sev for _, sev in _codes({"a.md": content}, ".md", "claude")] == expected


def test_capability_keys_reported_with_a_bad_tools_key():
    content = "---\nname: X\nmcpServers: []\n---\n# X\n"                     # no tools: and an MCP server
    found = _check_write_policy({"a.md": content}, agent_ext=".md", framework="claude", enabled=True)
    assert len(found) == 2 and {"mcpServers" in f.description for f in found} == {True, False}


def test_orchestrator_keeps_its_capability_keys():
    content = ("---\nname: O\ndescription: d\ntools: Read, Edit\nmcpServers: []\n---\n# O\n"
               "## Write Policy: Applying Proposals\n")
    assert _codes({"orchestrator.md": content}, ".md", "claude") == []


# --- P5a: per-framework scope and the explicit profile ----------------------------------------------


_DESC = {"project_goal": "x" * 20, "project_name": "P", "components": [{"slug": "a", "name": "A"}],
         "write_policy": "orchestrator-only", "privilege_profile": "confined",
         "write_policy_frameworks": ["claude", "goose"]}


@pytest.mark.parametrize("framework, on", [("claude", True), ("goose", True), ("copilot-vscode", False),
                                           ("codex", False), ("agents-md", False)])
def test_switch_scoped_to_listed_frameworks(framework, on):
    manifest = analyze.build_manifest(dict(_DESC), framework=framework)
    assert (manifest.get("write_policy") == "orchestrator-only") is on


def test_unlisted_framework_renders_like_no_switch():
    base = {k: v for k, v in _DESC.items() if k not in ("write_policy", "write_policy_frameworks")}
    assert json.dumps(analyze.build_manifest(dict(_DESC), framework="codex"), sort_keys=True) == \
        json.dumps(analyze.build_manifest(base, framework="codex"), sort_keys=True)


@pytest.mark.parametrize("bad", [[], ["codex"], "claude"])
def test_scope_list_validated(bad):
    with pytest.raises(ValueError, match="write_policy_frameworks"):
        analyze.build_manifest({**_DESC, "write_policy_frameworks": bad}, framework="claude")


def test_switch_needs_an_explicit_profile():
    desc = {k: v for k, v in _DESC.items() if k != "privilege_profile"}
    with pytest.raises(ValueError, match="explicit privilege_profile"):
        analyze.build_manifest(desc, framework="claude")
